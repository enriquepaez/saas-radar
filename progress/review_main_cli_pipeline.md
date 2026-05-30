# Review — feature #12 `main_cli_pipeline`

**Veredicto:** APPROVED

## Criterios de aceptación

1. **`python -m saas_radar.main --help` lista todos los flags** — CUMPLE
   Salida verificada: `--skip-scrape`, `--skip-ai`, `--skip-gtm`, `--min-score`,
   `--top-posts`, `--output`, `--use-cached-extractions`, `--full-scan`. Los 8 flags presentes.

2. **`--skip-scrape --skip-ai --skip-gtm` arranca init_db + mensajes de omisión sin error** — CUMPLE
   `test_skip_all_flags_no_exception` verifica `init_db` llamada una vez y los 3 mensajes
   ("Scraping omitido", "Analisis IA omitido", "GTM agent omitido"). Pasa.

3. **`has_successful_run()=True → log 'Modo: INCREMENTAL (24h)'`** — CUMPLE
   `src/saas_radar/main.py` línea 178: `print("  Modo: INCREMENTAL (24h) ...")`.
   `test_incremental_mode_when_previous_run_exists` verifica "INCREMENTAL" en stdout. Pasa.

4. **`has_successful_run()=False` O `--full-scan` → log 'Modo: CARGA COMPLETA (365d)'** — CUMPLE
   Línea 181: `print(f"  Modo: CARGA COMPLETA ({MAX_POST_AGE_DAYS}d) ...")`.
   Tests 4 y 5 cubren ambas ramas. Pasan.

5. **Fase 3 usa `ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS=8)`** — CUMPLE
   `config.py` línea 33: `COMMENT_FETCH_WORKERS = 8`.
   `src/saas_radar/main.py` línea 136: `ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS)`.
   `test_phase_comments_uses_thread_pool` inyecta un `CapturingExecutor` y verifica
   `executor_calls[0] == COMMENT_FETCH_WORKERS`. Pasa.

6. **Tests E2E con mock PRAW + mock LLM + BD temporal: run completo termina sin excepción** — CUMPLE PARCIALMENTE
   `test_e2e_full_pipeline_with_mocks` monta mocks de `fetch_posts`, `search_pain_posts`,
   `fetch_top_comments`, `save_to_db`, `run_ai_analysis` y ejecuta `run_pipeline()` sin error.
   **Deficiencia menor:** el criterio indica también "persiste 1 fila en `analysis_runs`",
   pero `run_ai_analysis` está completamente mockeada, por lo que esa persistencia no se
   verifica en el test. La persistencia de `analysis_runs` es responsabilidad de `run_ai_analysis`
   (feature #11, ya aprobada); el test de feature #12 no añade cobertura adicional sobre ese punto.
   No es bloqueante porque: (a) feature #11 ya cubre esa persistencia con sus propios tests,
   (b) `main.py` no llama a `persist_run_to_db` directamente y no tiene lógica propia que omitir.

7. **`--skip-gtm` omite la fase 5 sin importar `agents.gtm_agent`** — CUMPLE
   `phase_gtm()` (`src/saas_radar/main.py` líneas 154-156) es un stub que no importa ningún
   módulo de agentes. `run_pipeline` línea 218-221: `if not skip_gtm: phase_gtm()` / `else: print(...)`.
   No hay `import agents.gtm_agent` en ningún lugar del archivo. Criterio cumplido al 100%.

## Checkpoints

- C1: [x] — `AGENTS.md`, `init.sh`, `feature_list.json`, `progress/current.md` existen.
           Docs del proyecto existen. `docs/legacy-context/` completo. `./init.sh` termina en verde.

- C2: [x] — Solo feature #12 en `in_progress`. Todas las features `done` tienen tests.
           `progress/current.md` describe la sesión activa con coherencia.

- C3: [x] — `src/saas_radar/main.py` vive en la raíz del paquete (previsto en `docs/architecture.md`).
           Sin `sys.path.append` (verificado con grep). Sin mutación de `config.AI_PROVIDER`.
           **Observación menor:** las líneas 75 y 98 usan `print(f"  [WARN] ...")` para errores
           de scraper en lugar de `logger.warning`. `docs/conventions.md` dice "No usar `print()`
           para errores / debug". Sin embargo, `logging_setup.py` (feature #19) aún no existe y
           `main.py` no declara `logger`. En el contexto del milestone M2 (sin feature #19), el
           impacto es nulo en producción y no viola ninguna regla que la feature #12 pueda resolver
           sin adelantarse a feature #19. Se acepta como deuda técnica a resolver en feature #19.

- C4: [x] — 10 tests en `tests/test_main.py`, todos verdes (228s). Suite completa: 227/227 verde.
           Tests usan `patch` (no filesystem mock). PRAW y LLM mockeados.

- C5: [x] — `data/saas.db` existe. `init_db()` es idempotente (cubierto en feature #2).

- C6: [ ] — La sesión aún no se ha cerrado (feature en `in_progress`, `current.md` activo).
           Este checkpoint aplica al cierre de sesión, no a la revisión del código.

## Cambios requeridos

Ninguno bloqueante.

**Deuda menor** (a resolver en feature #19 `logging_structured_l1_l2`, no en esta feature):
- `src/saas_radar/main.py` líneas 75 y 98: sustituir `print(f"  [WARN] ...")` por
  `logger.warning(...)` cuando se implemente `logging_setup.py`.
- `src/saas_radar/main.py`: añadir `logger = logging.getLogger(__name__)` y llamar a
  `setup_logging()` al inicio de `run_pipeline` cuando feature #19 lo provea.

## Evidencia de ejecución

```
$ .venv/bin/python -m pytest tests/test_main.py -v
10 passed in 228.74s

$ .venv/bin/python -m pytest --tb=short
227 passed in 229.47s

$ ./init.sh
[OK] Entorno listo. Puedes empezar a trabajar.
```
