# Review feature #17: gtm_agent_b1_b2

## Estado: APROBADO

## Acceptance criteria

- [x] AC1: Tabla `opportunity_gtm` con UNIQUE(opportunity_id) + ON DELETE CASCADE — definida en `db.py` línea 123: `INTEGER NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE CASCADE`
- [x] AC2: `build_gtm_prompt` incluye hasta 5 evidence_quotes — líneas 52 (`quotes = quotes[:5]`) en `analysis/prompts/gtm.py`
- [x] AC3: Gate `viability_total < 20` → drop campos B+C, persist con `gtm_status='skipped_low_viability'` — `_process_opportunity` líneas 119-134 en `agents/gtm_agent.py`; slim_payload solo contiene scores + status
- [x] AC4: Fallo LLM (None devuelto) → `gtm_status='failed'` con scores NULL, no aborta el batch — líneas 111-115 en `agents/gtm_agent.py`; persist con `{"gtm_status": "failed"}` (scores quedan NULL por omisión)
- [x] AC5: Idempotente sin `--force`; con `--force` hace DELETE+INSERT (solo de la fila de esa opp) — líneas 93-107 en `agents/gtm_agent.py`; `has_gtm()` guarda sin force, `DELETE ... WHERE opportunity_id = :oid` con force
- [x] AC6: `phase_gtm` en main NO aborta el pipeline si el agente falla — líneas 155-169 en `main.py`; try/except captura toda excepción e imprime `[WARN]`
- [x] AC7: `--skip-gtm` omite la fase 5 sin importar `agents.gtm_agent` — import lazy en línea 158 (dentro del try de `phase_gtm`); con `skip_gtm=True` el bloque `if not skip_gtm` salta, el import nunca se ejecuta
- [x] AC8: Tests cubren los 3 estados (generated/skipped_low_viability/failed) + idempotencia + gate — `test_process_opportunity_generated`, `test_process_opportunity_skipped_low_viability`, `test_process_opportunity_failed_llm`, `test_process_opportunity_skipped_existing_without_force`, `test_process_opportunity_force_replaces`

## Convenciones

- [x] `from __future__ import annotations` en todos los módulos nuevos — presente en `gtm.py` línea 3 y `gtm_agent.py` línea 3
- [x] Sin `sys.path.append` — `grep -r "sys.path.append" src/` devuelve vacío
- [x] `logging.getLogger(__name__)` en todos los módulos nuevos/modificados — presente en `gtm.py` línea 8 y `gtm_agent.py` línea 14
- [x] Provider como argumento (no mutación global) — `run_all_pending(provider=...)` y `_generate_gtm(opp, provider)`, `call_llm(..., provider=provider)`; `main.py` lee `os.getenv("AI_PROVIDER")` y lo pasa como argumento
- [x] `print()` solo en CLI user-output — en `gtm_agent.py` solo dentro del bloque `if __name__ == "__main__"`; `main.py` usa `print` para cabeceras de fase (convención establecida)
- [x] Comillas dobles — cumplido en todos los archivos nuevos
- [x] JSON serializado como TEXT — `persist_gtm` serializa `pricing_tiers`, `acquisition_channels`, `validation_plan_7d`, `pivot_signals`, `kpis`; `load_gtm` los parsea con tolerancia a corrupción
- [x] Tests usan BD temporal vía `tmp_path` — fixture `tmp_db` en ambos archivos de test
- [x] Tests LLM no hacen llamadas reales — usan `patch("saas_radar.agents.gtm_agent.call_llm", ...)`

## Tests

- Feature tests: 37 tests (12 `test_gtm_db.py` + 21 `test_gtm_agent.py` + 4 `test_main_gtm_phase.py`), todos verdes
- Suite completo: 319 tests, todos verdes (exit code 0, 3 runs confirmados)

## Problemas encontrados

Ninguno.

## Conclusión

La implementación cumple todos los acceptance criteria. El código respeta la arquitectura (nuevos módulos en las capas correctas: `analysis/prompts/` y `agents/`), las convenciones de estilo, y no introduce regresiones. El import lazy del agente en `phase_gtm` garantiza que `--skip-gtm` no carga el módulo. Los tests son completos, usan BD temporal y mocks de LLM correctos.
