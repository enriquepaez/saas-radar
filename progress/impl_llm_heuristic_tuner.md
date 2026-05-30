# Implementación: #21 — llm_heuristic_tuner

## Qué cambió

- **`src/saas_radar/agents/heuristic_tuner.py`** (nuevo): módulo completo del agente heurístico LLM. Antes no existía → ahora contiene `generate_heuristic_suggestions`, `persist_heuristic_suggestions` y CLI `python -m saas_radar.agents.heuristic_tuner`.

- **`src/saas_radar/agents/tuning_rules.py`** (modificado): añadidas reglas A5, A6, A7 (`propose_add_queries_from_llm`, `propose_add_subreddits_from_llm`, `propose_add_phrases_from_llm`) y actualizado `propose_all_changes` para llamarlas. El docstring del módulo documenta las nuevas reglas.

- **`src/saas_radar/agents/tuner.py`** (modificado): `_ACTION_ORDER` extendido con `add_query` (4), `add_subreddit` (5), `add_phrase` (6). `_META_TYPE_TO_ACTION` extendido con los tres tipos nuevos. `render_report` incluye los nuevos contadores en el RESUMEN.

- **`src/saas_radar/main.py`** (modificado): añadido `import logging` + `logger = logging.getLogger(__name__)`. Nueva función `phase_heuristic_tuner(meta_json_path, top_posts_df, provider)`. En `run_pipeline`: búsqueda del meta-JSON más reciente y llamada a la fase 4.5 envuelta en try/except.

- **`tests/test_heuristic_tuner.py`** (nuevo): 16 tests cubriendo los 6 acceptance criteria.

- **`tests/fixtures/tuner_report_expected.txt`** (modificado): actualizado para incluir los nuevos campos en la línea RESUMEN (`add_query=0 add_sub=0 add_phrase=0`).

## Por qué

**`call_llm` al nivel de módulo (no lazy)**: inicialmente lo importé de forma lazy dentro de `generate_heuristic_suggestions` para seguir el patrón del legacy. Pero esto hace imposible hacer `patch("saas_radar.agents.heuristic_tuner.call_llm")` en los tests, porque el nombre no existe en el namespace del módulo. Mover el import al top-level permite mockear correctamente sin afectar la funcionalidad.

**Dedup case-insensitive**: los subreddits en config usan capitalización mixta (p.ej. `"PropertyManagement"`, `"AmazonSeller"`). El dedup compara en lowercase para evitar que el LLM sugiera "propertymanagement" y no sea filtrado por el dedup. Las frases de PAIN_SIGNAL_PHRASES también están en lowercase, por lo que el match es directo.

**`_validate_schema` como función privada separada**: la lógica de validación del schema del LLM es suficientemente compleja (3 listas con estructuras distintas) para justificar su propia función testeable. Devuelve bool en lugar de lanzar excepción para que `generate_heuristic_suggestions` pueda manejarla limpiamente con un log WARNING.

**Reglas A5/A6/A7 en `tuning_rules.py`, no en `tuner.py`**: la separación entre reglas (en `tuning_rules.py`) y el ejecutor/CLI (en `tuner.py`) es una invariante del diseño. Las reglas son puras (sin I/O), testables en aislamiento.

**`_ACTION_ORDER` con nuevas entradas en `tuner.py`**: las acciones A5-A7 van al final del orden (4, 5, 6) para mantener el principio conservador: primero eliminar/degradar, luego añadir señal conocida, finalmente añadir sugerencias LLM (más inciertas).

**Fixture `tuner_report_expected.txt` actualizado**: el snapshot test compara la salida exacta de `render_report`. Al añadir campos nuevos al RESUMEN, el fixture debe reflejar el nuevo formato. Se actualizó en lugar de eliminar el test, que sería la forma más rápida pero menos correcta.

**Fase 4.5 en `main.py` con búsqueda dinámica de meta-JSON**: `run_ai_analysis` no retorna el path del meta-JSON (y no debería, ya que la generación del meta-JSON es un paso separado que ocurre cuando `save_meta_analysis` es llamada desde el pipeline completo). La integración en `run_pipeline` busca el meta-JSON más reciente en `data/runs/` después de que el análisis IA ha terminado. Si no hay meta-JSON disponible (p.ej. pipeline sin runs anteriores), la fase 4.5 se omite silenciosamente.

**`glob` importado localmente en `run_pipeline`**: para evitar añadir un import al top del módulo por un uso único y evitar que los tests del pipeline se vean afectados. Es una concesión de legibilidad vs la regla general de imports al top, justificada por el scope estrecho.

## Impacto en el pipeline

- **LLM**: nueva llamada al LLM en fase 4.5 (provider configurable, phase='synthesis' usa CLAUDE_SYNTHESIS_MODEL). Solo si hay meta-JSON disponible y no se pasó `--skip-ai`.
- **BD (meta_recommendations)**: `persist_heuristic_suggestions` escribe directamente con `sqlite3` (no SQLAlchemy), consistente con `tuner.py` y `tuner.py::load_meta_recommendations`.
- **Tuner determinista**: `propose_all_changes` ahora devuelve hasta 3 categorías adicionales de propuestas (add_query, add_subreddit, add_phrase). Los tests snapshot del tuner CLI se actualizaron.
- **CLI**: nuevo módulo ejecutable `python -m saas_radar.agents.heuristic_tuner`.
- **Telegram/notificaciones**: sin impacto directo. La fase 4.5 ocurre antes de las notificaciones pero no genera output a Telegram.

## Explicación técnica

### `_validate_schema(payload: dict) -> bool`

Recibe el dict ya parseado por `_parse_json_payload` (que está en `llm_clients.py`). Comprueba:
1. Que `payload` sea un `dict` (no una lista, no None — aunque None no llega aquí porque se maneja antes).
2. Que las tres claves (`new_queries`, `new_subreddits`, `new_phrases`) existan.
3. Que cada una sea una `list`.
4. Que cada elemento de cada lista tenga los campos correctos con los tipos correctos.

El check `isinstance(item["weight"], int)` es necesario porque el LLM a veces devuelve `"weight": 2` (int) pero también puede devolver `"weight": "2"` (str). La validación estricta fuerza al schema.

### `_build_prompt(...)` → str

Construye el prompt en secciones marcadas con `##` para que el LLM pueda orientarse fácilmente. La instrucción "Respond ONLY with valid JSON" al final es clave para que `_parse_json_payload` en `llm_clients.py` lo parse correctamente.

Los `discovered_subreddits` del meta-JSON pueden tener la clave `"subreddit"` (cuando vienen de `_find_discovered_subreddits`) o `"name"` (cuando vienen del LLM). El prompt usa `d.get("subreddit") or d.get("name", "")` para manejar ambos casos.

### `_dedup_against_config(suggestions: dict) -> dict`

Usa un import lazy de `from saas_radar import config` dentro de la función. Esto es intencional: si se importara al top del módulo, el dedup usaría el estado de config al momento de importar el módulo (que en tests puede no estar correctamente inicializado). El import lazy siempre lee el estado actual de `config.PAIN_SEARCH_QUERIES`, etc.

El match de frases usa `p[0].lower()` porque `PAIN_SIGNAL_PHRASES` es una lista de tuplas `(str, int)`.

### `generate_heuristic_suggestions(meta_json_path, top_posts_df, provider)` → dict

Flujo:
1. Leer meta-JSON con manejo de error (OSError, JSONDecodeError) → devuelve `_EMPTY_SUGGESTIONS` con log WARNING.
2. Filtrar `recurring_niches` con `count >= 2` (no usa `recurrence` de BD, sino `count` del meta-JSON que es el acumulado del run actual).
3. Construir prompt con `_build_prompt`.
4. Llamar a `call_llm(prompt, provider=provider, phase="synthesis")` — el `phase='synthesis'` usa `CLAUDE_SYNTHESIS_MODEL` (Sonnet) en lugar de Haiku.
5. Si `result is None` → log WARNING + `_EMPTY_SUGGESTIONS`.
6. Si `not _validate_schema(result)` → log WARNING + `_EMPTY_SUGGESTIONS`.
7. Llamar a `_dedup_against_config(result)` → filtrado.

La función nunca lanza excepción al caller (toda la gestión de errores es interna con log + return vacío).

### `persist_heuristic_suggestions(suggestions: dict, db_path: str)` → int

Para cada (type, target):
```sql
-- Si existe:
UPDATE meta_recommendations SET recurrence = recurrence + 1 WHERE id = ?
-- Si no existe:
INSERT INTO meta_recommendations (type, target, recurrence, acted) VALUES (?, ?, 1, 0)
```

El `existing[0]` es el `id` del registro existente, usado para el UPDATE. La BD se abre con `sqlite3.connect(db_path)` y se cierra en el bloque `finally` para garantizar que no se deja un conection open en caso de error.

### Reglas A5/A6/A7 en `tuning_rules.py`

Las tres funciones siguen el mismo patrón:
- Filtran por `type == 'X_suggestion'`
- Requieren `recurrence >= 2` (lanzadas dos veces por el heuristic_tuner → merecen atención)
- Requieren `acted == 0` (no ya procesadas)
- Usan un `set seen` para evitar duplicados en el mismo batch

El `int(rec.get("recurrence", 0))` previene errores si el valor viene como string de la BD (SQLite puede devolver string si la columna no tiene tipo estricto, aunque en SQLite3 Python esto no ocurre en práctica).

### Fase 4.5 en `main.py`

```python
def phase_heuristic_tuner(meta_json_path, top_posts_df, provider):
    try:
        from saas_radar import config
        from saas_radar.agents.heuristic_tuner import (
            generate_heuristic_suggestions,
            persist_heuristic_suggestions,
        )
        suggestions = generate_heuristic_suggestions(...)
        db_path = config.DB_URL.replace("sqlite:///", "")
        persist_heuristic_suggestions(suggestions, db_path)
    except Exception as exc:
        logger.warning("Fase 4.5 heuristic_tuner falló (pipeline continúa): %s", exc)
```

El `import lazy` dentro de try/except garantiza que si hay cualquier error de importación o de ejecución, el pipeline no se rompe. El `db_path` se deriva de `config.DB_URL` quitando el prefijo `sqlite:///` (convención ya usada en otros módulos del proyecto).

## Tests añadidos

En `tests/test_heuristic_tuner.py` (16 tests):

1. `test_schema_valido_genera_sugerencias` — LLM devuelve JSON válido → result contiene las sugerencias.
2. `test_dedup_no_incluye_query_existente_en_config` — query ya en PAIN_SEARCH_QUERIES es filtrada.
3. `test_dedup_no_incluye_subreddit_existente_en_config` — subreddit ya en SUBREDDITS es filtrado.
4. `test_dedup_no_incluye_frase_existente_en_config` — frase ya en PAIN_SIGNAL_PHRASES es filtrada.
5. `test_dry_run_no_llama_a_persist` — CLI con `--dry-run` no llama a `persist_heuristic_suggestions`.
6. `test_recurrence_incrementa_en_segunda_insercion` — primera llamada inserta recurrence=1, segunda actualiza a 2.
7. `test_persist_inserta_los_tres_tipos` — los 3 tipos (query/subreddit/phrase_suggestion) se insertan correctamente.
8. `test_schema_invalido_devuelve_vacio_sin_excepcion` — 4 respuestas inválidas del LLM devuelven dict vacío sin crash.
9. `test_meta_json_inexistente_devuelve_vacio` — meta-JSON no encontrado devuelve dict vacío.
10. `test_regla_a5_propone_add_query_con_recurrence_2` — A5 genera propuesta con recurrence=2.
11. `test_regla_a5_no_propone_con_recurrence_1` — A5 ignora recurrence=1.
12. `test_regla_a5_no_propone_con_acted_1` — A5 ignora acted=1.
13. `test_regla_a6_propone_add_subreddit_con_recurrence_2` — A6 genera propuesta con recurrence=3.
14. `test_regla_a7_propone_add_phrase_con_recurrence_2` — A7 genera propuesta con recurrence=2.
15. `test_propose_all_changes_incluye_a5_a6_a7` — `propose_all_changes` incluye las 3 acciones nuevas.
16. `test_propose_all_changes_orden_conservador` — `remove_query` aparece antes que `add_query` en el output.

## Verificación

```
.venv/bin/pytest -q --tb=short
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 53%]
........................................................................ [ 71%]
........................................................................ [ 89%]
............................................                             [100%]
404 tests collected, 404 passed
```

Exit code 0. `./init.sh` termina con `[OK] Entorno listo`.
