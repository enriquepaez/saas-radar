# Implementación: #5 — text_cleaning_and_classifier

## Qué cambió

- **`src/saas_radar/analysis/__init__.py`**: Creado. Marca el directorio como paquete Python con el docstring de su responsabilidad.

- **`src/saas_radar/analysis/text_cleaning.py`**: Creado. Implementa `clean_text(text)` y `normalize_for_classifier(text)`. Antes no existía ningún módulo de análisis NLP en el proyecto.

- **`src/saas_radar/analysis/post_classifier.py`**: Creado. Implementa `classify_post(title, text)` con 6 categorías y 4 listas de keywords constantes. Antes no existía clasificación de posts.

- **`tests/test_text_cleaning.py`**: Creado. 24 tests que cubren: URL removal, stopwords EN/ES, puntuación, emojis, None/non-string guards, palabras cortas, palabras de contenido, y `normalize_for_classifier`.

- **`tests/test_post_classifier.py`**: Creado. 33 tests que cubren: los 3 criterios de aceptación explícitos del spec, las 6 categorías, edge cases (emoji, URL, None inputs), prioridad showcase > pain_point > discussion, y verificación de que las listas de keywords del legacy están presentes.

## Por qué

### Por qué combinar stopwords EN+ES

El legacy solo usaba stopwords en inglés (`stopwords.words("english")`). Los posts de Reddit en estos subreddits son mayoritariamente en inglés, pero `config.SUBREDDITS` incluye comunidades donde pueden aparecer textos mixtos. El criterio de aceptación dice "sin stopwords en inglés/español", así que combino ambos en un solo `set` en la línea de importación. Usar un `set` (no `list`) hace el lookup O(1) por palabra: comparar contra una lista de 300+ palabras es O(n) por palabra del texto, lo que importa cuando se procesan 20k posts.

### Por qué compilar regex una sola vez al importar

La convención del proyecto (y el spec) exige que las regex se compilen UNA vez al importar, no en cada llamada. `re.compile()` construye un autómata finito determinista (DFA) a partir del patrón. Hacerlo al importar significa que ese coste se paga una vez; en cada llamada solo se ejecuta el DFA ya compilado, que es O(n) en longitud del input. Si compiláramos dentro de `clean_text()`, el coste de compilación se multiplicaría por N llamadas.

### Por qué `normalize_for_classifier` no elimina stopwords ni puntuación

El legacy explica esto con un docstring: el clasificador necesita "?" para detectar preguntas, "$" para detectar showcases de revenue, y palabras como "how", "anyone", "does anyone" para saber si el post es una pregunta. Si aplicáramos `clean_text` al clasificador, perderíamos exactamente la señal que distingue `question_operational` de `other`.

### Por qué las 6 categorías difieren levemente del legacy

El legacy tenía: `pain_point`, `showcase`, `question_operational`, `question_vague`, `emotional`, `other`. El spec del proyecto pide: `showcase`, `question_technical`, `question_operational`, `pain_point`, `discussion`, `other`. Las diferencias:
- `question_vague` → `discussion`: el spec unifica las preguntas vagas y el contenido emocional en una sola categoría `discussion`.
- `emotional` → `discussion`: ídem.
- Se añade `question_technical` para distinguir preguntas de setup/código de preguntas operacionales.

### Por qué la prioridad explícita en `_PRIORITY` en lugar de `max(scores)`

El legacy usa `best = max(scores, key=scores.get)`, que en caso de empate devuelve el primero que Python encuentre en el dict (no determinista en distintas versiones). La especificación dice que el orden de prioridad es `showcase > pain_point > question_operational > question_technical > discussion > other`. Usar una lista `_PRIORITY` y recorrerla en orden hace la prioridad explícita y determinista: si `showcase` y `pain_point` tienen el mismo score, siempre gana `showcase`.

### Por qué `PAIN_KEYWORDS` incluye palabras del spec que no estaban en el legacy

El spec dice: `classify_post('I hate manual process', '...')` → `pain_point`. Para que eso funcione, "hate" y "manual" deben estar en PAIN_KEYWORDS. El legacy tenía "i hate" (frase) pero no "hate" sola ni "manual" sola. Añado las variantes sueltas para cubrir el criterio de aceptación.

### Por qué el umbral de `question_operational` es `op_score >= 1` (vs `>= 2` del legacy)

El criterio de aceptación dice: `classify_post('How do you handle invoices?', 'manage track')`. Con el texto "how do you handle invoices? manage track", las keywords operacionales presentes son "handle", "manage", "track", "invoice" → `op_score = 4`. Pero si el text fuera solo "manage", `op_score = 1`. El umbral `>= 2` del legacy requeriría 2 keywords en el body, lo que fallaría en casos con body corto. Con `>= 1` la detección es más sensible, que es lo correcto para `PAIN_CATEGORIES = ["pain_point", "question_operational"]`.

### Por qué las frases multi-palabra de SHOWCASE_KEYWORDS dan +2 y las mono-palabra +1

"i built", "i made", "we launched" son señales mucho más fuertes de showcase que "built" o "made" solos (que pueden aparecer en "I had a problem built into the process"). Dar más peso a las frases multi-palabra reduce falsos positivos. La selección por prioridad ya garantiza que showcase gana sobre pain_point, pero el peso diferencial hace el score más robusto cuando se añadan funciones de ranking en el futuro.

## Impacto en el pipeline

- **text_cleaning**: `clean_text` es el paso 1 de `enrich_posts()` (feature #12). Se aplica a cada post y comentario antes de guardarlo en `reddit_posts.clean_text`. `normalize_for_classifier` es el paso 1 del clasificador y del futuro `_semantic_score` (feature #6).

- **post_classifier**: `classify_post` produce `reddit_posts.category`. El campo `category` es el filtro principal en `load_pain_posts()` (feature #7): solo pasan posts con `category IN ('pain_point', 'question_operational')` (definido en `config.PAIN_CATEGORIES`). Sin categorización correcta, el pipeline IA no recibe los posts adecuados.

- **analysis/ directory**: Al crear el paquete `analysis/`, las features #6 (pain_filter), #7 (data_loader), #8 (llm_clients), #9-#11 (extraction/synthesis/ai_analyzer) y #13 (meta_analysis) ya tienen su directorio destino creado.

## Explicación técnica

### `text_cleaning.py`

**Constantes de módulo:**

```python
nltk.download("stopwords", quiet=True)
```
`nltk.download` descarga el corpus si no está en el directorio NLTK data (`~/nltk_data` por defecto). El parámetro `quiet=True` suprime el output en stdout. Si ya está descargado, no hace nada. Se llama al importar el módulo, no dentro de las funciones, para que el coste de red (si aplica) se pague una vez al arrancar el proceso.

```python
_STOP_WORDS: set[str] = set(stopwords.words("english")) | set(stopwords.words("spanish"))
```
`stopwords.words("english")` devuelve una `list[str]` de ~179 palabras. `set(...)` convierte a conjunto (O(n) una vez). El operador `|` une los dos sets. El resultado es un `set` de ~350 palabras (hay solapes: "a", "no" aparecen en ambos idiomas). La anotación de tipo `set[str]` es Python 3.9+ syntax, permitida porque el `pyproject.toml` declara `target-version = "py311"`.

```python
_RE_URL: re.Pattern[str] = re.compile(r"https?://\S+", re.IGNORECASE)
```
`https?` usa `?` cuantificador (0 o 1 ocurrencias de "s") para cubrir tanto "http" como "https". `://` es literal. `\S+` es "uno o más caracteres que no sean espacio", que captura el path, query string y fragmento de la URL. `re.IGNORECASE` cubre "HTTP://" y "HTTPS://" en mayúsculas (como el caso de aceptación "HTTP://x.com"). El tipo `re.Pattern[str]` es la anotación correcta para un objeto compilado (disponible desde Python 3.7 via `from __future__ import annotations`).

```python
_RE_NOT_ALPHA: re.Pattern[str] = re.compile(r"[^a-z\s]")
```
`[^...]` es la clase de caracteres complementada: todo lo que NO sea `a-z` (letras minúsculas) ni `\s` (espacio, tab, newline). Esto elimina dígitos, puntuación (`.`, `,`, `!`, `?`, `$`, `@`, `#`…), y emojis (que en Unicode están fuera del rango ASCII a-z). Se aplica DESPUÉS de hacer `.lower()` sobre el texto, por eso el rango es solo `a-z` (sin `A-Z`).

**`clean_text(text)`:**

```python
if not isinstance(text, str) or not text.strip():
    return ""
```
Guard de tipo y contenido. `isinstance(text, str)` es False para `None`, `int`, `float`, etc. `text.strip()` elimina espacios al inicio/final antes de evaluar si está vacío: " " es vacío a efectos prácticos.

```python
text = text.lower()
```
`.lower()` devuelve una nueva `str`; no muta `text` in-place (las strings son inmutables en Python). Hacemos lowercase antes de las regex para que `_RE_NOT_ALPHA` solo necesite `[a-z]` (no `[a-zA-Z]`).

```python
text = _RE_URL.sub("", text)
```
`re.Pattern.sub(repl, string)` devuelve `string` con todos los matches de la regex reemplazados por `repl`. Con `repl=""` simplemente elimina las URLs.

```python
text = _RE_NOT_ALPHA.sub("", text)
```
Ídem: elimina todo lo que no sea letra o espacio. Los emojis, aunque son Unicode válido, no están en el rango ASCII `a-z`, así que desaparecen aquí.

```python
text = _RE_MULTI_SPACE.sub(" ", text).strip()
```
Después de eliminar URLs y puntuación, quedan múltiples espacios donde antes había caracteres. `\s+` los colapsa a un solo espacio. `.strip()` elimina espacios al inicio/final.

```python
words = [w for w in text.split() if w not in _STOP_WORDS and len(w) > 2]
```
List comprehension que filtra en un solo paso:
- `text.split()`: divide por cualquier whitespace (incluyendo múltiples espacios). Devuelve `list[str]`.
- `w not in _STOP_WORDS`: lookup O(1) en el set. `True` si la palabra no es stopword.
- `len(w) > 2`: elimina palabras de 1-2 caracteres. Esto captura artículos y preposiciones muy cortas que pudieran no estar en el corpus de stopwords (p.ej. "yo" está en el corpus español, pero "al" puede no estarlo dependiendo de la versión de NLTK).

```python
return " ".join(words)
```
`" ".join(iterable)` construye un string uniendo los elementos con " " como separador. Si `words` está vacío (todas eran stopwords), devuelve `""`.

**`normalize_for_classifier(text)`:**

Más simple que `clean_text`. Solo elimina URLs y hace lowercase. No filtra stopwords ni puntuación porque el clasificador necesita `?`, `$`, `how`, `anyone`, `does anyone` para sus reglas.

```python
if not isinstance(text, str):
    return ""
```
Guard de tipo. Nótese que aquí NO comprobamos `text.strip()` porque un string de solo espacios puede ser "" válido para el clasificador (que devolvería "other").

### `post_classifier.py`

**Las listas de keywords:**

`PAIN_KEYWORDS`, `SHOWCASE_KEYWORDS`, `EMOTIONAL_KEYWORDS`, `OPERATIONAL_KEYWORDS` son listas de strings en minúsculas, definidas como constantes de módulo. Están en minúsculas porque `normalize_for_classifier` hace `.lower()` antes de hacer el matching con `in`.

Todas son `list[str]` (no sets) porque el orden no importa para el matching, y las listas son más legibles como inventario visual de keywords.

**`_PRIORITY: list[str]`:**

El orden `["showcase", "pain_point", "question_operational", "question_technical", "discussion", "other"]` es la prioridad explícita del clasificador. Es una lista (no un dict) para que el orden sea garantizado e inequívoco.

**`classify_post(title, text)`:**

```python
title_str = title if isinstance(title, str) else ""
text_str = text if isinstance(text, str) else ""
```
Guard de tipo que convierte `None` u otros tipos a `""`. Permite llamar `classify_post(None, text)` sin `TypeError`.

```python
if not title_str.strip() and not text_str.strip():
    return "other"
```
Cortocircuito: si ambos están vacíos, no hay señal posible. Devuelve "other" directamente sin construir el string `full` ni calcular scores.

```python
full = normalize_for_classifier(f"{title_str} {text_str}")
```
F-string que concatena título y body con un espacio. `normalize_for_classifier` hace lowercase y elimina URLs. El clasificador trabaja sobre el texto concatenado porque "I built X" puede estar en el título y el body puede dar contexto adicional.

```python
scores: dict[str, int] = {cat: 0 for cat in _PRIORITY}
```
Dict comprehension que inicializa el score de cada categoría a 0. El tipo `dict[str, int]` es Python 3.9+ syntax (permitido por `target-version = "py311"`).

```python
for k in SHOWCASE_KEYWORDS:
    if k in full:
        scores["showcase"] += 2 if " " in k else 1
```
`k in full` es substring search: `"built" in "i built a tool"` → `True`. Las frases multi-palabra (` ` en `k`) reciben +2 porque son más específicas (ver "Por qué" arriba). Frases como "i built" necesariamente implican showcase; "built" solo podría ser "this was built into the system".

```python
is_question = "?" in full or any(full.startswith(w) for w in _QUESTION_WORDS)
```
Un post es pregunta si:
1. Contiene "?" en cualquier posición (`"?" in full`), O
2. Empieza por una de las palabras interrogativas.

`full.startswith(w)` comprueba si el texto completo (ya normalizado) empieza con la palabra. Esto cubre "how do you handle..." que empieza por "how".

`any(iterable)` es cortocircuito: para en el primer `True`. Si hay "?", no evalúa `startswith`.

```python
tech_score = sum(1 for k in _TECHNICAL_KEYWORDS if k in full)
op_score = sum(1 for k in OPERATIONAL_KEYWORDS if k in full)
```
Generator expressions (no list comprehensions) para evitar crear una lista temporal: `sum()` consume el generador directamente.

```python
if op_score >= 1:
    if tech_score >= 2 and tech_score > op_score:
        scores["question_technical"] += 3
    else:
        scores["question_operational"] += 3
```
Si hay al menos 1 keyword operacional, la pregunta es operacional por defecto. Solo si hay 2+ keywords técnicas Y más técnicas que operacionales → es técnica. Esta condición doble evita que "how to setup invoicing?" (que tiene "how to" y "invoic") se clasifique como técnica cuando la intención es claramente operacional.

```python
for cat in _PRIORITY:
    if scores[cat] > 0:
        return cat
```
El bucle recorre `_PRIORITY` en orden. La primera categoría con score positivo gana. Si `showcase` tiene score > 0, devuelve "showcase" sin evaluar el resto. Esto implementa la prioridad del spec de forma explícita y determinista.

## Tests añadidos

**`tests/test_text_cleaning.py` (24 tests):**

| Test | Qué cubre |
|---|---|
| `test_clean_text_removes_http_url` | URLs http desaparecen |
| `test_clean_text_removes_https_url` | URLs HTTPS mayúsculas desaparecen |
| `test_clean_text_removes_punctuation` | Comas, exclamaciones, puntos eliminados |
| `test_clean_text_removes_english_stopwords` | "the", "is", "on" eliminados |
| `test_clean_text_removes_spanish_stopwords` | "de" eliminado |
| `test_clean_text_full_acceptance_case` | Criterio de aceptación: "HTTP://x.com hola mundo" |
| `test_clean_text_returns_empty_for_empty_input` | "" → "" |
| `test_clean_text_returns_empty_for_whitespace_only` | "   " → "" |
| `test_clean_text_returns_empty_for_none_type` | None → "" (guard tipo) |
| `test_clean_text_returns_empty_for_non_string` | 42 → "" (guard tipo) |
| `test_clean_text_returns_empty_for_only_stopwords` | Solo stopwords → "" |
| `test_clean_text_keeps_content_words` | Palabras de contenido sobreviven |
| `test_clean_text_removes_short_words` | 1-2 char words eliminadas |
| `test_clean_text_lowercases` | Output siempre en minúsculas |
| `test_clean_text_emoji_only` | Solo emojis → "" |
| `test_clean_text_pain_keywords_survive` | "nightmare", "manual", "frustrated" sobreviven |
| `test_normalize_removes_urls` | normalize elimina URLs |
| `test_normalize_preserves_question_mark` | "?" preservado |
| `test_normalize_preserves_dollar_sign` | "$" preservado |
| `test_normalize_preserves_stopwords` | Stopwords preservadas en normalize |
| `test_normalize_lowercases` | Output lowercase |
| `test_normalize_returns_empty_for_non_string` | None/int → "" |
| `test_normalize_handles_empty_string` | "" → "" |
| `test_normalize_strips_leading_trailing_spaces` | Sin espacios al inicio/final |

**`tests/test_post_classifier.py` (33 tests):**

| Test | Qué cubre |
|---|---|
| `test_classify_showcase_from_acceptance` | "I built X" → showcase (spec) |
| `test_classify_question_operational_from_acceptance` | "How do you handle invoices?" → question_operational (spec) |
| `test_classify_pain_point_from_acceptance` | "I hate manual process" → pain_point (spec) |
| `test_classify_empty_returns_other` | "" → other (spec) |
| `test_classify_showcase_with_launched` | "launched" → showcase |
| `test_classify_showcase_with_mrr` | "mrr" → showcase |
| `test_classify_showcase_with_show_hn` | "show hn" → showcase |
| `test_classify_showcase_i_made` | "i made" → showcase |
| `test_classify_question_technical` | Pregunta con keywords técnicas → question_technical |
| `test_classify_question_technical_best_way` | "best way to" + técnicas → question_technical |
| `test_classify_question_operational_handle` | "how do you handle" + operacional → question_operational |
| `test_classify_question_operational_manage` | "how do you manage" + operacional → question_operational |
| `test_classify_pain_point_nightmare` | "nightmare" + "painful" → pain_point |
| `test_classify_pain_point_frustrated` | "frustrated" → pain_point |
| `test_classify_pain_point_tedious` | "tedious" + "manually" → pain_point |
| `test_classify_discussion_emotional` | "burned out" + "tough" → discussion |
| `test_classify_discussion_burnt_out` | "burnt out" → discussion |
| `test_classify_other_empty` | "" → other |
| `test_classify_other_none_like` | Solo espacios → other |
| `test_classify_emoji_only` | Solo emojis → other |
| `test_classify_url_only` | Solo URLs → other |
| `test_classify_showcase_beats_pain` | Showcase > pain_point en prioridad |
| `test_classify_pain_beats_discussion` | Pain_point > discussion en prioridad |
| `test_classify_showcase_beats_question` | Showcase > question_operational en prioridad |
| `test_classify_question_without_operational_keyword` | Pregunta sin keywords → no question_operational |
| `test_classify_accepts_none_title` | None como título no crashea |
| `test_classify_accepts_none_text` | None como body no crashea |
| `test_classify_accepts_both_none` | Ambos None → other |
| `test_pain_keywords_has_legacy_words` | PAIN_KEYWORDS contiene las palabras del legacy |
| `test_showcase_keywords_has_legacy_words` | SHOWCASE_KEYWORDS contiene las palabras del legacy |
| `test_emotional_keywords_has_legacy_words` | EMOTIONAL_KEYWORDS contiene las palabras del legacy |
| `test_operational_keywords_has_legacy_words` | OPERATIONAL_KEYWORDS contiene las palabras del legacy |
| `test_keyword_lists_are_lowercase` | Todas las listas están en minúsculas |

## Verificación

```
.venv/bin/pytest tests/test_text_cleaning.py tests/test_post_classifier.py -v

============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 57 items

tests/test_text_cleaning.py ........................                     [ 42%]
tests/test_post_classifier.py .................................          [100%]

============================== 57 passed in 0.66s ==============================

.venv/bin/pytest -v (suite completa)

============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.13.0
collected 109 items

tests/test_config.py ................................                    [ 29%]
tests/test_db.py ..................                                      [ 45%]
tests/test_import.py ..                                                  [ 47%]
tests/test_post_classifier.py .................................          [ 77%]
tests/test_text_cleaning.py ........................                     [100%]

109 passed in 0.56s ==============================

./init.sh → [OK] Entorno listo.
```
