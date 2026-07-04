# Implementación: 28 — investigate_meta_recommendations_empty

## Diagnóstico

Causa raíz (evidencia completa en `progress/explore_meta_code.md` y
`progress/explore_meta_runtime.md`):

1. **La fase de meta-análisis nunca se ejecutaba en producción.**
   `generate_meta_analysis` y `save_meta_analysis`
   (`src/saas_radar/analysis/meta_analysis.py`) no tenían **ningún caller en
   `src/`** — solo en `tests/test_meta_analysis.py`. `run_ai_analysis`
   terminaba en `persist_run_to_db` sin invocar el meta-análisis. Era código
   muerto en producción: no fallaba, no logueaba, no existía en el flujo.
2. **Evidencia en runtime:** los logs de GitHub Actions de los 3 runs verdes
   del 2026-07-04 muestran FASE 1→2→3→`RESULTADOS DE ANÁLISIS IA`→FASE 5, sin
   rastro de `META-ANALISIS DEL RUN` ni de la fase 4.5. La BD de la release
   `db-20260704` tiene `analysis_runs = 27` filas (2 `ok`, 6 `partial` con
   34-61 extracciones válidas) y `meta_recommendations = 0` filas. Con material
   de sobra en los runs ok/partial, la única explicación es que la fase no
   corre — no que las condiciones de generación no se cumplan (con cualquier
   run real, `check_silent` y/o `prune_queries` disparan casi siempre).
3. **En cascada, la fase 4.5 (heuristic tuner) tampoco corría nunca:**
   `main.py` buscaba `glob("data/runs/*_meta.json")`, pero (a) nadie escribía
   ese archivo, y (b) los results reales van a
   `data/ai_analysis.json/<ts>_results.json` (el `--output` por defecto que
   `main.py` pasa a `run_ai_analysis`), no a `data/runs/`. Doble desajuste:
   el `runs.tar.gz` de la release contiene un `data/runs/` vacío.
4. **Bug latente adicional detectado al implementar:** `save_meta_analysis`
   derivaba la ruta con `run_json_path.replace(".json", "_meta.json")` sobre
   la ruta **completa**. Con la ruta real de producción
   (`data/ai_analysis.json/<ts>_results.json`) habría reemplazado también el
   nombre del directorio → `data/ai_analysis_meta.json/<ts>_results_meta.json`
   (directorio inexistente/incorrecto). Aunque alguien hubiera cableado la
   fase, el meta JSON habría caído en un directorio corrupto.

## Qué cambió

- **`src/saas_radar/analysis/ai_analyzer.py`** (modificado):
  - Import nuevo de `generate_meta_analysis`, `print_meta_summary`,
    `save_meta_analysis` desde `saas_radar.analysis.meta_analysis`.
  - Nuevo **Paso 9** al final de `run_ai_analysis`, tras `persist_run_to_db`:
    genera el meta-análisis con las extracciones válidas y opps del run,
    imprime el resumen y lo guarda (JSON + `persist_meta_recommendations` vía
    `run_id`). Todo envuelto en `try/except` con
    `logger.warning(..., exc_info=True)`: un fallo del meta-análisis **no**
    aborta el pipeline ni impide el `return`.
  - El dict de retorno gana la clave `meta_json_path` (ruta del meta JSON o
    `None` si falló / run abortado antes). Los 3 early-returns de fallo
    también la incluyen (`None`) para mantener el contrato homogéneo.
  - Docstring actualizado (flujo + Returns).
- **`src/saas_radar/analysis/meta_analysis.py`** (modificado):
  - `save_meta_analysis` ya no hace `replace(".json", "_meta.json")` sobre la
    ruta completa; delega en el helper nuevo `_derive_meta_path`, que solo
    toca el **nombre** del archivo: `<ts>_results.json` → `<ts>_meta.json`
    (y `run.json` → `run_meta.json` como fallback genérico). Antes:
    `data/ai_analysis.json/X_results.json` → `data/ai_analysis_meta.json/X_results_meta.json`
    (roto). Después: → `data/ai_analysis.json/X_meta.json`.
  - `os.makedirs(os.path.dirname(meta_path) or ".", ...)`: el `or "."` evita
    `FileNotFoundError` si la ruta es un nombre pelado sin directorio.
- **`src/saas_radar/main.py`** (modificado): el glob de la fase 4.5 pasa de
  `"data/runs/*_meta.json"` (hardcodeado, siempre vacío) a
  `os.path.join(output, "*_meta.json")` — el directorio de output real donde
  `run_ai_analysis`/`save_meta_analysis` escriben results y meta.
- **`tests/test_ai_analyzer.py`** (modificado): 3 tests nuevos (10-12) + 2
  imports (`glob`, `logging`, `os`).
- **`tests/test_main.py`** (modificado): test nuevo (15) del wiring
  glob→`phase_heuristic_tuner`; limpieza de lint preexistente (imports
  `StringIO`/`MagicMock` sin uso, variable `db_url` muerta).
- **`tests/test_meta_analysis.py`** (modificado): 2 tests nuevos (8-9) de la
  derivación de ruta; import de `save_meta_analysis`.

## Por qué

- **Cablear en `ai_analyzer` y no en `main.py`:** el meta-análisis necesita
  datos que solo existen dentro de `run_ai_analysis` (`valid_extractions`,
  `opps`, `run_id`, `json_path`, `post_age_days`, `db_url`). Cablearlo en
  `main.py` habría obligado a exponer las extracciones en el dict de retorno
  (payload grande) o a releerlas del cache (frágil). Además, así el
  meta-análisis también corre cuando alguien invoca `run_ai_analysis`
  directamente (tests, futuros CLIs), no solo vía pipeline.
- **Solo en el camino ok/partial:** los 3 early-returns de fallo (sin posts,
  <2 extracciones válidas, síntesis `None`) no ejecutan meta-análisis porque
  no hay `json_path` donde anclar el meta JSON y no hay material (0-1
  extracciones). Es el mismo gate natural que ya tenía el run.
- **Runs `partial` (extracciones válidas + 0 opps) SÍ generan meta-análisis:**
  es el caso mayoritario en producción (6 de 8 runs con Groq).
  `generate_meta_analysis` solo usa `opportunities` para `len()` en el
  summary, así que funciona con lista vacía — verificado por el test 7
  existente (`test_generate_meta_analysis_summary_keys` pasa
  `opportunities=[]`) y ahora ejercitado end-to-end por
  `test_partial_status_when_no_opportunities` (que ya no mockea el meta paso).
- **Unificación de rutas "meta junto al results" + glob sobre `output`:** es
  la opción menos invasiva. Alternativas descartadas: (a) forzar `output` a
  `data/runs/` — rompería el default documentado del CLI (`--output
  data/ai_analysis.json`) y la ruta que ya consume el workflow; (b) pasar
  `meta_json_path` por el dict de retorno y eliminar el glob — más limpio en
  teoría, pero cambia el contrato de la fase 4.5 (el glob permite además
  reutilizar el meta de un run anterior si el actual falló) y toca más
  superficie. Con el fix, la ruta escrita y la buscada derivan ambas del
  mismo `output`, así que no pueden divergir.
- **`try/except` con WARNING + traceback:** el meta-análisis es
  observabilidad/tuning, no el producto del run. Un fallo suyo no debe tirar
  un run que ya costó ~15 min de LLM. `exc_info=True` garantiza que si vuelve
  a fallar silenciosamente, esta vez el traceback completo queda en los logs
  de Actions (acceptance #2 de la feature).

## Impacto en el pipeline

- **`meta_recommendations` se puebla en cada run ok/partial**: camino A
  (determinista) vivo. `recurrence` acumula entre runs vía el dedup
  `(type, target)` de `persist_meta_recommendations`.
- **La fase 4.5 (heuristic tuner LLM, #21) puede correr por fin**: el glob
  encuentra el `<ts>_meta.json` recién escrito → `phase_heuristic_tuner` →
  `persist_heuristic_suggestions` (camino B vivo, sujeto a que el LLM
  responda con schema válido).
- **El tuner determinista (#18) y el modo PR (#20) reciben input**: dejaban de
  proponer nada porque leían una tabla vacía; el ciclo de tuning automático
  queda desbloqueado.
- **El `runs.tar.gz` de la release diaria (#29) llevará los meta JSON**: se
  empaqueta `data/ai_analysis.json/`, donde ahora caen `<ts>_results.json` y
  `<ts>_meta.json`.
- **Salida de consola**: aparece el bloque `META-ANALISIS DEL RUN` tras los
  resultados IA (visible en los logs de Actions — sirve de verificación manual
  del fix en el próximo run real).
- Sin cambios de schema de BD, sin cambios en workflows, sin tocar `data/`.

## Explicación técnica

### `ai_analyzer.py` — imports

```python
from saas_radar.analysis.meta_analysis import (
    generate_meta_analysis,
    print_meta_summary,
    save_meta_analysis,
)
```

Import a nivel de módulo (no lazy): `meta_analysis` solo depende de `config`
y `storage.db`, que `ai_analyzer` ya importa — no hay ciclo. Importarlas como
nombres del módulo (`from X import y`) hace que los tests puedan parchearlas
con `patch("saas_radar.analysis.ai_analyzer.generate_meta_analysis", ...)`:
`unittest.mock.patch` sustituye el atributo en el namespace del módulo que
las *usa*, y como `run_ai_analysis` las resuelve como globals en tiempo de
llamada, el parche surte efecto.

### `ai_analyzer.py` — Paso 9

```python
meta_json_path: str | None = None
```
Se inicializa a `None` **antes** del `try`: si cualquier línea del bloque
lanza, la variable existe igualmente y el `return` posterior no da
`UnboundLocalError`. La anotación `str | None` (sintaxis 3.10+ de unión)
documenta el contrato: ruta o ausencia.

```python
meta = generate_meta_analysis(
    extractions=valid_extractions,
    opportunities=opps,
    post_age_days=post_age_days,
    db_url=db_url,
)
```
- `valid_extractions`: la lista post-`_clean_extractions` — cada dict lleva
  `_subreddit`, `has_problem`, `payment_signal`, `who_has_it`, etc., que es
  exactamente lo que las secciones 1/3/4 del meta-análisis agregan con
  `Counter`. Se pasan las válidas (no `all_extractions`) porque el hit-rate
  por subreddit debe medirse sobre señal limpia, no sobre errores de batch.
- `opps`: la lista de oportunidades **sin serializar** (no
  `serialized_opps`): `generate_meta_analysis` solo hace
  `len(opportunities)` para el summary, y pasar los dicts con listas nativas
  evita acoplar el meta-análisis al formato SQLite (JSON strings).
- `post_age_days`: mismo rango temporal del run — `_find_empty_queries` y
  `_find_discovered_subreddits` calculan `cutoff = time.time() - days*86400`
  y filtran `created_utc >= cutoff` en SQL; usar otro rango daría señal
  incoherente con lo que el run realmente analizó.
- `db_url`: el `sqlite:///{db_path}` construido al inicio de
  `run_ai_analysis`. Se propaga explícitamente para que los tests con BD
  temporal no caigan en el default de producción (`_get_db_url(None)` →
  env/`data/saas.db`).
- Kwargs explícitos en la llamada: a prueba de reordenaciones futuras de la
  firma.

```python
print_meta_summary(meta, db_url=db_url)
```
Imprime el bloque `META-ANALISIS DEL RUN` (user-facing output del CLI, igual
que `_print_results` — permitido por conventions.md §Logging). Necesita
`db_url` porque internamente consulta `_get_recurring_recommendations`
(recomendaciones con `recurrence >= 2` de runs anteriores). Se llama **antes**
de `save_meta_analysis` a propósito: así "RECURRENTES" refleja la acumulación
de runs *previos*, sin contar el actual dos veces.

```python
meta_json_path = save_meta_analysis(meta, json_path, run_id=run_id, db_url=db_url)
```
- `json_path`: la ruta devuelta por `_save_results`
  (`<output>/<ts>_results.json`) — ancla del meta JSON en el mismo directorio.
- `run_id=run_id`: el entero devuelto por `persist_run_to_db` en el Paso 8.
  Es la condición del gate interno de `save_meta_analysis`
  (`if run_id is not None and meta.get("recommendations")`): con él, las
  recomendaciones se persisten en `meta_recommendations` asociadas al run.
- Devuelve la ruta del meta JSON escrita, que se guarda para el dict de
  retorno.

```python
except Exception as exc:
    logger.warning("Meta-análisis falló (el pipeline continúa): %s", exc, exc_info=True)
```
- `except Exception` (no `BaseException`): captura cualquier fallo de
  aplicación (SQL, IO, KeyError) pero deja pasar `KeyboardInterrupt`/
  `SystemExit`, que deben abortar de verdad.
- `%s` con `exc` como argumento (lazy formatting de `logging`): el mensaje
  solo se formatea si el nivel WARNING está activo.
- `exc_info=True`: adjunta el **traceback completo** al registro — es el
  requisito del acceptance ("el except deja de tragarse el error sin log");
  sin él, un `KeyError` diría solo `'subreddit_signal'` sin decir dónde.
- Nivel WARNING y no ERROR: es una degradación recuperable (el run en sí
  terminó bien), consistente con la tabla de niveles de conventions.md.

```python
"meta_json_path": meta_json_path,
```
Clave nueva en el dict de retorno (y `"meta_json_path": None` en los 3
early-returns de fallo): los callers y tests pueden saber si el meta JSON se
generó sin globear el filesystem.

### `meta_analysis.py` — `_derive_meta_path`

```python
p = Path(run_json_path)
name = p.name
```
`Path.name` es solo el último componente (`20260101_000000_results.json`),
sin directorios: cualquier transformación posterior no puede tocar
`data/ai_analysis.json/` (el bug latente del `.replace` global).

```python
if name.endswith("_results.json"):
    meta_name = name[: -len("_results.json")] + "_meta.json"
```
Caso principal: recorta el sufijo con slicing negativo (`name[:-13]`) y añade
`_meta.json` → `<ts>_meta.json`, el nombre que documentaba el acceptance de la
feature #13 y que casa con el patrón `*_meta.json` del glob. Se usa
`endswith` + slicing en vez de `replace` para operar solo sobre el **sufijo**
(un `replace` reemplazaría también una aparición en medio del nombre).

```python
elif name.endswith(".json"):
    meta_name = name[: -len(".json")] + "_meta.json"
else:
    meta_name = name + "_meta.json"
```
Fallbacks: nombres genéricos (`run.json` → `run_meta.json`, el comportamiento
histórico) y nombres sin extensión (nunca pierde el meta por un input raro).

```python
return p.with_name(meta_name)
```
`with_name` sustituye solo el último componente conservando el directorio
padre intacto.

```python
os.makedirs(os.path.dirname(meta_path) or ".", exist_ok=True)
```
`os.path.dirname("run_meta.json")` devuelve `""`, y `os.makedirs("")` lanza
`FileNotFoundError`; el `or "."` degrada a "directorio actual", que con
`exist_ok=True` es un no-op seguro.

### `main.py` — glob de la fase 4.5

```python
meta_files = sorted(_glob.glob(os.path.join(output, "*_meta.json")))
```
- `os.path.join(output, ...)`: compone el patrón sobre el directorio de output
  **real** del run (el `--output` del CLI, default `data/ai_analysis.json`),
  el mismo que `run_ai_analysis` recibe como `output_path` y donde
  `save_meta_analysis` acaba de escribir. Antes el patrón era el literal
  `"data/runs/*_meta.json"`, un directorio donde nadie escribía.
- `sorted(...)` + `meta_files[-1]` (línea existente): los nombres empiezan por
  timestamp `YYYYMMDD_HHMMSS`, así que el orden lexicográfico es cronológico y
  el último elemento es el meta más reciente.

## Tests añadidos

- `tests/test_ai_analyzer.py::test_meta_analysis_populates_recommendations_and_writes_json`
  — integración: run simulado (LLM mockeado + BD temporal) → `run_ai_analysis`
  deja ≥1 fila en `meta_recommendations` (con el `run_id` del run) y escribe
  `<ts>_meta.json` junto a `<ts>_results.json` (mismo timestamp) con la clave
  `recommendations` no vacía.
- `tests/test_ai_analyzer.py::test_meta_json_path_matches_phase45_glob` — usa
  como output un directorio llamado `ai_analysis.json` (el caso real de
  producción) y verifica que la **misma expresión de glob de main.py**
  encuentra exactamente el meta generado, y que el directorio no se corrompe.
- `tests/test_ai_analyzer.py::test_meta_analysis_failure_does_not_abort_run` —
  `generate_meta_analysis` lanza `RuntimeError` → `run_ai_analysis` devuelve
  su resultado normal (`status='ok'`, run persistido en BD,
  `meta_json_path=None`) y queda 1 registro WARNING con `exc_info` (caplog).
- `tests/test_main.py::test_phase45_glob_finds_meta_json_in_output_dir` —
  wiring en `run_pipeline`: un `run_ai_analysis` falso escribe el meta en su
  `output_path` y `phase_heuristic_tuner` (mockeado) recibe exactamente esa
  ruta — prueba que la ruta escrita y la buscada coinciden.
- `tests/test_meta_analysis.py::test_save_meta_analysis_path_inside_dir_named_json`
  — regresión del bug de ruta: results dentro de un dir `*.json` → meta en el
  mismo dir como `<ts>_meta.json`.
- `tests/test_meta_analysis.py::test_save_meta_analysis_generic_json_name` —
  fallback: `run.json` → `run_meta.json` (comportamiento histórico intacto).

Además, los tests existentes 1/4/7 de `test_ai_analyzer.py` (full pipeline,
cached, partial) ahora ejercitan el Paso 9 real sin mocks del meta-análisis y
siguen verdes — en particular el 7 confirma que un run `partial` (0 opps)
genera meta-análisis sin fallar (punto 3 del contrato).

## Verificación

`./venv/bin/pytest` (venv real: `.venv/bin/pytest`):

```
427 passed, 4 skipped in 167.76s (0:02:47)
```

(4 skips preexistentes de dedup-v2, requieren sentence-transformers.)

`ruff check` limpio en los 6 archivos tocados (quedan 23 avisos preexistentes
en archivos fuera del scope: `agents/tuner.py`, `analysis/dedup.py`, otros
tests — no se tocan en esta feature).

`./init.sh` (últimas líneas):

```
── 6. Verificando anti-patrones del legacy ────────────
[OK]    Sin sys.path.append en src/

── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
```

Nota: init.sh §5 avisa "pytest no instalado" porque busca `pytest` en el
PATH del sistema; la suite se ejecutó completa con el pytest del venv del
proyecto (exit 0, salida arriba).

**Verificación manual pendiente para el líder/usuario:** tras el próximo run
real del cron, el log de Actions debe mostrar `META-ANALISIS DEL RUN` y
`-- FASE 4.5: Sugerencias heurísticas LLM`, y la BD de la release debe tener
`SELECT COUNT(*) FROM meta_recommendations` ≥ 1.
