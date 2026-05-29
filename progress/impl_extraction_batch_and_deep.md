# Implementación: #9 — extraction_batch_and_deep

## Qué cambió

- **`src/saas_radar/analysis/extraction.py`** (archivo nuevo): módulo completo de extracción de problemas de Reddit usando LLM. Contiene prompts, funciones de extracción (single/deep/batch), circuit breaker y pipeline de limpieza en 4 funciones puras.

- **`tests/test_extraction.py`** (archivo nuevo): 14 tests unitarios con mocks de `call_llm` y `_fetch_comments_for_post`. Cubre todos los casos de aceptación de la feature.

## Por qué

**Prompts copiados exactamente del legacy**: los 3 prompts (`EXTRACTION_PROMPT`, `DEEP_EXTRACTION_PROMPT`, `EXTRACTION_BATCH_PROMPT`) son strings calibrados empiricamente con cientos de posts reales. Cambiarlos introduce regresiones no predecibles en calidad de extracción.

**4 funciones puras en lugar del monolito de 86 líneas**: la convención del proyecto (`docs/conventions.md` sección "Convenciones específicas del legacy a CAMBIAR") exige separar `_clean_extractions` en funciones testeables individualmente. Cada función tiene una responsabilidad única: descartar, inferir o corregir. Esto permite probar cada regla de limpieza de forma aislada sin depender del estado de las otras.

**`_fetch_comments_for_post` importa `engine` a nivel de módulo (no lazy)**: el legacy usaba un import lazy dentro de la función para evitar circularidades. En el nuevo paquete no hay circularidades porque `extraction.py` vive en `saas_radar.analysis` y `engine` en `saas_radar.storage.db`, que son ramas independientes del árbol de imports. El import al nivel del módulo es más limpio y permite que los tests lo parcheen correctamente con `patch`.

**Circuit breaker local con `CIRCUIT_BREAKER_THRESHOLD = 3`**: aunque `config.py` podría tener esta constante, la especificación pide que `extraction.py` sea autónomo. Definirla localmente evita que un cambio accidental en `config.py` altere el comportamiento del circuit breaker.

**Ningún `print()` en el módulo**: el legacy usa `print()` con emojis para debug. La convención del nuevo proyecto es `logger.*` para todos los eventos del pipeline. El CLI (`main.py`, feature #12) es el único lugar donde `print()` está autorizado para output humano.

## Impacto en el pipeline

- **Extracción**: este módulo es la Fase 1 del pipeline de IA. Toma los posts rankeados por `data_loader.py` (feature #7) y los convierte en dicts estructurados con `has_problem`, `who_has_it`, `problem_description`, etc.
- **LLM**: consume `call_llm` de `llm_clients.py` (feature #8) con `phase="extraction"`, lo que selecciona el modelo económico (Haiku para Claude, no Sonnet).
- **BD**: `_fetch_comments_for_post` consulta `reddit_comments` directamente via SQLAlchemy. No escribe nada.
- **Síntesis**: el output de `_clean_extractions` es la entrada de `build_synthesis_prompt` (feature #10).
- **Orquestador**: `run_batch_extraction` será llamado desde `ai_analyzer.py` (feature #11) para el modo batch.

## Explicación técnica

### Constantes

```python
EXTRACTION_BATCH_SIZE = 5
DEEP_EXTRACTION_THRESHOLD = 30
CIRCUIT_BREAKER_THRESHOLD = 3
```

`EXTRACTION_BATCH_SIZE=5` es el número de posts por llamada al LLM en modo batch. Valor calibrado: más grande ahorra tokens pero el LLM empieza a perder calidad en post 6+. `DEEP_EXTRACTION_THRESHOLD=30` es el umbral: si hay ≤30 posts, el orquestador usará `extract_problem_deep` post a post (más caro pero más rico). Con >30 posts usa batch. `CIRCUIT_BREAKER_THRESHOLD=3` es el número de batches consecutivos con 100% de errores antes de abortar el loop.

### `extract_problem_from_post(row, comments)`

Recibe una `pd.Series` con los campos del post y una lista de strings de comentarios.

- `str(row.get("title", "")).strip()`: convierte a str defensivamente (pandas puede devolver `float('nan')` para celdas vacías), luego elimina whitespace exterior.
- `str(row.get("text", "")).strip()[:TEXT_SNIPPET_LEN]`: trunca a 500 caracteres. El legacy midió que el LLM no usa más contexto en modo single-post cuando hay comentarios. Reduce tokens.
- `f"\ntop comments:\n{joined}"`: el salto de línea inicial antes de "top comments" es parte del template del prompt (separación visual para el LLM).
- `EXTRACTION_PROMPT.format(...)`: usa `.format()` porque el prompt tiene `{subreddit}`, `{title}`, etc. Las llaves dobles `{{` en el prompt se convierten en `{` literales tras el format, preservando el JSON del ejemplo.
- Si `call_llm` devuelve `None` (fallo definitivo tras retries): devuelve `{"has_problem": False, ...}` sin campos de metadata. El orquestador trata `has_problem=False` como señal de skip.
- Si OK: añade 6 campos `_*` (prefijo de dunder metadata según convención) al dict devuelto por el LLM. Estos campos permiten trazar cada extracción de vuelta a su post original en BD.

### `_fetch_comments_for_post(post_id, limit=15)`

- `engine.connect()`: abre una conexión del pool de SQLAlchemy (no crea una nueva conexión física si el pool tiene una libre).
- `sql_text(...)`: envuelve SQL literal en el tipo `TextClause` de SQLAlchemy, requerido para usar parámetros nominales `:pid` y `:lim` de forma segura (evita SQL injection).
- `WHERE length(text) > 50`: filtra comentarios muy cortos que no aportan contexto. El legacy midió que comentarios de <50 chars son casi siempre reacciones sin información ("same here", "lol", etc.).
- `ORDER BY score DESC LIMIT :lim`: trae los comentarios más votados primero, que suelen ser los más informativos. Limita a 15 para controlar tokens.
- `row[0]`: cada `row` del fetchall es una tupla de 1 elemento (solo seleccionamos `text`).

### `extract_problem_deep(row)`

Similar a `extract_problem_from_post` pero:
- NO trunca `text` (texto completo). En modo deep el LLM recibe todo el contexto.
- Carga comentarios desde BD (no los recibe como argumento) con `_fetch_comments_for_post(post_id)`.
- Trunca cada comentario a 400 chars (vs 200 en el modo single): más contexto por comentario porque hay menos posts que analizar.
- El header del bloque de comentarios incluye el conteo: `f"\ntop comments ({len(comments)}):\n{joined}"`. Esto es para que el LLM calibre la representatividad de la muestra.
- `call_llm(prompt, max_tokens=800, phase="extraction")`: más tokens de respuesta porque el prompt `DEEP_EXTRACTION_PROMPT` pide campos adicionales (`comment_signals`, `estimated_frequency`, `tam_clues`).
- Si None: añade `_error=True` (además de `has_problem=False`) para que el circuit breaker pueda distinguir entre "no hay problema" y "el LLM falló".
- Si OK: añade `_deep=True` como bandera. El orquestador usa esto para logging diferenciado y para entender qué modo usó.

### `extract_problems_batch(rows)`

- El bucle `enumerate(rows, 1)` genera índices 1-based para el bloque `[POST 1]`, `[POST 2]`, etc. El LLM devuelve `post_index` con estos mismos números.
- `if src == "comment" and not title`: los comentarios convertidos en posts virtuales (feature #7) tienen `source="comment"` y título vacío. Se les asigna un título especial para que el LLM entienda que no es un post top-level y no lo penalice por falta de título.
- `"\n\n".join(posts_block_parts)`: línea en blanco entre posts para que el LLM los procese como entidades separadas (los modelos son sensibles a este tipo de separadores visuales).
- `max_tokens=220 * len(rows)`: 220 tokens por post es el margen mínimo medido. Si se pasa un batch de 5, se reservan 1100 tokens. Minimizar `max_tokens` es importante en Groq que tiene límites de TPD (tokens por día) agresivos.
- `if not result or "results" not in result`: la condición compuesta cubre tanto `result=None` (fallo de API) como `result={}` (JSON vacío) como `result={"error": "..."}` (JSON sin la clave esperada).
- El relleno `items[i] if i < len(items) else {"has_problem": False}`: si el LLM devuelve 3 results para 5 posts, los posts 4 y 5 se marcan automáticamente como sin problema. Esto preserva la bijección entre `rows` y `extractions`.

### `run_batch_extraction(posts, batch_size)`

- `range(0, len(posts), batch_size)`: genera los índices de inicio de cada batch: 0, 5, 10, ...
- `posts[start : start + batch_size]`: slice del rango actual. El último batch puede ser más pequeño si `len(posts)` no es múltiplo de `batch_size`.
- `all(item.get("_error") for item in batch_results)`: un batch falla completamente cuando TODOS sus items tienen `_error=True`. Un batch parcial (algunos `has_problem=False` sin `_error`) no cuenta como fallo.
- `consecutive_errors = 0` en el else: resetea el contador si el batch tuvo aunque sea un item sin error. El circuit breaker solo dispara con fallos CONSECUTIVOS.
- `if consecutive_errors >= CIRCUIT_BREAKER_THRESHOLD: break`: tras 3 batches fallidos seguidos, el loop para. Los posts restantes no se procesan. El orquestador (feature #11) detectará que hay menos resultados que posts y lo manejará.

### `_extraction_haystack(ex)`

```python
return " ".join([...]).lower()
```

Concatena los 3 campos de texto más ricos de la extracción en un string en minúsculas. Este string es el "pajar" donde buscan las reglas de limpieza. Usar `or ""` en cada `get` cubre el caso de que el LLM devuelva `null` JSON (que pandas convierte en `None`).

### `_drop_who_vago(extractions)`

Descarta extracciones donde `who_has_it` es vacío o demasiado genérico para ser accionable como segmento de mercado. "people" o "anyone" como público objetivo no le sirve a un builder para posicionar su SaaS. Devuelve una tupla `(lista_supervivientes, nº_descartados)` para que el orquestador pueda loggear la limpieza de forma informativa.

### `_drop_non_saas(extractions)`

Usa `_extraction_haystack` para buscar señales de dolor no-SaaS. Si detecta 1+ señal no-SaaS Y ninguna herramienta de "rescate" (excel, airtable, etc.), descarta la extracción. La lógica de rescate existe porque un post puede mencionar "burnout" en el contexto de usar Excel 8 horas al día — eso SÍ es un problema SaaS (automatización del proceso de Excel que causa el burnout).

### `_fix_workaround(extractions)`

No descarta ninguna extracción. Opera en dos pasos:
1. Si `current_workaround` está vacío o en `_NO_WORKAROUND_PHRASES`: busca en el haystack usando `_WORKAROUND_KEYWORDS` en orden (más específicos primero para evitar falsos positivos: "google sheets" antes que "google doc" antes que "google").
2. Si se infiere: actualiza `current_workaround` con `"{label} (inferred)"`. El sufijo `(inferred)` permite que el orquestador y el revisor humano distingan entre workarounds explícitos e inferidos.
3. Si no se infiere: `current_workaround = "no explicit workaround mentioned"` y `_weak_workaround = True`. El flag permite filtrar estas extracciones en síntesis si se quiere ser más conservador.

Devuelve una tupla de 3 elementos para logging: `(lista, recuperados, mantenidos_sin_wk)`.

### `_fix_payment_signal(extractions)`

Regla simple de coherencia: `payment_signal=True` sin `payment_quote` es contradictorio (¿cómo sabe el LLM que hay señal de pago si no puede citar dónde?). Se corrige silenciosamente poniéndolo a `False`.

### `_clean_extractions(extractions)`

Orquestador de limpieza. El orden importa:
1. Filtrar `has_problem=False` y `_error=True` primero: no tiene sentido aplicar reglas de calidad a extracciones ya descartadas.
2. `_drop_who_vago`: descarta antes de gastar ciclos en análisis semántico.
3. `_drop_non_saas`: análisis de texto más costoso, se aplica solo a los supervivientes.
4. `_fix_workaround`: ningún descarte, solo enrichment.
5. `_fix_payment_signal`: corrección puntual sin impacto en longitud.

El `logger.info` solo se emite si algún contador > 0, evitando ruido en runs limpios.

## Tests añadidos

| Test | Qué cubre |
|------|-----------|
| `test_extract_problem_from_post_ok` | LLM devuelve dict válido → se añaden los 6 campos `_*` de metadata |
| `test_extract_problem_from_post_llm_none` | LLM devuelve None → `has_problem=False` sin crash |
| `test_extract_problem_deep_ok` | Comentarios mockeados + LLM OK → resultado tiene `_deep=True` y todos los campos de metadata |
| `test_extract_problem_deep_llm_none` | LLM None en modo deep → `_error=True` |
| `test_extract_problems_batch_ok` | 1 post, LLM devuelve 1 result → campos `_*` añadidos correctamente |
| `test_extract_problems_batch_partial_results` | LLM devuelve lista vacía para 2 posts → ambos tienen `has_problem=False` |
| `test_extract_problems_batch_llm_none` | LLM None → todos con `_error=True` |
| `test_drop_who_vago` | 1 extracción válida + 1 con `who_has_it="people"` → solo la válida sobrevive, contador=1 |
| `test_drop_non_saas` | Extracción con "burnout and loneliness" descartada; extracción con "burnout using excel" mantenida |
| `test_fix_workaround_inference` | `current_workaround=""` con "spreadsheets" en description → inferido como `"spreadsheets (inferred)"` |
| `test_fix_workaround_kept_as_weak` | `current_workaround=""` sin keywords → `_weak_workaround=True`, extracción mantenida |
| `test_fix_payment_signal_cleared` | `payment_signal=True, payment_quote=""` → `payment_signal` se pone a `False` |
| `test_clean_extractions_full_pipeline` | Lista mezclada con 4 extracciones → solo la válida sobrevive |
| `test_circuit_breaker_fires` | 20 posts, LLM siempre None → solo se procesan 3 batches×5 = 15 resultados |

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 14 items

tests/test_extraction.py ..............                                  [100%]

============================== 14 passed in 0.26s ==============================

[OK]    Todos los tests pasan
[OK]    Entorno listo. Puedes empezar a trabajar.
```

## Fix post-review
Añadida función `extract_problems(posts)` que bifurca entre deep y batch según DEEP_EXTRACTION_THRESHOLD.
Añadidos tests: test_extract_problems_uses_deep_when_few_posts, test_extract_problems_uses_batch_when_many_posts.
