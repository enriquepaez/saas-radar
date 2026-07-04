# Implementación: 26 — pipeline_db_compression

## Qué cambió

Dos archivos de código tocados (100% infra, cero Python):

- **`.github/workflows/pipeline.yml`** (modificado):
  1. **Nuevo step `Restore saas.db from data branch`** (entre `Restore saas.db from cache` y `Setup Python 3.11`). Antes no existía ningún restore desde la rama `data`: el workflow dependía solo de `actions/cache` (si el cache se evictaba, el pipeline arrancaba con BD vacía — gap latente heredado de #22). Ahora, si el cache no restauró `data/saas.db`, se descomprime `persist/data/saas.db.zst` o, si solo existe el plano (estado actual de la rama), se copia tal cual.
  2. **Step `Persist to data branch` modificado.** Antes: `cp data/saas.db persist/data/saas.db` (blob de 99→104 MB que GitHub rechaza desde el 26-jun) + `git add data/saas.db data/runs/`. Después: `sqlite3 data/saas.db 'VACUUM;'` + `zstd -T0 -15` generando `persist/data/saas.db.zst` (~22 MB), `git rm --ignore-unmatch --quiet data/saas.db` para sacar el blob grande del árbol de la rama, y `git add` del `.zst` (con guard de existencia) + `data/runs/`. Todo en el mismo commit.
  3. **Sin cambios en**: `concurrency: saas-radar` (`cancel-in-progress: false`), `actions/cache@v4`, copia de `data/runs/` sin comprimir, artefacto `run-outputs`, checkout dual main+data, `permissions.contents: write`, idiom `git diff --cached --quiet || (commit && push)`.

- **`.github/workflows/tuner.yml`** (modificado — añadido al scope por el leader tras el hallazgo del riesgo, ver acceptance ampliado): **nuevo step `Restore saas.db from data branch`** entre `Checkout data branch` y `Setup Python`. Antes: los dos steps del tuner (`Run tuner (dry-run)` línea 59 y `Run tuner (apply PR)` línea 84) pasan `--db-path persist/data/saas.db`, que dejará de existir en el árbol de la rama `data` tras el primer push con `.zst`. Después: si `persist/data/saas.db` no existe pero sí `persist/data/saas.db.zst`, se descomprime in situ a `persist/data/saas.db` — los dos comandos del tuner quedan **sin tocar**. Misma estructura de guards que el restore de `pipeline.yml`.

- **`progress/impl_pipeline_db_compression.md`** (nuevo): este archivo.
- **`progress/current.md`** (modificado): plan y estado de la sesión.
- **`feature_list.json`**: la feature ya estaba `in_progress` (lo marcó el leader); no se toca el status aquí.

## Por qué

### El bug: límite de 100 MB por archivo en GitHub

Desde el 26-jun el step `Persist to data branch` falla en todos los runs del cron:

```
remote: error: File data/saas.db is 104.07 MB; this exceeds GitHub's file size limit of 100.00 MB
! [remote rejected] data -> data (pre-receive hook declined)
```

El pipeline (scrape + IA) termina bien; solo el push es rechazado, así que el trabajo diario se pierde: el step falla → el job falla → `actions/cache` **tampoco** guarda (el post-step de cache no salva en jobs fallidos), y el último snapshot bueno en `origin/data` es del 24-jun (99 MB, al borde del límite).

### Por qué compresión (vs alternativas)

- **Poda de datos (borrar posts viejos)**: destruye historia que el data_loader (`post_age_days=365`) y el tuner usan; solo pospone el problema unos meses; y decidir qué borrar es una decisión de producto, no un fix operativo.
- **Git LFS**: requiere configurar LFS en el repo y en el runner, cuota de bandwidth de GitHub LFS (1 GB/mes gratis; un pull diario de 100 MB la agota en 10 días), y complica el `git checkout origin/data -- ...` local.
- **Storage externo (S3, GCS, GH Releases)**: añade credenciales, otro servicio que mantener, y rompe el patrón "la rama data es la fuente de verdad" del que dependen `tuner.yml` y la sincronización local.
- **Compresión zstd**: SQLite comprime ~4.5x (texto en inglés muy redundante), `zstd` viene preinstalado en `ubuntu-latest`, el cambio es local al workflow y reversible, y deja margen para años de crecimiento (22 MB actuales → el límite de 100 MB se alcanzaría con una BD plana de ~450 MB).

Medido localmente sobre la BD real de `origin/data`: **98.1 MB → 22.0 MB** (ver sección "Tamaño real" abajo). Margen de sobra respecto al esperado (<50 MB) del acceptance.

### Por qué VACUUM antes de comprimir

SQLite no devuelve al filesystem el espacio de filas borradas/actualizadas: lo deja en páginas libres internas. `VACUUM` reconstruye el archivo compactándolo (aquí: 98.1 → 90.0 MB, −8 MB de páginas muertas) y además reordena las páginas, lo que mejora la compresibilidad. Coste: unos segundos y ~2x el tamaño de la BD en disco temporal — trivial en el runner.

### Por qué `git rm --ignore-unmatch` en el mismo commit

El objetivo no es solo añadir el `.zst`: es que el árbol de la rama `data` **deje de contener** `data/saas.db`. Si solo añadiéramos el `.zst`, el commit seguiría arrastrando el blob de 99 MB en el árbol y cada `checkout` de la rama lo bajaría. `--ignore-unmatch` hace la operación idempotente: en el segundo run (cuando el plano ya no exista en la rama) el comando sale 0 en vez de fallar con "did not match any files" — crítico porque el bloque `run:` corre con `set -e`.

Nota: el blob de 99 MB sigue existiendo en la **historia** de la rama `data` (commits antiguos). Eso no viola el límite (el pre-receive hook solo rechaza archivos nuevos >100 MB) pero engorda el clone completo. No es problema: el workflow usa `fetch-depth: 1` (shallow) y el usuario hace `git checkout origin/data -- <paths>`, no clones completos. Si algún día molesta, se puede reescribir la rama con `git filter-repo` (fuera de scope).

### Por qué el restore da prioridad al cache

El nuevo step de restore solo actúa si `data/saas.db` **no** existe (`[ ! -f data/saas.db ]`), es decir, si `actions/cache` no lo restauró. En estado estacionario cache y rama son idénticos; con cache caliente nos ahorramos la descompresión. Si el cache se evicta (GH lo purga tras ~7 días sin acceso), la rama `data` actúa de fuente de verdad — que es exactamente el gap que #22 dejó abierto y esta feature cierra de paso (lo exige el acceptance 3).

### Por qué el fallback al `saas.db` plano

En el primer run tras el merge, la rama `data` todavía contiene `persist/data/saas.db` (el plano del 24-jun) y **no** contiene `.zst`. Si el cache fallara justo en ese run, sin el fallback arrancaríamos con BD vacía. El `elif` cubre esa ventana; tras el primer push exitoso el `.zst` existe y la rama de código puede olvidarse del plano.

### Por qué también `tuner.yml` (ampliación de scope decidida por el leader)

Yo mismo detecté y anoté el riesgo: `tuner.yml` consume la BD de la rama `data` con `--db-path persist/data/saas.db`. Mergear solo el cambio de `pipeline.yml` habría roto el tuner en el primer run post-push — exactamente el patrón de la regresión `8409bb9` (cambiar el contrato de la rama `data` sin actualizar a sus consumidores, documentado en #22). El leader amplió el acceptance de la feature para cubrirlo en la misma rama.

Diseño elegido: **descomprimir hacia la ruta que el tuner ya espera** (`persist/data/saas.db`) en vez de cambiar los `--db-path` de los dos comandos. Ventajas: (a) un solo punto de cambio en vez de dos invocaciones; (b) el fallback pre-migración es implícito — si el plano aún existe en la rama (antes del primer push con `.zst`), el step no hace nada y el tuner lo usa tal cual; (c) el working tree sucio de `persist/` es inocuo porque `tuner.yml` nunca commitea a la rama `data` (su PR de tuning sale del checkout de `main` vía `gh`).

## Impacto en el pipeline

1. **Persistencia (rama `data`)**: cada run exitoso commitea `data/saas.db.zst` (~22 MB) + `data/runs/` y elimina `data/saas.db` del árbol. El push vuelve a funcionar → el trabajo diario deja de perderse.
2. **Restore**: el pipeline gana resiliencia a evicción de cache (antes: BD vacía silenciosa; ahora: restore desde la rama).
3. **Sincronización local de la BD**: cambia el comando (ahora hay que descomprimir; ver sección abajo). Es el único cambio visible para el usuario.
4. **`tuner.yml`**: riesgo detectado y **cubierto en esta misma feature** (acceptance ampliado por el leader). El nuevo step de restore descomprime el `.zst` a `persist/data/saas.db` antes de invocar el tuner, así que los dos comandos (`dry-run` y `apply PR`) siguen funcionando sin cambios, tanto antes como después del primer push con `.zst`.
5. **Artefacto `run-outputs`, `actions/cache`, `concurrency`, scraping, scoring, LLM, Telegram, CLI**: sin cambios.
6. **CPU del runner**: VACUUM (~segundos) + zstd -15 con `-T0` (~5 s medidos en local con 90 MB; el runner de GH tiene 4 vCPU, será similar). Despreciable frente a los minutos de scrape+IA.

## Explicación técnica (línea a línea del YAML)

### Step nuevo: `Restore saas.db from data branch`

```yaml
- name: Restore saas.db from data branch
  run: |
    mkdir -p data
    if [ ! -f data/saas.db ]; then
      if [ -f persist/data/saas.db.zst ]; then
        zstd -d -f persist/data/saas.db.zst -o data/saas.db
      elif [ -f persist/data/saas.db ]; then
        cp persist/data/saas.db data/saas.db
      fi
    fi
```

- `run: |` — bloque shell multilínea; GH Actions lo ejecuta con `bash -e` (aborta al primer comando que falle sin capturar).
- `mkdir -p data` — asegura que el directorio destino existe. `-p` = idempotente: no falla si ya existe. El checkout de `main` trae `data/` (contiene `.gitignore`), pero el guard cuesta nada y protege contra reorganizaciones futuras.
- `if [ ! -f data/saas.db ]; then` — `[ -f X ]` es el test POSIX "X existe y es archivo regular"; `!` lo niega. Solo restauramos si `actions/cache` (step anterior) NO dejó ya la BD: el cache tiene prioridad porque con cache caliente es instantáneo y ya validado por #22.
- `if [ -f persist/data/saas.db.zst ]; then` — camino preferente: existe el snapshot comprimido en el checkout de la rama `data` (que vive en `persist/` desde #22).
- `zstd -d -f persist/data/saas.db.zst -o data/saas.db` — descompresión:
  - `-d` = decompress (por defecto zstd comprime).
  - `-f` = force: sobrescribe `data/saas.db` si existiera (no puede en esta rama del if, pero hace el comando idempotente y evita el prompt interactivo de confirmación, que en CI colgaría o fallaría).
  - `-o data/saas.db` = ruta de salida explícita. Sin `-o`, zstd escribiría `persist/data/saas.db` (quita la extensión `.zst` en el mismo directorio), que no es donde el pipeline lee.
- `elif [ -f persist/data/saas.db ]; then cp ...` — compat hacia atrás: en el primer run tras el merge la rama `data` aún tiene el plano del 24-jun y ningún `.zst`. `cp` simple, como hacía #22.
- Si no existe ninguno de los dos (rama `data` virgen), no se hace nada y el pipeline arranca en modo CARGA COMPLETA con BD nueva — comportamiento pre-existente, intacto.

### Step modificado: `Persist to data branch`

```yaml
mkdir -p persist/data/runs
if [ -f data/saas.db ]; then
  sqlite3 data/saas.db 'VACUUM;'
  zstd -T0 -15 -f data/saas.db -o persist/data/saas.db.zst
fi
```

- `if [ -f data/saas.db ]` — guard defensivo heredado de #22: si el run no generó BD (edge: `--skip-scrape --skip-ai` manual), no fallamos.
- `sqlite3 data/saas.db 'VACUUM;'` — el CLI `sqlite3` (preinstalado en ubuntu-latest) abre la BD, ejecuta la sentencia SQL `VACUUM` y sale. `VACUUM` reconstruye el archivo completo copiando solo las páginas vivas a un archivo temporal y reemplazando el original: elimina fragmentación y páginas libres (aquí −8 MB). Se hace **sobre `data/saas.db` en el workdir**, antes de comprimir, para que el `.zst` parta del archivo mínimo. Efecto lateral benigno: el `actions/cache` post-step guardará la versión vacuumed (misma data lógica).
- `zstd -T0 -15 -f data/saas.db -o persist/data/saas.db.zst` — compresión:
  - `-T0` = multithreading con tantos hilos como cores detecte (0 = auto). En local usó ~5 cores y tardó 5 s; en el runner (4 vCPU) será comparable. Sin `-T0`, zstd usa 1 hilo (~4-5x más lento a nivel 15).
  - `-15` = nivel de compresión (rango 1-19 estándar; default 3). Nivel 15 es el sweet spot para archivos que se comprimen 1 vez/día y se descomprimen poco: los niveles >15 ganan <1% de ratio a cambio de 3-5x más CPU. La **descompresión** en zstd es igual de rápida a cualquier nivel (~1 s para este archivo), así que el restore no paga el nivel alto.
  - `-f` = sobrescribe el `.zst` del run anterior que el checkout de la rama `data` dejó en `persist/data/` (a partir del segundo run siempre existirá). Sin `-f`, zstd fallaría con "already exists".
  - `-o <path>` = escribe directamente en el clon de la rama `data`; ahorra el `cp` intermedio.

```yaml
cd persist
git config user.email "github-actions[bot]@users.noreply.github.com"
git config user.name "github-actions[bot]"
```

Sin cambios respecto a #22: entrar al clon de la rama `data` y configurar el bot user para el commit.

```yaml
git rm --ignore-unmatch --quiet data/saas.db
```

- `git rm` = borra el archivo del **working tree** y lo stagea como eliminación en el index (equivale a `rm` + `git add` de la eliminación, en un comando atómico).
- `--ignore-unmatch` = exit 0 aunque el pathspec no matchee nada. Crítico para la idempotencia: en el primer run tras el merge `data/saas.db` existe en la rama (se elimina y stagea); del segundo run en adelante ya no existe, y sin este flag `git rm` saldría con código 1 → `bash -e` abortaría el step → el job fallaría cada día.
- `--quiet` = suprime el output `rm 'data/saas.db'` (ruido en el log).
- Al ir **antes** del `git add` y del check `git diff --cached --quiet`, la eliminación entra en el mismo commit que añade el `.zst`: la rama nunca queda en un estado intermedio con ambos archivos o con ninguno.

```yaml
if [ -f data/saas.db.zst ]; then
  git add data/saas.db.zst
fi
git add data/runs/
```

- El guard `[ -f data/saas.db.zst ]` (relativo a `persist/`, porque ya hicimos `cd persist`) evita que `git add` falle con "pathspec did not match" en el edge case de run sin BD (el mismo que protege el `if [ -f data/saas.db ]` de arriba). `git add` con una ruta inexistente devuelve código != 0 y `bash -e` abortaría.
- `git add data/runs/` — sin cambios: los JSON de runs se versionan planos (KBs, legibles en la GH UI, usados por el tuner).
- Se mantienen rutas concretas (no `git add -A`) por la convención del proyecto: evita stagear basura accidental del clon.

```yaml
git diff --cached --quiet || (git commit -m "chore: pipeline run $(date -u +%FT%TZ)" && git push origin data)
```

Sin cambios (idiom de #22: commit/push solo si hay algo staged). Nota: la eliminación stageada por `git rm` **sí** cuenta como cambio para `git diff --cached`, así que el commit de transición plano→zst se dispara aunque el resto no cambiara.

### Step nuevo en `tuner.yml`: `Restore saas.db from data branch`

```yaml
- name: Restore saas.db from data branch
  run: |
    if [ ! -f persist/data/saas.db ]; then
      if [ -f persist/data/saas.db.zst ]; then
        zstd -d -f persist/data/saas.db.zst -o persist/data/saas.db
      fi
    fi
```

- Colocado justo después de `Checkout data branch` (que puebla `persist/`) y antes de `Setup Python`: `zstd` no necesita Python y así la BD está lista antes de cualquier step que pudiera consumirla.
- `if [ ! -f persist/data/saas.db ]; then` — guard exterior: si el plano **ya existe** en la rama (estado actual, pre-migración), no se hace nada y el tuner lo usa tal cual. Es el equivalente al `elif` de compat de `pipeline.yml`, pero invertido: aquí el "fallback al plano" es el caso por defecto porque el destino de la descompresión ES la ruta del plano.
- `if [ -f persist/data/saas.db.zst ]; then` — guard interior: solo descomprime si el snapshot comprimido existe. Si no existe ninguno de los dos (rama `data` virgen), el step no hace nada; el tuner CLI ya maneja BD ausente/vacía por su cuenta (mismo contrato que antes de esta feature: el archivo tampoco existía si la rama estaba vacía).
- `zstd -d -f persist/data/saas.db.zst -o persist/data/saas.db` — mismos flags que el restore de `pipeline.yml` (`-d` descomprime, `-f` fuerza sobrescritura/no-interactividad en CI, `-o` fija el destino), pero con **destino dentro de `persist/`**: se materializa el plano exactamente donde los dos `--db-path` (líneas 67 y 92 tras el cambio) lo esperan, sin tocar esos comandos.
- Por qué no hay `mkdir -p`: `persist/data/` existe garantizado — o bien el checkout de la rama `data` lo trae (contiene `runs/` y/o el `.zst`), o bien no hay nada que descomprimir y el step es no-op.
- Nota: el `persist/data/saas.db` descomprimido deja sucio el working tree del clon de `data`, pero `tuner.yml` **no** hace commit/push a esa rama (a diferencia de `pipeline.yml`), así que no hay riesgo de re-subir el blob de 90 MB accidentalmente.

## Tamaño real medido (acceptance 6)

Sobre la copia de la BD extraída de `origin/data` (`git show origin/data:data/saas.db`, snapshot del 24-jun):

| Etapa | Bytes | Tamaño |
|---|---|---|
| Original (`origin/data`) | 102.912.000 | 98,1 MiB |
| Tras `VACUUM` | 94.412.800 | 90,0 MiB |
| Tras `zstd -T0 -15` | **23.108.252** | **22,0 MiB** |

- Ratio: 24,5% del archivo vacuumed (~4,1x); 22,5% del original (~4,5x).
- Tiempo de compresión: 5,0 s wall (`-T0`, ~489% CPU en 6 cores).
- Roundtrip verificado: `zstd -d` produce un archivo **byte-idéntico** (`cmp` OK), `PRAGMA integrity_check` = `ok`, `SELECT COUNT(*) FROM reddit_posts` = 27.718 (esperado).
- Muy por debajo del umbral de 50 MB del acceptance. Margen de crecimiento: el límite de 100 MB del `.zst` se alcanzaría con una BD plana de ~450 MB (≈4,5 años al ritmo actual de ~20 MB/mes).

## Nuevo flujo de sincronización local

Tras el primer run verde post-merge, la rama `data` contiene `data/saas.db.zst` (y ya no `data/saas.db`). Para traer la BD al workdir local:

```bash
git fetch origin data
git checkout origin/data -- data/saas.db.zst data/runs/
zstd -d -f data/saas.db.zst -o data/saas.db
```

1. `git fetch origin data` — actualiza `refs/remotes/origin/data` sin tocar la rama actual.
2. `git checkout origin/data -- data/saas.db.zst data/runs/` — extrae solo esos paths al workdir, sin cambiar HEAD (mismo patrón que documentó #22).
3. `zstd -d -f data/saas.db.zst -o data/saas.db` — descomprime encima de la BD local (`-f` sobrescribe; `-o` fija el destino). La descompresión tarda ~1 s.

Opcional: `rm data/saas.db.zst` después (está ignorado por gitignore de `data/` en main, pero ocupa 22 MB). **Mientras la rama `data` no tenga aún el `.zst`** (antes del primer run post-merge), sigue funcionando el flujo antiguo de #22 (`git checkout origin/data -- data/saas.db data/runs/`).

## Tests añadidos

Ninguno automatizado — **limitación conocida y aceptada por el acceptance 8**: los workflows de GitHub Actions solo se validan de verdad ejecutándose en GH Actions (no hay mock local fiable de `actions/checkout` + `actions/cache` + runner). Verificación local realizada en su lugar:

1. **Sintaxis YAML**: `yaml.safe_load` sobre `pipeline.yml` **y** `tuner.yml` → ambos parsean sin excepción; los 11 steps de cada uno listados en orden correcto (ver sección Verificación).
2. **Lógica shell**: el pipeline VACUUM→zstd→roundtrip se ejecutó literalmente en local sobre la BD real de `origin/data` (tabla de arriba), incluyendo los flags exactos del workflow (`-T0 -15 -f -o` / `-d -f -o`).
3. **Suite completa**: `./.venv/bin/pytest -q` → exit 0 (563 pass, 4 skip) — confirmado que nada de `src/`/`tests/` se ve afectado.

### Plan de verificación manual (post-merge)

1. `gh workflow run 'saas-radar pipeline'` (workflow_dispatch).
2. Run **verde** de punta a punta (incluido `Persist to data branch`, que fallaba desde el 26-jun).
3. `git fetch origin data && git ls-tree --name-only origin/data data/` debe mostrar `data/saas.db.zst` y `data/runs/`, y **NO** `data/saas.db`.
4. Commit nuevo en `origin/data` con timestamp de hoy: `git log origin/data -1`.
5. Descomprimir localmente y comprobar `PRAGMA integrity_check` + counts.
6. **Tuner**: tras ese run del pipeline, el `tuner.yml` que se dispara por `workflow_run` debe terminar verde con la rama `data` ya en formato `.zst` (su step de restore descomprime; verificar en el log del job que `Run tuner (dry-run)` no falla por BD ausente).

### Riesgo detectado durante la implementación (resuelto en esta misma feature)

**Confirmado** con evidencia: `.github/workflows/tuner.yml` pasaba `--db-path persist/data/saas.db` (plano, desde su propio checkout de la rama `data`) en sus dos invocaciones. Tras el primer push con `.zst`, ese archivo habría desaparecido del árbol y el tuner habría operado contra BD inexistente — repitiendo el patrón de la regresión `8409bb9`. Inicialmente lo anoté como riesgo fuera de scope; el leader amplió el acceptance de la feature y el fix (step de restore en `tuner.yml`, ver arriba) quedó incluido en esta misma rama.

## Verificación

```
$ ./.venv/bin/python -c "..."  # yaml.safe_load sobre ambos workflows
.github/workflows/pipeline.yml -> OK; steps: ['Checkout main', 'Checkout data branch', 'Restore saas.db from cache', 'Restore saas.db from data branch', 'Setup Python 3.11', 'Install dependencies', 'Download NLTK stopwords', 'Prepare data directories', 'Run pipeline', 'Persist to data branch', 'Upload run outputs']
.github/workflows/tuner.yml -> OK; steps: ['Checkout main', 'Checkout data branch', 'Restore saas.db from data branch', 'Setup Python', 'Install dependencies', 'Download NLTK stopwords', 'Check Telegram secrets', 'Run tuner (dry-run)', 'Upload tuner report artifact', 'Send Telegram report', 'Run tuner (apply PR)']

$ ./.venv/bin/pytest -q >/dev/null 2>&1; echo "exit=$?"
exit=0

$ ./init.sh
...
── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
(exit 0)
```
