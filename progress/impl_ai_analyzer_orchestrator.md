# Implementación: #11 — ai_analyzer_orchestrator

## Qué cambió

- **`src/saas_radar/analysis/ai_analyzer.py`** (NUEVO): Orquestador IA que encadena
  `load_pain_posts` → extracción (deep/batch) → `_clean_extractions` → `build_synthesis_prompt`
  → `call_llm` → `_validate_synthesis` → `_print_results` + `_save_results` + `persist_run_to_db`.
  Incluye el cache defensivo `_save_extractions_cache` y la serialización de campos complejos
  a JSON para SQLite. Antes no existía; ahora expone `run_ai_analysis(...)` como punto de
  entrada para el pipeline IA completo.

- **`tests/test_ai_analyzer.py`** (NUEVO): 8 tests con `unittest.mock.patch` que cubren:
  flujo ok, abort por < 2 extracciones, cache defensivo, uso de cache existente, status
  `partial`, LLM None en síntesis, y comportamientos extremos de `_save_extractions_cache`.

## Por qué

La arquitectura del proyecto tiene cada feature como un módulo independiente. Los módulos
`data_loader`, `extraction`, `synthesis`, `llm_clients` y `db` ya existían pero no había
nadie que los encadenara. Esta feature es el "pegamento" del pipeline IA. Sin ella, el
pipeline no se puede ejecutar de extremo a extremo.

Decisiones no obvias:

- **`_save_extractions_cache` como función separada (no inline en `run_ai_analysis`)**: la
  lógica defensiva es compleja (3 ramas) y tiene que testearse de forma aislada. Una función
  separada permite mockearla en otros tests y testear su comportamiento directamente.

- **`_extract_and_cache` como helper**: separa la responsabilidad de "elegir modo de extracción
  y persistir el cache" de `run_ai_analysis`. Permite mockear `run_batch_extraction` y
  `extract_problem_deep` de forma limpia en los tests sin que interfieran con el flujo del
  orquestador.

- **`_serialize_opportunities` antes de `persist_run_to_db`**: el LLM devuelve `evidence_items`,
  `evidence_quotes` y `mentioned_competitors` como listas Python. SQLite espera TEXT. Si no se
  serializan antes, `persist_run_to_db` intenta insertar un tipo Python `list` en una columna
  TEXT y SQLite lo acepta (como string repr) o falla según el driver. json.dumps garantiza un
  JSON string bien formado y reversible.

- **`persist_run_to_db` usando `db_url` en lugar del engine global**: el módulo `db.py` tiene
  un engine global construido al import. Si los tests modificaran ese engine, contaminarían la
  BD real. Pasando `db_url` explícito, cada test usa su propia BD temporal sin side-effects.

- **`_print_results` con `print()` y NO con `logger`**: según `docs/architecture.md` §9 y
  `docs/conventions.md` §Logging, el output visible al usuario (resumen de fase, resultados
  del análisis) va a `print`. Los logs de debug/info van a `logger`. Mezclarlos sería un
  anti-patrón.

## Impacto en el pipeline

- **AI analysis**: antes no había orquestador. Ahora `run_ai_analysis` es el punto de entrada
  que el futuro `main.py` (feature #12) llamará en su "Fase 4".
- **BD**: `persist_run_to_db` crea filas en `analysis_runs` y `opportunities` con el status
  correcto (`ok`/`partial`/`failed`). `has_successful_run()` (usada en feature #12 para
  detección incremental vs full) depende de que estas filas existan.
- **Cache**: `extractions_cache.json` permite relanzar solo la síntesis sin re-extraer.
  El cache defensivo garantiza que un fallo de red (LLM devuelve [] de extracciones) no
  destruye un cache previo bueno.
- **No toca**: scraping (feature #4), scoring (feature #6), meta-análisis (feature #13),
  notificaciones Telegram (feature #14).

## Explicación técnica

### `_save_extractions_cache(new_data, cache_path)`

Recibe la lista de extracciones (puede estar vacía) y la ruta del cache JSON.

```
if new_data:
    p.write_text(...)  # Hay datos nuevos → sobrescribir sin condición
    return
```
El caso más común: la extracción funcionó y hay datos. `p.write_text` es atómico a nivel
de fichero en la mayoría de sistemas POSIX: si el proceso muere a mitad de escritura, el
archivo puede quedar corrupto pero el original ya estaba borrado. En este proyecto eso es
aceptable (no es una base de datos transaccional).

```
if p.exists():
    existing = json.loads(p.read_text(...))
    valid_old = len([e for e in existing if e.get("has_problem") and not e.get("_error")])
```
Lee el cache previo y cuenta cuántas extracciones son realmente válidas
(`has_problem=True` y sin `_error`). Esto es más preciso que contar todas las filas del
JSON, que puede incluir extracciones descartadas (`has_problem=False`).

```
if valid_old > 0:
    Path(failed_path).write_text(json.dumps(new_data, ...), ...)
    return  # NO toca el archivo original
```
Si el cache previo tiene valor, escribe el estado fallido en `<path>.failed.json` y sale
sin tocar el original. El nombre `.failed.json` es intencional: permite al usuario
inspeccionar qué devolvió el LLM en el fallo.

### `_print_results(results)`

Recibe el dict de resultados de `_validate_synthesis`. Imprime en stdout un resumen
legible: número de oportunidades, ideas descartadas, top 3, y para cada oportunidad
su nombre, nicho, señal de pago y evidencias. Usa `print()` (no `logger`) porque es
output para el humano, no para el sistema de logging.

### `_save_results(results, output_path, timestamp)`

Crea el directorio `output_path` si no existe (`mkdir(parents=True, exist_ok=True)` —
`parents=True` crea directorios intermedios; `exist_ok=True` no falla si ya existe).
Construye el nombre de fichero concatenando `timestamp` (formato `YYYYMMDD_HHMMSS`) con
`_results.json`. Usa `json.dumps(ensure_ascii=False, indent=2)` para producir JSON
legible con caracteres Unicode sin escapar. Devuelve el path como string para que el
orquestador lo pase a `persist_run_to_db` en el campo `json_path`.

### `run_ai_analysis(...)`

**Paso 1 — `init_db(db_url)`**: asegura que el schema está actualizado antes de cualquier
operación. `init_db` es idempotente (CREATE TABLE IF NOT EXISTS + PRAGMA migrations), así
que es seguro llamarla siempre al inicio.

**Paso 2 — `load_pain_posts(...)`**: pasa `include_comments=True` para que los comentarios
con señal de dolor se mezclen como posts virtuales. Si el DataFrame resultante está vacío
(ningún post supera los filtros), persiste un run con `status='failed'` y retorna
inmediatamente. Esto es necesario para que `analysis_runs` siempre tenga registro del intento.

**Paso 3 — extracción**:
```python
posts_list = [posts_df.iloc[i] for i in range(len(posts_df))]
```
Convierte el DataFrame en una lista de `pd.Series` para pasarlos a las funciones de
extracción, que esperan Series individuales (no DataFrames completos).

Si `use_cached_extractions=True` y el cache existe, carga el JSON directamente. Si el
cache está corrupto (`json.loads` falla), lo trata como si no existiera y re-extrae.
Esto es robusto ante ficheros truncados o dañados.

**`_extract_and_cache`**: bifurca entre `extract_problem_deep` (post a post, para lotes
pequeños ≤ 30) y `run_batch_extraction` (batches de 5, para lotes grandes). El threshold
30 viene de `DEEP_EXTRACTION_THRESHOLD` en `extraction.py`. Siempre llama a
`_save_extractions_cache` al final, incluso si el resultado está vacío, para activar la
lógica defensiva del cache.

**Paso 4 — abort**:
```python
if len(valid_extractions) < 2:
```
RULE 1 del prompt de síntesis exige mínimo 2 evidencias distintas para cualquier
oportunidad. Con menos de 2 extracciones válidas es matemáticamente imposible satisfacer
RULE 1. Persistir el run como `failed` permite que `has_successful_run()` devuelva
correctamente `False` (no hubo un run exitoso).

**Paso 5 — `build_synthesis_prompt`**: devuelve una tupla `(prompt_str, ordered_extractions)`.
El segundo elemento es la lista reordenada por subreddit (pre-clustering). Se necesita
para que `_validate_synthesis` construya el mapa `idx → problem_description` con los
índices correctos (los del prompt, no los de la lista original).

**Paso 6 — `call_llm`**: si devuelve `None` (fallo definitivo tras retries), persiste
`status='failed'` y retorna. No propaga la excepción porque `call_llm` ya logueó el error;
el orquestador solo necesita reaccionar al `None`.

**Paso 7 — `_validate_synthesis`**: aplica los checks de cantidad y coherencia léxica
(feature #10). El resultado puede tener menos oportunidades que las que devolvió el LLM.

**Paso 8 — `status = 'ok' if len(opps) >= 1 else 'partial'`**: `ok` significa que el
pipeline produjo al menos 1 oportunidad válida. `partial` significa que completó pero
no encontró oportunidades (quizás todas fueron descartadas por los filtros). Esto es
distinto de `failed` (fallo técnico que impidió completar el pipeline).

**`_serialize_opportunities`**: itera sobre los campos que pueden ser listas Python
(`evidence_items`, `evidence_quotes`, `mentioned_competitors`, `mvp_scope`) y los
convierte a JSON string con `json.dumps`. El check `isinstance(val, (list, dict))`
evita doble-serialización si el valor ya es un string (por ejemplo, si el LLM devolvió
un string donde debería haber una lista).

## Tests añadidos

1. **`test_full_pipeline_ok`**: mockea todos los módulos dependientes, verifica que
   con 3 posts y una síntesis válida el resultado es `status='ok'` y la BD tiene 1
   fila en `analysis_runs` con ese status.

2. **`test_abort_too_few_valid`**: `_clean_extractions` devuelve solo 1 extracción →
   verifica `status='failed'` y que `call_llm` NO se llamó (el abort ocurre antes).

3. **`test_defensive_cache`**: cache previo con 2 extracciones válidas + nueva
   extracción vacía → llama directamente a `_save_extractions_cache([])` → el cache
   original no cambia y existe un `.failed.json`.

4. **`test_use_cached_extractions`**: cache existente + `use_cached_extractions=True`
   → verifica que `run_batch_extraction` y `extract_problem_deep` NO se llaman. El
   mock de ambas funciones con `MagicMock()` permite verificar con `.assert_not_called()`.

5. **`test_save_extractions_cache_no_prev`**: sin cache previo + new_data vacío →
   escribe igualmente y no crea `.failed.json` (no hay nada que proteger).

6. **`test_save_extractions_cache_with_new_data`**: con new_data no vacío → siempre
   sobrescribe el cache, incluso si había datos previos.

7. **`test_partial_status_when_no_opportunities`**: síntesis que devuelve 0
   oportunidades válidas → `status='partial'` en el return y en la BD.

8. **`test_llm_none_in_synthesis`**: `call_llm` devuelve None → `status='failed'`
   con `error_message` que contiene "None".

## Verificación

```
.venv/bin/python -m pytest tests/test_ai_analyzer.py -v

============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 8 items

tests/test_ai_analyzer.py ........                                       [100%]

============================== 8 passed in 0.29s

.venv/bin/python -m pytest tests/
217 passed in 0.91s
```

Todos los tests del proyecto pasan. Sin errores de ruff check.
