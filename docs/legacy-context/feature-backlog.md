# Backlog priorizado — saas-radar

> Lista de features para reconstruir el pipeline en `saas-radar` siguiendo el
> harness `ejemplo-harness-subagentes`. Cada entrada está lista para volcarse
> a `feature_list.json` del harness (sección §3) y al `progress/history.md`
> conforme se cierren.

---

## 1. Principios de ordenación

1. **Dependencias técnicas**: lo que arranca el resto va primero.
2. **Riesgo de bloqueo**: feature que depende de un servicio externo (Reddit, LLM, Telegram) se prueba antes que el resto.
3. **Valor incremental**: cada feature `done` debe dejar un sistema utilizable por sí solo, aunque sea sin features posteriores.
4. **Tamaño**: cada feature se cierra en una sesión humana (1-3h). Si excede, se subdivide.
5. **Acceptance verificable**: ningún `done` sin tests o smoke ejecutable.

Las features se agrupan en **5 hitos**:

- **M1 — Foundation** (#1-#4): proyecto pip-installable, BD, scraping, persistencia básica.
- **M2 — Pipeline IA mínimo** (#5-#8): pre-filtro semántico, extracción IA, síntesis, validación.
- **M3 — Productización** (#9-#12): incremental mode, comentarios paralelos, meta-análisis, Telegram.
- **M4 — Deploy + agentes** (#13-#16): CI con cron, dedup, GTM agent, tuner dry-run.
- **M5 — Operación avanzada** (#17-#20): tuner PR mode, dashboard real, observabilidad, retención.

Cuando arranca una conversación nueva, el agente debería poder coger la siguiente `pending` y entregarla sin tocar features futuras.

---

## 2. Lista priorizada

### M1 — Foundation

#### #1 — `bootstrap_package`
- **Título**: Proyecto pip-installable con dependencias declaradas.
- **Descripción**: Estructura `src/saas_radar/...` con `pyproject.toml` completo (`[project]` con name, version, python>=3.11, dependencies). Sin `sys.path.append` en ningún sitio.
- **Acceptance**:
  - `pip install -e .` desde la raíz funciona sin errores.
  - `python -c "import saas_radar"` no levanta `ModuleNotFoundError`.
  - `pyproject.toml` declara: `praw`, `pandas`, `sqlalchemy`, `nltk`, `httpx`, `python-dotenv`, `pytest`, `ruff`.
  - `pyproject.toml` configura `ruff` (line-length 120, target py311) y `pytest` (testpaths=tests).
  - `python -m pytest` ejecuta (puede tener 0 tests, pero no error).
- **Depende de**: nada.
- **Estado**: pending.

#### #2 — `db_layer_with_migrations`
- **Título**: Capa de persistencia SQLAlchemy/SQLite con migraciones idempotentes.
- **Descripción**: `src/saas_radar/storage/db.py` con `init_db()` que crea las 7 tablas del legacy + índices. Soportar el patrón de migración `PRAGMA table_info + ALTER TABLE IF NOT EXISTS column`. Helpers `save_to_db`, `load_from_db`, `db_stats`.
- **Acceptance**:
  - `init_db()` crea las 7 tablas si no existen y es idempotente (llamar 2 veces no falla).
  - Schema replica exactamente el del legacy (ver `docs/legacy-context/inventory.md` §2).
  - `save_to_db(df, table_name)` hace `INSERT OR IGNORE` vía staging y normaliza `subreddit` a minúsculas.
  - Tests cubren: init_db idempotente, save con duplicados (verifica IGNORE), load_from_db, db_stats con BD vacía y poblada.
  - Si existe `data/saas.db` del legacy en el repo, abrirlo sin error.
- **Depende de**: #1.
- **Estado**: pending.

#### #3 — `config_module`
- **Título**: Módulo de configuración con env vars + listas mutables.
- **Descripción**: `src/saas_radar/config.py` con `python-dotenv`. Replica las constantes del legacy: scraping (POST_LIMIT, etc.), AI (modelos, URLs), IA (MAX_POSTS, MIN_SEMANTIC_SCORE, etc.). Listas mutables: `SUBREDDITS`, `HIGH_SIGNAL_SUBREDDITS`, `PAIN_SEARCH_QUERIES`, `PAIN_SIGNAL_PHRASES`, `SHOWCASE_TITLE_PREFIXES`, `OFF_TOPIC_SIGNALS`.
- **Acceptance**:
  - `from saas_radar import config` carga sin error sin `.env`.
  - `config.AI_PROVIDER` lee de env, default `claude`.
  - `config.PAIN_SIGNAL_PHRASES` es lista de tuplas `(str, int)`.
  - Tests: env var override (`monkeypatch.setenv("AI_PROVIDER", "gemini")`), tipos correctos.
  - **NO** hay `print` ni efectos secundarios al importar.
- **Depende de**: #1.
- **Estado**: pending.

#### #4 — `scraper_reddit_basic`
- **Título**: Scraper Reddit con PRAW (singleton + 3 funciones).
- **Descripción**: `src/saas_radar/scrapers/reddit_scraper.py` con `get_reddit()` singleton, `fetch_posts(sub, limit, incremental)`, `search_pain_posts(query, limit, incremental)`, `fetch_top_comments(post_id, limit)`. Replica el comportamiento del legacy (modo full vs incremental).
- **Acceptance**:
  - `fetch_posts("nocode", limit=10)` devuelve `pd.DataFrame` con cols esperadas.
  - `search_pain_posts("Excel to track", limit=5)` ejecuta multireddit search sobre `SUBREDDITS`.
  - `fetch_top_comments("<some_id>", limit=10)` filtra por `len(body) >= COMMENT_MIN_LENGTH`.
  - **Tests con mocks de PRAW**: NO hits reales en CI. Smoke test manual documentado en `progress/`.
  - Modo `incremental=True` usa `subreddit.new() + .hot() + .top("day")` y `time_filter="day"` en search.
- **Depende de**: #2, #3.
- **Estado**: pending.

### M2 — Pipeline IA mínimo

#### #5 — `text_cleaning_and_classifier`
- **Título**: Limpieza NLP + clasificación de posts.
- **Descripción**: `src/saas_radar/analysis/text_cleaning.py` (`clean_text`, `normalize_for_classifier`) + `analysis/post_classifier.py` (`classify_post(title, text)` con 6 categorías).
- **Acceptance**:
  - `clean_text("HTTP://x.com  hola mundo")` devuelve string sin URL ni stopwords ni puntuación.
  - `classify_post("I built X", "...")` → "showcase".
  - `classify_post("How do you handle invoices?", "manage track")` → "question_operational".
  - NLTK stopwords se descargan en `init_db()`-equivalente o al primer uso (con cache).
  - Tests cubren las 6 categorías + edge cases (texto vacío, solo emoji).
- **Depende de**: #1.
- **Estado**: pending.

#### #6 — `semantic_score_filter`
- **Título**: Pre-filtro semántico con regex pre-compiladas.
- **Descripción**: `src/saas_radar/analysis/pain_filter.py:_semantic_score(title, text)`. Reglas: -99 si título matches `SHOWCASE_TITLE_PREFIXES`, -50 si contenido matches `OFF_TOPIC_SIGNALS`, suma de pesos por `PAIN_SIGNAL_PHRASES` (bonus x0.5 si también en título).
- **Acceptance**:
  - `_semantic_score("How I built X", "...")` → -99.
  - `_semantic_score("Real pain", "I'm burned out")` → -50.
  - `_semantic_score("invoice trouble", "I use Excel to track invoices")` ≥ 3 (al menos 1 phrase +3 con bonus título).
  - Regex se compilan UNA vez al import del módulo (medir con `pytest --durations`).
  - Tests cubren: showcase prefix, off-topic, suma de phrases, bonus título, texto vacío.
- **Depende de**: #3.
- **Estado**: pending.

#### #7 — `data_loader_with_ranking`
- **Título**: Carga + ranking de posts para IA.
- **Descripción**: `src/saas_radar/analysis/data_loader.py:load_pain_posts(min_score, top_n, include_comments, post_age_days)`. Filtros, recálculo de `_semantic_score`, merge de comentarios como posts virtuales, ranking blend 10/15/75 + cap por subreddit.
- **Acceptance**:
  - Con BD legacy importada: `load_pain_posts(min_score=5, top_n=20)` devuelve DataFrame con ≤20 filas.
  - El ranking respeta `HIGH_SIGNAL_SUBREDDITS` (cap 10) vs default (cap 4).
  - Comentarios entran como `source="comment"` con pseudo-título (primera frase ≤120 chars).
  - Tests con fixtures de BD temporal: filtro temporal, filtro semántico, ranking estable.
- **Depende de**: #2, #6.
- **Estado**: pending.

#### #8 — `llm_clients_dispatcher`
- **Título**: Clientes LLM (Claude + Gemini + Groq) con dispatcher.
- **Descripción**: `src/saas_radar/analysis/llm_clients.py` con `call_claude`, `call_gemini`, `call_groq`, `_parse_json_payload`, `call_llm(prompt, max_tokens, phase, max_retries, provider)`. **Cambio respecto al legacy**: `provider` como arg explícito, no leer de `config.AI_PROVIDER` global.
- **Acceptance**:
  - `_parse_json_payload('```json\n{"a":1}\n```')` → `{"a": 1}`.
  - `call_llm("...", provider="claude")` usa Anthropic API; `provider="gemini"` Google; `provider="groq"` Groq.
  - `phase="synthesis"` con `provider="claude"` selecciona Sonnet; `phase="extraction"` selecciona Haiku.
  - Retry con parseo de `retry-after` (Claude), `retryDelay` (Gemini), `Please try again in Xs` (Groq).
  - Tests con `httpx.MockTransport` o `respx`: 200 OK parsea JSON, 429 espera y reintenta, 5xx aborta tras N retries.
- **Depende de**: #3.
- **Estado**: pending.

### M3 — Productización

#### #9 — `extraction_batch_and_deep`
- **Título**: Extracción IA modo batch + deep + limpieza + circuit breaker.
- **Descripción**: `src/saas_radar/analysis/extraction.py` con `extract_problem_from_post`, `extract_problem_deep`, `extract_problems_batch`. `_clean_extractions` como 4 funciones puras (NO mezcladas como en el legacy). `EXTRACTION_BATCH_SIZE=5`, `DEEP_EXTRACTION_THRESHOLD=30`.
- **Acceptance**:
  - Con `len(posts) <= 30`: usa `extract_problem_deep` (1 post + comentarios BD + prompt enriquecido).
  - Con `>30`: batch de 5 posts/llamada con `TEXT_SNIPPET_LEN=500`.
  - `_clean_extractions` en 4 pasos puros: drop_who_vago, drop_non_saas, fix_workaround, fix_payment_signal.
  - Circuit breaker: tras 3 batches consecutivos con `_error`, aborta el loop.
  - Tests con mocks de `call_llm`: schema válido, batch parcial, who vago descartado, dolor físico descartado.
- **Depende de**: #7, #8.
- **Estado**: pending.

#### #10 — `synthesis_with_validation`
- **Título**: Síntesis IA con RULES 1-7 + validación post-LLM.
- **Descripción**: `src/saas_radar/analysis/synthesis.py:build_synthesis_prompt(extractions)` con pre-clustering por subreddit + RULES 1-7. `_validate_synthesis(results, ordered_extractions)` con check cantidad + check coherencia léxica (sobre `problem_description` real).
- **Acceptance**:
  - El prompt incluye separadores `### CLUSTER: r/<sub> (N items) ###`.
  - `_validate_synthesis` descarta opps con `len(evidence_items) < 2` o `len(evidence_quotes) < 2`.
  - `_coherence_words` filtra contra `_COHERENCE_STOP` rico de dominio (incluye `manu`, `trac`, `spre`).
  - Test: opp con 2 evidence_items que NO comparten 2+ raíces → descartada con `rule_violated` en `disqualified_ideas`.
  - Test: opp con 3 evidence_items que comparten raíces de dominio → kept.
- **Depende de**: #9.
- **Estado**: pending.

#### #11 — `ai_analyzer_orchestrator`
- **Título**: Orquestador IA con cache defensivo + Telegram + persist.
- **Descripción**: `src/saas_radar/analysis/ai_analyzer.py:run_ai_analysis(...)` que encadena: `load_pain_posts` → extracción (deep/batch) → `_clean_extractions` → `build_synthesis_prompt` → `call_llm` → `_validate_synthesis` → `print_results` + `save_results` + `persist_run_to_db`. Cache defensivo (`_save_extractions_cache`).
- **Acceptance**:
  - `--use-cached-extractions` salta la fase de extracción si existe `extractions_cache.json`.
  - Cache defensivo: si `valid_new=0` y `valid_old>0`, no sobrescribe, escribe `<path>.failed.json`.
  - Aborta antes de síntesis si `len(valid) < 2`.
  - Persiste `analysis_runs` con `status='ok'/'partial'/'failed'`.
  - Tests con `respx` para mockear los providers + tabla temporal SQLite.
- **Depende de**: #10, #2.
- **Estado**: pending.

#### #12 — `main_cli_pipeline`
- **Título**: CLI `main.py` con todas las fases + detección de modo.
- **Descripción**: `src/saas_radar/main.py` con argparse: `--skip-scrape`, `--skip-ai`, `--min-score`, `--top-posts`, `--output`, `--use-cached-extractions`, `--full-scan`. Fases 1-3 (scrape + pain_search + comments). Detección automática `INCREMENTAL` vs `CARGA COMPLETA` vía `has_successful_run()`. `ThreadPoolExecutor(max_workers=8)` para comentarios.
- **Acceptance**:
  - `python -m saas_radar.main --help` lista todos los flags.
  - `python -m saas_radar.main --skip-scrape --skip-ai` arranca init_db + log "Scraping omitido" + "Analisis IA omitido" sin error.
  - `has_successful_run()=True` → modo `INCREMENTAL (24h)`.
  - `has_successful_run()=False` o `--full-scan` → modo `CARGA COMPLETA (365d)`.
  - Tests E2E: mock de PRAW + mock de LLM → run completo termina con exit code 0 y persiste 1 fila en `analysis_runs`.
- **Depende de**: #4, #11.
- **Estado**: pending.

### M4 — Deploy + agentes

#### #13 — `meta_analysis_and_recommendations`
- **Título**: Meta-análisis post-run con `meta_recommendations`.
- **Descripción**: `src/saas_radar/analysis/meta_analysis.py:generate_meta_analysis(extractions, opportunities, post_age_days)`. Tabla `meta_recommendations` con `recurrence` que se incrementa si el mismo `type+target` ya existía.
- **Acceptance**:
  - Genera `data/runs/<ts>_meta.json` con subreddit_signal, silent_subreddits, empty_queries, recurring_niches, pain_categories, discovered_subreddits, recommendations.
  - Persiste recomendaciones en BD con dedup por (type, target): incrementa `recurrence`.
  - `print_meta_summary` imprime resumen compacto sin romper formato (tests snapshot).
- **Depende de**: #11.
- **Estado**: pending.

#### #14 — `telegram_notifications`
- **Título**: Notificaciones Telegram (opp alert + run summary + helpers).
- **Descripción**: `src/saas_radar/notifications/telegram.py` con `send_opportunity_alert(opp)`, `send_run_summary(...)`, `send_text(text)`, `send_tuner_report(path)`. No-op silencioso sin `TELEGRAM_BOT_TOKEN`.
- **Acceptance**:
  - Sin env vars: las funciones devuelven `False` sin error.
  - Con env vars: usa `httpx` para POST a `api.telegram.org/bot<token>/sendMessage`.
  - Trunca a 4000 chars (Telegram limit 4096).
  - Tests con `httpx.MockTransport`: payload correcto, modo Markdown, truncado funciona.
- **Depende de**: #1.
- **Estado**: pending.

#### #15 — `dedup_jaccard_v1`
- **Título**: Dedup semántico v1 con `canonical_id` (Jaccard).
- **Descripción**: `src/saas_radar/analysis/dedup.py:find_canonical(opp, existing, threshold=0.3)`. Schema: `opportunities.canonical_id`. Wiring en `persist_run_to_db` y vista `load_active_opportunities`.
- **Acceptance**:
  - Migración idempotente añade `canonical_id` si falta.
  - `find_canonical` con threshold=0.3 sobre fixtures (2 opps idénticas matchean, evidencia disjunta no matchea, etc.).
  - `persist_run_to_db` setea `canonical_id` al insertar; opp nueva sin match → autoreferencia tras INSERT.
  - `load_active_opportunities()` devuelve solo `id == canonical_id AND discarded = 0`.
  - Script `scripts/backfill_canonical.py` con `--dry-run/--yes/--force`.
- **Depende de**: #11.
- **Estado**: pending.

#### #16 — `github_actions_pipeline_workflow`
- **Título**: Workflow GitHub Actions (cron diario + rama `data`).
- **Descripción**: `.github/workflows/pipeline.yml` con cron `0 8 * * *`, `workflow_dispatch` con `full_scan`. Checkout dual main+data, restore de `persist/data/`, install deps + NLTK, run, commit push a `data`.
- **Acceptance**:
  - `gh workflow run "Reddit SaaS Radar pipeline" -f full_scan=true` arranca el job.
  - Job termina verde con BD vacía (run que no produce opps no falla).
  - Commit a `data` solo si hay cambios.
  - Verificado manualmente (no automatizable en CI): 3 runs consecutivos verdes.
- **Depende de**: #12.
- **Estado**: pending.

### M5 — Operación avanzada

#### #17 — `gtm_agent_b1_b2`
- **Título**: GTM agent (B1: tabla + CLI) + (B2: fase 5 en main).
- **Descripción**: Tabla `opportunity_gtm` (1:1 con `opportunities`). Prompt `analysis/prompts/gtm.py:build_gtm_prompt(opp)` con 3 tareas (viabilidad + GTM + plan 7d). CLI `python -m saas_radar.agents.gtm_agent --opp-id N / --all-pending --force --min-priority`. Fase 5 en `main.py:phase_gtm()` envuelta en try/except.
- **Acceptance**:
  - Gate `viability_total < 20` → drop campos B+C, persiste con `gtm_status='skipped_low_viability'`.
  - Fallo LLM → `gtm_status='failed'` con scores NULL, no aborta el batch.
  - Idempotente sin `--force`; con `--force` hace DELETE+INSERT.
  - `phase_gtm` en `main` no aborta el pipeline si el agente falla.
  - Tests cubren los 3 estados + idempotencia + gate.
- **Depende de**: #15.
- **Estado**: pending.

#### #18 — `tuning_rules_a1_a2_a3`
- **Título**: Tuner: reglas deterministas + CLI dry-run + workflow + Telegram.
- **Descripción**: `agents/tuning_rules.py` (4 reglas: promote, remove, demote, remove_query). `agents/tuner.py` CLI dry-run con `--lookback`, `--max-changes`, `--show-diff`. Workflow `tuner.yml` con `workflow_run` sobre `pipeline.yml`, sube artefacto y manda Telegram.
- **Acceptance**:
  - `propose_all_changes` devuelve `list[Proposal]` con orden conservador.
  - CLI imprime formato fijo (tests snapshot).
  - Workflow se dispara solo si pipeline=success.
  - Helpers `send_tuner_report` truncan a 3900 chars.
  - Tests: 4 reglas (A1) + CLI (A2) + Telegram (A3) con mocks.
- **Depende de**: #13, #14, #16.
- **Estado**: pending.

#### #19 — `logging_structured_l1_l2`
- **Título**: Logging estructurado (L1 setup + L2 módulos internos).
- **Descripción**: `src/saas_radar/logging_setup.py:setup_logging(level, fmt)` con JSON opcional. Migrar `print` a `logger` en módulos sin formato consumido: `storage/db.py`, `helpers/*`, `analysis/dedup.py`, `analysis/llm_clients.py`, `scrapers/*`, `notifications/*`.
- **Acceptance**:
  - `LOG_LEVEL=DEBUG LOG_FORMAT=json` produce líneas JSON parseables.
  - Default `text` produce formato humano legible.
  - El CLI del pipeline mantiene su output (cabeceras de fase) — eso es L3 (no incluido aquí).
  - Tests con `caplog`.
- **Depende de**: #2.
- **Estado**: pending.

#### #20 — `tuner_a4_pr_mode`
- **Título**: Tuner modo PR real con `--apply` + `gh pr create`.
- **Descripción**: Flag `--apply` que edita `config.py` con regex acotadas (no libcst si no es necesario). `gh pr create` desde el workflow con rama `chore/auto-tuning-YYYYMMDD`. Append automático al registro de tuning del README. Guard de PR abierto.
- **Acceptance**:
  - `--apply` edita `config.py` para los cambios propuestos sin tocar formato/comentarios.
  - PR se abre con body = `tuner_report.txt` completo.
  - Si ya hay `chore/auto-tuning-*` abierto, el agente skip + reporta.
  - `meta_recommendations.acted=1` tras incluir en PR; revierte a 0 si PR se cierra sin merge.
  - Test E2E con repo mock + `gh` mockeado.
- **Depende de**: #18.
- **Estado**: pending.

---

## 3. Formato listo para `feature_list.json`

Lista mínima inicial (M1) lista para volcar:

```json
{
  "project": "saas-radar",
  "description": "Pipeline Python que escanea subreddits buscando dolores reales y los analiza con LLM para detectar oportunidades de micro-SaaS. Reconstrucción del legacy reddit-saas-radar sobre el arnés ejemplo-harness-subagentes.",
  "rules": {
    "one_feature_at_a_time": true,
    "require_tests_to_close": true,
    "valid_status": ["pending", "in_progress", "done", "blocked"]
  },
  "features": [
    {
      "id": 1,
      "name": "bootstrap_package",
      "title": "Proyecto pip-installable con dependencias declaradas",
      "description": "Estructura src/saas_radar/ con pyproject.toml completo. Sin sys.path.append. Importable como package desde un fresh venv.",
      "acceptance": [
        "pip install -e . funciona en una venv limpia",
        "python -c 'import saas_radar' no falla",
        "pyproject.toml lista praw/pandas/sqlalchemy/nltk/httpx/python-dotenv/pytest/ruff en [project].dependencies",
        "ruff y pytest configurados via [tool.ruff] y [tool.pytest.ini_options]",
        "python -m pytest ejecuta sin error (0 tests aceptable)"
      ],
      "status": "pending"
    },
    {
      "id": 2,
      "name": "db_layer_with_migrations",
      "title": "Capa SQLAlchemy/SQLite con 7 tablas + migraciones idempotentes",
      "description": "src/saas_radar/storage/db.py replica el schema del legacy. init_db crea tablas + índices y es seguro de llamar N veces. save_to_db usa INSERT OR IGNORE vía staging.",
      "acceptance": [
        "init_db() llamado 2 veces no rompe",
        "Las 7 tablas (reddit_posts, reddit_comments, analysis_runs, opportunities, meta_recommendations, opportunity_gtm, sqlite_sequence) coinciden con docs/legacy-context/inventory.md §2",
        "Migración idempotente: si falta semantic_score o canonical_id, lo añade con ALTER TABLE",
        "save_to_db normaliza subreddit a minúsculas",
        "Tests: idempotencia, INSERT OR IGNORE, load, db_stats con BD vacía y poblada",
        "Si data/saas.db del legacy ya existe, init_db no rompe ni destruye datos"
      ],
      "status": "pending"
    }
  ]
}
```

Las features #3-#20 van en la misma estructura con los `acceptance` listados en §2. Cuando el implementer cierra una y queda `pending` la siguiente, el leader lanza el implementer otra vez sin tener que decidir qué viene después.

---

## 4. Cosas que NO entran en el backlog (decisiones explícitas)

| Item | Por qué no entra |
|---|---|
| Dashboard Streamlit real | Hosting 24/7 sin decidir. Esperar a M5 completo + decisión consciente. |
| Autenticación / multi-tenant | Uso personal. Cambiaría todo el modelo de datos. |
| i18n del output | Reddit es inglés. El usuario lee español. No hay tercera audiencia. |
| Dedup v2 con embeddings | Trigger del legacy 2026-06-11. Esperar evidencia real de FNs. |
| Migración a Postgres | Trigger A5 del tuner. Hoy no hay locks reales. |
| Análisis temporal de tendencias | Requiere ≥60 runs acumulados. Esperamos al volumen. |
| CRM de oportunidades | Bloqueado por dashboard real. |
| Cost tracking de LLM | Nice-to-have. Posterior a M5. |
| Reminders workflow | Heredar de legacy si vuelve a haber fechas. Hoy no tenemos. |
| Docker / Compose | NO se usaba en producción en el legacy. NO incluir hasta que haya host permanente. |

---

## 5. Dependencias visuales

```
M1: #1 ── #2 ── #3
     │     │     │
     │     ▼     ▼
     └─→  #4     #5 ── #6
                       │
                       ▼
M2:                   #7 ── #8
                       │     │
                       └──┬──┘
                          ▼
                         #9 ── #10
                                │
                                ▼
                               #11 ── #12
                                       │
M3:                                    │
                                       ▼
                                #13   #14   #15
                                  └────┼────┘
                                       │
M4:                                    ▼
                                      #16
                                       │
                          ┌────────────┴────────────┐
M5:                       ▼                         ▼
                         #17                       #18 ── #20
                                                    │
                                                   #19
```

`#19` (logging) puede arrancar en paralelo a `#13-#16` si hay capacidad — bloquea solo `#20`.

---

## 6. Cómo usar este backlog

1. **Para arrancar el harness `saas-radar`**: copiar el JSON de §3 a `feature_list.json` y empezar por #1.
2. **Cada feature cerrada**: añadir entrada en `progress/history.md` con fecha, agente, plan ejecutado, verificación. Cambiar status a `done`.
3. **Cada vez que se quiera arrancar la siguiente**: el implementer lee `feature_list.json`, elige el `pending` con menor `id` cuyas dependencias estén `done`, pone en `in_progress`, ejecuta.
4. **Si una feature crece más allá de 1 sesión**: documentar el bloqueo (`status: "blocked"`), abrir una sub-feature acotada, y volver.
5. **No saltar features sin justificación escrita** en `progress/history.md`. Si #5 se salta y se hace #6 antes, hay que explicar por qué (ej: bug urgente, descubrimiento de bloqueador).

---

## 7. Volumen estimado total

- **20 features**.
- **~1-3h por feature** (rango realista para sesiones humanas con IA + tests).
- **Total**: ~40-60h de trabajo guiado, repartibles en ~2-3 semanas a 2-3 features/día con foco.

Si el ritmo es 1 feature/día de trabajo no continuo: ~20 días laborables → cierre en ~4-5 semanas.
