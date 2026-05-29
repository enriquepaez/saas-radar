# Implementación: #6 — semantic_score_filter

## Qué cambió

- **`src/saas_radar/analysis/pain_filter.py`** (creado): módulo nuevo que expone `_semantic_score(title, text) -> float`. Contiene dos constantes de módulo (`_PAIN_PATTERNS`, `_OFFTOPIC_PATTERN`) y la función de scoring. Antes no existía; ahora el pipeline puede pre-filtrar posts por señales semánticas de dolor sin necesidad de llamar al LLM.

- **`tests/test_pain_filter.py`** (creado): 22 tests pytest que cubren todos los criterios de acceptance más variantes de cobertura extra.

## Por qué

**Compilación al nivel de módulo**: Las regex se compilan UNA sola vez cuando Python importa el módulo, no en cada llamada a `_semantic_score`. El pipeline procesa decenas de miles de posts; compilar ~120 patrones por llamada multiplicaría el tiempo de CPU sin ganancia alguna. Colocar la compilación al nivel de módulo es el patrón estándar de Python para constantes derivadas de datos.

**`\b` solo al inicio de las pain phrases**: Las frases como `"manually enter"` no deben matchear dentro de `"mishandled"` ni `"reentry"`. El `\b` al inicio ancla el match al comienzo de una palabra. No se pone `\b` al final porque las phrases terminan en letras normales que ya forman boundary naturales con espacios/puntuación, y añadir `\b` final rompería la cobertura de sufijos morfológicos (`"manually entering"`, `"manually entered"`) que el legacy explícitamente cubre.

**`\b` en ambos extremos de off-topic**: Las señales off-topic son palabras completas (`"burnout"`, `"politics"`) donde importa no matchear en prefijos (`"burning"`, `"apolitical"`). Por eso llevan `\b` inicial Y final.

**Un único regex alternado para off-topic** (`"|".join(...)`): más eficiente que iterar señal por señal; el motor de regex evalúa todas las alternativas en un solo pasaje del string.

**Orden de evaluación showcase → off-topic → suma de phrases**: El showcase es la penalización más dura y más frecuente (hay ~80 prefijos); evaluar primero evita el coste de la búsqueda de off-topic y phrases en posts que ya son showcase.

**Alternativa descartada: compilar las phrases dentro de `_semantic_score`**: haría la función pura y sin estado de módulo, pero el coste de compilación repetida es inaceptable para un pipeline de ~20k posts.

## Impacto en el pipeline

- **Scoring/filtrado**: `_semantic_score` es el gatekeeper semántico que se llama en la feature #7 (`data_loader_with_ranking`) para filtrar posts con `score >= MIN_SEMANTIC_SCORE` antes de pasarlos al LLM. Sin esta función, la feature #7 está bloqueada.
- **LLM / AI**: reduce drásticamente el número de posts que llegan a la fase de extracción, lo que baja coste y latencia.
- **BD**: el campo `semantic_score` en `reddit_posts` se recalcula desde este módulo (no se usa el valor persistido en el legacy, lección aprendida).
- **Scraping / Telegram / CLI**: no afectados directamente por esta feature.

## Explicación técnica

### `_PAIN_PATTERNS`

```python
_PAIN_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\b" + re.escape(phrase), re.IGNORECASE), points)
    for phrase, points in PAIN_SIGNAL_PHRASES
]
```

- `PAIN_SIGNAL_PHRASES` es una lista de tuplas `(str, int)` importada de `saas_radar.config`.
- `re.escape(phrase)` escapa cualquier carácter especial de regex que pudiera estar en la frase (ej. `"$"` en `"$ per month"`).
- `r"\b" + re.escape(phrase)` antepone un word-boundary al patrón literal.
- `re.IGNORECASE` hace el match insensible a mayúsculas.
- La list comprehension produce una lista de `(re.Pattern, int)` que se evalúa exactamente una vez al importar el módulo.
- La anotación `list[tuple[re.Pattern[str], int]]` documenta el tipo sin coste en runtime.

### `_OFFTOPIC_PATTERN`

```python
_OFFTOPIC_PATTERN: re.Pattern[str] = re.compile(
    "|".join(r"\b" + re.escape(s) + r"\b" for s in OFF_TOPIC_SIGNALS),
    re.IGNORECASE,
)
```

- `OFF_TOPIC_SIGNALS` es una lista de strings de `saas_radar.config`.
- `"|".join(...)` produce un patrón de alternación: `\bburned out\b|\bburnout\b|...`.
- Un solo `re.compile` para todas las señales; el motor de regex las evalúa en un único pasaje.

### `_semantic_score(title, text) -> float`

```python
combined = (title or "") + " " + (text or "")
title_lc = (title or "").lower().strip()
```

- `(title or "")` convierte `None` y `""` de forma segura en string vacío. Esto permite pasar `None` sin lanzar `TypeError`.
- `combined` concatena título y texto con un espacio separador para que los patrones no atraviesen el límite (ej. `"painstuff"` no debe matchear si el título termina en `"pain"` y el texto empieza en `"stuff"`).
- `title_lc` normaliza a minúsculas y elimina espacios extremos para la comparación con los prefijos showcase, que en config ya están en lowercase.

```python
for prefix in SHOWCASE_TITLE_PREFIXES:
    if title_lc.startswith(prefix):
        return -99.0
```

- `str.startswith(prefix)` es O(len(prefix)) y muy rápido.
- Se itera sobre todos los prefijos; al primer match se retorna inmediatamente (-99.0) sin evaluar el resto.
- El retorno temprano evita el coste de los otros pasos para posts showcase, que son frecuentes.

```python
if _OFFTOPIC_PATTERN.search(combined):
    return -50.0
```

- `re.Pattern.search` busca el patrón en cualquier posición del string (no ancla al inicio como `match`).
- Evalúa `combined` (título + texto) porque el off-topic puede aparecer en cualquiera de los dos.
- Retorno temprano si hay match.

```python
score = 0.0
for pattern, points in _PAIN_PATTERNS:
    if pattern.search(combined):
        score += points
        if pattern.search(title_lc):
            score += points * 0.5
```

- Itera todos los patrones de dolor; cada match suma sus `points` al acumulador.
- Si el mismo patrón también aparece en el título (`title_lc`), suma adicionalmente `points * 0.5`. El bonus de título refleja que una señal en el título tiene más peso semántico (el autor la eligió para resumir su problema).
- `0.5` viene del legacy y está calibrado empíricamente: suficientemente pequeño para no distorsionar el ranking relativo, suficientemente grande para que los títulos con señal real asciendan.
- El score puede ser `0.0` si no hay ninguna señal.

## Tests añadidos

| Test | Caso que cubre |
|---|---|
| `test_semantic_score_returns_negative99_for_showcase_prefix` | Título "how i built" → -99.0 (prefijo real) |
| `test_semantic_score_showcase_prefix_case_insensitive` | Mayúsculas en título showcase → -99.0 |
| `test_semantic_score_showcase_i_built_prefix` | Prefijo "i built" → -99.0 |
| `test_semantic_score_showcase_we_launched_prefix` | Prefijo "we launched" → -99.0 |
| `test_semantic_score_returns_negative50_for_offtopic_signal` | "burned out" en texto → -50.0 |
| `test_semantic_score_returns_negative50_for_burnout_word` | "burnout" en texto → -50.0 |
| `test_semantic_score_returns_negative50_for_politics` | "politics" en texto → -50.0 |
| `test_semantic_score_showcase_beats_offtopic` | Título showcase + texto off-topic → -99.0 (showcase gana) |
| `test_semantic_score_pain_phrase_with_title_bonus` | "i use excel to" en título y texto → score >= 3 (con bonus) |
| `test_semantic_score_pain_phrase_title_bonus_additive` | "manually track" en título y texto → score >= 4.5 |
| `test_semantic_score_pain_phrase_no_title_bonus` | "copy paste" solo en texto → score >= 3 (sin bonus) |
| `test_semantic_score_pain_phrase_exact_weight_without_title` | "spreadsheet hell" solo en texto → score >= 3, > 0 |
| `test_semantic_score_empty_strings` | ("", "") → 0.0 |
| `test_semantic_score_none_values_dont_raise` | (None, None) → 0.0 sin excepción |
| `test_semantic_score_none_title_with_text` | (None, texto neutro) → 0.0 |
| `test_semantic_score_empty_title_with_pain_in_text` | ("", texto con señal) → score > 0 |
| `test_pain_patterns_compiled_at_module_level` | `_PAIN_PATTERNS` es lista de `(re.Pattern, int)` con longitud igual a config |
| `test_offtopic_pattern_compiled_at_module_level` | `_OFFTOPIC_PATTERN` es `re.Pattern` |
| `test_pain_patterns_use_word_boundary_prefix` | Todos los patrones empiezan con `\b` |
| `test_all_showcase_prefixes_trigger_negative99` | Cada uno de los ~80 prefijos de config dispara -99.0 |
| `test_all_offtopic_signals_trigger_negative50` | Cada una de las señales off-topic de config dispara -50.0 |
| `test_pain_phrases_produce_positive_scores` | Las 5 primeras pain phrases de config producen score >= su peso |

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 22 items

tests/test_pain_filter.py ......................                         [100%]

============================== 22 passed in 0.03s ==============================
```

`init.sh` finaliza con `[OK] Entorno listo. Puedes empezar a trabajar.`
