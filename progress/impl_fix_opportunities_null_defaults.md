# Implementación: 27 — fix_opportunities_null_defaults

## Qué cambió

- **`src/saas_radar/storage/db.py`** — 2 cambios:
  1. `persist_run_to_db`: tras construir `opp_row = {f: opp.get(f) for f in opp_fields}`,
     un dict `flag_defaults = {"reviewed": 0, "starred": 0, "discarded": 0}` rellena
     con `0` cualquiera de esas tres claves que venga a `None` (antes → se insertaba
     `NULL` explícito; después → siempre `0` salvo valor explícito distinto).
  2. `init_db`: nueva migración de backfill dentro de la misma transacción que las
     migraciones existentes (`semantic_score`, `canonical_id`):
     `UPDATE opportunities SET <flag> = 0 WHERE <flag> IS NULL` para las tres columnas.
- **`tests/test_db.py`**: 5 tests nuevos (secciones 16 y 17) que cubren los dos cambios.

## Por qué

### El bug: semántica de DEFAULT en SQL con INSERT parametrizado

El schema declara `reviewed INTEGER DEFAULT 0` (ídem `starred`, `discarded`). Pero el
`DEFAULT` de SQL **solo aplica cuando la columna no aparece en el INSERT**. El INSERT de
`persist_run_to_db` lista SIEMPRE las 24 columnas de `opp_fields`, con placeholders
parametrizados (`:reviewed`, `:starred`, `:discarded`). Como el dict de oportunidad que
produce la síntesis LLM no trae esas claves, `opp.get(f)` devuelve `None`, el driver lo
traduce a `NULL`, y SQLite almacena **NULL explícito** — el DEFAULT nunca entra en juego.

### El efecto: NULL vs 0 en el WHERE

`load_active_opportunities` filtra con `WHERE id = canonical_id AND discarded = 0`.
En SQL, cualquier comparación con NULL (`NULL = 0`, `NULL != 0`) evalúa a **UNKNOWN**,
no a verdadero, así que las filas con `discarded = NULL` no matchean y desaparecen del
resultado. Consecuencia observada en la BD de producción (release `db-latest`): las
opportunities existentes tienen `discarded = NULL` → `load_active_opportunities`
devuelve 0 filas → el agente GTM (`--all-pending`) no ve ninguna oportunidad →
`opportunity_gtm` vacía pese a haber opps con `priority_score` 8.

### Alternativas descartadas

- **Quitar los flags del INSERT** (dejar que el DEFAULT actúe): funcionaría para el
  caso "clave ausente", pero rompería el caso legítimo en que el caller pasa
  `discarded=1` (el test 13 existente lo hace), obligando a construir la lista de
  columnas dinámicamente por opp. Más complejo y más frágil que un dict de defaults.
- **`COALESCE(discarded, 0)` en el WHERE de `load_active_opportunities`**: solo
  parchea un lector; cualquier otra query futura sobre esos flags volvería a tropezar
  con el NULL. Mejor sanear el dato en origen (insert) y el histórico (backfill).
- **Script de backfill suelto** (tipo `scripts/backfill_canonical.py`): requeriría
  ejecución manual en cada entorno (local, CI, BD restaurada de release). El backfill
  en `init_db` se ejecuta automáticamente al arrancar cualquier pipeline, es idempotente
  y sigue el patrón de migraciones ya establecido en el módulo.

## Impacto en el pipeline

- **GTM agent** (`--all-pending`): vuelve a recibir las opportunities activas tras el
  primer `init_db` sobre la BD afectada. Es el efecto principal del fix.
- **`load_active_opportunities`**: sin cambios de código; ahora su filtro
  `discarded = 0` matchea porque los datos ya no tienen NULL.
- **Dedup** (`find_canonical` / `find_canonical_v2`): sin impacto. El pool `existing_rows`
  de `persist_run_to_db` se carga con `SELECT ... FROM opportunities` sin filtrar por
  `discarded`, y las funciones de dedup no leen esos flags.
- **Notificaciones Telegram**: `send_opportunity_alert` recibe el dict de la síntesis
  (antes de persistir), no filas de BD, así que no estaba afectada ni cambia.
- **BD / migraciones**: `init_db` gana un tercer bloque de migración. Sobre BD vacía o
  recién creada el UPDATE afecta 0 filas y no falla (la tabla ya existe porque
  `_CREATE_TABLES` corre antes en la misma transacción).
- **Runs futuros**: toda opp nueva se inserta con los tres flags a 0 (o al valor
  explícito que traiga el dict), nunca NULL.

## Explicación técnica

### `persist_run_to_db` — dict de defaults

```python
flag_defaults = {"reviewed": 0, "starred": 0, "discarded": 0}
```

Se define junto a `opp_fields`, **fuera** del loop `for opp in opportunities` (se
construye una vez, no por cada opp). Es un dict literal clave→valor por defecto: las
tres columnas de estado humano que el LLM nunca produce.

```python
opp_row = {f: opp.get(f) for f in opp_fields}
for flag, default in flag_defaults.items():
    if opp_row[flag] is None:
        opp_row[flag] = default
```

- `opp.get(f)` (método de dict con default implícito `None`) devuelve `None` tanto si
  la clave **no existe** como si existe **con valor None**. Por eso un único chequeo
  `is None` sobre `opp_row` cubre los dos casos del acceptance: tras la dict
  comprehension ambos colapsan en `opp_row[flag] is None`.
- `opp_row[flag]` (acceso directo, no `.get`) es seguro aquí: `flag_defaults` solo
  contiene claves que están garantizadas en `opp_fields`, así que la comprehension ya
  las creó. Si alguien renombrara una columna y desincronizara los dos dicts, el
  `KeyError` sería inmediato y ruidoso — preferible a un `.get` que ocultara el bug.
- `is None` y no `not opp_row[flag]`: `0` y `False` son falsy en Python; con `not` un
  `discarded=0` explícito también entraría al branch (inofensivo aquí, pero impreciso).
  El contrato es "solo rellenar cuando falta el valor", y eso es exactamente `is None`.
- Valores explícitos (`discarded=1` del test 13, o los `1,1,1` del test nuevo) pasan
  intactos: no son `None`, el branch no se ejecuta.

### `init_db` — migración de backfill

```python
for flag in ("reviewed", "starred", "discarded"):
    result = conn.execute(text(f"UPDATE opportunities SET {flag} = 0 WHERE {flag} IS NULL"))
    if result.rowcount:
        logger.info("Migración: backfill %s=0 en %d opportunities", flag, result.rowcount)
```

- **Ubicación**: dentro del `with conn.begin()` de `init_db`, después de las
  migraciones de columnas (`semantic_score`, `canonical_id`) y antes de crear índices.
  Va tras el ALTER de `canonical_id` por consistencia con el orden "primero shape,
  luego datos"; y dentro de la transacción para que un fallo a mitad no deje la BD en
  estado parcial (rollback automático).
- **`IS NULL` y no `= NULL`**: en SQL, `NULL` no es un valor comparable sino "ausencia
  de valor"; `columna = NULL` evalúa a UNKNOWN para TODAS las filas (incluso las que
  tienen NULL) y el UPDATE no tocaría nada. `IS NULL` es el operador específico para
  testear ausencia. Es exactamente la misma trampa semántica que causó el bug original
  en el `WHERE discarded = 0`.
- **Idempotencia**: la primera ejecución convierte los NULL en 0; en la segunda el
  `WHERE flag IS NULL` ya no matchea ninguna fila y el UPDATE es un no-op. Ejecutable
  N veces sin efecto adicional. Sobre BD vacía, 0 filas afectadas, sin error.
- **f-string en el SQL**: interpolar `{flag}` en el texto de la query suele ser mala
  práctica (inyección SQL), pero aquí el valor viene de una tupla literal hardcodeada
  de 3 strings, nunca de input externo. Los nombres de columna no pueden parametrizarse
  con placeholders (`:x` solo sustituye valores, no identificadores), así que la
  f-string es la opción estándar — mismo patrón que ya usa `_column_exists` con
  `PRAGMA table_info({table})`.
- **`result.rowcount`**: atributo del resultado de SQLAlchemy con el número de filas
  afectadas por el UPDATE. Se usa como guard del log: solo se emite `logger.info`
  cuando la migración hizo trabajo real, evitando 3 líneas de ruido en cada arranque
  (init_db corre en cada run del pipeline). Mismo criterio que los logs de las
  migraciones de columnas, que solo se emiten cuando el ALTER se ejecuta.

## Tests añadidos

Todos en `tests/test_db.py`, sobre BD temporal (`tmp_db` fixture, `tmp_path` de pytest):

1. `test_persist_run_to_db_flags_default_to_zero_when_keys_missing` — dict de opp sin
   las claves `reviewed`/`starred`/`discarded` (el caso real del LLM) → SELECT directo
   confirma `(0, 0, 0)`, no NULL.
2. `test_persist_run_to_db_flags_default_to_zero_when_keys_are_none` — claves presentes
   pero con valor `None` explícito → también `(0, 0, 0)` (segunda mitad del acceptance).
3. `test_persist_run_to_db_flags_respect_explicit_values` — claves con valor `1` → se
   respetan `(1, 1, 1)`; el default no pisa valores legítimos.
4. `test_init_db_backfills_null_flags_and_restores_active_opportunities` — fixture con
   una opp insertada a mano con SQL (`reviewed/starred/discarded = NULL`,
   `canonical_id = id` autorreferencial, replicando el estado de la BD de producción).
   Verifica primero que `load_active_opportunities` devuelve 0 filas (reproduce el bug),
   luego que tras `init_db` la devuelve y las tres columnas son 0.
5. `test_init_db_backfill_is_idempotent` — dos opps (una con `discarded=1` legítimo,
   otra con NULL); `init_db` dos veces → la NULL pasa a 0, la `1` NO se toca, y la
   segunda pasada no produce efecto adicional.

Nota: `ruff check tests/test_db.py` reporta un F841 (variable `run_id` sin usar en
`test_load_active_opportunities`) que es **preexistente** en `main` (verificado con
`git stash`) y queda fuera del scope de esta feature. `init.sh` no ejecuta ruff.

## Verificación

`./venv/bin/pytest` no existe en este repo (el venv es `.venv/`); la suite se ejecutó
con `.venv/bin/pytest -q` y `./init.sh` (que usa `python3 -m pytest -q`).

Suite completa:

```
$ .venv/bin/pytest -q
........................ssss............................................ [ 33%]
........................................................................ [ 50%]
........................................................................ [ 67%]
........................................................................ [ 84%]
.................................................................        [100%]
exit=0
```

(Los 4 `s` son skips preexistentes, no relacionados con esta feature.)

`./init.sh` (últimas líneas):

```
── 5. Ejecutando tests ─────────────────────────────────
[OK]    Todos los tests pasan

── 6. Verificando anti-patrones del legacy ────────────
[OK]    Sin sys.path.append en src/

── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
exit=0
```
