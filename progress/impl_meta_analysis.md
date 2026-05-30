# Implementación: #13 — meta_analysis_and_recommendations

## Qué cambió

- **`src/saas_radar/analysis/meta_analysis.py`** (archivo nuevo): módulo de meta-análisis post-run portado desde el legacy `analysis/meta_analysis.py`. Antes no existía en el nuevo paquete.

- **`tests/test_meta_analysis.py`** (archivo nuevo): suite de 7 tests que validan todos los criterios de aceptación de la feature.

---

## Por qué

El legacy tenía tres problemas que se han corregido en el port:

1. **Dependencia global del `engine`**: el legacy importaba `from storage.db import engine` al nivel del módulo y todas las funciones lo usaban directamente. En el nuevo paquete se usa `_get_db_url(db_url)` + `_make_engine(url)` localmente, que ya existen en `db.py`. Esto permite que los tests creen una BD temporal sin monkey-patching del módulo.

2. **Las funciones `_build_recommendations` no tenían campo `target`** en las recomendaciones del legacy. `persist_meta_recommendations` en `db.py` ya usa `_extract_target(rec_type, action)` para inferir el target del campo `action`, pero es más correcto y explícito incluir `target` directamente en cada recomendación para que el dedup `(type, target)` funcione de manera predecible. Se añadió el campo `target` a cada recomendación en `_build_recommendations`.

3. **`generate_meta_analysis` accedía a BD indirectamente**: el legacy llamaba directamente a `_find_empty_queries()` y `_find_discovered_subreddits()` sin pasar el `db_url`. La nueva versión pasa `db_url` a todas las sub-funciones que tocan BD.

---

## Impacto en el pipeline

- **Meta-análisis (análisis)**: se crea el módulo `analysis/meta_analysis.py` que es llamado desde `main.py` (feature #12) o desde el orquestador `ai_analyzer.py` (feature #11) tras la síntesis.
- **BD**: escribe en la tabla `meta_recommendations` vía `persist_meta_recommendations` (ya implementado en `db.py`). Incrementa `recurrence` si la misma `(type, target)` ya existe con `acted=0`.
- **Telegram / tuner**: las recomendaciones con `recurrence >= 2` son leídas por `_get_recurring_recommendations` y se mostrarán en el summary del tuner (#18). No afecta directamente a scraping ni LLM.

---

## Explicación técnica

### `generate_meta_analysis(extractions, opportunities, post_age_days, db_url=None)`

Recibe las listas ya calculadas (no accede a BD); delega a sub-funciones para las partes que sí necesitan SQL.

**Paso 1 — subreddit_signal**:
```python
sub_counts: Counter = Counter()
sub_problems: Counter = Counter()
sub_payment: Counter = Counter()
for ex in extractions:
    sub = ex.get("_subreddit", "unknown")
    sub_counts[sub] += 1
    ...
```
`Counter()` de `collections` es un dict con valor 0 por defecto: `sub_counts[sub] += 1` funciona aunque la clave no exista. Se itera sobre `extractions` (lista de dicts producidos por `extraction.py`). `_subreddit` es el campo con prefijo `_` que indica metadato de procedencia (convención del proyecto, ver `docs/conventions.md`).

Luego se construye `subreddit_signal` como lista de dicts, ordenada por `sub_problems` descendente (más productivos primero). `round(ratio, 2)` redondea el hit rate a 2 decimales para JSON limpio.

**Paso 2 — silent_subreddits**:
```python
configured = {s.lower() for s in config.SUBREDDITS}
active = {s["subreddit"].lower() for s in subreddit_signal}
silent_subreddits = sorted(configured - active)
```
`{s.lower() for s in config.SUBREDDITS}` crea un set con todos los subs normalizados a minúsculas. Se hace diferencia de conjuntos (`-`) para obtener los que están en la config pero no aparecieron en el run. `sorted()` garantiza orden estable en el JSON.

**Paso 3 — empty_queries**: delega a `_find_empty_queries(post_age_days, db_url)`.

**Paso 4 — recurring_niches**:
```python
who_counter: Counter = Counter()
for ex in extractions:
    if ex.get("has_problem"):
        who = ex.get("who_has_it", "").strip().lower()
        if who and who not in {"unknown", "n/a", "the user"}:
            who_counter[who] += 1
```
Solo cuenta extracciones con `has_problem=True`. Excluye valores vagos (`"unknown"`, `"n/a"`, `"the user"`) para evitar contaminar el top con etiquetas no descriptivas. `.strip().lower()` normaliza antes de contar para que "Freelancers" y "freelancers" sean la misma clave.

**Paso 5 — pain_categories**:
```python
pain_terms = ["spreadsheet", "excel", "manual", ...]  # 17 términos fijos
haystack = " ".join([...]).lower()
for term in pain_terms:
    if term in haystack:
        pain_keywords[term] += 1
```
`" ".join(...)` concatena `problem_description`, `workflow_context` y `current_workaround` en un solo string. Usar `in haystack` (substring match) en lugar de word boundary regex es suficiente para términos como "spreadsheet" o "excel" que raramente aparecen como subcadena de otra palabra. `.most_common(10)` devuelve los 10 más frecuentes.

**Paso 6 — discovered_subreddits**: delega a `_find_discovered_subreddits(post_age_days, db_url)`.

**Paso 7 — recommendations**: delega a `_build_recommendations(...)`.

**Paso 8 — summary**:
```python
"summary": {
    "total_extractions": len(extractions),
    "with_problem": sum(1 for e in extractions if e.get("has_problem")),
    ...
}
```
`sum(1 for e in ... if cond)` es el equivalente idiomático de `len(list(filter(...)))` pero más eficiente en memoria (generador vs lista). Todos los totales se calculan a partir de los datos ya procesados.

---

### `save_meta_analysis(meta, run_json_path, run_id=None, db_url=None)`

```python
meta_path = run_json_path.replace(".json", "_meta.json")
os.makedirs(os.path.dirname(meta_path), exist_ok=True)
with open(meta_path, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
```
`run_json_path.replace(".json", "_meta.json")` deriva la ruta del meta JSON a partir del JSON del run principal (p.ej. `data/runs/20260530_run.json` → `data/runs/20260530_run_meta.json`). `os.makedirs(..., exist_ok=True)` crea el directorio si no existe sin lanzar error si ya existe (idempotente). `ensure_ascii=False` preserva caracteres UTF-8 como acentos en el JSON. `indent=2` hace el JSON legible para inspección humana.

```python
if run_id is not None and meta.get("recommendations"):
    persist_meta_recommendations(run_id, meta["recommendations"], db_url)
```
Solo persiste si hay `run_id` (estamos en un run real, no un test puntual) y hay recomendaciones. Llama a la función ya implementada en `db.py`.

---

### `print_meta_summary(meta, db_url=None)`

```python
print(f"\n{'=' * 70}")
print("META-ANALISIS DEL RUN")
print(f"{'=' * 70}")
```
`'=' * 70` produce una cadena de 70 caracteres `=`. La cabecera es exactamente lo que el test de snapshot busca. `db_url=None` se pasa a `_get_recurring_recommendations` para poder usar BD temporal en tests.

---

### `_find_empty_queries(post_age_days, db_url=None)`

```python
cutoff = time.time() - post_age_days * 86400
url = _get_db_url(db_url)
eng = _make_engine(url)
```
`time.time()` devuelve el timestamp Unix actual en segundos. `post_age_days * 86400` convierte días a segundos (86400 = 24h * 60min * 60s). `cutoff` es el timestamp mínimo de los posts a considerar. `_get_db_url` y `_make_engine` son helpers de `db.py` que devuelven la URL correcta y crean un engine de SQLAlchemy.

```python
for query in config.PAIN_SEARCH_QUERIES:
    result = conn.execute(
        text("SELECT COUNT(*) FROM reddit_posts WHERE search_query = :q AND created_utc >= :cutoff"),
        {"q": query, "cutoff": cutoff},
    )
    if result.scalar() == 0:
        empty.append(query)
```
Para cada query de la config, ejecuta un `COUNT(*)` en `reddit_posts` filtrando por `search_query` (el campo que guarda qué query de dolor originó el post) y `created_utc >= cutoff` (posts dentro del rango temporal del run). `.scalar()` extrae el entero del resultado de `COUNT(*)`. Si es 0, la query no produjo resultados en este run.

Nota: se hace una query SQL por cada entrada de `PAIN_SEARCH_QUERIES`. El legacy también lo hace así. Es aceptable porque `PAIN_SEARCH_QUERIES` tiene ~60 entradas y `reddit_posts` tiene índice en `created_utc` (`idx_posts_created`).

---

### `_find_discovered_subreddits(post_age_days, db_url=None)`

```python
rows = conn.execute(
    text(
        "SELECT LOWER(subreddit) as sub, COUNT(*) as cnt "
        "FROM reddit_posts "
        "WHERE source = 'pain_search' AND created_utc >= :cutoff "
        "GROUP BY LOWER(subreddit) "
        "HAVING cnt >= 2 "
        "ORDER BY cnt DESC"
    ),
    {"cutoff": cutoff},
).fetchall()
```
`LOWER(subreddit)` normaliza en SQL (en lugar de hacerlo en Python) para que el `GROUP BY` agrupe correctamente independientemente del case. `source = 'pain_search'` filtra solo posts que llegaron via `search_pain_posts`, no via `fetch_posts`. `HAVING cnt >= 2` excluye subs con un único post (puede ser ruido). `ORDER BY cnt DESC` pone los más frecuentes primero para luego quedarse con el top 10.

```python
discovered = []
for row in rows:
    if row[0] not in configured:
        discovered.append({"subreddit": row[0], "posts": row[1]})
return discovered[:10]
```
`row[0]` es el sub normalizado, `row[1]` es el count. Se filtra contra `configured` (set de subs ya en la config) en Python. `discovered[:10]` limita a 10 resultados para que el JSON no sea demasiado largo.

---

### `_build_recommendations(...)`

```python
for s in subreddit_signal:
    if s["posts_analyzed"] >= 3 and s["hit_rate"] == 0:
        recs.append({
            "type": "remove_subreddit",
            "target": f"r/{s['subreddit']}",
            "action": "...",
        })
```
Umbral `posts_analyzed >= 3`: necesitamos un mínimo estadístico para recomendar quitar un sub. Con 1-2 posts puede ser un run atípico. `hit_rate == 0` es comparación exacta con float; es seguro porque `hit_rate = round(ratio, 2)` y `ratio = 0/total = 0.0` exacto cuando `problems == 0`.

Se añade campo `target` explícito (ausente en el legacy). Esto permite que `persist_meta_recommendations` haga dedup `(type, target)` de manera determinista sin depender del parsing de `_extract_target` sobre el campo `action`. Para `remove_subreddit` y `boost_subreddit` el target es `"r/<sub>"`. Para `check_silent` es `"silent_subreddits"` (invariante por tipo). Para `add_subreddit` es `"r/<sub>"`. Para `prune_queries` es `"empty_queries"`. Para `emerging_niche` es el nombre del nicho.

---

### `_get_recurring_recommendations(min_recurrence=2, db_url=None)`

```python
rows = conn.execute(
    text(
        "SELECT type, target, action, recurrence FROM meta_recommendations "
        "WHERE acted = 0 AND recurrence >= :min "
        "ORDER BY recurrence DESC"
    ),
    {"min": min_recurrence},
).fetchall()
```
`acted = 0` excluye recomendaciones ya actuadas (el tuner pone `acted = 1` cuando genera un PR). `recurrence >= :min` filtra por número mínimo de apariciones. `:min` es bind parameter de SQLAlchemy para evitar SQL injection. `ORDER BY recurrence DESC` pone las más urgentes primero.

---

## Tests añadidos

| Test | Qué cubre |
|---|---|
| `test_recurrence_increments` | Llamar `persist_meta_recommendations` dos veces con la misma `(type, target)` incrementa `recurrence` a 2 (el dedup de BD funciona). |
| `test_recommendations_remove_subreddit` | `_build_recommendations` con un sub de `posts_analyzed=5` y `hit_rate=0` produce al menos una rec de tipo `remove_subreddit`. |
| `test_recommendations_boost_subreddit` | Sub con `hit_rate=0.7` y `with_payment_signal=2` produce rec de tipo `boost_subreddit`. |
| `test_find_empty_queries` | BD temporal con posts para una query pero no para otra → `_find_empty_queries` devuelve solo la vacía. |
| `test_find_discovered_subreddits` | BD temporal con posts en `source='pain_search'` de un sub no en SUBREDDITS (3 posts) y uno en SUBREDDITS (2 posts) → solo el desconocido aparece. |
| `test_print_meta_summary_format` | `print_meta_summary` con meta dict mínimo produce salida que incluye `"META-ANALISIS DEL RUN"` y `"="*70`. |
| `test_generate_meta_analysis_summary_keys` | `generate_meta_analysis` con extractions mínimas devuelve exactamente las 8 claves del schema esperado. |

---

## Verificación

```
tests/test_meta_analysis.py .......   [100%]
7 passed in 0.39s
```

Suite completa (todos los tests del proyecto):

```
........................................................................ [ 30%]
........................................................................ [ 61%]
........................................................................ [ 92%]
..................                                                       [100%]
236 passed, 0 failed
```
