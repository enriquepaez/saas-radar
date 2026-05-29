# Implementación: #2 — db_layer_with_migrations

## Qué cambió

- **`src/saas_radar/storage/__init__.py`**: creado vacío (re-export mínimo). Convierte `storage/` en paquete Python para que `from saas_radar.storage.db import ...` funcione sin trucos de path.

- **`src/saas_radar/storage/db.py`**: implementación completa de la capa de persistencia. Antes no existía. Ahora contiene: DDL de 6 tablas + 13 índices, 2 funciones privadas de soporte, y 8 funciones públicas: `init_db`, `save_to_db`, `load_from_db`, `db_stats`, `persist_run_to_db`, `load_active_opportunities`, `persist_meta_recommendations`, `has_successful_run`.

- **`tests/test_db.py`**: creado con 18 tests que cubren los 15 casos de aceptación especificados + 3 casos adicionales para `_extract_target`.

## Por qué

### Orden migración-antes-índices

El bug más sutil de la implementación inicial fue crear los índices **antes** de ejecutar las migraciones. El índice `idx_posts_semantic ON reddit_posts(semantic_score)` falla con `no such column: semantic_score` si la tabla ya existe sin esa columna (caso de la BD legacy). La solución es ejecutar las migraciones `ALTER TABLE` inmediatamente después de `CREATE TABLE IF NOT EXISTS` y **antes** de `CREATE INDEX IF NOT EXISTS`. Esto garantiza que cuando se crea el índice, la columna ya existe.

### No singleton de engine

Se crea un engine nuevo por llamada a función en lugar de un singleton global. En features futuras, el orquestador (`ai_analyzer.py`) decidirá si reutilizar o no el engine. Un singleton en un módulo de storage introduce acoplamiento de ciclo de vida que dificulta los tests con BD temporal (cada test usa una URL diferente).

### INSERT OR IGNORE vía staging

Pandas `df.to_sql()` no soporta `INSERT OR IGNORE` directamente. La técnica de staging consiste en: (1) volcar el DataFrame completo en `_staging_<tabla>` con `if_exists="replace"` (sin restricciones de unicidad), (2) ejecutar `INSERT OR IGNORE INTO <tabla> SELECT * FROM _staging` con SQL puro (aquí SQLite aplica la PRIMARY KEY), (3) eliminar la staging. Esto permite bulk inserts idempotentes sin iterar fila a fila, lo que es crítico para los ~20k posts del legacy.

### canonical_id autorreferencial

La feature #15 (dedup Jaccard) calculará el `canonical_id` real. Por ahora, cada oportunidad nueva que no traiga `canonical_id` se actualiza con `UPDATE opportunities SET canonical_id = id WHERE id = <nuevo_id>` inmediatamente tras el INSERT. Esto significa "esta opp es canónica de sí misma", que es el estado correcto antes de que exista dedup. `load_active_opportunities` filtra `id = canonical_id AND discarded = 0` — si `canonical_id` fuera NULL, la fila no aparecería, rompiendo la feature #7.

## Impacto en el pipeline

- **BD**: toda escritura de posts, comentarios, runs y oportunidades pasa por este módulo.
- **Scraping (feature #4)**: `save_to_db(df, "reddit_posts")` y `save_to_db(df, "reddit_comments")` recibirán los DataFrames del scraper.
- **AI analyzer (feature #11)**: `persist_run_to_db`, `has_successful_run`, `load_active_opportunities`.
- **Meta-análisis (feature #13)**: `persist_meta_recommendations`.
- **Data loader (feature #7)**: `load_from_db` y `db_stats`.
- **BD legacy**: `init_db` sobre `data/saas.db` ejecuta `CREATE TABLE IF NOT EXISTS` (no toca datos) y las migraciones idempotentes (solo añade columnas si faltan). Los ~19702 posts existentes se preservan.

## Explicación técnica

### `_CREATE_TABLES` y `_CREATE_INDEXES`

Listas de strings con DDL SQL. Se definen como constantes de módulo para que estén disponibles en el momento del import sin ejecutar nada (sin side effects). Cada tabla usa `CREATE TABLE IF NOT EXISTS` — SQLite ignora el CREATE si la tabla ya existe, preservando los datos del legacy.

### `_get_db_url(db_url)`

Recibe `db_url: str | None`. Si es `None`, lee `os.environ.get("DB_URL", "sqlite:///data/saas.db")`. Centraliza la resolución de URL en un lugar para evitar duplicación en cada función pública.

### `_make_engine(db_url)`

Llama a `create_engine(db_url, connect_args={"check_same_thread": False})`. El arg `check_same_thread=False` es necesario para SQLite cuando se usa desde múltiples threads (el pipeline usa `ThreadPoolExecutor` en feature #12). Sin él, SQLite lanza `ProgrammingError` si la conexión se usa desde un thread diferente al que la creó.

### `_column_exists(conn, table, column)`

Ejecuta `PRAGMA table_info(<tabla>)` que devuelve una fila por columna: `(cid, name, type, notnull, dflt_value, pk)`. El índice `[1]` es el nombre. Itera y busca coincidencia exacta con `column`. Si la tabla no existe, PRAGMA devuelve lista vacía → devuelve `False`. Esto es más robusto que parsear `sqlite_master`.

### `_extract_target(rec_type, action)`

Si `rec_type` contiene `"subreddit"`, divide `action` por espacios y busca el primer token que empiece por `r/`. Si no hay ninguno, toma los primeros 50 chars del action. Para otros tipos, siempre toma `action[:50]`. Los 50 chars son el límite que permite identificar unívocamente la recomendación para el dedup de `persist_meta_recommendations`.

### `init_db(db_url=None)`

Orden de operaciones dentro de una sola transacción:
1. `CREATE TABLE IF NOT EXISTS` × 6 — crea las tablas que falten.
2. `ALTER TABLE reddit_posts ADD COLUMN semantic_score REAL` — solo si `_column_exists` devuelve `False`.
3. `ALTER TABLE opportunities ADD COLUMN canonical_id INTEGER` — ídem.
4. `CREATE INDEX IF NOT EXISTS` × 13 — ahora que las columnas existen, los índices pueden crearse.

SQLite no soporta `ALTER TABLE ADD COLUMN IF NOT EXISTS`, por eso se usa `PRAGMA table_info` para detectar si la columna existe antes de intentar `ALTER TABLE`.

### `save_to_db(df, table_name, db_url=None)`

1. `df = df.copy()` — evita mutar el DataFrame original del caller (el legacy tenía bugs de este tipo).
2. `df["subreddit"].str.lower()` — normaliza subreddit a minúsculas. `str.lower()` de pandas aplica lowercase a cada elemento del Series, manejando NaN sin lanzar excepción.
3. `df.to_sql(staging, conn, if_exists="replace", index=False)` — escribe el DataFrame en `_staging_<tabla>`. `if_exists="replace"` hace DROP + CREATE de la staging en cada llamada (es temporal, no importa). `index=False` evita que pandas añada una columna numérica automática.
4. `conn.execute(text(f"INSERT OR IGNORE INTO {table_name} ({cols}) SELECT {cols} FROM {staging}"))` — `INSERT OR IGNORE` hace que SQLite ignore la fila si ya existe una con la misma PRIMARY KEY. Al especificar explícitamente las columnas (`{cols}`), el INSERT funciona aunque la tabla destino tenga más columnas que el staging (p.ej. columnas con DEFAULT).
5. Devuelve `count_after - count_before` — número de filas **efectivamente** insertadas (no las ignoradas).

### `load_from_db(table_name, db_url=None)`

`pd.read_sql(f"SELECT * FROM {table_name}", conn)` — pandas ejecuta la query y construye un DataFrame con los tipos inferidos de SQLite. Si la tabla tiene 0 filas, devuelve un DataFrame vacío **con las columnas correctas** (inferidas del schema). Esto es importante para que el código llamador pueda acceder a `df.columns` sin errores.

### `db_stats(db_url=None)`

Itera sobre `("reddit_posts", "reddit_comments")` y ejecuta `SELECT COUNT(*) FROM <tabla>`. El bloque `try/except Exception` captura `OperationalError` si la tabla no existe (p.ej. en una BD completamente nueva antes de `init_db`). En ese caso devuelve 0 para esa tabla sin propagar el error, lo que permite llamar a `db_stats` de forma segura en cualquier momento del ciclo de vida.

### `persist_run_to_db(run_data, opportunities, db_url=None)`

1. Construye la lista de columnas presentes en `run_data` para el INSERT en `analysis_runs` (solo inserta las que vienen, el resto toma DEFAULT).
2. `conn.execute(text("SELECT last_insert_rowid()"))` — SQLite devuelve el ROWID de la última inserción en la conexión actual. Es equivalente a `cursor.lastrowid` de sqlite3 pero funciona con SQLAlchemy text().
3. Para cada opp: inserta con `all_cols = ["run_id"] + opp_fields` — siempre incluye todas las columnas para que los DEFAULT de la tabla (reviewed=0, starred=0, discarded=0) se apliquen correctamente.
4. Si `opp.get("canonical_id") is None`: ejecuta `UPDATE opportunities SET canonical_id = :oid WHERE id = :oid`. El mismo valor para ambos parámetros hace que `canonical_id = id` (autoreferencia). Usa parámetro nombrado `:oid` en lugar de f-string para evitar SQL injection y para que SQLAlchemy gestione el binding de tipos.

### `load_active_opportunities(db_url=None)`

`SELECT * FROM opportunities WHERE id = canonical_id AND discarded = 0` — la condición `id = canonical_id` filtra las oportunidades que son su propio canónico (no son duplicados que apuntan a otra). Las que tienen `discarded = 1` son las marcadas como desechadas por el usuario. Las que tienen `canonical_id IS NULL` no pasan el filtro (NULL != cualquier valor, incluyendo NULL).

### `persist_meta_recommendations(run_id, recs, db_url=None)`

Para cada rec: calcula `target = _extract_target(type, action)`, busca en BD una fila con `type=t AND target=tg AND acted=0`. Si existe, incrementa `recurrence` con `UPDATE ... SET recurrence = recurrence + 1`. Si no existe, inserta nueva fila con `recurrence=1`. La condición `acted=0` es clave: si el tuner ya aplicó la recomendación (`acted=1`), no la reutiliza — inserta una nueva entrada. Esto modela "la misma señal apareció de nuevo después de actuar".

### `has_successful_run(db_url=None)`

`SELECT COUNT(*) FROM analysis_runs WHERE status = 'ok'`. El bloque `try/except` captura el caso en que la tabla `analysis_runs` no existe (BD nueva sin `init_db`). Esta función la usa `main.py` para decidir entre modo INCREMENTAL (24h) y CARGA COMPLETA (365d) — debe ser robusta incluso con BD sin inicializar.

## Tests añadidos

| Test | Caso cubierto |
|---|---|
| `test_init_db_idempotent` | Llamar `init_db` dos veces no falla y las 6 tablas existen |
| `test_migration_semantic_score_added` | Tabla `reddit_posts` sin `semantic_score` → `init_db` la añade; segunda llamada no falla |
| `test_migration_canonical_id_added` | Tabla `opportunities` sin `canonical_id` → `init_db` la añade; segunda llamada no falla |
| `test_save_to_db_insert_or_ignore` | 3 filas + 1 dup → 3 insertadas; 1 fila nueva + 1 dup → 1 insertada; mismas filas → 0 insertadas |
| `test_save_to_db_normalizes_subreddit` | `subreddit="Entrepreneur"` → en BD queda `"entrepreneur"` |
| `test_load_from_db_returns_rows` | Devuelve DataFrame con las filas insertadas |
| `test_db_stats_empty` | BD vacía → `{"reddit_posts": 0, "reddit_comments": 0}` |
| `test_db_stats_with_rows` | 2 posts insertados → `{"reddit_posts": 2, "reddit_comments": 0}` |
| `test_has_successful_run_empty` | BD vacía → `False` |
| `test_has_successful_run_with_ok_run` | Run con `status='ok'` → `True` |
| `test_persist_run_to_db_returns_run_id` | Insertar run con 2 opps → `run_id >= 1` y opps tienen `run_id` correcto |
| `test_persist_run_to_db_canonical_id_self_referential` | Cada opp nueva tiene `canonical_id == id` |
| `test_load_active_opportunities` | Opp con `discarded=0` aparece; opp con `discarded=1` no aparece |
| `test_persist_meta_recommendations_increments_recurrence` | Misma rec dos veces → `recurrence=2` |
| `test_persist_meta_recommendations_no_increment_if_acted` | Rec con `acted=1` → segunda llamada inserta nueva fila (total 2) |
| `test_extract_target_subreddit_type_with_r_prefix` | `"add r/microsaas to the list"` → `"r/microsaas"` |
| `test_extract_target_subreddit_type_no_r_prefix` | Sin `r/` → primeros 50 chars del action |
| `test_extract_target_non_subreddit_type` | Tipo no-subreddit → primeros 50 chars del action |

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.23.0
collected 18 items

tests/test_db.py ..................                                      [100%]

============================== 18 passed in 0.37s ==============================
```

`ruff check src/saas_radar/storage/` → `All checks passed!`

`./init.sh` → `[OK] Entorno listo. Puedes empezar a trabajar.`
