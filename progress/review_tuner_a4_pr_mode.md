# Review: #20 — tuner_a4_pr_mode

## Resultado: APROBADO

## Acceptance criteria

- [x] AC1: `--apply` edita `config.py` para los cambios propuestos sin tocar formato/comentarios. Verificado: `apply_proposals` usa edición línea a línea (`_find_block_range` + `_insert_into_set` + `_remove_from_collection`). Los tests `test_preserva_comentarios_y_formato` y `test_accion_desconocida_no_modifica` lo confirman explícitamente.
- [x] AC2: PR se abre con body = `tuner_report.txt` completo + link al meta-JSON. Verificado: `tuner.py` líneas 520-526 construyen `pr_body = f"{report_text}\n\nMeta-JSON: '{meta_link}'"` y lo pasan a `gh pr create --body`.
- [x] AC3: Si ya hay PR abierto con prefijo `chore/auto-tuning-`, el agente skip + reporta en stdout. Verificado: `check_open_pr("chore/auto-tuning-")` + guard en `main()` líneas 501-503 con `print(f"[SKIP] PR ya abierto: ...")`. Test `test_apply_skip_cuando_pr_ya_abierto` confirma que config.py no se modifica y stdout contiene "SKIP".
- [x] AC4: `meta_recommendations.acted=1` tras incluir en PR; revierte a 0 si PR se cierra sin merge en el siguiente run. Verificado: `mark_acted()` + `sync_acted_status()`. Tests `test_resetea_acted_cuando_pr_cerrado_sin_merge` y `test_no_resetea_cuando_pr_merged` lo cubren.
- [x] AC5: Test E2E con repo mock + gh mockeado (`subprocess.run` patched). Verificado: `TestCliApply` con 2 tests, `monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)`.
- [x] AC6: `pytest -q` termina con exit code 0 y todos los tests verdes. Verificado: suite completa 390 tests, exit code 0 confirmado manualmente.

## Checkpoints

- [x] C1: Arnés completo. `AGENTS.md`, `init.sh`, `feature_list.json`, `progress/current.md`, `docs/architecture.md`, `docs/conventions.md`, `docs/verification.md`, `CHECKPOINTS.md`, los 4 docs legacy — todos presentes. `./init.sh` termina verde.
- [x] C2: Estado coherente. Solo una feature `in_progress` (#20). Todas las `done` tienen tests que pasan. `progress/history.md` tiene entradas históricas correctas. `progress/current.md` describe la sesión activa. Dependencias respetadas (#18 done antes de #20).
- [x] C3: Código respeta la arquitectura. Sin `sys.path.append` (grep vacío). Sin mutación de globales de `config` en runtime (import tardío solo para leer valores en `main()`). Módulos nuevos (`_find_block_range`, `apply_proposals`, `check_open_pr`, `mark_acted`, `sync_acted_status`) viven en `agents/tuner.py` — capa correcta. Todos los nuevos imports son stdlib (`re`, `subprocess`, `pathlib`, `datetime`, `dataclasses`, `collections.abc`); sin dependencias externas nuevas en `pyproject.toml`. Logging via `logger.getLogger(__name__)` presente. `print()` solo para user output del CLI (report, PR URL, SKIP, advertencias a stderr) — justificado por convención.
- [x] C4: Verificación real. 35 tests en `test_tuner.py` (14 pre-existentes + 21 nuevos). Tests de config.py usan `tmp_path` (disco temporal). Tests de subprocess usan `monkeypatch` (no llamadas reales). Tests de BD usan `_make_meta_recs_db` con BD temporal en `tmp_path`. `pytest -q` → 390 tests, exit code 0.
- [x] C5: BD heredada funciona. `data/saas.db` existe. `SELECT COUNT(*) FROM reddit_posts` → 19702. `SELECT COUNT(*) FROM opportunities` → 10. Feature #20 no añade columnas (no aplica migración).
- [x] C6: Sesión no cerrada aún (en curso). `tuner_report.txt` presente como untracked — generado durante la ejecución del workflow (artefacto esperado). No está en `.gitignore` pero tampoco es basura de sesión; se genera en tiempo de ejecución. Sin `.tmp` ni `.failed.json`. `__pycache__` cubierto por `.gitignore`.

## Observaciones

- El `print(f"[WARN]...", file=sys.stderr)` en `load_recent_runs` (línea 53) es un patrón heredado de feature #18 ya aprobado. Borderline con la convención de usar `logger.warning`, pero no es una violación nueva.
- El workflow `tuner.yml` no pasa `--config-path` en el step apply, lo que significa que usará el default `src/saas_radar/config.py`. Este es el comportamiento correcto en producción (el repo clonado en Actions tiene esa ruta).
- `tuner_report.txt` no está en `.gitignore`. No bloquea la aprobación porque es un artefacto de ejecución, no un archivo de desarrollo. Se recomienda añadirlo al `.gitignore` en una sesión futura.
