# Inventario técnico — reddit-saas-radar (legacy)

> Snapshot del estado a **2026-05-28**. Documento de referencia para reconstruir el
> proyecto en `saas-radar`. No se actualiza tras un cambio en el código vivo;
> verificar contra `git log` antes de portar nada.

Resumen rápido:

- **8.843 líneas Python** en 31 ficheros `.py`.
- **7 tablas SQLite** en `data/saas.db` (79 MB).
- **3 workflows GitHub Actions** (pipeline, tuner, reminders).
- **3 proveedores LLM** (Claude, Gemini, Groq), pluggable vía `AI_PROVIDER`.
- **5 fases** en `main.py` (scrape subreddits, pain search, comments, IA, GTM).
- **9 suites de tests** pytest (~119 tests cuando se cerró B2).

---

## 1. Módulos por capa

### 1.1 Orquestación — `main.py` (351 líneas)

CLI con argparse. Funciones principales:

| Función | Qué hace |
|---|---|
| `_fmt(seconds)` | Formatea segundos como `mm:ss` / `hh:mm:ss`. |
| `enrich_posts(df)` | Aplica `clean_text`, `classify_post`, `_semantic_score` a un DataFrame. |
| `enrich_comments(comments)` | Lo mismo para una lista de dicts de comentarios. |
| `phase_subreddits(incremental)` | Fase 1: scrape de subreddits configurados. |
| `phase_pain_search(incremental)` | Fase 2: búsqueda por queries de dolor. |
| `phase_comments(posts_df)` | Fase 3: comentarios de posts con `num_comments >= 100`. |
| `phase_gtm(min_priority=7)` | Fase 5: GTM agent sobre opps canónicas pendientes (envuelto en try/except). |
| `run_pipeline(...)` | Encadena todas las fases con detección automática de modo (incremental vs full). |

Flags CLI (todos opcionales):

| Flag | Default | Efecto |
|---|---|---|
| `--skip-scrape` | False | Salta fases 1-3, usa datos en BD. |
| `--skip-ai` | False | Salta fase 4 (análisis IA). |
| `--skip-gtm` | False | Salta fase 5 (GTM agent). |
| `--min-score` | 5 | Score mínimo de Reddit para incluir un post. |
| `--top-posts` | 80 (`MAX_POSTS`) | Máximo de posts a analizar con IA. |
| `--output` | `data/ai_analysis.json` | Ruta del JSON de resultados. |
| `--use-cached-extractions` | False | Reanuda síntesis desde `extractions_cache.json`. |
| `--full-scan` | False | Fuerza scan 365d aunque ya haya runs exitosos. |

Detección automática de modo: `has_successful_run() → INCREMENTAL (24h)` / sino `CARGA COMPLETA (365d)`.

### 1.2 Configuración — `config.py` (589 líneas)

Toda la configuración del pipeline. Bloques:

- **Credenciales (env vars vía `python-dotenv`)**: `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `REDDIT_CLIENT_ID/SECRET/USER_AGENT`, `DB_URL` (default `sqlite:///data/saas.db`).
- **Constantes scraping**: `POST_LIMIT=100`, `PAIN_SEARCH_LIMIT=50`, `COMMENT_MIN_LENGTH=50`, `HIGH_ENGAGEMENT_THRESHOLD=100`, `COMMENT_FETCH_WORKERS=8`, `COMMENT_TARGET_POSTS=200`.
- **AI provider**: `AI_PROVIDER` (claude | gemini | groq), URLs y modelos por proveedor. Default: Claude Haiku 4.5 para extracción + Sonnet 4.6 para síntesis.
- **Constantes IA**: `MAX_POSTS=80`, `TEXT_SNIPPET_LEN=500`, `MIN_SEMANTIC_SCORE=1.5`, `MAX_POST_AGE_DAYS=365`, `INCREMENTAL_POST_AGE_DAYS=1`, `CIRCUIT_BREAKER_THRESHOLD=3`.
- **`PAIN_CATEGORIES`**: `["pain_point", "question_operational"]` — categorías que entran al pipeline IA.
- **`PAIN_SIGNAL_PHRASES`**: ~120 tuplas `(phrase, peso 1-3)` para `_semantic_score`. Bloques: workaround manual (+3), herramienta concreta con limitación (+3), ausencia de tool (+2), pregunta operacional (+1/+2), frustración temporal (+2/+3), pago explícito (+3), integraciones rotas (+2).
- **`SHOWCASE_TITLE_PREFIXES`**: ~80 prefijos de título que disparan penalización -99 (showcases, "how I…", "I built…", "advice from…").
- **`OFF_TOPIC_SIGNALS`**: ~60 frases que disparan penalización -50 (burnout, política, salud mental, carrera personal, motivacional, off-topic temáticos).
- **`HIGH_SIGNAL_SUBREDDITS`**: set de 13 subreddits con cap de posts más generoso (msp, sysadmin, devops, accounting, bookkeeping, taxpros, restaurantowners, amazonseller, agency, propertymanagement, construction, nocode, freelance, ecommerce, smallbusiness, zapier).
- **`POSTS_CAP_HIGH_SIGNAL=10` / `POSTS_CAP_DEFAULT=4`**: cap por subreddit en el ranking final.
- **`SUBREDDITS`**: lista de 36 subreddits organizada por tiers A/B/C/D + descubiertos.
- **`PAIN_SEARCH_QUERIES`**: ~90 queries de dolor agrupadas por nicho (contabilidad, restaurantes, property mgmt, agencias, MSP, construcción, no-code).

### 1.3 Análisis — `analysis/`

| Fichero | Líneas | Funciones públicas | Rol |
|---|---:|---|---|
| `text_cleaning.py` | 27 | `clean_text(text)`, `normalize_for_classifier(text)` | NLP básico con NLTK stopwords. |
| `post_classifier.py` | 86 | `classify_post(title, text)` | Clasifica en 6 categorías por keywords. |
| `pain_filter.py` | 65 | `_semantic_score(title, text)` | Score semántico con regex pre-compiladas + showcase/off-topic. |
| `data_loader.py` | 156 | `load_pain_posts(min_score, top_n, include_comments, post_age_days)`, `load_pain_comments_as_posts()` | Carga BD → filtros → merge comentarios → ranking blend 10/15/75. |
| `extraction.py` | 487 | `extract_problem_from_post`, `extract_problem_deep`, `extract_problems_batch`, `_clean_extractions`, `_infer_workaround`, `_is_non_saas_pain` | Fase 4a: 3 prompts (single, deep, batch), 4 reglas de limpieza. |
| `synthesis.py` | 477 | `build_synthesis_prompt(extractions)`, `_validate_synthesis(results, ordered_extractions)`, `_coherence_words`, `_quotes_are_coherent` | Fase 4b: prompt v3 con RULES 1-7 + validador post-síntesis (cantidad mínima + coherencia léxica). |
| `llm_clients.py` | 266 | `call_claude`, `call_gemini`, `call_groq`, `call_llm` (dispatcher), `_parse_json_payload` | Clientes HTTP con retries, parseo de rate limits, JSON tolerante a fences. |
| `ai_analyzer.py` | 443 | `run_ai_analysis(...)`, `print_results`, `save_results`, `_save/_load_extractions_cache` | Orquestador IA: extracción (deep ≤30 posts, batch >30) + retry + síntesis + persistencia + Telegram + meta. |
| `meta_analysis.py` | 302 | `generate_meta_analysis`, `save_meta_analysis`, `print_meta_summary`, `_find_empty_queries`, `_find_discovered_subreddits`, `_build_recommendations` | Informe post-run: hit rate por sub, queries vacías, nichos recurrentes, descubrimientos, recomendaciones accionables. |
| `dedup.py` | 163 | `find_canonical(opp, existing, threshold=0.3)`, `evidence_overlap`, `name_similarity` | B0: Jaccard sobre tokens de `evidence_quotes`. |
| `prompts/gtm.py` | 185 | `build_gtm_prompt(opp)` | Prompt 3 tareas (viabilidad + GTM + plan 7d + KPIs). |

### 1.4 Scraping — `scrapers/reddit_scraper.py` (122 líneas)

| Función | Qué hace |
|---|---|
| `get_reddit()` | Singleton de cliente PRAW. |
| `fetch_posts(sub, limit, incremental)` | Hot + new + top-month + top-year (full) o new + hot + top-day (incremental). Dedup por id. |
| `search_pain_posts(query, limit, incremental)` | Búsqueda en el multireddit de todos los subreddits configurados; `time_filter='day'` en incremental. |
| `fetch_top_comments(post_id, limit=30)` | Comentarios top con `replace_more(limit=0)` y filtro `len >= COMMENT_MIN_LENGTH`. |

### 1.5 Storage — `storage/db.py` (533 líneas)

SQLAlchemy sobre SQLite. Una migración idempotente por columna añadida (`semantic_score`, `canonical_id`). 7 tablas (ver §2).

| Función | Qué hace |
|---|---|
| `init_db()` | Crea tablas + índices + migraciones idempotentes. |
| `save_to_db(df, table_name)` | INSERT OR IGNORE vía tabla `_staging_<name>`. Normaliza subreddit a minúsculas. |
| `load_from_db(table_name)` | `pd.read_sql("SELECT * FROM ...")`. |
| `db_stats()` | Conteos de `reddit_posts` y `reddit_comments`. |
| `persist_run_to_db(...)` | Inserta `analysis_runs` + N `opportunities` con `canonical_id` calculado por `find_canonical`. Devuelve `run_id`. |
| `load_active_opportunities()` | Vista canónica: `id == canonical_id AND discarded = 0`. |
| `persist_meta_recommendations(run_id, recs)` | Inserta o incrementa `recurrence` cuando ya existe el mismo `type+target` no actuado. |
| `_extract_target(rec_type, action)` | Extrae el target (subreddit, query…) de una recomendación para deduplicar entre runs. |
| `has_successful_run()` | `True` si hay al menos 1 run con `status='ok'`. |
| `persist_gtm(opportunity_id, payload)` | Inserta fila en `opportunity_gtm` con serialización JSON automática de 5 campos. |
| `load_gtm(opportunity_id)` | Carga con parseo tolerante de JSONs corruptos. |
| `has_gtm(opportunity_id)` | Comprobación binaria. |

### 1.6 Agents — `agents/`

| Fichero | Líneas | Rol |
|---|---:|---|
| `tuning_rules.py` | 295 | 4 reglas deterministas (sin LLM): promover/quitar/demote subreddit, quitar query. Orquestador `propose_all_changes`. |
| `tuner.py` | 273 | CLI `python -m agents.tuner` con `--lookback`, `--max-changes`, `--dry-run`, `--show-diff`. Carga meta-JSONs, consulta `meta_recommendations`, prioriza (conservador primero + recurrence desc), renderiza report y diff simulado de `config.py`. Modo PR real (`--apply`) **NO implementado** — fase A4 pendiente. |
| `gtm_agent.py` | 335 | CLI `python -m agents.gtm_agent --opp-id N` / `--all-pending`. Llama LLM via `build_gtm_prompt`, valida payload, aplica gate `viability_total < 20 → skipped_low_viability`. Estados: `generated`, `skipped_low_viability`, `failed`. |

### 1.7 Helpers — `helpers/`

| Fichero | Líneas | Rol |
|---|---:|---|
| `audit_filter.py` | 343 | Auditoría offline del pre-filtro semántico. Genera `data/audit_filter.md` con stats por sub/query/phrase/source + top supervivientes/near misses + ejemplos descartados. No consume API. |
| `groq_quota.py` | 127 | Check de cuota Groq vía headers `x-ratelimit-*`. |
| `gemini_quota.py` | 114 | Check de cuota Gemini (Google no expone counters — solo response status). |

### 1.8 Notificaciones — `notifications/telegram.py` (168 líneas)

| Función | Qué hace |
|---|---|
| `send_opportunity_alert(opp)` | Envía alerta si `priority_score >= TELEGRAM_ALERT_THRESHOLD` (default 8). |
| `send_run_summary(...)` | Resumen del run (posts, opps, duración, modo). |
| `send_tuner_report(path)` | Envía el dry-run report del tuner como bloque ``` (truncado a 3900 chars). |
| `send_text(text)` | Helper genérico (truncado a 4000 chars). Usado por `reminders.yml`. |

No-op silencioso si faltan `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID`.

### 1.9 Scripts — `scripts/backfill_canonical.py` (136 líneas)

One-shot: recorre `opportunities` en orden cronológico, propone `canonical_id` con `find_canonical`. Soporta `--dry-run`, `--yes`, `--force`, `--threshold`. Migración inline (añade columna si la BD es antigua).

### 1.10 Dashboard — `dashboard/app.py` (17 líneas)

**Scaffold mínimo**. Solo muestra `len(df)` y top 10 posts por upvotes. No tiene oportunidades, no tiene GTM, no tiene autenticación.

### 1.11 Tests — `tests/`

| Fichero | Tests aprox. | Cubre |
|---|---:|---|
| `test_text_cleaning.py` | ~5 | `clean_text` paths. |
| `test_post_classifier.py` | ~10 | Categorías pain/showcase/operational. |
| `test_dedup.py` | 19 | Jaccard, evidence_overlap, name_similarity, find_canonical (B0). |
| `test_tuning_rules.py` | 26 | Las 4 reglas A1 con fixtures. |
| `test_tuner.py` | 17 | CLI dry-run, loaders, priorización, cap, diff. |
| `test_gtm_db.py` | 12 | persist_gtm / load_gtm / has_gtm. |
| `test_gtm_agent.py` | 22 | generate_gtm, gate viabilidad, process_opportunity, run_all_pending. |
| `test_main_gtm_phase.py` | 4 | `--skip-gtm`, fallo aislado en try/except. |
| `test_telegram.py` | 4 | `send_tuner_report` truncado, no-op sin secrets. |

**Total al cerrar B2: 119 tests verdes.**

---

## 2. Schema de la base de datos

Ruta: `data/saas.db` (SQLite). Tamaño: 79 MB. Datos actuales: 19.702 posts, 12.654 comments, 10 opportunities (de las cuales 4 canónicas reales tras B0), 1 fila `opportunity_gtm`, 35 `meta_recommendations`, 10 `analysis_runs`.

### 2.1 `reddit_posts` (19.702 filas)

```sql
id             TEXT PRIMARY KEY,       -- id Reddit (t3_xxxx sin prefijo)
source         TEXT,                   -- 'subreddit_feed' | 'pain_search' | 'comment'
subreddit      TEXT,                   -- minúscula, normalizado
title          TEXT,
text           TEXT,                   -- selftext
score          INTEGER,
upvote_ratio   REAL,
num_comments   INTEGER,
created_utc    REAL,
url            TEXT,
flair          TEXT,
search_query   TEXT,                   -- la query de pain_search que lo trajo (o NULL)
clean_text     TEXT,                   -- texto NLP-limpio
category       TEXT,                   -- output de classify_post
semantic_score REAL                    -- output de _semantic_score (migrado idempotente)
```

Índices: `subreddit`, `category`, `score`, `created_utc`, `semantic_score`.

### 2.2 `reddit_comments` (12.654 filas)

```sql
comment_id   TEXT PRIMARY KEY,
post_id      TEXT,
subreddit    TEXT,
text         TEXT,
score        INTEGER,
created_utc  REAL,
clean_text   TEXT,
category     TEXT
```

Índices: `post_id`, `subreddit`.

### 2.3 `analysis_runs` (10 filas)

```sql
id                  INTEGER PRIMARY KEY AUTOINCREMENT,
run_at              TEXT NOT NULL,         -- ISO 8601
posts_analyzed      INTEGER DEFAULT 0,
valid_extractions   INTEGER DEFAULT 0,
opportunities_count INTEGER DEFAULT 0,
json_path           TEXT,                  -- data/runs/<ts>.json
status              TEXT,                  -- 'ok' | 'partial' | 'failed'
duration_sec        INTEGER,
error_message       TEXT,
ai_provider         TEXT
```

### 2.4 `opportunities` (10 filas, 4 canónicas)

```sql
id                    INTEGER PRIMARY KEY AUTOINCREMENT,
run_id                INTEGER REFERENCES analysis_runs(id),
product_name          TEXT,
niche                 TEXT,
core_problem          TEXT,
why_gap_exists        TEXT,
concrete_workaround   TEXT,
workaround_cost       TEXT,
mvp_scope             TEXT,                -- JSON: list[str]
estimated_price       TEXT,
monetization          TEXT,
competitor_gap        TEXT,
mentioned_competitors TEXT,                -- JSON: list[str]
payment_signal        TEXT,                -- 'high'|'medium'|'low'
payment_evidence      TEXT,
solo_buildable        INTEGER,             -- 0/1
mvp_weeks             INTEGER,
priority_score        INTEGER,             -- 0-10
priority_reason       TEXT,
evidence_items        TEXT,                -- JSON: list[int]
evidence_quotes       TEXT,                -- JSON: list[str] (prefijo "[item N]")
reviewed              INTEGER DEFAULT 0,
starred               INTEGER DEFAULT 0,
discarded             INTEGER DEFAULT 0,
user_notes            TEXT,
created_at            TEXT,
canonical_id          INTEGER              -- B0: id de la primera opp del cluster (autoreferencial si es la primera)
```

Índices: `priority_score DESC`, `starred`, `reviewed`, `canonical_id`.

### 2.5 `meta_recommendations` (35 filas)

```sql
id          INTEGER PRIMARY KEY AUTOINCREMENT,
run_id      INTEGER REFERENCES analysis_runs(id),
type        TEXT,                     -- 'remove_subreddit'|'boost_subreddit'|'add_subreddit'|'check_silent'|'prune_queries'|'emerging_niche'
action      TEXT,                     -- texto humano
target      TEXT,                     -- extracted via _extract_target (subreddit name o primeros 50 chars)
recurrence  INTEGER DEFAULT 1,        -- se incrementa al ver el mismo type+target en otro run
acted       INTEGER DEFAULT 0,        -- el tuner pone 1 al incluir en un PR
created_at  TEXT
```

Índices: `type`, `target`.

### 2.6 `opportunity_gtm` (1 fila)

```sql
id                     INTEGER PRIMARY KEY AUTOINCREMENT,
opportunity_id         INTEGER NOT NULL UNIQUE
                       REFERENCES opportunities(id) ON DELETE CASCADE,
viability_desperation  INTEGER,
viability_build_ease   INTEGER,
viability_scalability  INTEGER,
viability_total        INTEGER,       -- gate < 20 ⇒ skipped_low_viability
elevator_pitch         TEXT,
pricing_tiers          TEXT,           -- JSON: [{name, price, features}]
acquisition_channels   TEXT,           -- JSON: [{platform, tactic, cost_estimate}] (sin Reddit)
cold_outreach_script   TEXT,           -- ≤80 palabras
organic_post_template  TEXT,           -- ≤120 palabras
validation_plan_7d     TEXT,           -- JSON: [{day, action, success_criterion}] (7 entradas)
pivot_signals          TEXT,           -- JSON: list[str]
kpis                   TEXT,           -- JSON: {cac_target, activation_target, mrr_target_m3}
gtm_status             TEXT,           -- 'generated'|'skipped_low_viability'|'failed'
user_notes             TEXT,
created_at             TEXT
```

Índices: `opportunity_id` (UNIQUE), `gtm_status`, `viability_total`.

### 2.7 `sqlite_sequence`

Tabla automática de SQLite para autoincrement. No tocar.

---

## 3. Variables de entorno

| Variable | Obligatoria | Default | Uso |
|---|:---:|---|---|
| `REDDIT_CLIENT_ID` | Sí (scraping) | — | PRAW |
| `REDDIT_CLIENT_SECRET` | Sí (scraping) | — | PRAW |
| `REDDIT_USER_AGENT` | No | `saas-radar/1.0` | PRAW |
| `AI_PROVIDER` | No | `claude` | `claude`/`gemini`/`groq` |
| `ANTHROPIC_API_KEY` | Sí si AI=claude | — | Messages API |
| `CLAUDE_EXTRACTION_MODEL` | No | `claude-haiku-4-5-20251001` | Override fase 4a |
| `CLAUDE_SYNTHESIS_MODEL` | No | `claude-sonnet-4-6` | Override fase 4b |
| `GEMINI_API_KEY` | Sí si AI=gemini | — | Google AI Studio (gratis) |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Override modelo |
| `GROQ_API_KEY` | Sí si AI=groq | — | OpenAI-compatible API |
| `DB_URL` | No | `sqlite:///data/saas.db` | SQLAlchemy URL |
| `TELEGRAM_BOT_TOKEN` | No | — | Sin esto las alertas son no-op |
| `TELEGRAM_CHAT_ID` | No | — | Idem |
| `TELEGRAM_ALERT_THRESHOLD` | No | `8` | Score mínimo para `send_opportunity_alert` |
| `LOG_LEVEL` | No | `INFO` | Solo set en `pipeline.yml` — no consumido aún (logging estructurado aplazado) |

---

## 4. Dependencias

`requirements.txt` (sin pin de versión):

```
praw
pandas
sqlalchemy
nltk
httpx
python-dotenv
streamlit
plotly
ruff
pytest
```

`pyproject.toml`: solo configura `ruff` (line-length 120, target py310, selecciona E/F/I/B/UP, ignora E501 y E701) y `pytest` (`testpaths=tests`, `addopts="-q"`).

Falta `requests` declarado, pero `helpers/*_quota.py` lo importa — funciona porque viene transitivamente con otra dependencia.

---

## 5. CI / Despliegue

### 5.1 `.github/workflows/pipeline.yml`

- Cron `0 8 * * *` (08:00 UTC diario, ~10:00 España invierno).
- `workflow_dispatch` con input `full_scan` (boolean).
- `concurrency: reddit-saas-radar`, no cancela en progreso.
- Checkout dual: `main` (código) + `data` (rama con `data/` persistido en `./persist`).
- Setup Python 3.11, instala `requirements.txt`, descarga NLTK stopwords.
- Ejecuta `python main.py` (con `--full-scan` si el input está activo).
- Commit + push de `data/` a la rama `data` con mensaje `radar: run <ISO timestamp>`. Skip si no hay cambios.

### 5.2 `.github/workflows/tuner.yml`

- Trigger `workflow_run` sobre `pipeline.yml` (solo si `conclusion=success`) + `workflow_dispatch`.
- Checkout dual main+data.
- Secret check: falla si faltan `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID`.
- Ejecuta `python -m agents.tuner --runs-dir persist/data/runs --db-path persist/data/saas.db --lookback 10 --max-changes 5 --show-diff | tee tuner_report.txt`.
- Sube `tuner_report.txt` como artefacto (retención 30 días).
- Envía a Telegram vía `python -m notifications.telegram tuner-report tuner_report.txt`.

**Modo dry-run hasta A4** (fecha objetivo 2026-05-14): el tuner solo imprime/notifica, no abre PRs.

### 5.3 `.github/workflows/reminders.yml`

Cron 3 fechas:

| Cron | Fecha | Mensaje |
|---|---|---|
| `0 9 8 5 *` | 2026-05-08 09:00 UTC | A4 listo: verifica ≥14 reports A3 + abre `feat/tuner-a4-pr-mode`. |
| `0 9 28 5 *` | 2026-05-28 09:00 UTC | B3 listo: verifica volumen opps con GTM + abre `feat/telegram-gtm-summary`. |
| `0 9 11 6 *` | 2026-06-11 09:00 UTC | Revisar dedup v1 → v2 con embeddings si ≥3 clusters duplicados. |

`workflow_dispatch` permite disparo manual con `phase` (a4 | b3 | dedup-v2).

### 5.4 Docker (NO usado en producción)

- `Dockerfile`: `python:3.11-slim`, pre-descarga NLTK stopwords a `/usr/local/share/nltk_data`, `CMD ["python", "main.py"]`.
- `docker-compose.yml`: dos services (`pipeline` one-shot + `dashboard` Streamlit en `:8501`) sobre volumen named `radar-data` montado en `/app/data`.
- `.dockerignore` excluye `.env`, `data/`, caches, `.git`, `PLAN.md`.

---

## 6. Artefactos en `data/`

| Path | Qué es |
|---|---|
| `data/saas.db` | SQLite con las 7 tablas. **79 MB**. |
| `data/ai_analysis.json` | Output del último run (extractions + synthesis). Sobrescrito cada run. |
| `data/extractions_cache.json` | Cache de extracciones para reanudar síntesis. Guarda defensiva: si nuevo=0 válidas y viejo>0, no sobrescribe. |
| `data/extractions_cache.json.failed.json` | Estado fallido cuando la guarda defensiva impide sobrescribir. |
| `data/runs/<ts>.json` | Copia histórica del JSON de cada run. |
| `data/runs/<ts>_meta.json` | Informe de meta-análisis del run. Consumido por el tuner. |
| `data/audit_filter.md` | Output de `helpers/audit_filter.py` (auditoría offline del filtro semántico). |

---

## 7. Skills locales (`.claude/skills/`)

| Skill | Trigger |
|---|---|
| `radar-pain-signals-tune` | "ajustar PAIN_SIGNAL_PHRASES", "el scoring no detecta X". |
| `radar-pipeline-run` | "correr el radar", "ejecutar el pipeline", elegir flags. |
| `radar-prompt-iterate` | "el modelo inventa", "mejorar el prompt", iterar Groq. |
| `radar-subreddit-taxonomy` | "añadir /r/X", "quitar subreddit", tier A/B/C/D. |
| `radar-db-inspect` | Consultar/backup/purga de `data/saas.db`. |

---

## 8. Convenciones del proyecto (de `CLAUDE.md`)

- Idioma: respuestas siempre en español.
- Nombres: `snake_case` ficheros/funciones, `PascalCase` clases.
- Logging del pipeline: `print` con emojis + separadores `──`. Logging estructurado aplazado a fase L1+L2+L3 (ver `plan/backlog.md`).
- **Regla pedagógica** al modificar código: explicar qué/por qué/impacto + explicación línea a línea (objetivo: que el usuario aprenda Python/SQL/regex revisando el diff).
- Ramas: `feat/...`, `fix/...`, `chore/...`, `docs/...`. PRs vía `gh pr create` + merge.
- Cambios triviales en docs (`README.md`, `PLAN.md`, `CLAUDE.md`) pueden ir directo a `main`.
