# Implementación: #29 — db_storage_github_releases

## Qué cambió

### `.github/workflows/pipeline.yml` (modificado)

| Step | Antes | Después |
|---|---|---|
| `Checkout data branch` | Checkout obligatorio de la rama `data` en `persist/`, con `token` y `persist-credentials: true` (necesarios para el push posterior). | Renombrado a `Checkout data branch (fallback transitorio)`, con `continue-on-error: true` y sin credenciales persistentes (ya no se hace push). Solo existe para el primer run tras el merge y se eliminará junto con la rama. |
| `Restore saas.db from data branch` | Solo miraba `persist/data/saas.db.zst` y `persist/data/saas.db`. | Renombrado a `Restore saas.db from release or data branch`. Primero intenta `gh release download db-latest`; si no hay release, cae al `.zst` de `persist/`; si tampoco, al `saas.db` plano; si nada existe, arranca con BD nueva. Necesita `GH_TOKEN` en el env del step. |
| `Persist to data branch` | VACUUM + zstd + `git commit`/`git push` a la rama `data` (blobs de ~25 MB acumulándose para siempre). | **Eliminado.** Sustituido por `Publish DB to GitHub Releases`: VACUUM + zstd + subida del `.zst` a la release rodante `db-latest` y al snapshot diario `db-YYYYMMDD`, que además recibe `runs.tar.gz` con los JSON de resultados. |
| — (nuevo) | No existía. | `Rotate daily snapshots (keep 7)`: borra las releases `db-YYYYMMDD` dejando solo las 7 más recientes. `db-latest` no matchea el patrón y nunca se toca. |
| — (nuevo) | No existía: el pipeline falló 9 días sin que nadie se enterara. | `Notify failure via Telegram` con `if: failure()`: manda un mensaje con el nombre del workflow y el link al run. Si los secrets faltan, el step no falla. |
| `Restore saas.db from cache`, `concurrency`, `permissions: contents: write`, `Upload run outputs` | — | **Sin cambios.** El cache sigue siendo la vía rápida; el artifact de 30 días se mantiene como acceso rápido por-run (el histórico duradero ahora vive en los snapshots). |

### `.github/workflows/tuner.yml` (modificado)

| Step | Antes | Después |
|---|---|---|
| `Checkout data branch` | Checkout obligatorio de `data` en `persist/`. | Mismo tratamiento que en pipeline.yml: `continue-on-error: true` + comentario de transición. |
| `Restore saas.db from data branch` | Solo descomprimía `persist/data/saas.db.zst` si existía. | Renombrado a `Restore saas.db from release or data branch`: descarga `saas.db.zst` de `db-latest` a `persist/data/` y lo descomprime a `persist/data/saas.db`; fallback al `.zst` del checkout. Añade `mkdir -p persist/data/runs` para que `--runs-dir` no rompa si el checkout falla. Los `--db-path persist/data/saas.db` y `--runs-dir persist/data/runs` del tuner **no cambian**. |
| — (nuevo) | No existía. | `Notify failure via Telegram`, idéntico al del pipeline pero con el nombre `saas-radar tuner`. |

### `tests/test_pipeline_workflow.py` (modificado)

Ya existían tests estructurales del YAML del pipeline (parsean el workflow
con `yaml.safe_load` y validan su estructura como regression-guard). El
cambio de contrato de F22 → F29 obligaba a adaptarlos, y de paso se cubrió
el nuevo contrato y `tuner.yml` (que no tenía tests):

- **`test_has_persist_step`** (guard de F22, exigía un step "Persist to data
  branch") → **eliminado y sustituido** por `test_no_push_to_data_branch`,
  que exige lo contrario: ningún step del pipeline contiene `git push` (la
  rama `data` queda congelada).
- **`test_has_data_branch_checkout`** → renombrado a
  `test_has_data_branch_checkout_as_tolerant_fallback`: sigue exigiendo el
  checkout de `data` (fallback de transición) pero ahora también que lleve
  `continue-on-error: true`.
- **`test_permissions_contents_write`** → docstring actualizado (el permiso
  ahora lo justifican las releases, no el push).
- **Nuevos** (pipeline.yml): `test_restore_step_downloads_from_db_latest_release`,
  `test_publish_step_uploads_to_releases`,
  `test_rotation_step_keeps_seven_and_spares_db_latest`,
  `test_failure_alert_step`.
- **Nuevos** (tuner.yml, con fixture `tuner_workflow` propia):
  `test_tuner_workflow_is_valid_yaml`,
  `test_tuner_restore_downloads_from_db_latest_release`,
  `test_tuner_data_branch_checkout_is_tolerant`,
  `test_tuner_failure_alert_step`.

No se tocó ningún archivo de `src/` (la feature es workflows + tests
estructurales del YAML + docs).

## Por qué

Tres problemas estructurales de la rama `data` como almacén:

1. **Bloat sin límite.** Cada commit diario añade un blob zstd completo de
   ~25 MB. zstd es un formato comprimido: dos snapshots consecutivos no
   comparten bytes aprovechables, así que git no puede deltificar. La rama
   acumula ya **1,25 GB en 14 snapshots** (~9 GB/año proyectado). GitHub
   empieza a avisar con repos de 5 GB. Todo clon/fetch del repo arrastra ese
   peso para siempre (los blobs quedan en la historia aunque se borren del
   árbol).
2. **El límite de 100 MB/archivo volverá.** El fix de la #26 (compresión)
   compró tiempo: con ratio ~4,5x, cuando la BD real supere ~450 MB el `.zst`
   superará los 100 MB y el push volverá a fallar. Es la misma avería del
   26-jun aplazada.
3. **Fallo silencioso.** El pipeline estuvo 9 días fallando en el step de
   persistencia sin que nadie se enterara: no había ninguna notificación de
   fallo, y el cron verde-o-rojo solo se ve entrando a mirar.

Por qué GitHub Releases lo resuelve:

- **Límite de 2 GB por asset** (vs 100 MB por archivo en git): margen de ~80x
  sobre el `.zst` actual de ~25 MB.
- **Cero crecimiento del repo**: los assets de release viven fuera del objeto
  git; subir/borrar assets no añade historia. `--clobber` sobre `db-latest`
  reemplaza el asset, no acumula.
- **Mismo `GITHUB_TOKEN`**: crear/subir/borrar releases usa el permiso
  `contents: write` que el workflow ya tiene. Cero secrets nuevos, cero
  servicios externos (se descartaron R2/S3 por añadir credenciales y un
  proveedor más, y la rama huérfana con force-push por seguir contando contra
  el tamaño del repo hasta que el GC remoto decida correr).
- Los **snapshots `db-YYYYMMDD` con retención 7** dan rollback (si un run
  corrompe la BD, se restaura el snapshot de ayer) y los `runs.tar.gz` sacan
  los JSON de resultados de la retención de 30 días de los artifacts.

La alerta Telegram cierra el problema 3: cualquier fallo de cualquiera de los
dos workflows llega al chat ya configurado para las oportunidades.

## Impacto en el pipeline

- **pipeline.yml (cron diario):** la BD se restaura desde `db-latest` y se
  publica en Releases. El comportamiento del pipeline Python (scraping,
  scoring, LLM, BD, Telegram de oportunidades) no cambia en absoluto: los
  cambios son solo de transporte de la BD.
- **tuner.yml:** lee la BD fresca desde `db-latest` en vez de la rama `data`
  (que se quedaba congelada en el último push). Los flags del CLI del tuner
  no cambian.
- **Rama `data`: queda congelada.** Deja de escribirse desde este merge. NO
  se borra en esta feature — el checkout de fallback la sigue leyendo durante
  la transición. Plan documentado: tras verificar varios runs verdes con
  releases, borrar la rama manualmente (`git push origin --delete data`) y
  eliminar los steps de checkout de fallback de ambos workflows. Gracias a
  `continue-on-error: true`, si la rama se borra antes de limpiar los
  workflows, nada falla.
- **Sincronización local:** cambia de `git fetch origin data && git checkout
  origin/data -- ...` al comando `gh release download` (ver sección más abajo).
- **Primer run tras el merge:** `db-latest` no existe → `gh release download`
  falla → se usa el `.zst` de la rama `data` (estado actual) → al final del
  run se crean `db-latest` y el primer snapshot. A partir del segundo run, la
  release manda.
- **Sin cambios** en `src/`, `tests/`, schema de BD ni CLI.

## Explicación técnica

### pipeline.yml — Checkout data branch (fallback transitorio)

```yaml
- name: Checkout data branch (fallback transitorio)
  uses: actions/checkout@v4
  continue-on-error: true
  with:
    ref: data
    path: persist
    fetch-depth: 1
```

- `continue-on-error: true` — si este step falla (por ejemplo, cuando la rama
  `data` se borre en el futuro), el job **no** se marca como fallido y sigue
  con el siguiente step. Sin esto, borrar la rama rompería todos los runs
  hasta editar el workflow. Es la pieza que hace la transición limpia.
- Se eliminaron `token: ${{ secrets.GITHUB_TOKEN }}` y
  `persist-credentials: true` del step original: solo eran necesarios para
  que el `git push` posterior tuviera credenciales escritas en
  `persist/.git/config`. Como ya no hay push, dejar credenciales persistidas
  en disco sería superficie de riesgo gratuita (checkout usa el token por
  defecto para el fetch igualmente).
- `path: persist` — clona en el subdirectorio `persist/` para no pisar el
  checkout de `main`; `fetch-depth: 1` trae solo el último commit (no los
  1,25 GB de historia).

### pipeline.yml — Restore saas.db from release or data branch

```yaml
env:
  GH_TOKEN: ${{ github.token }}
```

- La CLI `gh` no lee el token del remote git: exige la variable de entorno
  `GH_TOKEN` (o `GITHUB_TOKEN`). `${{ github.token }}` es el mismo token
  efímero del job (idéntico a `secrets.GITHUB_TOKEN`); con `permissions:
  contents: write` alcanza para descargar, crear y borrar releases del propio
  repo. Se declara a nivel de step (no de job) para que solo los steps que
  usan `gh` lo tengan.

```bash
mkdir -p data
if [ ! -f data/saas.db ]; then
```

- `mkdir -p` crea `data/` si no existe (`-p` no falla si ya existe).
- `[ ! -f data/saas.db ]` — si `actions/cache` (step anterior, intacto) ya
  restauró la BD, no se descarga nada: el cache sigue siendo la vía rápida.
  `-f` comprueba "existe y es archivo regular"; `!` lo niega.

```bash
if gh release download db-latest --pattern 'saas.db.zst' --output data/saas.db.zst --clobber; then
```

- `gh release download db-latest` descarga assets de la release cuyo **tag**
  es `db-latest`. `--pattern 'saas.db.zst'` filtra qué asset bajar (glob
  contra el nombre del asset; así si un día la release tuviera más assets,
  solo baja la BD). `--output` fija la ruta destino; `--clobber` sobrescribe
  si el archivo ya existiera (sin él, `gh` aborta ante un destino existente).
- El comando va **como condición del `if`**: si la release no existe todavía
  (primer run tras el merge), `gh` devuelve exit code ≠ 0 y el flujo cae al
  `elif` sin matar el step. Detalle importante: los shells de Actions corren
  con `bash -e` (errexit: cualquier comando que falla aborta el script), pero
  errexit **no aplica a comandos usados como condición de `if`** — por eso el
  download fallido no rompe nada.

```bash
  zstd -d -f data/saas.db.zst -o data/saas.db
  rm -f data/saas.db.zst
```

- `zstd -d` descomprime; `-f` fuerza sobrescritura del destino; `-o` fija el
  archivo de salida (sin `-o`, zstd derivaría el nombre quitando `.zst`, pero
  explícito es más claro y no depende de la ruta de entrada).
- `rm -f` borra el `.zst` intermedio para que no acabe dentro del
  `runs.tar.gz` ni ocupe disco; `-f` evita error si no existe.

```bash
elif [ -f persist/data/saas.db.zst ]; then
  zstd -d -f persist/data/saas.db.zst -o data/saas.db
elif [ -f persist/data/saas.db ]; then
  cp persist/data/saas.db data/saas.db
else
  echo "Sin release db-latest ni rama data: arranque con BD nueva."
fi
```

- Cadena de fallbacks idéntica a la lógica de la #26: `.zst` de la rama
  `data` (estado actual de la rama) y, por compatibilidad con snapshots
  antiguos, el `saas.db` plano. El `else` con `echo` documenta en el log del
  run el arranque en frío (`init_db()` del pipeline crea el schema).

### pipeline.yml — Publish DB to GitHub Releases

```bash
if [ ! -f data/saas.db ]; then
  echo "No existe data/saas.db; nada que publicar."
  exit 0
fi
```

- Guard defensivo: un run con `--skip-scrape --skip-ai` teórico o un fallo
  raro podría dejar el job "verde" sin BD. `exit 0` termina el step **con
  éxito** (skip explícito), en vez de dejar que `sqlite3` explote con un
  error confuso.

```bash
sqlite3 data/saas.db 'VACUUM;'
zstd -T0 -15 -f data/saas.db -o saas.db.zst
```

- `VACUUM` reconstruye el archivo SQLite compactando páginas libres
  (los DELETE/UPDATE dejan huecos que inflan el archivo y comprimen peor).
- `zstd -T0` usa todos los cores del runner; `-15` es nivel de compresión
  alto (~4,5x sobre esta BD, medido en la #26: 98 MB → 22 MB) con tiempo
  razonable; `-f` sobrescribe. El `.zst` se escribe en la **raíz del
  workspace** (no en `data/`) a propósito: así nunca puede colarse en el
  `tar` de outputs ni en el artifact.

```bash
if gh release view db-latest >/dev/null 2>&1; then
  gh release upload db-latest saas.db.zst --clobber
else
  gh release create db-latest saas.db.zst \
    --title "db-latest" \
    --notes "BD rodante del pipeline: siempre el asset del último run verde."
fi
```

- `gh release view <tag>` devuelve 0 si la release existe; se usa solo como
  test de existencia, por eso stdout y stderr van a `/dev/null` (`>/dev/null
  2>&1`: redirige stdout y luego duplica stderr sobre él).
- Si existe: `gh release upload --clobber` **reemplaza** el asset homónimo.
  Sin `--clobber`, `gh` falla si ya hay un asset `saas.db.zst` — y siempre lo
  habrá a partir del segundo run. Este reemplazo es la clave del "cero
  crecimiento": una sola release, un solo asset, sobrescrito a diario.
- Si no existe (solo el primer run): `gh release create <tag> <archivos>`
  crea la release y sube los assets en un solo comando. `--notes` es
  obligatorio en la práctica (sin `--notes`/`--notes-file`/`--generate-notes`,
  `gh` intenta abrir un editor interactivo y falla en CI).

```bash
tar_paths=""
if [ -d data/ai_analysis.json ]; then tar_paths="$tar_paths data/ai_analysis.json"; fi
if [ -d data/runs ]; then tar_paths="$tar_paths data/runs"; fi
if [ -n "$tar_paths" ]; then
  tar -czf runs.tar.gz $tar_paths
fi
```

- Los JSON del run viven en **dos** sitios, comprobado en el código:
  `main.py` pasa `output="data/ai_analysis.json"` a `run_ai_analysis`, y
  `_save_results` (ai_analyzer.py:126) trata esa ruta como **directorio**
  (`Path(output_path).mkdir(...)`) — es decir, los resultados quedan en
  `data/ai_analysis.json/<ts>_results.json` (sí, un directorio con extensión
  `.json`; rareza heredada que no toca arreglar en esta feature). Y
  `data/runs/` es donde el workflow prepara outputs y donde `main.py` busca
  los `*_meta.json`. Se empaquetan **los que existan**, acumulando rutas en
  `tar_paths` y expandiendo la variable **sin comillas** a propósito: aquí
  queremos word-splitting para que `tar` reciba cada ruta como argumento
  separado (con comillas sería un único argumento inválido con espacio
  dentro). Es seguro porque las rutas son literales sin espacios.
- `[ -n "$tar_paths" ]` — solo se crea el tar si hay algo que empaquetar
  (`-n`: string no vacío). `tar -czf` = **c**rear, comprimir con g**z**ip,
  **f** archivo de salida.

```bash
snapshot="db-$(date -u +%Y%m%d)"
assets="saas.db.zst"
if [ -f runs.tar.gz ]; then assets="$assets runs.tar.gz"; fi
if gh release view "$snapshot" >/dev/null 2>&1; then
  for asset in $assets; do
    gh release upload "$snapshot" "$asset" --clobber
  done
else
  gh release create "$snapshot" $assets \
    --title "$snapshot" \
    --notes "Snapshot diario de la BD y outputs del run ($(date -u +%F))."
fi
```

- `date -u +%Y%m%d` — fecha UTC (`-u`) en formato `YYYYMMDD` (p.ej.
  `db-20260704`). UTC y no local porque el cron de Actions corre en UTC: así
  el tag del snapshot coincide con el día del run sin ambigüedad de zona.
- La rama del `if` cubre re-runs el mismo día (workflow_dispatch tras el
  cron): la release del día ya existe, así que se **reemplazan** sus assets
  con `--clobber` en vez de fallar. Se itera con `for` porque `gh release
  upload` sí acepta varios archivos, pero iterar deja un log línea-a-línea
  más claro y falla señalando el asset concreto.
- `$assets` sin comillas: mismo word-splitting intencional que en `tar`.

### pipeline.yml — Rotate daily snapshots (keep 7)

```bash
gh release list --limit 100 --json tagName --jq '.[].tagName' \
  | { grep -E '^db-[0-9]{8}$' || true; } \
  | sort -r \
  | tail -n +8 \
  | while read -r tag; do
      echo "Borrando snapshot antiguo: $tag"
      gh release delete "$tag" --cleanup-tag --yes
    done
```

- `gh release list --json tagName --jq '.[].tagName'` — pide la lista como
  JSON y extrae solo el campo `tagName` con el jq embebido de `gh` (un tag
  por línea, sin columnas de tabla que parsear). `--limit 100` cubre de sobra
  (nunca habrá más de ~8 releases `db-*` + alguna manual).
- `grep -E '^db-[0-9]{8}$'` — regex anclada: exactamente `db-` + **8 dígitos**
  y fin de línea. `db-latest` no matchea (letras ≠ dígitos), así que la
  release rodante es **estructuralmente imposible de borrar** por este step;
  tampoco matchearía un tag de versión tipo `v1.0`. El grep va envuelto en
  `{ ... || true; }` por errexit: `grep` devuelve exit 1 cuando no encuentra
  nada (día 1: aún no hay snapshots), y aunque en un pipe sin `pipefail` eso
  no aborta, el `|| true` lo hace robusto también si algún día se activa
  `pipefail` en el shell del workflow.
- `sort -r` — orden lexicográfico inverso. Con fechas `YYYYMMDD` el orden
  lexicográfico ES el cronológico (por eso ese formato y no `DD-MM-YYYY`),
  así que la primera línea es el snapshot más reciente.
- `tail -n +8` — imprime **desde la línea 8** (el `+N` de tail es "a partir
  de la línea N"). Las líneas 1-7 (los 7 más recientes) se conservan; todo lo
  demás pasa al bucle de borrado.
- `while read -r tag` — lee línea a línea; `-r` evita que `read` interprete
  backslashes (higiene estándar).
- `gh release delete "$tag" --cleanup-tag --yes` — borra la release **y su
  tag git** (`--cleanup-tag`; sin él quedarían tags huérfanos acumulándose en
  el repo). `--yes` suprime la confirmación interactiva que colgaría el CI.

### pipeline.yml — Notify failure via Telegram

```yaml
- name: Notify failure via Telegram
  if: failure()
```

- `if: failure()` — el step corre **solo** si algún step anterior del job
  falló (por defecto los steps llevan un `success()` implícito que los salta
  tras un fallo; `failure()` invierte eso). Va como último step para cubrir
  cualquier fallo: restore, instalación, pipeline o publicación.

```yaml
env:
  RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

- Se compone la URL del run con contextos de Actions: `github.server_url`
  (`https://github.com`), `github.repository` (`owner/repo`) y
  `github.run_id`. Pasarla por `env` en vez de interpolar `${{ }}` dentro del
  script es la práctica recomendada: el shell ve una variable normal y no hay
  riesgo de inyección de template en el código bash. (`TELEGRAM_BOT_TOKEN` y
  `TELEGRAM_CHAT_ID` ya están en el env del job en pipeline.yml; en tuner.yml
  se declaran en el step porque allí no hay env de job.)

```bash
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
  curl -sS --max-time 15 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=[ALERTA] Workflow 'saas-radar pipeline' ha fallado. Run: ${RUN_URL}" \
    || true
else
  echo "Secrets de Telegram no configurados; alerta omitida."
fi
```

- Guard `[ -n ... ] && [ -n ... ]`: si los secrets no están configurados
  (fork, entorno de prueba), el step imprime el aviso y termina **verde** —
  requisito explícito del acceptance. Un step de alerta que falla encima del
  fallo real solo añade ruido.
- `curl -sS`: `-s` silencia la barra de progreso, `-S` re-habilita los
  mensajes de error reales (la combinación estándar "silencioso pero no
  mudo"). `--max-time 15` corta a los 15s si Telegram no responde — sin
  timeout, un cuelgue de red dejaría el job zombi hasta el timeout global de
  6 horas.
- `--data-urlencode` convierte la petición en POST y codifica el valor
  (espacios, comillas, los `/` de la URL del run) — con `--data` a pelo, el
  texto rompería el `application/x-www-form-urlencoded`.
- `|| true` final: si incluso con secrets el curl falla (red, token
  revocado), el step no falla. La alerta es best-effort; el fallo real ya
  está registrado en el run.
- No se reutiliza `saas_radar.notifications.telegram` porque en el momento
  del fallo Python o las dependencias pueden no estar instaladas (el fallo
  puede ser justamente `pip install`). `curl` está siempre en el runner.

### tuner.yml — Restore saas.db from release or data branch

```bash
mkdir -p persist/data/runs
if [ ! -f persist/data/saas.db ]; then
  if gh release download db-latest --pattern 'saas.db.zst' --output persist/data/saas.db.zst --clobber; then
    zstd -d -f persist/data/saas.db.zst -o persist/data/saas.db
  elif [ -f persist/data/saas.db.zst ]; then
    zstd -d -f persist/data/saas.db.zst -o persist/data/saas.db
  else
    echo "Sin release db-latest ni rama data: el tuner correra sin BD previa."
  fi
fi
```

- Misma cadena que el pipeline, pero el destino es `persist/data/saas.db`
  para **no tocar** los flags `--db-path persist/data/saas.db` y `--runs-dir
  persist/data/runs` que el tuner ya usa (requisito del diseño: evitar otra
  regresión tipo 8409bb9 por cambiar rutas a los consumidores).
- `mkdir -p persist/data/runs` — doble función: crea el árbol destino para el
  download cuando el checkout de `data` falló (con `continue-on-error`,
  `persist/` podría no existir), y garantiza que `--runs-dir
  persist/data/runs` apunte a un directorio existente (vacío en el peor caso)
  en vez de reventar el CLI.
- Aquí el `--output` del download apunta directamente a
  `persist/data/saas.db.zst`: si el checkout dejó un `.zst` viejo de la rama,
  `--clobber` lo **sobrescribe** con el de la release (que siempre es igual o
  más reciente que el último push a la rama congelada). No se borra el `.zst`
  tras descomprimir porque en este workflow nada lo empaqueta después.

### tuner.yml — Notify failure via Telegram

Idéntico al del pipeline salvo el texto (`'saas-radar tuner'`) y que los
secrets se declaran en el `env` del step (este workflow no tiene env a nivel
de job). Cubre también los fallos "de negocio" del tuner (p.ej. el step
`Check Telegram secrets` que sale con exit 1).

## Tests añadidos

**Limitación de fondo (aceptada en el acceptance):** los workflows de
GitHub Actions no son *ejecutables* en pytest — no existe runner local del
YAML de Actions, así que ningún test puede probar que `gh release upload`
funciona de verdad contra la API. Lo que SÍ es testeable (y el repo ya lo
hacía para F22) es la **estructura** del YAML como regression-guard: parsear
el workflow y asegurar que el contrato (steps, flags, guards) no se rompe
por accidente en futuras ediciones. Tests en `tests/test_pipeline_workflow.py`:

| Test | Qué caso cubre |
|---|---|
| `test_no_push_to_data_branch` | Ningún step del pipeline vuelve a hacer `git push` (rama data congelada). |
| `test_has_data_branch_checkout_as_tolerant_fallback` | El checkout de `data` existe como fallback Y lleva `continue-on-error: true`. |
| `test_restore_step_downloads_from_db_latest_release` | El restore intenta `gh release download db-latest` (asset `saas.db.zst`), descomprime, conserva el fallback a `persist/` y tiene `GH_TOKEN` en el env. |
| `test_publish_step_uploads_to_releases` | El publish mantiene `VACUUM` + `zstd -T0 -15`, usa `--clobber`, crea el snapshot `db-$(date -u +%Y%m%d)` y empaqueta `runs.tar.gz`. |
| `test_rotation_step_keeps_seven_and_spares_db_latest` | La rotación usa exactamente la regex `^db-[0-9]{8}$` (no puede matchear `db-latest`), `tail -n +8` (conserva 7) y `--cleanup-tag --yes`. |
| `test_failure_alert_step` | Hay exactamente un step `if: failure()` con curl a Telegram, guard de secrets, `RUN_URL` compuesto en env, y es el ÚLTIMO step. |
| `test_tuner_workflow_is_valid_yaml` | tuner.yml parsea y tiene el job `tune`. |
| `test_tuner_restore_downloads_from_db_latest_release` | El tuner restaura desde `db-latest` al destino `persist/data/saas.db` (los `--db-path` no cambian). |
| `test_tuner_data_branch_checkout_is_tolerant` | Checkout de `data` del tuner con `continue-on-error: true`. |
| `test_tuner_failure_alert_step` | Alerta de fallo del tuner con guard, texto identificando el workflow, última posición. |

Lo que los tests estructurales no pueden cubrir (comportamiento real de
`gh`, `tar`, `curl` bajo `bash -e`) se verificó con simulación local:

1. Sintaxis YAML de ambos workflows parseada con `yaml.safe_load` → OK
   (13 steps pipeline, 12 steps tuner).
2. `bash -n` (syntax check) sobre los 16 bloques `run` embebidos → todos OK.
3. Simulación bajo `bash -e` (el modo real de Actions) con un `gh` mockeado:
   - Rotación con 9 snapshots + `db-latest` → borra exactamente los 2 más
     antiguos, conserva 7, **no toca `db-latest`**.
   - Rotación sin ningún snapshot con fecha → exit 0 (el `|| true` del grep).
   - Restore con release inexistente → cae al `.zst` de `persist/` y
     descomprime bien; sin nada → mensaje de BD nueva, exit 0.
   - Publish primera vez (con `sqlite3` y `zstd` reales) → `gh release create
     db-latest` + `gh release create db-20260704 saas.db.zst runs.tar.gz`; el
     tar contiene `data/ai_analysis.json/` y `data/runs/`.
   - Guard de Telegram con secrets vacíos → rama del `else`, exit 0.

**Plan de verificación manual (post-merge, lo ejecuta el líder/usuario):**

1. `gh workflow run 'saas-radar pipeline'` y esperar el run verde.
2. Comprobar `gh release view db-latest` → asset `saas.db.zst` con fecha de
   hoy y tamaño ~25 MB.
3. Comprobar `gh release view db-$(date -u +%Y%m%d)` → assets `saas.db.zst`
   **y** `runs.tar.gz`.
4. Verificar que `origin/data` NO tiene commit nuevo (la rama quedó
   congelada).
5. Probar la alerta: lanzar un run destinado a fallar (p.ej. deshabilitar
   temporalmente un secret de Reddit) o añadir transitoriamente un step
   `run: exit 1` en una rama de prueba → debe llegar el mensaje `[ALERTA]`
   con el link al run. Alternativa sin romper nada: probar el curl a mano con
   los secrets reales.
6. Segundo run el mismo día → `db-latest` y el snapshot del día se
   actualizan (`--clobber`), sin release duplicada.
7. Tras 8+ días de runs verdes: `gh release list` debe mostrar `db-latest` +
   exactamente 7 snapshots `db-YYYYMMDD`.
8. Tras varios runs verdes: borrar manualmente la rama `data`
   (`git push origin --delete data`) y, en una feature/chore posterior,
   eliminar los steps `Checkout data branch (fallback transitorio)` y las
   ramas `elif` del restore.

## Sincronización local de la BD

Nuevo flujo (sustituye a `git fetch origin data && git checkout origin/data -- ...`):

```bash
gh release download db-latest -p saas.db.zst -O data/saas.db.zst --clobber && \
  zstd -d -f data/saas.db.zst -o data/saas.db
```

- `-p` es la forma corta de `--pattern`; `-O` de `--output`.
- `--clobber` sobrescribe el `.zst` local de una sincronización anterior.
- `zstd -d -f` descomprime pisando la `data/saas.db` local.
- Los outputs JSON de un día concreto: `gh release download db-YYYYMMDD -p
  runs.tar.gz` y `tar -xzf runs.tar.gz` (extrae `data/ai_analysis.json/` y
  `data/runs/` relativos al cwd).

## Verificación

Suite completa con el venv del proyecto:

```
$ .venv/bin/pytest
416 passed, 4 skipped in 167.43s (0:02:47)
$ echo $?
0
```

`ruff check tests/test_pipeline_workflow.py` → "All checks passed!".
(Nota: `ruff format --check` reformatearía 30 archivos preexistentes del
repo — el proyecto solo aplica `ruff check`; no se reformateó nada para no
generar ruido en líneas ajenas a la feature.)

Últimas líneas de `./init.sh`:

```
── 6. Verificando anti-patrones del legacy ────────────
[OK]    Sin sys.path.append en src/

── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
```

(`init.sh` avisa "pytest no instalado" porque invoca el `python3` del
sistema, no el venv — comportamiento preexistente; la suite se ejecutó
aparte con `.venv/bin/pytest`, exit 0.)
