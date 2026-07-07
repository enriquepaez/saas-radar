# Review — fix/tuner-runs-alignment

**Veredicto:** APROBADO

## Alcance del diff (src/ y tests/)

Archivos tocados por el implementer:

- `src/saas_radar/main.py` — dentro de alcance.
- `src/saas_radar/agents/tuner.py` — dentro de alcance.
- `tests/test_main.py`, `tests/test_tuner.py`, `tests/test_ai_analyzer.py`, `tests/test_meta_analysis.py` — dentro de alcance.
- ⚠️ `src/saas_radar/analysis/meta_analysis.py` — **fuera de la lista acordada**, pero el cambio es
  **solo docstring** (líneas 163-164 de `_derive_meta_path`): el texto afirmaba que
  `data/ai_analysis.json/` era "caso real de producción", lo cual pasa a ser falso con este fix.
  Sin cambio funcional, cero riesgo, y evita dejar documentación mentirosa. No bloqueo por esto,
  pero queda registrado como desviación de alcance.

Los cambios en `.github/workflows/pipeline.yml` son del leader — excluidos de esta review.

## Parte A — default de output alineado con el tuner

- `main.py:205`: firma de `run_pipeline`, `output: str = "data/runs"`. ✅
- `main.py:338-339`: argparse `--output` default `"data/runs"` + help corregido (antes decía "Ruta del JSON",
  pero el valor siempre se trató como directorio). Ambos defaults eran independientes; cambiar solo uno
  habría reintroducido el bug por la vía CLI o por la programática. ✅
- Grep de `ai_analysis` en `src/`: no queda **ninguna referencia funcional** al default antiguo.
  Las únicas menciones a `data/ai_analysis.json` son el docstring de `meta_analysis.py:164` (etiquetado
  como legacy) y comentarios en tests. ✅
- Glob de fase 4.5 (`main.py:265`): `_glob.glob(os.path.join(output, "*_meta.json"))` deriva del parámetro
  `output`, así que se auto-ajusta al nuevo default. Coherente. ✅
- Los tests de caso límite "directorio con `.json` en el nombre"
  (`test_meta_json_path_matches_phase45_glob`, `test_save_meta_analysis_path_inside_dir_named_json`,
  `test_phase45_glob_finds_meta_json_in_output_dir`) se conservan con paths propios: correcto,
  esa robustez sigue vigente para directorios legacy.

## Parte B — visibilidad del fallo de `gh pr create`

- `tuner.py:627-641`: se elimina `check=True`; `capture_output=True, text=True` se mantiene.
- **Camino de éxito idéntico** (`returncode == 0`): `pr_url = result.stdout.strip()` →
  `print` → `mark_acted` (l.655) → escritura de `tuner_state.json` (l.658) →
  `_append_readme_registry` (l.661) → `return 0`. Mismo orden y efectos que antes. ✅
- **Camino de fallo** (`tuner.py:642-650`): imprime a `sys.stderr` el exit code + stdout + stderr de gh
  y `return 1` **antes** de `mark_acted`/state/README — mismos (no-)efectos persistidos que el antiguo
  raise de `CalledProcessError`, pero con el motivo real visible y exit code ≠ 0 vía `sys.exit(main())`. ✅
- `sys` ya estaba importado (tuner.py:23). ✅

## Tests

- `tests/test_main.py::test_run_pipeline_default_output_is_data_runs` (nuevo): verifica el default
  real vía `inspect.signature` — cubre la regresión que el test 1 (parser replicado a mano) no detectaría. ✅
- `tests/test_main.py::test_argparse_has_all_required_flags`: default esperado actualizado. ✅
- `tests/test_tuner.py::TestCliApply::test_apply_gh_pr_create_falla_imprime_stderr` (nuevo):
  mockea `subprocess.run`, fuerza `returncode=1` en `gh pr create` y asserta rc==1, stderr con el
  mensaje de gh + prefijo `gh pr create fallo`, ausencia de `tuner_state.json` y README sin registro.
  Verificado que la ruta del assert (`runs_dir.parent / "tuner_state.json"`) coincide con la ruta real
  que usa el código (`tuner.py:586`), así que el assert no pasa trivialmente. ✅

## Convenciones y arquitectura

- Sin `sys.path.append`, sin mutación de globales de config, imports ordenados, f-strings,
  comillas dobles, tests con `tmp_path` y mocks de subprocess (sin llamadas reales). ✅
- `ruff check` sobre los archivos tocados reporta 4 hallazgos (UP017 ×3, F841 ×1) en
  `tuner.py:171,610` y `test_tuner.py:367,384` — **todos preexistentes en main** (verificado con
  `git show main:... | ruff check`), en líneas que este diff no toca. Igual con `ruff format --check`:
  el drift de formato preexiste en main y no afecta a las líneas nuevas. No imputable al implementer.

## Verificación

- `./init.sh` con el venv en PATH: **verde**, exit 0 (incluye suite completa de pytest, todos los tests pasan).
- Suite: 441 passed, 4 skipped.

## Checkpoints aplicables

- C1 (arnés completo, init.sh verde): [x]
- C3 (arquitectura, sin anti-patrones): [x]
- C4 (verificación real, tests con tmp_path/mocks, suite verde): [x]
- C2/C5/C6: no aplican a este bugfix (no es feature de feature_list.json; el cierre de sesión
  corresponde al leader).
