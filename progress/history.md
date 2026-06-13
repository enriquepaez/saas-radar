# Bitácora histórica (append-only)

> Cada vez que se cierra una sesión, su resumen se añade aquí.
> No edites entradas anteriores. Solo añades al final.

---

## Sesión 2026-05-30 — Feature #19: logging_structured_l1_l2

- **Rama:** `feat/19-logging_structured_l1_l2`
- **Estado final:** APROBADO por reviewer

### Lo que se hizo

1. Creado `src/saas_radar/logging_setup.py` con `setup_logging(level, fmt)`: formato JSON (parseable) y texto humano, `stream=sys.stdout encoding='utf-8'`, idempotente.
2. Migrado `notifications/telegram.py`: 3 `print()` → `logger.warning`, añadido `logger = logging.getLogger(__name__)`.
3. Wiring en `main.py`: llama `setup_logging(LOG_LEVEL, LOG_FORMAT)` al inicio.
4. Creado `tests/test_logging_setup.py` con 8 tests (JSON, texto, niveles, idempotencia, encoding).
5. Actualizado `tests/test_telegram.py` para usar caplog.

### Resultado

Suite completa pasa (exit code 0). Reviewer aprobó todos los acceptance criteria.

---

## Sesión 2026-05-30 — Feature #16: github_actions_pipeline_workflow

- Workflow `.github/workflows/pipeline.yml` con cron diario `0 8 * * *` y `workflow_dispatch`
- Persistencia de `saas.db` con `actions/cache@v4` (reemplaza lógica de rama `data` que daba error de 50MB)
- Fix `run_ai_analysis()`: args incorrectos `top_posts→top_n`, `output→output_path`, añadido `provider=os.getenv("AI_PROVIDER")`
- Fix creación rama `data`: `git remote set-url` en lugar de `git remote add`
- Regla en `CLAUDE.md`: NUNCA commitear directamente en `main`
- Run real verificado: run ID 26683979527, success, 17m28s
- 19 tests en `tests/test_pipeline_workflow.py`

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

## 2026-05-30 — Feature #14: telegram_notifications

- **Agente:** Claude Sonnet 4.6 (leader) + implementer + reviewer.
- **Feature:** `telegram_notifications` (#14, M3_productizacion).
- **Archivos creados:**
  - `src/saas_radar/notifications/__init__.py` — paquete vacío.
  - `src/saas_radar/notifications/telegram.py` — `_get_config()`, `send_opportunity_alert(opp)` (skip si priority_score < threshold), `send_run_summary(posts_analyzed, opportunities_count, duration_sec, mode)`, `send_text(text)` (trunca a 4000 chars), `send_tuner_report(path)` (trunca cuerpo a 3900, envuelve en ``` ), `_send_message(token, chat_id, text)` (httpx POST, parse_mode=Markdown, timeout=10). No-op silencioso sin TELEGRAM_BOT_TOKEN. CLI `__main__` con subcomandos `tuner-report` y `send --text`.
  - `tests/test_telegram.py` — 10 tests con monkeypatch de `_send_message` y respx para verificar payload HTTP.
- **Verificación:** 10/10 tests verdes (ciclo 2 tras añadir 3 tests pedidos por reviewer: `send_opportunity_alert` no-op/skip-score y payload Markdown). Suite completa → exit 0. `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente (ciclo 2).
- **Cierre:** Feature #14 marcada `done`. Desbloquea: #18 (tuning_rules, junto con #13 ✓ y #16).

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

---

## Sesión 2026-05-30 — Feature #15: dedup_jaccard_v1

- **Rama:** `feat/15-dedup_jaccard_v1`
- **Archivos creados/modificados:**
  - `src/saas_radar/analysis/dedup.py` — algoritmo Jaccard sobre evidence_quotes (funciones: `find_canonical`, `evidence_overlap`, `name_similarity`, helpers privados).
  - `src/saas_radar/storage/db.py` — wiring de `find_canonical` en `persist_run_to_db`: carga opps existentes antes del loop, asigna canonical_id real o autoreferencia.
  - `scripts/backfill_canonical.py` — script one-shot idempotente con `--dry-run/--yes/--force/--threshold`.
  - `tests/test_dedup.py` — 19 tests portados del legacy.
- **Verificación:** 259 tests totales → todos pasan. `ruff check` → All checks passed.
- **Review:** APROBADO por reviewer subagente (sin cambios requeridos).
- **Cierre:** Feature #15 marcada `done`. Desbloquea: #17 (gtm_agent_b1_b2).

---

## Sesión 2026-05-30 — Feature #17: gtm_agent_b1_b2

- **Rama:** `feat/17-gtm_agent_b1_b2`
- **Archivos creados:**
  - `src/saas_radar/analysis/prompts/__init__.py` — paquete vacío.
  - `src/saas_radar/analysis/prompts/gtm.py` — `build_gtm_prompt(opp)` con 3 tareas: viabilidad (desperation/build_ease/scalability), GTM (elevator_pitch, pricing_tiers, acquisition_channels, cold_outreach_script, organic_post_template), plan 7 días (validation_plan_7d, pivot_signals, kpis). Incluye hasta 5 evidence_quotes de la opp.
  - `src/saas_radar/agents/__init__.py` — paquete vacío.
  - `src/saas_radar/agents/gtm_agent.py` — `_generate_gtm` (llama LLM, valida schema, calcula viability_total), `_process_opportunity` (gate viability<20, estados: generated/skipped_low_viability/failed, idempotencia con --force), `run_all_pending` (filtra por priority_score >= min_priority). CLI con `--opp-id`, `--all-pending`, `--force`, `--min-priority`, `--provider`, `--db-url`.
  - `tests/test_gtm_db.py` — 12 tests para persist_gtm/load_gtm/has_gtm.
  - `tests/test_gtm_agent.py` — 21 tests para _generate_gtm, _process_opportunity, run_all_pending.
  - `tests/test_main_gtm_phase.py` — 4 tests para phase_gtm en main.py.
- **Archivos modificados:**
  - `src/saas_radar/storage/db.py` — añadidas `persist_gtm`, `load_gtm`, `has_gtm` al final (serialización JSON automática de 5 campos, parseo tolerante a corrupción).
  - `src/saas_radar/main.py` — `phase_gtm()` reemplazada por implementación real con import lazy de `run_all_pending` dentro de try/except aislado.
- **Verificación:** 37 tests nuevos, 319 totales → todos pasan. `ruff check` → All checks passed. `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente. Los 8 acceptance criteria verificados.
- **Cierre:** Feature #17 marcada `done`. Desbloquea: #18 (tuning_rules_a1_a2_a3).

---

## Sesión 2026-05-30 — Feature #18: tuning_rules_a1_a2_a3

- **Rama:** `feat/18-tuning_rules_a1_a2_a3`
- **Archivos creados:**
  - `src/saas_radar/agents/tuning_rules.py` — 4 reglas deterministas: `propose_promote_to_high_signal`, `propose_remove_from_subreddits`, `propose_demote_from_high_signal`, `propose_remove_queries`. Orquestadora `propose_all_changes` con orden conservador (remove_query > demote > remove_subreddit > add_high_signal). Dataclass `Proposal`. Helpers: `_aggregate_subreddit_stats`, `_count_consecutive_silent`, `_count_consecutive_empty_query`.
  - `src/saas_radar/agents/tuner.py` — CLI dry-run: `load_recent_runs` (carga meta-JSONs con tolerancia a corruptos), `load_meta_recommendations` (sqlite3 directo), `prioritize_and_cap` (orden conservador + recurrence desc + cap), `render_report` (formato fijo con timestamp UTC), `render_config_diff` (pseudo-Python para preview). `main()` con import lazy de `from saas_radar import config`.
  - `.github/workflows/tuner.yml` — trigger `workflow_run` sobre `saas-radar pipeline` (solo si conclusion=success) + `workflow_dispatch`. Checkout dual main+data, `pip install -e .[dev]`, NLTK download, ejecución del tuner, artefacto 30 días, Telegram.
  - `tests/test_tuning_rules.py` — 26 tests portados del legacy.
  - `tests/test_tuner.py` — 18 tests (17 del legacy + snapshot del formato del report).
  - `tests/fixtures/tuner_report_expected.txt` — fixture de snapshot del render_report.
- **Verificación:** 54 tests nuevos, suite completa → todos pasan (0 fallos, 0 regresiones). `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente.
- **Cierre:** Feature #18 marcada `done`. Desbloquea: #20 (tuner_a4_pr_mode) y #21 (llm_heuristic_tuner, junto con #13 ✓ y #8 ✓).

---

## Sesión 2026-05-30 — Feature #20: tuner_a4_pr_mode

- **Feature:** #20 `tuner_a4_pr_mode` — Tuner modo PR real con `--apply` + `gh pr create`
- **Agente:** implementer + reviewer
- **Archivos modificados:**
  - `src/saas_radar/agents/tuner.py` — añadidas 7 funciones nuevas: `apply_proposals`, `_find_block_range`, `_insert_into_set`, `_remove_from_collection`, `check_open_pr`, `mark_acted`, `sync_acted_status`, `_append_readme_registry`. Flag `--apply` + flujo completo en `main()`.
  - `tests/test_tuner.py` — 14 → 35 tests (4 clases nuevas: TestApplyProposals, TestCheckOpenPr, TestMarkActed, TestCliApply).
  - `.github/workflows/tuner.yml` — permisos `contents:write` + `pull-requests:write` + step `Run tuner (apply PR)`.
- **Verificación:** suite completa (35 tests en test_tuner.py, todos pasan; suite global exit code 0). `./init.sh` → OK.
- **Review:** APROBADO por reviewer subagente.
- **Cierre:** Feature #20 marcada `done`. Desbloquea: #21 (llm_heuristic_tuner, todas las deps ✓).

---

## Sesión 2026-05-30 — Feature #21: llm_heuristic_tuner

- **Rama:** `feat/21-llm_heuristic_tuner`
- **Archivos creados:**
  - `src/saas_radar/agents/heuristic_tuner.py` — `generate_heuristic_suggestions(meta_json_path, top_posts_df, provider)` con prompt que incluye nichos recurrentes (recurrence≥2), top posts (title+snippet+subreddit), subreddits descubiertos y queries vacías. Llama a `call_llm(phase='synthesis')`. Valida schema JSON con `_parse_json_payload`; schema inválido → log WARNING + dict vacío. Dedup contra config (PAIN_SEARCH_QUERIES, SUBREDDITS, PAIN_SIGNAL_PHRASES). `persist_heuristic_suggestions(suggestions, db_path)` via sqlite3 con upsert (INSERT recurrence=1 o UPDATE recurrence+1). CLI `__main__` con `--meta-json`, `--provider`, `--dry-run`.
  - `tests/test_heuristic_tuner.py` — 6 tests cubriendo todos los acceptance criteria.
- **Archivos modificados:**
  - `src/saas_radar/agents/tuning_rules.py` — reglas A5/A6/A7 en `propose_all_changes`: proponen `add_query`, `add_subreddit`, `add_phrase` cuando meta_recommendations tiene `type in {query_suggestion, subreddit_suggestion, phrase_suggestion}` y `recurrence >= 2` y `acted = 0`.
  - `src/saas_radar/agents/tuner.py` — soporte para los nuevos tipos en el renderizado CLI del report.
  - `src/saas_radar/main.py` — `phase_heuristic_tuner()` (import lazy, try/except) como fase 4.5, llamada desde `run_pipeline()` tras `generate_meta_analysis`.
  - `tests/fixtures/tuner_report_expected.txt` — snapshot actualizado con nuevos tipos de propuesta.
- **Verificación:** suite completa → exit code 0. Reviewer aprobó todos los acceptance criteria.
- **Cierre:** Feature #21 marcada `done`. Milestone M4_operacion_avanzada completado. Todas las features del proyecto done.

---

## Sesión 2026-06-12 — Feature #22 `pipeline_persistence_restoration`

- **Contexto:** auditoría del estado del scraping post-MVP. Diagnosticadas dos regresiones críticas:
  1. Commit `8409bb9 fix(#16)` quitó el push a la rama `data` y lo reemplazó por `actions/cache`. Como `tuner.yml` sigue leyendo de `data`, el tuner operaba contra una BD congelada del 30-may; los `meta_recommendations` no se actualizaban; A4 no abría PRs.
  2. 2 runs failed del 30-may con Gemini ('0 extracciones válidas') por respuesta sin clave `results`. Logging insuficiente.
- **Subagentes:** 2 Explore en paralelo (audit_gemini_fail, audit_cron_state) + 1 implementer + 1 reviewer.
- **Cambios:**
  - `.github/workflows/pipeline.yml` — dual checkout main+data, `permissions.contents: write`, step "Persist to data branch" con copia + commit/push condicional, mantiene `actions/cache` + `upload-artifact`.
  - `feature_list.json` — añadido milestone `M5_post_mvp_refinement` con 4 features nuevas (#22-25). F22 cerrada `done`; F23 (`extraction_gemini_hardening`), F24 (`signal_tuning_apply_findings`), F25 (`dedup_v2_embeddings`) quedan `pending`.
  - `progress/audit_gemini_fail.md` — diagnóstico fail Gemini.
  - `progress/audit_cron_state.md` — diagnóstico cron + regresión 8409bb9.
  - `progress/impl_pipeline_persistence_restoration.md` — implementación + sync local (`git fetch origin data && git checkout origin/data -- data/saas.db data/runs/`).
  - `progress/review_pipeline_persistence_restoration.md` — APPROVED.
- **Verificación:** YAML válido (`yaml.safe_load`). No hay tests automatizados para workflows; verificación final en runtime tras mergear y observar `origin/data`.
- **Cierre:** F22 marcada `done`. F23-25 quedan en backlog para próximas sesiones.

---

## Sesión 2026-06-13 — Feature #24 `signal_tuning_apply_findings`

- **Rama:** `feat/24-signal_tuning_apply_findings`
- **Subagentes:** implementer + reviewer
- **Archivos modificados:**
  - `src/saas_radar/config.py` — `MIN_SEMANTIC_SCORE` 1.5→1.0, `POSTS_CAP_HIGH_SIGNAL` 10→15, `indiehackers` añadido a `HIGH_SIGNAL_SUBREDDITS`, 31 queries muertas (yield=0 en 60d, consultado sobre `data/saas.db`) eliminadas de `PAIN_SEARCH_QUERIES`, 4 frases nuevas añadidas a `PAIN_SIGNAL_PHRASES` (`pdf to csv`, `spending too much time`, `converting bank statement`, `manage inventory in shopify`; `drowning in spreadsheets` ya existía).
  - `tests/test_config.py` — ajustado el assert de longitud de `PAIN_SEARCH_QUERIES` (95→64).
- **Verificación:** 73/73 tests verdes (test_config, test_pain_filter, test_data_loader). Reviewer aprobó todos los acceptance criteria.
- **Cierre:** F24 marcada `done`. Desbloquea: #25 `dedup_v2_embeddings`.

---

## Sesión 2026-06-12 — Feature #23 `extraction_gemini_hardening`

- **Rama:** `feat/23-extraction_gemini_hardening`
- **Subagentes:** implementer + reviewer
- **Archivos modificados:**
  - `src/saas_radar/analysis/llm_clients.py` — `call_gemini` valida shape del envelope antes de devolver al caller; si `candidates[0].content.parts[0].text` no existe, loguea WARNING con `body[:500]` truncado y devuelve None.
  - `src/saas_radar/analysis/extraction.py` — `extract_problems_batch` loguea WARNING cuando `call_llm` devuelve None o cuando `results` no está en el resultado. `run_batch_extraction` implementa fallback a groq: cuando el circuit breaker dispara con `provider != fallback`, reintenta todos los batches una sola vez con `EXTRACTION_PROVIDER_FALLBACK`.
  - `src/saas_radar/config.py` — nueva variable `EXTRACTION_PROVIDER_FALLBACK` (default `"groq"`, sobreescribible por env var).
  - `tests/test_llm_clients.py` — test caplog: mock 200 OK con shape inesperada → WARNING + None.
  - `tests/test_extraction.py` — test fallback: mock `call_llm` devuelve None para gemini, dict válido para groq → fallback activa, `valid_extractions > 0`.
- **Verificación:** suite completa (422 tests, exit code 0). Reviewer aprobó todos los acceptance criteria A1-A7.
- **Cierre:** F23 marcada `done`. Desbloquea: #24 `signal_tuning_apply_findings`.

---

## Sesión 2026-06-13 — Feature #25 dedup_v2_embeddings

**Rama:** `feat/25-dedup_v2_embeddings`  
**Estado final:** DONE (reviewer aprobó)

### Cambios

- `src/saas_radar/analysis/dedup.py`: añadido `find_canonical_v2` con sentence-transformers (lazy singleton `_get_st_model`), `_cosine` puro en Python, falla limpia con `RuntimeError` si el paquete no está instalado.
- `src/saas_radar/config.py`: añadida constante `ENABLE_DEDUP_V2` (default '0').
- `src/saas_radar/storage/db.py`: bifurcación en `persist_run_to_db` — usa v2 si `ENABLE_DEDUP_V2=='1'`, v1 en caso contrario.
- `pyproject.toml`: dependencia opcional `[dedup-v2] = ["sentence-transformers>=2.7"]`.
- `scripts/backfill_canonical_v2.py`: script standalone con `--dry-run/--yes/--force`.
- `tests/test_dedup.py`: 5 tests nuevos de v2 (4 con `pytest.importorskip` para CI sin el modelo).

### Verificación

363 passed, 4 skipped (sentence-transformers no instalado en CI). Todos los tests de v1 intactos.
