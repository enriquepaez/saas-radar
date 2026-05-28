# Arquitectura — Qué significa "hacer un buen trabajo"

> Este documento define el estándar de calidad. Los agentes revisores
> evalúan código contra este archivo. Si no está aquí, no es un requisito.

## Principios

1. **Capas claras.** El proyecto tiene 6 capas y solo 6. Cada módulo vive en
   una sola y NO mezcla responsabilidades:
   - `src/saas_radar/storage/` — persistencia (SQLAlchemy + SQLite).
   - `src/saas_radar/scrapers/` — clientes externos (PRAW).
   - `src/saas_radar/analysis/` — pipeline IA (cleaning, classifier, scoring,
     extracción, síntesis, dedup, meta-análisis, llm_clients).
   - `src/saas_radar/agents/` — agentes autónomos (tuner, gtm_agent) con
     decisiones que afectan a config o a opps.
   - `src/saas_radar/helpers/` — utilities sin dominio (quota checks,
     auditoría).
   - `src/saas_radar/notifications/` — outputs externos (Telegram).

   No introducir capas adicionales sin una razón documentada en
   `feature_list.json` y aprobada por revisor.

2. **Paquete pip-installable.** El proyecto se instala con `pip install -e .`
   desde un venv limpio. `pyproject.toml` declara TODAS las dependencias no
   stdlib. **No hay `sys.path.append`** en ningún sitio (anti-patrón del
   legacy, ver `legacy-context/lessons-learned.md` §2.4).

3. **Configuración por argumento, no por mutación global.** Los módulos NO
   leen ni mutan `saas_radar.config.AI_PROVIDER` (u otros) en runtime. Si
   una función necesita el provider, lo recibe como argumento explícito o
   se inyecta vía constructor. Anti-patrón del legacy §2.5.

4. **Migraciones idempotentes.** `init_db()` puede llamarse N veces sin
   romper. Para añadir columnas: patrón `PRAGMA table_info → ALTER TABLE`
   con guarda `if column not in existing`. No usar Alembic (overkill en
   este proyecto).

5. **Errores explícitos en bordes del sistema.** Las funciones de capa
   externa (LLM clients, PRAW scrapers) devuelven `None` (o `{}`) en fallo
   y loguean — no propagan excepciones de red al pipeline. Las funciones
   internas (analysis, storage) lanzan excepciones nombradas.

6. **Validación en código de cualquier salida del LLM.** Si el LLM debe
   cumplir una regla (cantidad mínima, formato, vocabulario), siempre hay
   un validador Python que verifica esa regla post-respuesta. Lección
   `legacy-context/lessons-learned.md` §1.1.

7. **Cache defensivo en pipelines costosos.** Si un cache se sobrescribe
   en mitad de un pipeline con LLM, debe llevar guarda: nunca destruir
   datos "buenos" con datos "vacíos" nuevos. Persistir el estado fallido
   en `<path>.failed.json` para inspección. Lección §1.2.

8. **Circuit breaker en loops de llamadas externas.** Cualquier loop que
   llame a un servicio remoto (LLM, PRAW) lleva contador de fallos
   consecutivos. Tras N (default 3), abortar. Lección §1.3.

9. **Logging estructurado desde el día 1.** Cada módulo declara su logger
   con `logging.getLogger(__name__)`. El CLI principal hace `setup_logging`
   antes de cualquier llamada. El user output del CLI (cabeceras de fase
   visibles al humano) va a `print`; los logs de debug/info a `logger`.
   No se mezclan los dos en el mismo módulo sin justificación.

## Estructura objetivo del paquete

```
src/saas_radar/
├── __init__.py
├── config.py               # env vars + listas mutables (SUBREDDITS, PAIN_SIGNAL_PHRASES, …)
├── logging_setup.py        # setup_logging(level, fmt) — feature #19
├── main.py                 # CLI orquestador (feature #12)
├── storage/
│   ├── __init__.py
│   └── db.py               # init_db, save_to_db, persist_run_to_db, … (feature #2)
├── scrapers/
│   ├── __init__.py
│   └── reddit_scraper.py   # fetch_posts, search_pain_posts, fetch_top_comments (feature #4)
├── analysis/
│   ├── __init__.py
│   ├── text_cleaning.py    # clean_text, normalize_for_classifier (feature #5)
│   ├── post_classifier.py  # classify_post (feature #5)
│   ├── pain_filter.py      # _semantic_score (feature #6)
│   ├── data_loader.py      # load_pain_posts (feature #7)
│   ├── llm_clients.py      # call_claude/gemini/groq, call_llm dispatcher (feature #8)
│   ├── extraction.py       # extract_problem_*, _clean_extractions (feature #9)
│   ├── synthesis.py        # build_synthesis_prompt, _validate_synthesis (feature #10)
│   ├── ai_analyzer.py      # run_ai_analysis orquestador (feature #11)
│   ├── meta_analysis.py    # generate_meta_analysis (feature #13)
│   ├── dedup.py            # find_canonical Jaccard v1 (feature #15)
│   └── prompts/
│       └── gtm.py          # build_gtm_prompt (feature #17)
├── agents/
│   ├── __init__.py
│   ├── tuning_rules.py     # 4 reglas deterministas (feature #18)
│   ├── tuner.py            # CLI dry-run (feature #18) + apply (feature #20)
│   └── gtm_agent.py        # CLI + run_all_pending (feature #17)
├── helpers/
│   ├── __init__.py
│   ├── groq_quota.py       # check_quota (heredable del legacy)
│   ├── gemini_quota.py     # check_quota
│   └── audit_filter.py     # audit offline del filtro semántico
└── notifications/
    ├── __init__.py
    └── telegram.py         # send_opportunity_alert, send_run_summary, send_tuner_report (feature #14)
```

`scripts/backfill_canonical.py` vive en `scripts/` a nivel raíz, no en el
paquete (es one-shot, no librería).

## Flujo de datos del pipeline (objetivo, post-#12)

Idéntico al del legacy. Detalle completo en
`docs/legacy-context/architecture.md` §4. Resumen:

```
Reddit (PRAW)
    ↓ fase 1 (subreddits) + fase 2 (pain_search) + fase 3 (comments)
enrich_posts → save_to_db
    ↓
data/saas.db (reddit_posts, reddit_comments)
    ↓ load_pain_posts: filtros + ranking + merge comentarios virtuales
    ↓
extract_problem_{batch,deep} (fase 4a)
    ↓ _clean_extractions
build_synthesis_prompt → call_llm → _validate_synthesis (fase 4b)
    ↓
persist_run_to_db + find_canonical (dedup)
    ↓
opportunities + opportunity_gtm (vía phase_gtm)
    ↓
send_opportunity_alert (Telegram) + meta_analysis + tuner
```

## Schema de la BD

Replicar exactamente el del legacy (7 tablas). Detalle en
`docs/legacy-context/inventory.md` §2.

**Cambio respecto al legacy** (decisión documentada): considerar separar
flags humanos (`reviewed`, `starred`, `discarded`, `user_notes`) de
`opportunities` a una tabla `opportunity_state` 1:1. Lección legacy §2.7.
Esta decisión la TOMA la feature de `db_layer` cuando se implemente — NO
se ejecuta hasta entonces.

## Qué NO hacer

- No usar `print()` dispersos para errores o debug. Para errores: `logger.error`
  o `sys.stderr` + exit code != 0 en el CLI.
- No mezclar IO con lógica pura: las funciones de `analysis/` que no son
  orquestadoras (text_cleaning, pain_filter, dedup, etc.) NO tocan disco
  ni red.
- No leer/escribir SQLite en cada operación dentro de un bucle. Carga al
  inicio, modifica en memoria, persiste al final del bloque.
- No introducir un framework de DI / un ORM más allá del SQLAlchemy básico
  que ya usa el legacy.
- No replicar las 17 líneas de `dashboard/app.py` del legacy. Si se va a
  hacer dashboard, debe ser una feature explícita con acceptance claro
  y hosting decidido.
- No traer `Docker`/`docker-compose` del legacy. **NO se usaban en
  producción**, solo testing local. Si surge la necesidad real, hacerlo
  como feature explícita con acceptance.
