# Implementación: #4 — scraper_reddit_basic

## Qué cambió

- **`src/saas_radar/scrapers/__init__.py`**: archivo nuevo (vacío funcional). Convierte el directorio `scrapers/` en un paquete Python importable. Sin este archivo, `from saas_radar.scrapers.reddit_scraper import ...` fallaría con `ModuleNotFoundError`.

- **`src/saas_radar/scrapers/reddit_scraper.py`**: módulo nuevo con cuatro funciones públicas/privadas que encapsulan toda la interacción con la API de Reddit vía PRAW. Antes no existía ningún scraper en el nuevo paquete; este módulo lo implementa desde cero replicando el comportamiento del legacy pero adaptado a las convenciones de `saas-radar`.

- **`tests/test_reddit_scraper.py`**: suite nueva de 10 tests que cubren todos los criterios de aceptación de la feature. PRAW está completamente mockeado; ningún test hace llamadas reales a Reddit.

## Por qué

**Singleton `_reddit`**: la variable de módulo `_reddit: praw.Reddit | None = None` actúa como caché de la conexión. Crear un cliente PRAW es barato pero innecesariamente repetitivo si se llaman `fetch_posts` y `search_pain_posts` en el mismo run. El patrón `global _reddit; if _reddit is None: crear` es el más simple que funciona sin threading (el pipeline es single-threaded en su flujo principal).

**`getattr(post, campo, default)`**: los objetos PRAW pueden ser instancias de `Submission` completas o de `MoreComments` (nodos que requieren expansión adicional). Usar `getattr` con un default en lugar de acceso directo (`post.title`) evita `AttributeError` inesperados si algún objeto incompleto se cuela en el feed.

**`seen_ids: set[str]`**: Reddit devuelve los mismos posts en feeds distintos (un post muy upvoteado aparece en `hot` y también en `top("month")`). Usar un `set` para rastrear ids ya vistos permite dedup en O(1) durante la iteración, sin necesidad de `df.drop_duplicates()` posterior que sería O(n log n).

**`replace_more(limit=0)`**: los comentarios de Reddit están paginados con nodos `MoreComments`. Si no se resuelven, iterar la lista de comentarios devuelve objetos `MoreComments` sin atributo `body`. Con `limit=0` le decimos a PRAW "no expandas ningún MoreComments" — los descartamos sin hacer peticiones extra. Esto es el trade-off velocidad vs completitud que el legacy también eligió.

**`time.sleep(0.1)` en `search_pain_posts`**: la API de Reddit permite ~60 peticiones/minuto en modo read-only. Al iterar un search con 50 resultados, el loop hace 50 accesos de atributo sobre objetos lazy (que pueden triggear peticiones lazy). El sleep cortés de 100ms entre posts reduce la presión sobre la API sin ser demasiado lento para el pipeline.

**`subreddit_name.lower()` en `_post_to_dict`**: la convención del proyecto (y del legacy) normaliza subreddits a minúsculas para que `r/SaaS` y `r/saas` sean el mismo subreddit en BD. La normalización ocurre aquí, en el punto de entrada, para garantizar que todo row del DataFrame ya llega normalizado antes de persistirse.

**`post.subreddit.display_name.lower()` en `search_pain_posts`**: en el contexto de search, el subreddit no es el argumento de la función sino el del post devuelto (puede ser cualquier subreddit del multireddit). Se extrae del objeto post y se normaliza igual.

## Impacto en el pipeline

- **Scraping (fase 1 y 2 del main.py futuro)**: este módulo es el punto de entrada de todos los posts al sistema. `fetch_posts` alimenta la fase 1 (feeds por subreddit), `search_pain_posts` alimenta la fase 2 (búsqueda de dolor), `fetch_top_comments` alimenta la fase 3 (comentarios).
- **Base de datos**: los DataFrames que devuelve `fetch_posts` y `search_pain_posts` son los que se pasan a `save_to_db` (feature #2). El campo `subreddit` normalizado aquí es el que se almacena en `reddit_posts`.
- **Análisis posterior**: `source` distingue posts de feed (`"subreddit_feed"`) de posts de búsqueda (`"pain_search"`), lo que permite filtros en el data loader (feature #7).
- **No hay efecto en BD, LLM ni Telegram**: este módulo solo produce DataFrames y listas de dicts en memoria.

## Explicación técnica

### `get_reddit() -> praw.Reddit`

```python
global _reddit
if _reddit is None:
    _reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )
return _reddit
```

`global _reddit` es necesario para que la asignación `_reddit = praw.Reddit(...)` dentro de la función actualice la variable de módulo y no cree una variable local nueva. Sin `global`, Python interpretaría `_reddit = ...` como asignación local y la siguiente llamada seguiría viendo `_reddit` de módulo como `None`.

`praw.Reddit(client_id=..., client_secret=..., user_agent=...)` crea un cliente OAuth2 read-only. El `user_agent` es obligatorio para la API de Reddit (los bots deben identificarse); lo leemos de config donde tiene default `"saas-radar/1.0"`.

### `_post_to_dict(post, source, subreddit_name, search_query) -> dict`

Cada `getattr(post, "campo", default)` sirve como defensa: si PRAW devuelve un objeto incompleto (raro pero posible en posts borrados o banned), el campo toma un valor seguro en lugar de propagar `AttributeError`.

- `"text": getattr(post, "selftext", "")`: el cuerpo del post se llama `selftext` en PRAW, no `body`. `body` es el campo de los comentarios.
- `"flair": getattr(post, "link_flair_text", None)`: el flair puede ser `None` perfectamente (posts sin flair), por eso el default es `None` no `""`.
- `subreddit_name.lower()`: normalización a minúsculas; ver sección "Por qué" arriba.

### `fetch_posts(subreddit_name, limit, incremental) -> pd.DataFrame`

```python
if incremental:
    feeds = [sub.new(limit=limit), sub.hot(limit=limit), sub.top("day", limit=limit // 2)]
else:
    feeds = [sub.hot(limit=limit), sub.new(limit=limit // 2), sub.top("month", limit=limit // 2), sub.top("year", limit=limit // 2)]
```

Cada elemento de `feeds` es un generador de PRAW (iterable lazy). El orden importa para el dedup: en modo full, `hot` va primero porque sus posts tienen más engagement y son más relevantes para el análisis. En modo incremental, `new` va primero para capturar posts recientes antes de que entren en `hot`.

`limit // 2` (división entera): los feeds secundarios reciben la mitad del límite para no sobrecargar la cuota de la API con duplicados probables.

```python
seen_ids: set[str] = set()
for feed in feeds:
    for post in feed:
        pid = getattr(post, "id", None)
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)
        posts.append(...)
```

`not pid` cubre el caso de posts sin id (prácticamente imposible pero defensivo). `pid in seen_ids` es O(1) en un set, mucho más eficiente que buscar en la lista `posts` que sería O(n).

`pd.DataFrame(posts)` con lista vacía devuelve un DataFrame vacío (columnas vacías, 0 filas), no lanza error. Esto es el comportamiento esperado para el test de feeds vacíos.

### `search_pain_posts(query, limit, incremental) -> pd.DataFrame`

```python
sub_str = "+".join(SUBREDDITS)
```

La sintaxis `r/saas+r/entrepreneur+...` es la forma nativa de Reddit para crear un "multireddit" sin cuenta. PRAW lo expone como `reddit.subreddit("saas+entrepreneur+...")`.

```python
kwargs: dict = {"sort": "relevance", "limit": limit}
if incremental:
    kwargs["time_filter"] = "day"
```

`sort="relevance"` maximiza la señal de dolor (posts más relacionados con la query). `time_filter="day"` en modo incremental evita re-procesar posts ya analizados en runs anteriores.

```python
subreddit_name = getattr(post.subreddit, "display_name", "").lower()
```

`post.subreddit` en objetos de search es el subreddit real donde vive el post (puede ser `r/SaaS`, `r/Entrepreneur`, etc.). `.display_name` da el nombre canónico (con mayúsculas); `.lower()` normaliza.

### `fetch_top_comments(post_id, limit) -> list[dict]`

```python
submission = reddit.submission(id=post_id)
submission.comments.replace_more(limit=0)
```

`reddit.submission(id=post_id)` carga el post por su id. La propiedad `.comments` es lazy: la primera vez que se accede PRAW hace la petición HTTP. `replace_more(limit=0)` resuelve la lista plana de comentarios eliminando los nodos `MoreComments` sin expandirlos (0 peticiones extra).

```python
for comment in submission.comments[:limit]:
    if not hasattr(comment, "body"):
        continue
    if len(comment.body) < COMMENT_MIN_LENGTH:
        continue
```

`not hasattr(comment, "body")`: después de `replace_more(limit=0)`, pueden quedar objetos `MoreComments` si el límite de la llamada inicial fue bajo. Estos no tienen `body`. El `hasattr` los filtra sin error.

`len(comment.body) < COMMENT_MIN_LENGTH`: filtra comentarios demasiado cortos para análisis (default 50 chars en config). Un comentario de "lol" o "thanks" no aporta señal de dolor.

```python
"subreddit": submission.subreddit.display_name.lower(),
```

Se usa el subreddit del submission (no del comment) porque todos los comentarios del post están en el mismo subreddit.

## Tests añadidos

1. **`test_get_reddit_singleton`**: llama `get_reddit()` dos veces con `praw.Reddit` mockeado; verifica que el constructor se llama solo una vez y ambas referencias apuntan al mismo objeto.

2. **`test_fetch_posts_full_mode_feeds`**: en modo `incremental=False`, verifica que `sub.hot`, `sub.new`, `sub.top("month")` y `sub.top("year")` se invocan. Comprueba que el DataFrame tiene 4 filas (una por post distinto de cada feed).

3. **`test_fetch_posts_incremental_mode_feeds`**: en modo `incremental=True`, verifica que `sub.new`, `sub.hot` y `sub.top("day")` se invocan, y que `"month"` y `"year"` no aparecen en las llamadas a `top`.

4. **`test_fetch_posts_dedup`**: un mismo post aparece en feeds `hot` y `new`; verifica que el DataFrame resultante solo contiene una fila con ese id.

5. **`test_fetch_posts_returns_empty_dataframe_when_no_posts`**: todos los feeds devuelven listas vacías; verifica que el resultado es un DataFrame vacío sin levantar excepción.

6. **`test_search_pain_posts_uses_multireddit`**: verifica que `reddit.subreddit` se llama con `"+".join(SUBREDDITS)` y que `.search` recibe el query correcto.

7. **`test_search_pain_posts_incremental_adds_time_filter`**: en modo `incremental=True`, verifica que `time_filter="day"` aparece en los kwargs de `.search`.

8. **`test_fetch_top_comments_filters_short`**: un comentario corto (< `COMMENT_MIN_LENGTH`) y uno largo; verifica que solo el largo aparece en el resultado.

9. **`test_fetch_top_comments_calls_replace_more`**: verifica que `submission.comments.replace_more(limit=0)` se llama exactamente una vez.

10. **`test_subreddit_name_lowercased_in_fetch_posts`**: llama `fetch_posts("SaaS")`; verifica que el campo `subreddit` del DataFrame contiene `"saas"` (normalizado por `_post_to_dict`).

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 10 items

tests/test_reddit_scraper.py ..........                                  [100%]

============================== 10 passed in 0.27s ==============================
```

`init.sh` termina con `[OK] Entorno listo. Puedes empezar a trabajar.` (el WARN de pytest en el paso 5 del script es porque el `python3` del sistema no tiene pytest instalado globalmente; el venv sí lo tiene y los tests pasan correctamente).

## Fix de formato

Corrección aplicada tras revisión del reviewer (defectos I001 y E302/W391).

```
$ ruff format src/saas_radar/scrapers/reddit_scraper.py tests/test_reddit_scraper.py
2 files reformatted

$ ruff check --fix --select I src/saas_radar/scrapers/reddit_scraper.py tests/test_reddit_scraper.py
Found 1 error (1 fixed, 0 remaining).
```

Verificación posterior:

```
$ ruff format --check src/saas_radar/scrapers/reddit_scraper.py tests/test_reddit_scraper.py
2 files already formatted

$ ruff check --select E,F,I,B,UP src/saas_radar/scrapers/reddit_scraper.py tests/test_reddit_scraper.py
All checks passed!

$ python -m pytest tests/test_reddit_scraper.py -v
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 10 items

tests/test_reddit_scraper.py ..........                                  [100%]

============================== 10 passed in 0.26s ==============================
```
