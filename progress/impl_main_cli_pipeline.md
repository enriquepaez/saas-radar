# Implementación: #12 — main_cli_pipeline

## Qué cambió

- **`src/saas_radar/main.py`** (nuevo): Pipeline CLI completo. Antes no existía. Ahora
  orquesta las fases 1-5 con argparse, detección de modo INCREMENTAL/CARGA COMPLETA,
  enriquecimiento de posts/comentarios y ThreadPoolExecutor para comentarios.

- **`tests/test_main.py`** (nuevo): 10 tests que cubren flags argparse, detección de modo,
  stub de phase_gtm, uso de ThreadPoolExecutor y E2E con mocks de PRAW + LLM + BD temporal.

- **`feature_list.json`**: feature #12 cambiada de `pending` a `in_progress`.

## Por qué

El legacy (`/home/enriquepaez/projects/reddit-saas-radar/main.py`) tenía imports relativos
(`from analysis.ai_analyzer import ...`) que ya no sirven en el paquete pip-installable.
Se replico el comportamiento exacto (detección incremental, fases 1-3, phase_gtm stub)
pero usando imports absolutos `from saas_radar.*` según `docs/conventions.md`.

La `phase_gtm()` es un stub deliberado: feature #17 implementará el agente GTM real.
El stub solo imprime mensajes y no importa ningun agente externo, cumpliendo el criterio
"--skip-gtm omite la fase 5 sin importar agents.gtm_agent (import lazy)".

## Impacto en el pipeline

- **CLI**: `python -m saas_radar.main` ahora es el punto de entrada del pipeline completo.
- **Scraping (fases 1-3)**: `phase_subreddits`, `phase_pain_search`, `phase_comments` orquestan
  `fetch_posts`, `search_pain_posts`, `fetch_top_comments` del scraper.
- **BD**: `init_db()` se llama al arrancar; `save_to_db()` persiste posts y comentarios.
- **LLM (fase 4)**: `run_ai_analysis()` se llama con los parametros CLI.
- **GTM (fase 5)**: stub que imprime mensajes hasta que feature #17 la implemente.

## Explicación técnica

### `_fmt(seconds: float) -> str`

Convierte segundos enteros en formato legible. `divmod(seconds, 3600)` retorna el
cociente (horas) y resto. Luego `divmod(rem, 60)` extrae minutos y segundos. El
`:02d` en el f-string rellena con ceros a la izquierda hasta 2 digitos (ej: `05`
en vez de `5`). Solo muestra la parte de horas si `h > 0`, evitando `0h 01m 05s`.

### `enrich_posts(df: pd.DataFrame) -> pd.DataFrame`

Recibe un DataFrame con columnas `title` y `text`. Hace `.copy()` antes de mutar —
sin esto, pandas lanzaria `SettingWithCopyWarning` y los cambios podrian no propagarse
si `df` fuera un slice de otro DataFrame. Concatena title+text con espacio para que
`clean_text` tenga contexto completo. `df.apply(lambda row: ..., axis=1)` aplica la
funcion fila a fila (axis=1 = filas, axis=0 = columnas). `row.get("title", "")` es
mas seguro que `row["title"]` porque no lanza KeyError si la columna falta.

### `enrich_comments(comments: list[dict]) -> list[dict]`

Muta los dicts en el lugar (no copia) porque los comentarios no se reusan; se procesan
y persisten en seguida. `classify_post("", c["text"])` pasa titulo vacio porque los
comentarios no tienen titulo propio — el clasificador funciona solo con el body.

### `phase_subreddits(incremental: bool) -> pd.DataFrame`

Itera `SUBREDDITS` (36 subreddits de `config.py`). `time.sleep(1)` entre subreddits
es cortesia con la API de Reddit para no ser rate-limited. `pd.concat(all_posts, ignore_index=True)`
une todos los DataFrames en uno; `ignore_index=True` recalcula el indice numerico
desde 0 en vez de preservar indices de cada DataFrame fuente. `.drop_duplicates(subset="id")`
dedup por id de post (un post puede aparecer en multiples feeds). El `try/except` atrapa
errores de red o de PRAW por subreddit sin abortar el loop completo — patron del legacy
documentado en `docs/legacy-context/lessons-learned.md`.

### `phase_pain_search(incremental: bool) -> pd.DataFrame`

Identica a `phase_subreddits` pero itera `PAIN_SEARCH_QUERIES` y usa
`search_pain_posts(query)`. `time.sleep(2)` — mas tiempo que fase 1 porque las
busquedas suelen consumir mas quota de la API de Reddit.

### `phase_comments(posts_df: pd.DataFrame) -> None`

Filtra posts con `num_comments >= HIGH_ENGAGEMENT_THRESHOLD` (100). Si hay mas de
`COMMENT_TARGET_POSTS` (200) posts elegibles, los prioriza por `semantic_score` +
`num_comments` descendente y toma solo los primeros 200 — evita bajar comentarios
de miles de posts en modo full scan.

`ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS)` lanza hasta 8 hilos en
paralelo. Cada hilo ejecuta `fetch_safe(post_id)` que envuelve `fetch_top_comments`
en try/except para que un fallo en un post no cancele los demas. `as_completed(futures)`
es un generador que entrega futuros en el orden en que terminan (no en el orden de
envio) — mas eficiente que iterar `futures` en orden original esperando uno por uno.
`futures = {executor.submit(...): row["id"]}` es un dict de Future→post_id que permite
identificar que post fallo si necesitaramos hacer logging granular (aunque aqui no lo
usamos, es el patron standard del legacy).

`pd.DataFrame(all_comments).drop_duplicates(subset="comment_id")` — un mismo comentario
puede aparecer en multiples posts si Reddit lo devuelve como "cross-post". El dedup por
`comment_id` lo evita.

### `phase_gtm() -> None`

Stub deliberado: imprime dos lineas fijas y retorna. No importa ningun modulo de agentes.
Esto cumple el criterio de aceptacion 7: "--skip-gtm omite la fase 5 sin importar
agents.gtm_agent (import lazy)". Feature #17 reemplazara este stub con la logica real.

### `run_pipeline(...) -> None`

Funcion principal. Orden de operaciones:
1. `init_db()` — siempre, aunque se salten las fases de scrape. Garantiza que el schema
   esta actualizado antes de cualquier operacion.
2. Deteccion de modo: `has_successful_run()` consulta `analysis_runs` en la BD.
   Si devuelve True Y no se fuerza `--full-scan`, entra en modo incremental (solo 24h).
3. `post_age_days` se pasa a `run_ai_analysis()` para que el data_loader filtre posts
   por edad temporal.
4. Fases 1-3 solo si no `skip_scrape`. Se concatenan `subreddit_posts` y `pain_posts`
   con dedup antes de pasar a `phase_comments` — el DataFrame unificado permite priorizar
   correctamente por semantic_score.
5. Fase 4 solo si no `skip_ai`.
6. Fase 5 solo si no `skip_gtm`.

### Fix de encoding (lineas 27-29)

`sys.stdout.reconfigure(encoding="utf-8")` fuerza UTF-8 en consolas Windows (cp1252
por defecto) que no pueden renderizar emojis ni caracteres latinos del output. El guard
`hasattr(sys.stdout, "reconfigure")` es necesario porque en entornos donde stdout esta
redirigido a un StringIO (como en algunos tests de pytest) el metodo no existe.

## Tests añadidos

| Test | Qué cubre |
|------|-----------|
| `test_argparse_has_all_required_flags` | Verifica que los 8 flags con sus defaults correctos existen en argparse |
| `test_skip_all_flags_no_exception` | `--skip-scrape --skip-ai --skip-gtm` completa sin excepcion; init_db se llama una vez |
| `test_incremental_mode_when_previous_run_exists` | `has_successful_run=True` → output contiene "INCREMENTAL" |
| `test_full_load_mode_when_no_previous_run` | `has_successful_run=False` → output contiene "CARGA COMPLETA" |
| `test_full_scan_flag_forces_full_load` | `full_scan=True` con `has_successful_run=True` → output "CARGA COMPLETA" + "forzado con --full-scan" |
| `test_e2e_full_pipeline_with_mocks` | Run completo con mocks de fetch_posts, search_pain_posts, fetch_top_comments, run_ai_analysis, save_to_db — no lanza excepcion |
| `test_phase_gtm_stub_prints_message` | `phase_gtm()` imprime "FASE 5" y "feature #17" |
| `test_phase_comments_uses_thread_pool` | `ThreadPoolExecutor` se construye con `max_workers=COMMENT_FETCH_WORKERS` |
| `test_fmt_formats_seconds_as_mmss` | `_fmt(0)`, `_fmt(65)`, `_fmt(3661)` devuelven los formatos esperados |
| `test_enrich_posts_adds_required_columns` | El DataFrame resultado tiene columnas `clean_text`, `category`, `semantic_score` |

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 10 items

tests/test_main.py ..........                                            [100%]

======================== 10 passed in 228.81s (0:03:48) ========================
```

`./init.sh` finaliza con `[OK] Entorno listo. Puedes empezar a trabajar.` (exit code 0).
