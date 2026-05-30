# Implementación: #20 — tuner_a4_pr_mode

## Qué cambió

- **`src/saas_radar/agents/tuner.py`**: añadidos imports (`re`, `subprocess`, `Path`), 7 funciones nuevas de edición de config.py y helpers git/gh/BD, y ampliado `_parse_args()` y `main()` para el modo `--apply`.
- **`tests/test_tuner.py`**: añadidas 4 clases de tests nuevas (21 tests nuevos, de 14 a 35 en total) cubriendo edición de config.py, check_open_pr, mark_acted, sync_acted_status y el CLI apply E2E.
- **`.github/workflows/tuner.yml`**: añadidos permisos `contents: write` + `pull-requests: write` al job `tune` y step nuevo `Run tuner (apply PR)` tras el step de Telegram.

## Por qué

### Edición de config.py sin libcst

El plan original mencionaba libcst como posibilidad, pero el acceptance dice explícitamente "regex acotadas (no libcst si no es necesario)". Se eligió el enfoque línea a línea (`_find_block_range` + `_insert_into_set` + `_remove_from_collection`) porque:

1. config.py tiene un formato muy estable y predecible (una entrada por línea, con coma al final).
2. libcst añadiría una dependencia pesada para un caso de uso simple.
3. El enfoque línea a línea preserva comentarios y espaciado sin necesidad de round-trip de AST.

### Guard de PR abierto antes de editar config.py

El orden en `main()` con `--apply` es: sync_acted_status → check_open_pr → apply_proposals → git/gh. Esto garantiza que si hay un PR ya abierto, **no se modifica config.py**, evitando commits huérfanos.

### state_file en el padre de runs_dir

`state_file = Path(args.runs_dir).parent / "tuner_state.json"`. Si `runs_dir` es `persist/data/runs`, el estado queda en `persist/data/tuner_state.json`, junto al resto de datos persistidos. En tests se usa `tmp_path/runs` → estado en `tmp_path/tuner_state.json`.

### Matching case-insensitive en _remove_from_collection

config.py tiene `"PropertyManagement"` con mayúsculas pero el tuner normaliza los targets a minúsculas (`"propertymanagement"`). El patrón regex usa `re.IGNORECASE` para que la eliminación funcione correctamente.

### mark_acted acepta str en lugar de Path

`db_path` es `str` (heredado del resto del módulo que usa `os.path.exists` y `sqlite3.connect` con strings). Mantiene consistencia con `load_meta_recommendations`.

## Impacto en el pipeline

- **Tuner**: ahora tiene modo apply real, no solo dry-run. El modo dry-run sigue siendo el default (sin `--apply`).
- **config.py**: puede ser editado automáticamente por el tuner con `--apply`. Esto afecta a todas las fases del pipeline (scraping usa `SUBREDDITS` y `PAIN_SEARCH_QUERIES`, pain_filter usa `HIGH_SIGNAL_SUBREDDITS`).
- **BD (meta_recommendations)**: el campo `acted` se actualiza a 1 tras incluir un target en el PR, y se puede revertir a 0 si el PR se cierra sin merge.
- **GitHub**: el workflow crea PRs automáticos con rama `chore/auto-tuning-YYYYMMDD`.
- **README.md**: se añade un registro de tuning automático con cada PR creado.

## Explicación técnica

### `_find_block_range(lines, var_name)`

Recorre la lista de líneas buscando una que empiece con `var_name =` (regex `^VAR_NAME\s*[=]`). Cuando la encuentra, empieza a contar la profundidad de apertura vs cierre de llaves `{` y corchetes `[`. Cuando `depth <= 0`, la variable está cerrada y devuelve `(start, end)` inclusive. Devuelve `(-1, -1)` si la variable no se encuentra.

Por qué contar `{` y `[` juntos: config.py usa `{}` para sets (`HIGH_SIGNAL_SUBREDDITS`) y `[]` para listas (`SUBREDDITS`, `PAIN_SEARCH_QUERIES`). Una función que maneje ambos tipos evita duplicar código.

### `_insert_into_set(text, var_name, entry)`

1. Divide el texto en líneas con `.split("\n")`.
2. Llama a `_find_block_range` para localizar el bloque.
3. Comprueba si la entrada ya existe con un patrón case-insensitive para evitar duplicados.
4. Itera desde el final del bloque hacia atrás buscando la línea de cierre (`}`, `},`, `]`, `],`).
5. Detecta la indentación del último elemento real (no comentario, no vacío) para replicarla.
6. Inserta la línea nueva en esa posición con `list.insert(i, ...)`.
7. Vuelve a unir con `"\n".join(lines)`.

Por qué iterar hacia atrás para encontrar el cierre: algunas variables de config.py tienen comentarios inline antes del cierre. Si iteramos hacia adelante tomaríamos el primero `}` encontrado dentro del bloque, que puede ser de un dict anidado.

### `_remove_from_collection(text, var_name, entry)`

Similar a `_insert_into_set` pero en lugar de insertar, busca la primera línea que coincide con el patrón y la elimina con `list.pop(i)`. El uso de `re.IGNORECASE` permite que `"propertymanagement"` (minúsculas del tuner) match con `"PropertyManagement"` (en config.py).

### `apply_proposals(proposals, config_path)`

Lee el texto de config.py con `read_text()`, aplica cada propuesta en secuencia llamando a las funciones anteriores, y escribe el resultado con `write_text()`. No abre la BD ni hace llamadas externas — es pura transformación de texto en memoria.

### `check_open_pr(branch_prefix)`

Llama a `gh pr list --state open --json headRefName,url` y parsea el JSON. Devuelve la URL del primer PR cuyo `headRefName` empiece por el prefijo dado, o `None`. Captura `OSError` (gh no disponible), `json.JSONDecodeError` (output inesperado) y `subprocess.TimeoutExpired` (timeout de 30s).

### `mark_acted(db_path, proposals, acted=1)`

Para cada propuesta, ejecuta `UPDATE meta_recommendations SET acted = ? WHERE target = ?`. Usa el target tal cual de la propuesta (no normaliza a minúsculas aquí porque el valor que se buscó en la BD al construir la propuesta ya estaba normalizado).

### `sync_acted_status(db_path, state_file)`

Lee `tuner_state.json` que guarda la URL del último PR creado. Si el PR tiene `state == "CLOSED"` (cerrado sin merge), resetea `acted = 0` en todas las filas con `acted = 1`. La distinción `CLOSED` vs `MERGED` es la que usa la API de GitHub: `MERGED` es cuando el PR se integró correctamente.

### `_append_readme_registry(readme_path, date_str, proposals, pr_url)`

Añade una entrada con la fecha, la URL del PR y los cambios aplicados. Si la sección `## Registro de tuning automatico\n` no existe, la crea al final del README. Si ya existe, añade la entrada al final del fichero (sin buscar el final de la sección, lo que funciona bien cuando la sección está siempre al final).

### Bloque `--apply` en `main()`

El bloque se ejecuta solo si `args.apply` es `True`. El orden es crítico:

1. **sync_acted_status** primero: revierte cualquier PR cerrado sin merge antes de crear uno nuevo.
2. **check_open_pr**: guard para evitar crear PR duplicados. Si hay uno abierto, termina con `[SKIP]` sin modificar config.py.
3. **apply_proposals**: edita config.py en disco.
4. **git checkout/add/commit/push**: crea la rama y empuja los cambios.
5. **gh pr create**: crea el PR con el report completo como body.
6. **mark_acted**: marca las propuestas como aplicadas en la BD.
7. **state_file**: guarda la URL del PR para el siguiente run.
8. **_append_readme_registry**: actualiza el registro en README.md.

El argumento `check=True` en los comandos git garantiza que si alguno falla (p.ej. la rama ya existe), el proceso aborta con una excepción en lugar de continuar silenciosamente.

## Tests añadidos

### TestApplyProposals (7 tests)

- `test_add_high_signal_inserts_entry`: verifica que `"devops",` aparece dentro del bloque HIGH_SIGNAL_SUBREDDITS (usando `split("\nSUBREDDITS = ")` para aislar el bloque).
- `test_add_high_signal_no_duplica`: si la entrada ya existe, el conteo de ocurrencias sigue siendo 1.
- `test_demote_high_signal_elimina_entrada`: `"sysadmin"` desaparece del bloque HIGH_SIGNAL.
- `test_remove_subreddit_elimina_entrada_case_insensitive`: `"propertymanagement"` (minúsculas) elimina `"PropertyManagement"` (mayúsculas en config.py).
- `test_remove_query_elimina_entrada`: la query con apóstrofe se elimina correctamente.
- `test_preserva_comentarios_y_formato`: el comentario `# comment line` sigue presente tras añadir una entrada.
- `test_accion_desconocida_no_modifica`: acción desconocida → config.py sin cambios.

### TestCheckOpenPr (3 tests)

- `test_devuelve_url_cuando_pr_existe`: fake gh devuelve JSON con PR → función devuelve URL.
- `test_devuelve_none_cuando_no_hay_pr`: fake gh devuelve `[]` → función devuelve None.
- `test_devuelve_none_si_gh_falla`: fake gh devuelve returncode != 0 → función devuelve None.

### TestMarkActed (2 tests)

- `test_sets_acted_1_en_bd`: tras `mark_acted`, la fila tiene `acted = 1`.
- `test_noop_si_db_no_existe`: no lanza excepción si la BD no existe.

### TestSyncActedStatus (3 tests)

- `test_resetea_acted_cuando_pr_cerrado_sin_merge`: fake gh devuelve `{"state": "CLOSED"}` → `acted` vuelve a 0.
- `test_no_resetea_cuando_pr_merged`: fake gh devuelve `{"state": "MERGED"}` → `acted` sigue en 1.
- `test_noop_sin_state_file`: no lanza excepción si no existe el state file.

### TestCliApply (2 tests E2E)

- `test_apply_crea_pr_y_marca_acted`: con runs que triggean remove_query, verifica que config.py se modifica, que `gh pr create` se llamó, y que el README tiene el registro.
- `test_apply_skip_cuando_pr_ya_abierto`: fake gh devuelve PR abierto → salida con `[SKIP]` y config.py sin modificar.

## Verificación

Suite completa con `uv run pytest -q`:

```
........................................................................ [ 18%]
........................................................................ [ 37%]
........................................................................ [ 55%]
........................................................................ [ 74%]
........................................................................ [ 92%]
............................                                             [100%]
EXIT: 0
```

Tests de test_tuner.py específicamente:

```
collected 35 items

tests/test_tuner.py ...................................                  [100%]

============================== 35 passed in 0.04s ==============================
```

init.sh:

```
[OK]    Entorno listo. Puedes empezar a trabajar.
```
