# Bitácora histórica (append-only)

> Cada vez que se cierra una sesión, su resumen se añade aquí.
> No edites entradas anteriores. Solo añades al final.

---

## Sesión 2026-05-30 — Feature #11: ai_analyzer_orchestrator

- **Feature:** #11 — Orquestador IA con cache defensivo + persistencia
- **Estado final:** APROBADO por reviewer
- **Tests:** 217 passed (8 nuevos)
- **Archivos creados:** `src/saas_radar/analysis/ai_analyzer.py`, `tests/test_ai_analyzer.py`
- **Detalles:** `progress/impl_ai_analyzer_orchestrator.md`, `progress/review_ai_analyzer_orchestrator.md`

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

---

## 2026-05-30 — Feature #7: data_loader_with_ranking

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `data_loader_with_ranking` (#7, M2_pipeline_ia).
- **Archivos creados:**
  - `src/saas_radar/analysis/data_loader.py` — `load_pain_posts(min_score, top_n, include_comments, post_age_days)` con filtros (SUBREDDITS, PAIN_CATEGORIES, score, len(text), created_utc), recálculo de `_semantic_score`, merge de comentarios como posts virtuales y ranking blend 0.10/0.15/0.75 normalizado por subreddit. Cap por subreddit: HIGH_SIGNAL→10, default→4. `load_pain_comments_as_posts()` carga comentarios >200 chars, aplica `_semantic_score`, genera pseudo-título (primera frase ≤120 chars) y mapea a forma de reddit_posts con source='comment'.
  - `tests/test_data_loader.py` — 15 tests con BD temporal (tmp_path) y monkey-patch de `saas_radar.analysis.data_loader.engine`. Cubre: filtro temporal, semántico, de score, de categoría, ranking, cap high-signal y default, comentarios como posts virtuales, pseudo-título, merge, top_n.
- **Verificación:** 156 tests (15 nuevos) → todos pasan. `ruff check` → All checks passed. Sin `print()` en capa analysis (usa `logger.*`). `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente (ciclo 2 tras fix de print→logger y orden de imports en test file).
- **Cierre:** Feature #7 marcada `done`. Desbloquea: #9 (extraction_batch_and_deep, junto con #8).

---

## 2026-05-30 — Feature #8: llm_clients_dispatcher

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `llm_clients_dispatcher` (#8, M2_pipeline_ia).
- **Archivos creados:**
  - `src/saas_radar/analysis/llm_clients.py` — `_parse_json_payload` (extrae JSON de fences markdown o JSON pelado, usando `re.sub` en lugar de `lstrip` para evitar bug de caracteres), `call_claude` (Anthropic Messages API con retry sobre `retry-after` header), `call_gemini` (Google AI Studio con retry sobre `retryDelay`), `call_groq` (API OpenAI-compatible con retry sobre texto "Please try again in Xs"), `call_llm` dispatcher (recibe `provider` como argumento explícito, nunca lee/muta `config.AI_PROVIDER`; selecciona modelo según `phase`: synthesis→CLAUDE_SYNTHESIS_MODEL, extraction→CLAUDE_EXTRACTION_MODEL).
  - `tests/test_llm_clients.py` — 22 tests con `respx` mocks. Sin llamadas reales a ninguna API.
  - `pyproject.toml` — añade `respx>=0.21` a `[project.optional-dependencies].dev`.
- **Verificación:** 22 tests nuevos (178 suite completa) → todos pasan en 0.86s. `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente. Todos los acceptance criteria verificados.
- **Cierre:** Feature #8 marcada `done`. Desbloquea: #9 (extraction_batch_and_deep, junto con #7 ✓).

---

## 2026-05-30 — Feature #6: semantic_score_filter

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `semantic_score_filter` (#6, M2_pipeline_ia).
- **Archivos creados:**
  - `src/saas_radar/analysis/pain_filter.py` — `_semantic_score(title, text) -> float`. Pre-compila `_PAIN_PATTERNS` (~120 regex con `\b` inicial desde `PAIN_SIGNAL_PHRASES`) y `_OFFTOPIC_PATTERN` (una regex alternada para `OFF_TOPIC_SIGNALS`) al nivel de módulo. Lógica: showcase (-99) → off-topic (-50) → suma señales de dolor con bonus x0.5 si la señal aparece también en el título.
  - `tests/test_pain_filter.py` — 22 tests: criterios de acceptance + cobertura exhaustiva de cada prefijo showcase y señal off-topic reales de `config.py`.
- **Verificación:** 22/22 tests verdes. `ruff check` → All checks passed (fix de import order en segunda ronda). `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente (ciclo 2 tras fix de `import pytest` sin usar + I001 import order).
- **Cierre:** Feature #6 marcada `done`. Desbloquea: #7 (data_loader_with_ranking, que también depende de #2 ✓).

---

## 2026-05-30 — Feature #9: extraction_batch_and_deep

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `extraction_batch_and_deep` (#9, M2_pipeline_ia).
- **Archivos creados:**
  - `src/saas_radar/analysis/extraction.py` — 3 prompts LLM (EXTRACTION_PROMPT, DEEP_EXTRACTION_PROMPT, EXTRACTION_BATCH_PROMPT); funciones de extracción: `extract_problem_from_post`, `_fetch_comments_for_post`, `extract_problem_deep`, `extract_problems_batch`, `run_batch_extraction` (con circuit breaker tras 3 batches fallidos consecutivos), `extract_problems` (bifurcación ≤30→deep, >30→batch); 4 funciones puras de limpieza: `_drop_who_vago`, `_drop_non_saas`, `_fix_workaround` (con inferencia desde `_WORKAROUND_KEYWORDS`), `_fix_payment_signal`; orquestadora `_clean_extractions`.
  - `tests/test_extraction.py` — 16 tests con mocks de `call_llm` y `_fetch_comments_for_post`. Sin llamadas reales a LLM ni BD.
- **Verificación:** 16/16 tests verdes. `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente (ciclo 2 tras añadir función `extract_problems` orquestadora que aplicaba la bifurcación deep/batch).
- **Cierre:** Feature #9 marcada `done`. Desbloquea: #10 (synthesis_with_validation).

---

## 2026-05-30 — Feature #12: main_cli_pipeline

- **Feature:** #12 — CLI main.py con todas las fases + detección de modo
- **Estado final:** APROBADO por reviewer
- **Tests:** 10 nuevos (227 suite completa) → todos pasan
- **Archivos creados:** `src/saas_radar/main.py`, `tests/test_main.py`
- **Detalles:** `progress/impl_main_cli_pipeline.md`, `progress/review_main_cli_pipeline.md`

---

## 2026-05-30 — Feature #13: meta_analysis_and_recommendations

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `meta_analysis_and_recommendations` (#13, M3_productizacion).
- **Archivos creados:**
  - `src/saas_radar/analysis/meta_analysis.py` — `generate_meta_analysis(extractions, opportunities, post_age_days, db_url)` con 8 claves de salida; `save_meta_analysis(meta, run_json_path, run_id, db_url)` que persiste en `data/runs/<ts>_meta.json` y llama a `persist_meta_recommendations`; `print_meta_summary(meta, db_url)` con resumen compacto + recurrentes; helpers privados `_find_empty_queries`, `_find_discovered_subreddits`, `_build_recommendations`, `_get_recurring_recommendations`. Parámetro `db_url` en todas las funciones de BD (no usa global engine). 6 tipos de recomendación: `remove_subreddit`, `boost_subreddit`, `check_silent`, `add_subreddit`, `prune_queries`, `emerging_niche`.
  - `tests/test_meta_analysis.py` — 7 tests con BD temporal (tmp_path).
- **Verificación:** 7/7 tests verdes. `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente.
- **Cierre:** Feature #13 marcada `done`. Desbloquea: #18 (tuning_rules, junto con #14 y #16).

---

## 2026-05-30 — Feature #10: synthesis_with_validation

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `synthesis_with_validation` (#10, M2_pipeline_ia).
- **Archivos creados:**
  - `src/saas_radar/analysis/synthesis.py` — `build_synthesis_prompt(extractions)` con pre-clustering por subreddit (orden count desc), separadores `### CLUSTER: r/<sub> (N items) ###`, numeración global [1..N] y prompt completo con RULES 1-7. `_validate_synthesis(results, ordered_extractions)` con check de cantidad mínima (≥2 items y ≥2 quotes) y check de coherencia léxica sobre `problem_description` real del item (no sobre `evidence_quotes` del LLM). Helpers: `_COHERENCE_STOP` (stopwords funcionales + raíces de dominio: manu, trac, spre, exce, etc.), `_SHORT_TOOL_NAMES`, `_coherence_words`, `_quotes_are_coherent`. Logger estructurado (`logging.getLogger(__name__)`); cero `print()`.
  - `tests/test_synthesis.py` — 15 tests cubriendo todos los acceptance criteria.
- **Verificación:** 209 tests (15 nuevos) → todos pasan en 0.88s. `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente (ciclo 2 tras añadir logger y convertir 7 `print()` a `logger.debug/info`).
- **Cierre:** Feature #10 marcada `done`. Desbloquea: #11 (ai_analyzer_orchestrator).
