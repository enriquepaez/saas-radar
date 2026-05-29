# Implementación: #3 — config_module

## Qué cambió

- **`src/saas_radar/config.py`**: archivo nuevo. Antes no existía. Ahora contiene toda la configuración del pipeline: credenciales vía env vars, constantes de scraping, constantes IA/LLM, y las seis listas mutables del legacy replicadas exactamente.
- **`tests/test_config.py`**: archivo nuevo. 32 tests que cubren tipos, longitudes, valores por defecto, env var overrides y ausencia de side-effects al importar.

## Por qué

**Replicar el legacy exactamente:** El acceptance criteria exige replicar las listas y constantes tal como están en el legacy. Se leyó directamente el archivo `/home/enriquepaez/projects/reddit-saas-radar/config.py` (589 líneas) en lugar de depender del inventario de documentación, que tiene un error: dice "36 subreddits" pero el legacy real tiene 38. La fuente de verdad es el archivo, no el doc.

**`load_dotenv()` como único side-effect:** La arquitectura prohíbe side-effects al importar módulos, salvo `load_dotenv()` que está explícitamente permitido porque es inofensivo sin `.env` (la función simplemente no carga nada si no encuentra el archivo).

**Sin `sys.path.append`:** El paquete es pip-installable desde feature #1. Lección del legacy §2.4 documentada en `lessons-learned.md`.

**Defaults vacíos para API keys:** El legacy usa `os.getenv("GROQ_API_KEY")` que devuelve `None` si la var no existe. En saas-radar se usa `os.getenv("GROQ_API_KEY", "")` para que el tipo sea siempre `str`, no `str | None`. Esto evita errores de tipo downstream cuando se construye un header HTTP con la clave.

**Corrección del conteo de subreddits:** El `inventory.md` §1.2 dice "36 subreddits" pero el legacy real tiene 38. Al contar con `python3 -c "..."` contra el archivo real, el resultado es 38. El test refleja 38 con un comentario explicativo.

## Impacto en el pipeline

- **Scraping (feature #4):** `POST_LIMIT`, `PAIN_SEARCH_LIMIT`, `COMMENT_MIN_LENGTH`, `COMMENT_FETCH_WORKERS`, `COMMENT_TARGET_POSTS`, `SUBREDDITS` y `PAIN_SEARCH_QUERIES` son las entradas primarias del scraper.
- **Scoring (feature #6):** `PAIN_SIGNAL_PHRASES`, `SHOWCASE_TITLE_PREFIXES`, `OFF_TOPIC_SIGNALS`, `MIN_SEMANTIC_SCORE` gobiernan el pre-filtro semántico `_semantic_score`.
- **Data loader (feature #7):** `SUBREDDITS`, `PAIN_CATEGORIES`, `HIGH_SIGNAL_SUBREDDITS`, `POSTS_CAP_HIGH_SIGNAL`, `POSTS_CAP_DEFAULT`, `MAX_POST_AGE_DAYS`, `INCREMENTAL_POST_AGE_DAYS` controlan la carga y el ranking.
- **LLM clients (feature #8):** `AI_PROVIDER`, `ANTHROPIC_API_URL`, `CLAUDE_EXTRACTION_MODEL`, `CLAUDE_SYNTHESIS_MODEL`, `GEMINI_API_URL`, `GEMINI_MODEL`, `GROQ_API_URL`, `GROQ_MODEL` son las entradas del dispatcher. Siguiendo la lección del legacy §2.5 y la arquitectura §3, los módulos downstream deben recibir el provider como argumento, NO leer `config.AI_PROVIDER` como global mutable.
- **Pipeline general (feature #12):** `MAX_POSTS`, `TEXT_SNIPPET_LEN`, `CIRCUIT_BREAKER_THRESHOLD`, `INCREMENTAL_POST_AGE_DAYS`.
- **Telegram (feature #14):** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALERT_THRESHOLD`.
- **BD (feature #2):** `DB_URL` para la URL de SQLAlchemy.

## Explicación técnica

### `load_dotenv()`

```python
from dotenv import load_dotenv
load_dotenv()
```

`load_dotenv()` busca un archivo `.env` en el directorio de trabajo actual y sus padres. Si lo encuentra, carga las variables en `os.environ`. Si no existe, no hace nada (no lanza excepción). Es el único side-effect permitido al importar el módulo porque su efecto es idempotente y benigno.

### `os.getenv("VAR", "")`

Para las API keys se usa `os.getenv("VAR", "")` en lugar de `os.getenv("VAR")`. La diferencia:
- `os.getenv("VAR")` devuelve `None` si la variable no existe.
- `os.getenv("VAR", "")` devuelve `""` (string vacío) si la variable no existe.

Esto garantiza que el tipo siempre sea `str`, no `str | None`. Los LLM clients que construyen cabeceras HTTP (`Authorization: Bearer {key}`) no necesitan guardar contra `None`.

### `(os.getenv("REDDIT_CLIENT_ID") or "").strip()`

Este patrón es distinto: primero evalúa `os.getenv(...)` (que puede ser `None` o `""` si la var existe pero está vacía), luego aplica `or ""` para convertir `None` a `""`, y finalmente `.strip()` elimina espacios accidentales. Es el mismo patrón del legacy. Se aplica a las credenciales de PRAW porque el scraper hace comparaciones directas contra string vacío.

### `AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").lower()`

`.lower()` normaliza el valor aunque el usuario escriba `"Claude"` o `"CLAUDE"` en el `.env`. Esto es del legacy y evita bugs de comparación downstream.

### `TELEGRAM_ALERT_THRESHOLD = int(os.getenv("TELEGRAM_ALERT_THRESHOLD", "8"))`

La env var es siempre string; `int()` la convierte para que el tipo sea `int` (el umbral se compara numéricamente con `priority_score`).

### `HIGH_SIGNAL_SUBREDDITS`

Es un `set` Python, no una lista. Esto es intencionado: las búsquedas de pertenencia (`if sub in HIGH_SIGNAL_SUBREDDITS`) son O(1) con set vs O(n) con lista. Al tener ~17 elementos, la diferencia es despreciable en la práctica, pero el uso de set hace semánticamente explícito que no importa el orden ni hay duplicados.

Todos los elementos están en minúsculas. El `data_loader` normaliza los subreddits a minúsculas antes de hacer la comparación (herencia de la convención del legacy: `save_to_db` normaliza a minúsculas). Los elementos de `SUBREDDITS` pueden tener mayúsculas (`"PropertyManagement"`, `"Accounting"`) porque se pasan directamente a PRAW, que acepta cualquier capitalización. El test `test_high_signal_subreddits_subset_of_subreddits` hace la comparación case-insensitive para verificar coherencia.

### `PAIN_SIGNAL_PHRASES`

Lista de 116 tuplas `(phrase, weight)` donde:
- `phrase` es un string en minúsculas (matching se hace tras `lower()` en `_semantic_score`).
- `weight` es un entero 1, 2 o 3 (1=señal débil, 2=señal media, 3=señal fuerte/pago explícito).

El agrupamiento en comentarios (`# Workaround manual explícito — señal de pago más fuerte (+3)`) documenta la intención semántica de cada bloque. Esto no es decorativo: es la razón por la que el tuner puede proponer añadir frases a un bloque específico.

La frase `("$ per month", 1)` es peculiar: empieza por `$`. Funciona porque el matching en `_semantic_score` usa `phrase in full_text` (substring, no regex), y un post que diga "paying $50 per month for" matchea. No es un error.

### `SHOWCASE_TITLE_PREFIXES` y `OFF_TOPIC_SIGNALS`

Listas de strings en minúsculas. La lógica en `pain_filter.py` (feature #6) hace `title.lower().startswith(prefix)` para los prefijos showcase y `phrase in text.lower()` para off-topic. Los duplicados aparentes entre las dos listas (ej. `"stop building"` en ambas) son intencionales: el mecanismo es distinto (prefix en título vs substring en body).

### `PAIN_CATEGORIES`

```python
PAIN_CATEGORIES = ["pain_point", "question_operational"]
```

Solo estas dos categorías de las 6 posibles (`post_classifier.py` produce también `showcase`, `other`, `off_topic`, `technical`) entran al pipeline IA. Es una decisión de scope: los posts de tipo `showcase` ya tienen penalización -99 del filtro semántico, pero se filtra también por categoría para consistencia.

### Constantes numéricas

| Constante | Valor | Por qué ese valor |
|---|---|---|
| `POST_LIMIT` | 100 | Carga hot+new+top-month+top-year: 4 feeds × 25 ≈ 100 posts únicos |
| `MIN_SEMANTIC_SCORE` | 1.5 | Empíricamente calibrado: elimina ruido sin perder señal. Una sola frase de peso 2 ya supera el umbral. |
| `MAX_POSTS` | 80 | Balance entre cobertura IA y coste en tokens. 80 × 500 chars ≈ 40K tokens de input por extracción batch. |
| `TEXT_SNIPPET_LEN` | 500 | 500 chars ≈ 100 tokens en inglés. Suficiente para capturar el workaround sin gastar el contexto. |
| `CIRCUIT_BREAKER_THRESHOLD` | 3 | Tres fallos consecutivos = el provider está caído, no es ruido. Ver lección §1.3. |
| `POSTS_CAP_HIGH_SIGNAL` | 10 | Subreddits de alta señal merecen más representación en el ranking final. |
| `POSTS_CAP_DEFAULT` | 4 | Cap conservador para evitar que un subreddit grande domine el análisis IA. |

## Tests añadidos

| Test | Qué cubre |
|---|---|
| `test_import_without_dotenv` | El módulo se importa sin error aunque no haya `.env`. |
| `test_ai_provider_default` | `AI_PROVIDER` vale `"claude"` si no hay env var. |
| `test_ai_provider_env_override` | monkeypatch de `AI_PROVIDER` a `"gemini"` + reload. |
| `test_ai_provider_groq_override` | monkeypatch de `AI_PROVIDER` a `"groq"` + reload. |
| `test_groq_api_key_override` | `GROQ_API_KEY` se lee desde env. |
| `test_anthropic_api_key_override` | `ANTHROPIC_API_KEY` se lee desde env. |
| `test_gemini_api_key_override` | `GEMINI_API_KEY` se lee desde env. |
| `test_db_url_default` | `DB_URL` tiene el default correcto cuando no hay env var. |
| `test_pain_signal_phrases_is_list_of_tuples` | Cada elemento es tupla `(str, int)`. |
| `test_pain_signal_phrases_min_length` | La lista tiene al menos 100 entradas. |
| `test_pain_signal_phrases_weights_valid` | Todos los pesos están en {1, 2, 3}. |
| `test_subreddits_is_list_of_strings` | Cada elemento es `str`. |
| `test_subreddits_length` | Exactamente 38 subreddits (cuenta real del legacy; inventory.md dice 36 por error). |
| `test_subreddits_contains_known_entries` | Subreddits clave de tiers A/B/C/descubiertos están presentes. |
| `test_high_signal_subreddits_is_set` | El tipo es `set`. |
| `test_high_signal_subreddits_all_lowercase` | Todos los elementos están en minúsculas. |
| `test_high_signal_subreddits_subset_of_subreddits` | Coherencia: todo HIGH_SIGNAL está en SUBREDDITS. |
| `test_pain_search_queries_is_list_of_strings` | Tipo correcto. |
| `test_pain_search_queries_min_length` | Al menos 10 queries. |
| `test_showcase_title_prefixes_is_list_of_strings` | Tipo correcto. |
| `test_showcase_title_prefixes_min_length` | Al menos 5 prefijos. |
| `test_off_topic_signals_is_list_of_strings` | Tipo correcto. |
| `test_off_topic_signals_min_length` | Al menos 3 señales. |
| `test_min_semantic_score_type_and_value` | Tipo `float` o `int`, valor > 0. |
| `test_post_limit_type_and_value` | Tipo `int`, valor > 0. |
| `test_scraping_constants_present` | Valores exactos del legacy para las 6 constantes de scraping. |
| `test_ai_constants_present` | Valores exactos del legacy para las 6 constantes IA. |
| `test_pain_categories_present` | `PAIN_CATEGORIES` contiene `pain_point` y `question_operational`. |
| `test_posts_cap_constants` | `POSTS_CAP_HIGH_SIGNAL=10` y `POSTS_CAP_DEFAULT=4`. |
| `test_llm_api_urls_present` | Las tres URLs de API están definidas y tienen el dominio correcto. |
| `test_claude_model_defaults` | Modelos de Claude tienen los defaults del legacy. |
| `test_no_print_on_import` | stdout y stderr están vacíos tras importar el módulo. |

## Verificación

```
── 5. Ejecutando tests ─────────────────────────────────
....................................................                     [100%]
[OK]    Todos los tests pasan

── 6. Verificando anti-patrones del legacy ────────────
[OK]    Sin sys.path.append en src/

── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
```

32 tests en `tests/test_config.py` + 20 tests previos de `tests/test_db.py` = 52 tests en verde.
`ruff check src/saas_radar/config.py tests/test_config.py` → `All checks passed!`
