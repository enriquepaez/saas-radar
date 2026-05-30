# Review — feature #15 dedup_jaccard_v1

**Veredicto:** APROBADO

## Checkpoints

- C1: [x] — Todos los archivos base existen. `./init.sh` termina verde.
- C2: [x] — Una sola feature `in_progress` (#15). Estado coherente.
- C3: [x] — `dedup.py` vive en `analysis/` (capa correcta). Sin `sys.path.append`. Sin mutación de globales. Imports stdlib → third-party → internos en los dos módulos `src/`. Logger vía `logging.getLogger(__name__)`. Sin `print()` sueltos en módulos de librería.
- C4: [x] — 19 tests, todos verdes. BD temporal vía `tmp_path`. Sin LLM ni PRAW reales.
- C5: [x] — `canonical_id` ya estaba en `_CREATE_TABLES`. Migración idempotente vía `PRAGMA table_info → ALTER TABLE` si falta. `init_db()` idempotente.
- C6: [ ] — La sesión aún no se ha cerrado (pendiente de este review).

## Hallazgos

### Aprobados

1. **`dedup.py`** cumple la estructura de archivo exigida: `from __future__ import annotations`, docstring de módulo, imports ordenados, `logger = logging.getLogger(__name__)`, comillas dobles, sin `print()`.

2. **`db.py`** llama realmente a `find_canonical` (línea 336) y bifurca en `canonical is not None` (línea 340) vs autoreferencia (líneas 351-365). No es self-reference en todos los casos.

3. **Criterio #2** — `test_find_canonical_two_identical_match` verifica match ≥ 0.3. `test_find_canonical_same_name_disjoint_evidence_no_match` verifica que evidencia disjunta no matchea aunque el nombre sea idéntico.

4. **Criterio #3** — `test_persist_first_opp_canonical_self` comprueba la autoreferencia. `test_persist_duplicate_across_runs_collapses` verifica que el wiring es real y que dos runs con la misma opp producen 1 canónica.

5. **Criterio #4** — `load_active_opportunities` con `WHERE id = canonical_id AND discarded = 0` es correcto.

6. **Criterio #5** — `backfill_canonical.py` sin `sys.path.append`. Los flags `--dry-run`, `--yes`, `--force` están implementados. Lógica idempotente: respeta filas con `canonical_id != NULL` salvo con `--force`. Migración inline idempotente vía PRAGMA.

7. **Criterio #6** — 19 tests exactos recogidos por pytest.

8. **Criterio #7** — Limitación documentada en `progress/impl_dedup_jaccard_v1.md` (falsos negativos por evidencia disjunta entre runs).

9. **`ruff check`** — Pasa sin errores con la configuración del proyecto (E501 excluido).

10. **Test suite completa** — 259 tests, exit code 0. Sin regresiones.

### Observaciones menores (no bloquean aprobación)

- `tests/test_dedup.py` línea 307: `import json as _json` dentro de la función `_opp()` es redundante — `json` ya está importado a nivel de módulo en la línea 21. No lo detecta ruff bajo la configuración del proyecto. No es bloqueante (los tests pasan y el código funciona), pero es código muerto dentro de la función.

- `scripts/backfill_canonical.py` líneas 108-116: la llamada a `input()` se produce dentro del bloque `with engine.begin() as conn:` (abierto en línea 57), lo que mantiene la transacción abierta durante la espera del usuario. Para un script one-shot con BD local y pocos registros esto es inofensivo, pero en BD grandes puede retener un lock durante tiempo indefinido. No bloquea la aprobación dado que es un script one-shot y no librería.

## Cambios requeridos

Ninguno.
