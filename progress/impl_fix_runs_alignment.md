# Implementación: fix — alineación de runs del tuner + visibilidad de errores de gh

Rama: `fix/tuner-runs-alignment`. Bugfix con dos partes independientes: (A) el
pipeline escribía sus meta-JSONs en un directorio que el tuner no lee, por lo
que el auto-tuning corría siempre con `runs analizados: 0`; (B) cuando
`gh pr create` fallaba en CI, el motivo real quedaba invisible.

## Qué cambió

- **`src/saas_radar/main.py`** (modificado):
  - Default del parámetro `output` de `run_pipeline`: `"data/ai_analysis.json"` → `"data/runs"`.
  - Default del flag CLI `--output`: `"data/ai_analysis.json"` → `"data/runs"`, y help text actualizado de "Ruta del JSON de resultados" a "Directorio de salida de los JSON de resultados y meta" (el valor siempre se trató como directorio, el help mentía).
- **`src/saas_radar/agents/tuner.py`** (modificado): en `main()`, la llamada `subprocess.run(["gh", "pr", "create", ...])` ya no usa `check=True`. Ahora se inspecciona `result.returncode`: si es distinto de 0, se imprime a stderr el motivo (`stdout` + `stderr` de gh) y `main()` devuelve 1.
- **`src/saas_radar/analysis/meta_analysis.py`** (modificado, solo docstring): el docstring de `_derive_meta_path` decía que `data/ai_analysis.json/...` era el "caso real de producción"; tras este fix es un caso legacy. Se reformula para que siga explicando por qué existe la lógica sin afirmar algo ya falso.
- **`tests/test_main.py`** (modificado): default esperado de `--output` actualizado a `"data/runs"` (2 líneas) y test nuevo `test_run_pipeline_default_output_is_data_runs`.
- **`tests/test_tuner.py`** (modificado): test nuevo `test_apply_gh_pr_create_falla_imprime_stderr` en `TestCliApply`.
- **`tests/test_ai_analyzer.py`** y **`tests/test_meta_analysis.py`** (modificados, solo comentarios): los fixtures `tmp_path / "ai_analysis.json"` tenían comentarios que decían "nombre real de producción"; ahora dicen "nombre legacy con '.json' (caso límite que debe seguir funcionando)". Los tests en sí no cambian: siguen siendo válidos porque `_save_results` y `_derive_meta_path` deben seguir tolerando directorios con `.json` en el nombre.

## Por qué

**Parte A — el bug de los runs.** `run_ai_analysis` (ai_analyzer.py) trata su
`output_path` como un **directorio**: `_save_results` hace
`Path(output_path).mkdir(parents=True, exist_ok=True)` y escribe dentro
`<ts>_results.json` y `<ts>_meta.json`. Pero `main.py` le pasaba
`"data/ai_analysis.json"`, un nombre que parece archivo pero que acababa
creando un directorio literal `data/ai_analysis.json/`. El tuner, en cambio,
lee `--runs-dir data/runs` (que además ya es el default propio de
`run_ai_analysis`, `output_path: str = "data/runs/"` — la incoherencia estaba
solo en el valor que main.py inyectaba). Nadie escribía en `data/runs/`, así
que el workflow "saas-radar tuner" siempre reportaba 0 runs y nunca proponía
nada. La solución mínima es unificar el default de main.py con el directorio
que el tuner consume. No hace falta tocar el glob de la fase 4.5
(`main.py:265`): busca `*_meta.json` dentro de `output`, sea cual sea su
valor, así que sigue funcionando.

Alternativa descartada: cambiar el tuner para que lea `data/ai_analysis.json/`.
Peor opción porque (1) es un nombre engañoso (un directorio con extensión
`.json`), (2) `data/runs` ya era el default documentado en `run_ai_analysis` y
en el docstring del tuner, y (3) el workflow de CI ya pasa `--runs-dir data/runs`.

**Parte B — errores de gh invisibles.** Con `capture_output=True` +
`check=True`, un fallo de `gh pr create` lanza `CalledProcessError` cuyo
mensaje solo incluye el comando y el exit code — el stderr capturado existe en
la excepción pero nadie lo imprimía, así que el log de CI mostraba un
traceback sin el motivo real (p.ej. límites de rate de GitHub, permisos del
token, base branch inexistente). Capturar sin `check` y volcar
stdout+stderr a mano hace el fallo diagnosticable.

## Impacto en el pipeline

- **Análisis IA (fase 4)**: los JSON de resultados y meta pasan a escribirse en `data/runs/` por defecto. Mismo contenido, misma estructura de nombres (`<ts>_results.json`, `<ts>_meta.json`), solo cambia el directorio.
- **Fase 4.5 (heuristic tuner in-pipeline)**: sin cambio funcional; su glob usa la variable `output`, que ahora apunta a `data/runs`.
- **Tuner CI (workflow "saas-radar tuner")**: pasa de ver 0 runs a ver los meta-JSONs reales → las reglas deterministas (remove_query, add_high_signal…) podrán activarse por fin. Además, si `gh pr create` falla, el log de CI mostrará el motivo y el job terminará con exit 1 (fallo visible) en lugar de un traceback opaco.
- **Estado tras fallo de PR**: al devolver 1 antes de `mark_acted`, de escribir `tuner_state.json` y del append al README, un fallo de gh no deja recomendaciones marcadas como `acted` ni estado fantasma — el siguiente run reintentará desde limpio. (El commit+push de la rama `chore/auto-tuning-*` sí habrá ocurrido; el guard `check_open_pr` no lo detecta porque no hay PR, así que el retry del día siguiente usará otra rama con otra fecha. Comportamiento aceptable y sin cambio respecto a antes.)
- **BD, scraping, Telegram, GTM**: sin impacto.
- **Migración**: quien tenga un `data/ai_analysis.json/` local de runs previos simplemente dejará de alimentarlo; los archivos antiguos no se mueven (fuera de scope, y el tuner de CI trabaja sobre el artefacto/cache de `data/runs`).

## Explicación técnica

### `main.py` — `run_pipeline(output: str = "data/runs", ...)`

`output` viaja intacto hasta `run_ai_analysis(output_path=output)`. Dentro,
`_save_results` hace `out_dir = Path(output_path)` y `out_dir.mkdir(parents=True,
exist_ok=True)`: `Path()` convierte el string en objeto ruta sin tocar disco;
`mkdir(parents=True, exist_ok=True)` crea el directorio (y `data/` si faltara)
sin fallar si ya existe. Después construye `out_dir / f"{ts}_results.json"` —
el operador `/` de `pathlib` concatena componentes de ruta. Por eso el valor
correcto aquí es un directorio "de verdad" (`data/runs`) y no un nombre con
extensión.

### `main.py` — argparse `--output`

`parser.add_argument("--output", type=str, default="data/runs", help=...)`:
`default` es el valor cuando el flag no se pasa; tenía que cambiar en los dos
sitios (firma de `run_pipeline` y argparse) porque son defaults independientes
— el de la firma aplica a llamadas programáticas (tests, imports), el de
argparse al CLI. Dejar uno sin cambiar habría reintroducido el bug por una de
las dos vías.

### `main.py:265` — por qué el glob no cambia

```python
meta_files = sorted(_glob.glob(os.path.join(output, "*_meta.json")))
```
`os.path.join(output, "*_meta.json")` produce `data/runs/*_meta.json`;
`glob.glob` expande el comodín contra el sistema de archivos y `sorted` ordena
lexicográficamente — como los nombres empiezan por timestamp ISO, el orden
lexicográfico coincide con el cronológico y `meta_files[-1]` es el más
reciente. Todo derivado de `output`, así que se auto-ajusta.

### `tuner.py` — manejo del fallo de `gh pr create`

```python
result = subprocess.run([...], capture_output=True, text=True)
if result.returncode != 0:
    print(f"[ERROR] gh pr create fallo (exit {result.returncode}):", file=sys.stderr)
    if result.stdout:
        print(result.stdout, file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return 1
pr_url = result.stdout.strip()
```

- Se elimina `check=True`: con él, `subprocess.run` lanza `CalledProcessError` si el proceso sale con código ≠ 0, y ese raise abortaba `main()` sin imprimir el stderr capturado. Sin `check`, `run` siempre devuelve un `CompletedProcess` y el control queda en nuestro código.
- `capture_output=True, text=True` se mantiene: captura stdout/stderr del hijo como `str` (no `bytes`), necesario tanto para el `pr_url` en éxito como para el volcado en fallo.
- `result.returncode != 0`: convención Unix — 0 es éxito, cualquier otro valor es error. `gh` usa 1 para errores de API/validación.
- `print(..., file=sys.stderr)`: dirige el mensaje al flujo de errores en vez de a stdout. En GitHub Actions ambos acaban en el log, pero stderr es el canal semánticamente correcto para diagnósticos y es lo que capturan los tests con `capsys.readouterr().err`.
- Se imprimen `stdout` **y** `stderr` de gh (con guardas `if` para no imprimir líneas vacías) porque gh a veces escribe contexto útil en stdout incluso al fallar.
- `return 1`: `main()` devuelve un int que `sys.exit(main())` convierte en exit code del proceso → el step de CI aparece en rojo. Se eligió `return 1` sobre `raise SystemExit(1)` porque `main()` ya usa el patrón de códigos de retorno (`return 0` en múltiples salidas) y los tests lo invocan como función esperando un int.
- Colocar el `return 1` **antes** de `mark_acted`, de la escritura de `tuner_state.json` y del append al README preserva el mismo orden de efectos que tenía el raise de `check=True`: un fallo de PR no persiste estado.
- Camino de éxito idéntico: `pr_url = result.stdout.strip()` (gh imprime la URL con salto de línea final; `.strip()` lo quita) y el resto del flujo sin cambios.

### `meta_analysis.py` — docstring de `_derive_meta_path`

Solo texto: la lógica de recortar sufijos sobre `p.name` (y no sobre la ruta
completa) sigue siendo necesaria para no corromper directorios cuyo nombre
contiene `.json`; se reformula el ejemplo como "caso legacy" porque ya no es
el default de producción.

## Tests añadidos

- `tests/test_main.py::test_run_pipeline_default_output_is_data_runs` (nuevo): usa `inspect.signature(run_pipeline).parameters["output"].default` para verificar contra el **código real** (el test 1 preexistente replica el parser a mano, así que por sí solo no detectaría una regresión del default). `inspect.signature` extrae la firma de la función y `.parameters[...].default` da el valor por defecto declarado.
- `tests/test_main.py::test_argparse_has_all_required_flags` (ajustado): el default esperado de `--output` pasa a `"data/runs"` en las dos líneas que lo mencionaban.
- `tests/test_tuner.py::TestCliApply::test_apply_gh_pr_create_falla_imprime_stderr` (nuevo): monta el entorno estándar de la clase (`_prepare_env`: runs-dir con 3 metas que disparan `remove_query`, BD y config.py temporales), mockea `subprocess.run` para que `gh pr list` devuelva `[]` (sin PR abierto) y `gh pr create` devuelva `returncode=1` con un stderr realista. Asserts: `main()` devuelve 1; el stderr del test (`capsys.readouterr().err`) contiene tanto el mensaje de gh como el prefijo `gh pr create fallo`; `tuner_state.json` no se escribe y el README no registra el tuning (no hay efectos persistidos tras el fallo).

Tests preexistentes sobre el caso límite "directorio con `.json` en el nombre"
(`test_meta_json_path_matches_phase45_glob`, `test_save_meta_analysis_path_inside_dir_named_json`,
`test_phase45_glob_finds_meta_json_in_output_dir`) se conservan intactos a
propósito: fijan su propio `output_path` temporal y protegen una robustez que
sigue vigente.

## Verificación

`./.venv/bin/pytest -q` (suite completa, exit code 0). Nota: el `addopts = "-q"`
del `pyproject.toml` hace que con `-q` en CLI la línea-resumen se oculte
(doble quiet); relanzada con `-o addopts=""` para verla:

```
441 passed, 4 skipped in 167.55s (0:02:47)
```
