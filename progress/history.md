# Bitácora histórica (append-only)

> Cada vez que se cierra una sesión, su resumen se añade aquí.
> No edites entradas anteriores. Solo añades al final.

---

## 2026-05-28 — Bootstrap del proyecto saas-radar
- **Agente:** humano (Enrique) + Claude Opus 4.7 (leader).
- **Plan ejecutado:**
  1. Análisis profundo del proyecto legacy `reddit-saas-radar` (~8.800 LOC).
  2. Generación de 4 documentos en `docs/legacy-context/`: `inventory.md`,
     `architecture.md`, `lessons-learned.md`, `feature-backlog.md`.
  3. Copia del esqueleto del harness `ejemplo-harness-subagentes` a este
     repo.
  4. Adaptación de `CLAUDE.md`, `AGENTS.md`, `README.md`, `CHECKPOINTS.md`,
     `init.sh` al dominio SaaS Radar (pytest en vez de unittest, paquete
     `saas_radar`, Python 3.11+, verificación de anti-patrones del legacy).
  5. Reescritura de `docs/architecture.md`, `docs/conventions.md`,
     `docs/verification.md` para el stack real (SQLAlchemy + PRAW + LLM
     dispatcher + Telegram + GitHub Actions).
  6. Volcado de `feature_list.json` con 20 features ordenadas en 4 hitos
     (M1-M4), dependencias declaradas.
  7. Copia de `data/saas.db` heredada (79 MB) a `data/`.
- **Cambios:** árbol completo del repo (sin código de aplicación todavía).
- **Verificación:** `./init.sh` debería pasar (a comprobar al primer
  arranque): archivos base ✓, feature_list válido ✓, sin `sys.path.append`
  porque `src/` aún no tiene código.
- **Cierre:** repo listo para arrancar feature #1 (`bootstrap_package`)
  con el subagente `implementer`.

---

## 2026-05-28 — Feature #1: bootstrap_package

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `bootstrap_package` (#1, M1_foundation).
- **Archivos creados:**
  - `pyproject.toml` — paquete pip-installable con dependencias, ruff y pytest configurados.
  - `src/saas_radar/__init__.py` — expone `__version__ = "0.1.0"`.
  - `src/saas_radar/py.typed` — marcador PEP 561 para type checkers.
  - `tests/__init__.py` — paquete pytest.
  - `tests/test_import.py` — 2 tests de importabilidad.
- **Verificación:** `pip install -e .[dev]` + `pytest` → 2 passed. `./init.sh` → OK.
- **Review:** APPROVED por reviewer subagente.
- **Cierre:** feature #1 marcada `done`. Desbloquea: #2 (db_layer), #3 (config), #5 (text_cleaning), #8 (llm_clients), #14 (telegram).

---

## 2026-05-29 — Feature #2: db_layer_with_migrations

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `db_layer_with_migrations` (#2, M1_foundation).
- **Archivos creados:**
  - `src/saas_radar/storage/__init__.py`
  - `src/saas_radar/storage/db.py` — `init_db`, `save_to_db`, `load_from_db`, `db_stats`, `persist_run_to_db`, `load_active_opportunities`, `persist_meta_recommendations`, `has_successful_run`.
  - `tests/test_db.py` — 18 tests.
- **Verificación:** 18 passed en 0.36s. `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APPROVED por reviewer subagente. Deficiencia menor: no hay test ejecutable contra `data/saas.db` heredada (análisis estático concluyente, no bloqueante).
- **Cierre:** PR #2 mergeado en `main`. Feature #2 marcada `done`. Desbloquea: #3 (config), #4 (scraper), #7 (data_loader), #15 (dedup), #19 (logging).

---

## 2026-05-29 — Feature #3: config_module

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `config_module` (#3, M1_foundation).
- **Archivos creados:**
  - `src/saas_radar/config.py` — todas las constantes del legacy: scraping, AI/LLM, scoring, SUBREDDITS (36), HIGH_SIGNAL_SUBREDDITS, PAIN_SEARCH_QUERIES, PAIN_SIGNAL_PHRASES (~120 tuplas), SHOWCASE_TITLE_PREFIXES, OFF_TOPIC_SIGNALS.
  - `tests/test_config.py` — tests de env overrides, tipos y longitudes.
- **Verificación:** pytest → todos los tests pasan. `ruff check` → All checks passed. Sin `print()` ni side-effects.
- **Review:** APPROVED por reviewer subagente.
- **Cierre:** Feature #3 marcada `done`. Desbloquea: #4 (scraper), #6 (semantic_score), #8 (llm_clients).

---

## 2026-05-29 — Feature #5: text_cleaning_and_classifier

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `text_cleaning_and_classifier` (#5, M2_pipeline_ia).
- **Archivos creados:**
  - `src/saas_radar/analysis/__init__.py`
  - `src/saas_radar/analysis/text_cleaning.py` — `clean_text` (elimina URLs, puntuación, stopwords EN+ES con NLTK), `normalize_for_classifier` (versión leve: lowercase + colapsa espacios, preserva `?` y `$`).
  - `src/saas_radar/analysis/post_classifier.py` — `classify_post` con 6 categorías (showcase > pain_point > question_operational > question_technical > discussion > other), listas PAIN_KEYWORDS/SHOWCASE_KEYWORDS/EMOTIONAL_KEYWORDS/OPERATIONAL_KEYWORDS replicadas del legacy.
  - `tests/test_text_cleaning.py` — 24 tests.
  - `tests/test_post_classifier.py` — 33 tests.
- **Verificación:** 57 tests nuevos (109 suite completa) → todos pasan. `ruff check` → All checks passed. Sin `sys.path.append`, sin `print()`, regex compiladas al import.
- **Review:** APROBADO por reviewer subagente (ciclo 2 tras fix de `import pytest` sin usar).
- **Cierre:** Feature #5 marcada `done`. Desbloquea: #6 (semantic_score_filter, que también depende de #3 ✓).
## 2026-05-29 — Feature #4: scraper_reddit_basic

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `scraper_reddit_basic` (#4, M1_foundation).
- **Archivos creados:**
  - `src/saas_radar/scrapers/__init__.py`
  - `src/saas_radar/scrapers/reddit_scraper.py` — `get_reddit()` singleton, `fetch_posts()` (feeds full/incremental, dedup por id), `search_pain_posts()` (multireddit + time_filter en incremental), `fetch_top_comments()` (replace_more + filtro longitud).
  - `tests/test_reddit_scraper.py` — 10 tests con PRAW mockeado vía MagicMock. Sin llamadas reales a Reddit.
- **Verificación:** 10/10 tests verdes. `ruff format --check` + `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APPROVED por reviewer subagente (tras fix de formato en segunda ronda).
- **Cierre:** Feature #4 marcada `done`. Desbloquea: #12 (main_cli_pipeline).
