# Implementación: #15 — dedup_jaccard_v1

## Qué cambió

- **`src/saas_radar/analysis/dedup.py`** (CREADO): Módulo de deduplicación semántica de oportunidades entre runs. Antes no existía. Ahora expone `find_canonical`, `evidence_overlap`, `name_similarity` y funciones auxiliares `_name_tokens`, `_evidence_tokens`, `_coerce_quote_list`, `_jaccard`.

- **`src/saas_radar/storage/db.py`** (MODIFICADO): Se añadió el import `from saas_radar.analysis.dedup import find_canonical`. La función `persist_run_to_db` cambió de hacer siempre autoreferencia (`canonical_id = id`) a consultar opps existentes y llamar a `find_canonical` antes de insertar. Si hay match, la nueva opp hereda el `canonical_id` de la canónica existente; si no, se hace autoreferencia tras el INSERT.

- **`scripts/backfill_canonical.py`** (CREADO): Script one-shot portado del legacy para poblar `canonical_id` en la BD heredada. Acepta `--dry-run`, `--yes`, `--force`, `--threshold`, `--db`.

- **`tests/test_dedup.py`** (CREADO): 19 tests portados del legacy. Cubre tokenización, Jaccard, los 7 casos de `find_canonical` y 3 tests de integración con BD temporal.

## Por qué

**Algoritmo Jaccard y por qué se eligió:**

El pipeline puede ejecutarse diariamente. Sin dedup, la misma oportunidad (mismo nicho, misma evidencia) aparece como fila nueva en cada run, inflando la tabla `opportunities` con duplicados y saturando los alertas de Telegram. La dedup semántica colapsa estas repeticiones bajo un `canonical_id` compartido, de modo que `load_active_opportunities` (WHERE id = canonical_id) devuelve solo la primera instancia canónica.

Jaccard sobre tokens de `evidence_quotes` es el enfoque correcto porque:
1. **Es barato**: sin embeddings ni modelos. Solo conjuntos de tokens.
2. **Está calibrado**: el threshold 0.3 fue ajustado contra las 7 opps reales del repo legacy para producir exactamente 3 canónicas ({1}, {2,4,7}, {3,5,6}) sin falsos positivos.
3. **La señal ancla correcta es `evidence_quotes`**: los `evidence_items` son índices relativos a cada run (el ítem 26 de un run no tiene relación con el ítem 26 de otro run). Las quotes en cambio son el texto verbatim de los posts/comentarios — si dos runs citan los mismos fragmentos, es casi seguro que detectaron el mismo dolor.

**Por qué `name_similarity` es tie-breaker y no condición de match:**

Dos opps con el mismo nombre pero evidencia disjunta (por ej. "Customer Tracker" para CRM vs "Customer Tracker" para analytics) son oportunidades distintas. El nombre solo resuelve empates cuando la evidencia ya supera el umbral.

**Carga de existing UNA vez fuera del loop:**

Cargar las opps existentes dentro del loop de `persist_run_to_db` implicaría una query extra por cada opp nueva. Cargándolas antes del loop (una sola SELECT), el coste es O(1) queries independientemente del número de opps en el run. Además, dentro del loop se actualiza `existing_rows` con las opps recién insertadas en ese mismo run — esto permite que si el mismo run contiene dos opps similares, la segunda también se deduplique contra la primera.

**Autoreferencia tras INSERT cuando no hay match:**

`load_active_opportunities` filtra con `WHERE id = canonical_id`. Si una opp nueva no tiene canónica previa, debe ser su propia canónica. Hacerlo con un UPDATE post-INSERT es necesario porque el `id` es AUTOINCREMENT y no se conoce hasta después del INSERT.

## Impacto en el pipeline

- **BD (storage/db.py)**: `persist_run_to_db` ahora es el punto de dedup. Cada inserción de opp consulta las existentes y decide si es nueva canónica o duplicado. `load_active_opportunities` no cambió en código pero sí en comportamiento efectivo: ahora devuelve solo la primera instancia de cada grupo de opps similares.

- **Notificaciones Telegram**: Las alertas de opp usan `load_active_opportunities`. Con la dedup activa, una opp vista ayer no genera alerta nueva hoy.

- **Script de backfill**: Permite sanear la BD heredada (que tiene opps sin `canonical_id` seteado) sin necesidad de re-ejecutar el pipeline.

- **Módulos no afectados**: `ai_analyzer.py`, `synthesis.py`, `main.py` no cambiaron. La dedup es transparente: ocurre en la capa de persistencia.

## Explicación técnica

### `_NAME_STOP` y `_EVIDENCE_STOP`

Dos sets de stopwords diferentes con propósitos distintos:

- `_NAME_STOP`: para `product_name`. Incluye "tool", "app", "for", "with" — palabras que llenan nombres de productos sin aportar distinción semántica. Es pequeño deliberadamente: filtrar demasiado destruye precisión en nombres de 2-3 palabras.

- `_EVIDENCE_STOP`: para `evidence_quotes`. Aquí el objetivo es quitar relleno gramatical puro ("the", "and", "from", "with") preservando verbos y sustantivos del workflow del usuario. Más largo que `_NAME_STOP` porque el texto de quotes tiene más relleno lingüístico.

### `_ITEM_PREFIX = re.compile(r"^\s*\[item\s+\d+\]\s*", re.IGNORECASE)`

Regex compilada una sola vez al importar el módulo (no por llamada). Descarta el prefijo `[item N]` de cada quote antes de tokenizar. Sin este paso, el token "item" aparecería en TODAS las quotes de TODAS las opps y haría subir artificialmente el Jaccard entre opps no relacionadas.

`re.IGNORECASE` cubre el caso de `[Item N]` o `[ITEM N]` generados por variantes del LLM.

### `_name_tokens(name: str) -> frozenset[str]`

- `re.findall(r"[a-z0-9]+", str(name).lower())`: extrae solo tokens alfanuméricos en minúsculas. Ignora puntuación y guiones (de ahí que "E-commerce" produzca "commerce", no "e" ni "commerce").
- `len(w) >= 3`: filtra tokens de 1-2 chars que no aportan distinguibilidad.
- `frozenset` para que sea hashable y eficiente en operaciones de conjunto.

### `_evidence_tokens(quotes: Any) -> frozenset[str]`

- Acepta `list[str]`, `str` con JSON, o `None` vía `_coerce_quote_list`.
- Por cada quote, aplica `_ITEM_PREFIX.sub("", ...)` para quitar el prefijo `[item N]`.
- Token mínimo de 4 chars (vs 3 en `_name_tokens`): porque las quotes tienen mucho vocabulario de workflow corto ("for", "the") que ya estaría en stopwords, pero 4 chars captura "pack", "ship", "pick", "track" — verbos clave del dominio e-commerce.

### `_coerce_quote_list(quotes: Any) -> list[str]`

Normaliza la entrada que puede llegar en tres formatos:

1. `list[str]`: cuando viene directamente de `ai_analyzer.py` antes de serializar.
2. `str` (JSON): cuando se lee de SQLite (campo TEXT con `json.dumps`).
3. `None` / `""`: opps sin evidencia — devuelve lista vacía.

El try/except sobre `json.loads` es defensivo: si el string no es JSON válido (p.ej. una quote en plain text), se trata como una sola quote.

### `_jaccard(a: frozenset, b: frozenset) -> float`

Implementación estándar `|A∩B|/|A∪B|`. El caso `not a and not b` devuelve 0.0 (dos opps sin evidencia no son iguales — ausencia de señal no es señal de igualdad).

### `find_canonical(opp, existing, threshold=0.3) -> int | None`

Recorre `existing` y construye una lista de candidatos que superan el threshold. Si hay empates por `evidence_overlap`, se desempata por `name_similarity` (descendente) y luego por `id` ascendente (prefiere la canónica más antigua). Devuelve `best.get("canonical_id") or best.get("id")` — el `or` cubre el caso de la BD heredada donde `canonical_id` era NULL.

### Wiring en `persist_run_to_db`

```python
existing_rows = [
    {"id": r[0], "canonical_id": r[1], "product_name": r[2] or "", "evidence_quotes": r[3]}
    for r in conn.execute(
        text("SELECT id, canonical_id, product_name, evidence_quotes FROM opportunities")
    ).fetchall()
]
```

SELECT minimalista: solo 4 campos, no `SELECT *`. Esto evita cargar columnas innecesarias (evidence_items, mvp_scope, etc.) que no usa `find_canonical`.

Tras insertar una opp nueva sin match, se añade a `existing_rows`:

```python
existing_rows.append({
    "id": opp_id,
    "canonical_id": opp_id,
    "product_name": opp.get("product_name") or "",
    "evidence_quotes": opp.get("evidence_quotes"),
})
```

Esto es crítico: si un run contiene opps A y B con evidencia solapante, B puede deduplicarse contra A (recién insertada en el mismo run) sin necesidad de un segundo run.

## Tests añadidos

| Test | Qué cubre |
|---|---|
| `test_evidence_tokens_strips_item_prefix` | El prefijo `[item N]` se descarta antes de tokenizar |
| `test_evidence_tokens_filters_short_and_stopwords` | Tokens < 4 chars y stopwords no entran en el frozenset |
| `test_name_tokens_drops_glue_words` | "for", "tool", etc. se eliminan del product_name |
| `test_coerce_quote_list_accepts_json_string` | JSON string → lista de strings |
| `test_coerce_quote_list_accepts_list` | Lista Python → lista de strings |
| `test_coerce_quote_list_handles_none_and_empty` | None, "" y "[]" producen lista vacía |
| `test_evidence_overlap_identical_returns_one` | Jaccard 1.0 cuando tokens son idénticos |
| `test_evidence_overlap_disjoint_returns_zero` | Jaccard 0.0 cuando no hay tokens comunes |
| `test_name_similarity_drops_stopwords` | Jaccard correcto tras eliminar stopwords del nombre |
| `test_find_canonical_returns_none_when_no_existing` | Sin existentes → None |
| `test_find_canonical_two_identical_match` | Dos opps con evidencia casi idéntica → match |
| `test_find_canonical_same_name_disjoint_evidence_no_match` | Mismo nombre + evidencia disjunta → no match (restricción clave) |
| `test_find_canonical_different_name_identical_evidence_matches` | Nombre distinto + evidencia idéntica → match |
| `test_find_canonical_returns_existing_canonical_id_not_row_id` | Cluster ya existente: devuelve canonical_id del cluster, no el id de la fila match |
| `test_find_canonical_threshold_can_be_tuned` | Threshold ajustable; valor por defecto 0.3 excluye solapamiento bajo |
| `test_smoke_seven_real_opps_collapse_to_three_canonicals` | Las 7 opps reales producen exactamente 3 canónicas: {1}, {2,4,7}, {3,5,6} |
| `test_persist_first_opp_canonical_self` | Primera opp insertada tiene id == canonical_id (autoreferencia) |
| `test_persist_duplicate_across_runs_collapses` | Misma opp en 2 runs distintos → 2 filas, 1 canónica, 1 activa |
| `test_persist_disjoint_opps_keep_separate_canonicals` | Opps con evidencia disjunta → canonical_id propios distintos |

## Verificación

```
uv run python -m pytest tests/test_dedup.py -v
============================= test session starts ==============================
collected 19 items

tests/test_dedup.py::test_evidence_tokens_strips_item_prefix PASSED
tests/test_dedup.py::test_evidence_tokens_filters_short_and_stopwords PASSED
tests/test_dedup.py::test_name_tokens_drops_glue_words PASSED
tests/test_dedup.py::test_coerce_quote_list_accepts_json_string PASSED
tests/test_dedup.py::test_coerce_quote_list_accepts_list PASSED
tests/test_dedup.py::test_coerce_quote_list_handles_none_and_empty PASSED
tests/test_dedup.py::test_evidence_overlap_identical_returns_one PASSED
tests/test_dedup.py::test_evidence_overlap_disjoint_returns_zero PASSED
tests/test_dedup.py::test_name_similarity_drops_stopwords PASSED
tests/test_dedup.py::test_find_canonical_returns_none_when_no_existing PASSED
tests/test_dedup.py::test_find_canonical_two_identical_match PASSED
tests/test_dedup.py::test_find_canonical_same_name_disjoint_evidence_no_match PASSED
tests/test_dedup.py::test_find_canonical_different_name_identical_evidence_matches PASSED
tests/test_dedup.py::test_find_canonical_returns_existing_canonical_id_not_row_id PASSED
tests/test_dedup.py::test_find_canonical_threshold_can_be_tuned PASSED
tests/test_dedup.py::test_smoke_seven_real_opps_collapse_to_three_canonicals PASSED
tests/test_dedup.py::test_persist_first_opp_canonical_self PASSED
tests/test_dedup.py::test_persist_duplicate_across_runs_collapses PASSED
tests/test_dedup.py::test_persist_disjoint_opps_keep_separate_canonicals PASSED

19 passed in 0.29s

./init.sh → [OK] Entorno listo.
```

## Limitación conocida

**Falsos negativos por evidencia disjunta**: si el mismo nicho (por ejemplo, "herramienta de fulfillment para e-commerce") aparece en dos runs pero citando posts completamente distintos, los tokens de evidencia no se solapan y `find_canonical` devuelve None — cada run crea su propia canónica. Esto es comportamiento heredado del legacy y documentado en el plan: el algoritmo v1 (Jaccard sobre quotes) solo detecta duplicados cuando los posts fuente se repiten entre runs, lo cual ocurre frecuentemente con scans incrementales de 24h (los posts de alta señal permanecen en el feed varios días). El algoritmo v2 (embeddings) está planificado como mejora futura.
