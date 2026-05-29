# Implementación: #7 — data_loader_with_ranking

## Qué cambió

- **`src/saas_radar/storage/db.py`**: Se añadió la variable global `engine` al inicio del módulo (antes solo existía la función interna `_make_engine`). Ahora el módulo expone `engine = create_engine(os.environ.get("DB_URL", _DEFAULT_DB_URL), ...)` al importarse. Esto permite que los módulos de análisis hagan `from saas_radar.storage.db import engine` y que los tests hagan monkey-patch de ese nombre en el módulo importador.

- **`src/saas_radar/analysis/data_loader.py`** (nuevo): Módulo con dos funciones públicas:
  - `load_pain_comments_as_posts()`: lee comentarios de `reddit_comments`, filtra y convierte en posts virtuales.
  - `load_pain_posts(min_score, top_n, include_comments, post_age_days)`: carga posts, aplica filtros, recalcula semantic_score, fusiona comentarios, rankea y capea por subreddit.
  - Función privada auxiliar `_pseudo_title(text)`.

- **`tests/test_data_loader.py`** (nuevo): 15 tests con BD SQLite temporal (fixture `test_engine`) usando monkey-patch de `saas_radar.analysis.data_loader.engine`.

## Por qué

**Por qué añadir `engine` global a `db.py`**: El legacy tenía `engine = create_engine(os.getenv("DB_URL", "sqlite:///data/saas.db"))` como variable de módulo. En este proyecto `db.py` se había implementado con el patrón "engine local por función" (cada función llama a `_make_engine`), que es más explícito pero impide que otros módulos hagan `from saas_radar.storage.db import engine`. El módulo `data_loader` necesita un engine importable para que los tests puedan hacer monkey-patch efectivo del objeto que se usa en `pd.read_sql(..., con=engine)`. Sin el engine global, habría que parchear `pd.read_sql` directamente, lo que es más frágil.

**Por qué no copiar el código del legacy literalmente**: El legacy usaba `print()` para todo el output de debug mezclado con el output del pipeline. Este módulo usa `logger = logging.getLogger(__name__)` para debug interno y mantiene los `print()` solo para el output del pipeline visible al humano (cabeceras y resúmenes de progreso), siguiendo la convención del proyecto.

**Por qué `_pseudo_title` es función separada**: En el legacy era una función inline dentro de `load_pain_comments_as_posts`. Extraerla a función con nombre propio facilita el testing aislado y hace el código más legible.

**Por qué `body` en lugar de `text` en los helpers de test**: El parámetro `text` en un helper que también llama a `text(...)` de SQLAlchemy crea un shadowing del nombre importado, causando `TypeError: 'str' object is not callable`. Se renombró a `body` para evitar el conflicto de nombres.

## Impacto en el pipeline

- **Fase de análisis IA (M2)**: Este módulo es el entry point de la fase de extracción. `load_pain_posts` prepara el DataFrame que pasará a `extraction.py` (feature #9).
- **BD (`reddit_posts`, `reddit_comments`)**: Lectura con `pd.read_sql`. Nunca escribe.
- **Config (`SUBREDDITS`, `PAIN_CATEGORIES`, `MIN_SEMANTIC_SCORE`, etc.)**: Consume todas las constantes relevantes del módulo de configuración.
- **`pain_filter._semantic_score`**: Invocado por llamada para cada post y comentario, ignorando el valor persistido en BD. Esto es intencional: las listas `PAIN_SIGNAL_PHRASES` son mutables y pueden cambiar entre runs; el valor de BD puede estar desactualizado.
- **`storage/db.py`**: Se añade el `engine` global. Los tests existentes de `db.py` no importan `engine` directamente, así que no se ven afectados.

## Explicación técnica

### Variable global `engine` en `db.py`

```python
engine = create_engine(
    os.environ.get("DB_URL", _DEFAULT_DB_URL),
    connect_args={"check_same_thread": False},
)
```

- `os.environ.get("DB_URL", _DEFAULT_DB_URL)`: lee la URL de la BD del entorno; si no está definida, usa `"sqlite:///data/saas.db"` (relativa al directorio de trabajo).
- `connect_args={"check_same_thread": False}`: flag de SQLite que permite usar la conexión desde múltiples threads. Sin esto, SQLite lanza error si se accede desde un thread diferente al que creó la conexión. Necesario porque el ThreadPoolExecutor de la fase 3 (feature #12) podría acceder al engine desde workers.
- Al ser variable de módulo, se crea una sola vez cuando Python importa `saas_radar.storage.db`. Los módulos que hacen `from saas_radar.storage.db import engine` obtienen una referencia al mismo objeto. Cuando un test hace `patch("saas_radar.analysis.data_loader.engine", test_engine)`, reemplaza esa referencia en el namespace del módulo `data_loader`, sin tocar `db.py`.

### `_pseudo_title(text: str) -> str`

```python
def _pseudo_title(text: str) -> str:
    t = str(text).strip()
    for sep in [". ", "? ", "! ", "\n"]:
        idx = t.find(sep)
        if 0 < idx <= 120:
            return t[: idx + 1]
    return t[:120] + ("..." if len(t) > 120 else "")
```

- `str(text).strip()`: convierte a string explícitamente (el campo puede ser `None` en pandas) y elimina espacios iniciales/finales.
- `t.find(sep)`: busca el primer separador de frase. `find` devuelve -1 si no encuentra, o el índice del primer carácter del separador.
- `if 0 < idx <= 120`: el `0 <` evita que el texto empiece directamente con el separador (índice 0 no es una frase); el `<= 120` garantiza que el título extraído no supere 120 chars.
- `t[: idx + 1]`: incluye el carácter de puntuación pero no el espacio que sigue. Para `". "`, `idx` apunta al `.`, así `t[:idx+1]` captura hasta el `.` inclusive.
- `t[:120] + ("..." if len(t) > 120 else "")`: fallback si no hay separador en los primeros 120 chars. `"..."` solo se añade si el texto es más largo que 120 (si mide exactamente 120 o menos, no se trunca).

### `load_pain_comments_as_posts() -> pd.DataFrame`

```python
df = pd.read_sql(
    "SELECT comment_id, post_id, subreddit, text, score, created_utc "
    "FROM reddit_comments WHERE length(text) > 200",
    con=engine,
)
```

- `pd.read_sql(sql, con=engine)`: ejecuta la query SQL y devuelve un DataFrame. `con=engine` acepta un engine de SQLAlchemy o una conexión raw. La condición `WHERE length(text) > 200` filtra en SQL antes de traer datos a Python — más eficiente que filtrar en pandas.
- Solo se seleccionan las columnas necesarias (no `SELECT *`) para reducir la cantidad de datos transferidos.

```python
df["semantic_score"] = df.apply(
    lambda r: _semantic_score("", str(r["text"])), axis=1
)
```

- `df.apply(lambda r: ..., axis=1)`: aplica la lambda a cada fila (`axis=1`) del DataFrame, recibiendo una `pd.Series` con los valores de la fila.
- `_semantic_score("", str(r["text"]))`: para comentarios no hay título separado, se pasa string vacío como `title`. `str(r["text"])` convierte a string por si el campo es `None` o tiene tipo numérico.

```python
df = df[df["semantic_score"] >= MIN_SEMANTIC_SCORE]
```

- Filtro booleano de pandas: `df["semantic_score"] >= MIN_SEMANTIC_SCORE` devuelve una `pd.Series` de booleans, y `df[...]` selecciona solo las filas donde es `True`. `MIN_SEMANTIC_SCORE = 1.5`.

```python
mapped = pd.DataFrame(
    {
        "id": df["comment_id"],
        "source": "comment",
        ...
        "score": df["score"].fillna(0).astype(int),
        "upvote_ratio": 0.0,
        "num_comments": 0,
        ...
    }
)
```

- Se construye un `pd.DataFrame` desde un diccionario de columnas. Las columnas escalares (`"comment"`, `0.0`, `0`) se broadcast automáticamente para rellenar todas las filas.
- `df["score"].fillna(0).astype(int)`: `fillna(0)` reemplaza valores `NaN` por 0 (necesario porque `INTEGER` en SQLite puede ser NULL), y `.astype(int)` convierte el dtype de float64 (que pandas usa para columnas con NaN) a int64. Sin el `fillna` primero, `astype(int)` fallaría con NaN.
- `"upvote_ratio": 0.0` y `"num_comments": 0`: los comentarios no tienen estos campos en la tabla, se inicializan a valores neutros para que el ranking posterior los trate como señal mínima.
- `"source": "comment"`: discriminador que permite al downstream saber que esta fila vino de un comentario, no de un post.

### `load_pain_posts(min_score, top_n, include_comments, post_age_days)`

```python
posts = pd.read_sql("SELECT * FROM reddit_posts", con=engine)
posts["subreddit"] = posts["subreddit"].str.lower()
posts = posts[posts["subreddit"].isin({s.lower() for s in SUBREDDITS})]
```

- `SELECT * FROM reddit_posts`: carga toda la tabla. Se podría filtrar en SQL, pero la query resultante sería compleja y el volume de posts (≈20k) es manejable en memoria.
- `posts["subreddit"].str.lower()`: normaliza todos los subreddits a minúsculas. El accessor `.str` de pandas permite aplicar métodos de string vectorizadamente. Esto garantiza que `"SaaS"` y `"saas"` sean el mismo subreddit.
- `{s.lower() for s in SUBREDDITS}`: set comprehension para hacer la búsqueda O(1) en lugar de O(n). Se aplica `.lower()` a cada elemento de `SUBREDDITS` porque la lista del config puede tener capitalización mixta (e.g., `"PropertyManagement"`).

```python
if "created_utc" in posts.columns:
    cutoff = time.time() - post_age_days * 86400
    posts = posts[posts["created_utc"].fillna(0) >= cutoff]
```

- `time.time()`: devuelve el timestamp Unix actual en segundos (float).
- `post_age_days * 86400`: convierte días a segundos (86400 = 24 * 60 * 60).
- `posts["created_utc"].fillna(0)`: posts sin fecha (`NULL`) se tratan como si fueran del epoch Unix (1970), garantizando que sean descartados por el filtro temporal. Sin el `fillna`, la comparación con `NaN` siempre devuelve `False`, que en este caso produce el mismo efecto, pero el `fillna` lo hace explícito.

```python
posts["semantic_score"] = posts.apply(
    lambda r: _semantic_score(str(r["title"]), str(r["text"])), axis=1
).astype(float)
```

- `.astype(float)`: convierte el resultado a `float64`. Sin esto, si todos los valores fueran enteros (p.ej. todos -99), pandas podría inferir `int64`, y la normalización posterior con división flotante podría perder precisión.
- Se llama con `str(r["title"])` y `str(r["text"])` para manejar valores `NULL` (que pandas representa como `float NaN`), que convertidos con `str()` dan `"nan"`. Aunque no ideal, es el comportamiento del legacy y evita crashes.

```python
for col in ["score", "num_comments"]:
    posts[f"{col}_norm"] = posts.groupby("subreddit")[col].transform(
        lambda x: (x - x.min()) / (x.max() - x.min() + 1)
    )
```

- `posts.groupby("subreddit")`: agrupa el DataFrame por subreddit. La normalización es por subreddit (no global) para que un subreddit con posts de baja puntuación absoluta compita en igualdad de condiciones con uno de puntuaciones altas.
- `.transform(lambda x: ...)`: aplica la función a cada grupo y devuelve un resultado con el mismo índice que el DataFrame original, listo para asignar como nueva columna.
- `(x - x.min()) / (x.max() - x.min() + 1)`: normalización min-max modificada. El `+ 1` en el denominador es crucial: sin él, si todos los posts de un subreddit tienen el mismo valor (max == min), el denominador sería 0 y produciría `NaN` o `inf`. Con `+ 1`, el resultado cae en `[0, (max-min)/(max-min+1)]`, que siempre es menor que 1. Este es un trade-off documentado del legacy: ligeramente subestima los scores máximos pero nunca divide por cero.

```python
sem_max = posts["semantic_score"].max() or 1
posts["sem_norm"] = posts["semantic_score"] / sem_max
```

- `posts["semantic_score"].max() or 1`: si `max()` es 0 o `NaN`, usa 1 como divisor (evita división por cero). La normalización semántica es global (no por subreddit) porque el semantic_score ya es comparable entre subreddits al usar las mismas listas de señales.
- La fórmula `0.10*score_norm + 0.15*num_comments_norm + 0.75*sem_norm` es el blend documentado: el 75% del peso recae en la señal semántica, que es la métrica más fiable para detectar dolor real.

```python
def _cap(sub: str) -> int:
    return POSTS_CAP_HIGH_SIGNAL if sub.lower() in HIGH_SIGNAL_SUBREDDITS else POSTS_CAP_DEFAULT

posts = posts.sort_values("rank_score", ascending=False)
capped: list[pd.DataFrame] = []
for sub, group in posts.groupby("subreddit"):
    capped.append(group.head(_cap(sub)))
posts = pd.concat(capped).sort_values("rank_score", ascending=False).head(top_n)
```

- El cap se aplica **después** de ordenar por `rank_score`, así `group.head(_cap(sub))` se queda con los mejores N de cada subreddit.
- `pd.concat(capped)`: concatena todos los DataFrames de la lista. `ignore_index=False` (default) preserva los índices originales, que luego se descartan con `.reset_index(drop=True)` al final.
- `.head(top_n)`: límite global tras el cap por subreddit. Garantiza que el resultado tenga ≤ top_n filas.

```python
return posts.reset_index(drop=True)
```

- `reset_index(drop=True)`: regenera el índice entero 0..N-1. Sin esto, el DataFrame devuelto tendría índices discontinuos (e.g., 0, 3, 7, ...) porque proviene de operaciones de filtrado y concatenación. El downstream (extraction.py) puede iterar con `iterrows()` o acceder por posición con `iloc[i]` sin sorpresas de índice.

## Tests añadidos

| Test | Caso que cubre |
|------|----------------|
| `test_temporal_filter_removes_old_posts` | Post con `created_utc` fuera del rango `post_age_days` no aparece en el resultado. |
| `test_semantic_filter_removes_low_score_posts` | Post sin señal de dolor semántica (semantic_score < MIN_SEMANTIC_SCORE) queda excluido. |
| `test_semantic_score_recalculated_not_from_db` | Post con semantic_score persistido = -99 pero con señal real sí aparece, confirmando que se recalcula. |
| `test_ranking_formula_applied` | El DataFrame tiene columna `rank_score` y está ordenado descendente. |
| `test_cap_high_signal_subreddit` | Un subreddit HIGH_SIGNAL (`msp`) no supera `POSTS_CAP_HIGH_SIGNAL=10` posts. |
| `test_cap_default_subreddit` | Un subreddit normal (`notion`) no supera `POSTS_CAP_DEFAULT=4` posts. |
| `test_top_n_limits_result` | Con `top_n=5`, el resultado tiene ≤ 5 filas. |
| `test_comments_loaded_as_posts` | `load_pain_comments_as_posts` devuelve filas con `source='comment'` y campo `title`. |
| `test_comments_pseudo_title_first_sentence` | El pseudo-título es la primera frase del comentario (terminada en punto). |
| `test_short_comments_excluded` | Comentarios con ≤ 200 caracteres no entran en el resultado. |
| `test_include_comments_merges_into_posts` | Con `include_comments=True`, los posts de la BD aparecen en el resultado. |
| `test_empty_db_returns_empty_dataframe` | Con BD vacía, `load_pain_posts` devuelve `pd.DataFrame()` vacío sin error. |
| `test_min_score_filter` | Post con `score < min_score` no aparece en el resultado. |
| `test_category_filter` | Post con `category='showcase'` (no en `PAIN_CATEGORIES`) no aparece. |
| `test_result_has_reset_index` | El índice del DataFrame devuelto es continuo: `[0, 1, 2, ...]`. |

## Verificación

```
source .venv/bin/activate && python -m pytest tests/test_data_loader.py -v
```

```
============================= test session starts ==============================
collected 15 items

tests/test_data_loader.py::test_temporal_filter_removes_old_posts PASSED
tests/test_data_loader.py::test_semantic_filter_removes_low_score_posts PASSED
tests/test_data_loader.py::test_semantic_score_recalculated_not_from_db PASSED
tests/test_data_loader.py::test_ranking_formula_applied PASSED
tests/test_data_loader.py::test_cap_high_signal_subreddit PASSED
tests/test_data_loader.py::test_cap_default_subreddit PASSED
tests/test_data_loader.py::test_top_n_limits_result PASSED
tests/test_data_loader.py::test_comments_loaded_as_posts PASSED
tests/test_data_loader.py::test_comments_pseudo_title_first_sentence PASSED
tests/test_data_loader.py::test_short_comments_excluded PASSED
tests/test_data_loader.py::test_include_comments_merges_into_posts PASSED
tests/test_data_loader.py::test_empty_db_returns_empty_dataframe PASSED
tests/test_data_loader.py::test_min_score_filter PASSED
tests/test_data_loader.py::test_category_filter PASSED
tests/test_data_loader.py::test_result_has_reset_index PASSED

15 passed in 0.36s
```

Suite completa:

```
source .venv/bin/activate && python -m pytest
```

```
156 passed in 0.75s
```

Ruff: sin errores. Import: OK.
