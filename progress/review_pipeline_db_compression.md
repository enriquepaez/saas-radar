# Review — feature #26 `pipeline_db_compression`

**Veredicto:** APROBADO

**Rama:** `feat/26-pipeline_db_compression` (working tree sin commitear)
**Fecha:** 2026-07-04
**Archivos revisados:** `.github/workflows/pipeline.yml`, `.github/workflows/tuner.yml`,
`feature_list.json`, `progress/current.md`, `progress/impl_pipeline_db_compression.md`

## Acceptance criteria (9/9 cumplidos)

| # | Criterio | Veredicto | Evidencia |
|---|---|---|---|
| 1 | Persist: VACUUM + zstd nivel alto `-T0` → `persist/data/saas.db.zst` | ✅ | `pipeline.yml:95-96`: `sqlite3 data/saas.db 'VACUUM;'` seguido de `zstd -T0 -15 -f data/saas.db -o persist/data/saas.db.zst`, dentro del guard `[ -f data/saas.db ]` |
| 2 | `git rm` del plano en el mismo commit que añade el `.zst` | ✅ | `pipeline.yml:104`: `git rm --ignore-unmatch --quiet data/saas.db` va **antes** de `git add` (106,108) y del check `git diff --cached --quiet` (109) → una sola transición atómica plano→zst. `--ignore-unmatch` hace el step idempotente del 2.º run en adelante (sin él, `bash -e` abortaría) |
| 3 | Restore: `.zst` → descomprimir; solo plano → copiar | ✅ | `pipeline.yml:56-65`: nuevo step con `if [ -f persist/data/saas.db.zst ]` → `zstd -d -f ... -o data/saas.db`, `elif [ -f persist/data/saas.db ]` → `cp`. Solo actúa si el cache no restauró (`[ ! -f data/saas.db ]`), preservando la prioridad del cache de #22 |
| 4 | `data/runs/` sin comprimir; artefacto `run-outputs` intacto | ✅ | `pipeline.yml:98-100` (cp -r sin cambios) y `pipeline.yml:111-118` (upload-artifact idéntico) |
| 5 | `concurrency` y `actions/cache` intactos | ✅ | `pipeline.yml:14-16` (`saas-radar`, `cancel-in-progress: false`) y `pipeline.yml:49-54` (`actions/cache@v4`, misma key/restore-keys) sin tocar |
| 6 | Tamaño real del `.zst` documentado (< 50 MB) | ✅ | `impl_pipeline_db_compression.md` §"Tamaño real medido": 98,1 MB → VACUUM 90,0 MB → **22,0 MB** (23.108.252 bytes), roundtrip byte-idéntico + `integrity_check` ok |
| 7 | Flujo de sincronización local documentado | ✅ | `impl_...md` §"Nuevo flujo": `git fetch origin data && git checkout origin/data -- data/saas.db.zst && zstd -d -f ...`, con nota de compat pre-migración |
| 8 | Limitación de tests + plan de verificación manual documentados | ✅ | `impl_...md` §"Tests añadidos" y §"Plan de verificación manual" (workflow_dispatch verde + `git ls-tree origin/data` debe mostrar `.zst` y NO el plano) |
| 9 | `tuner.yml` adaptado con fallback al plano | ✅ | `tuner.yml:33-39`: nuevo step antes de Setup Python; si el plano no existe y el `.zst` sí, descomprime a `persist/data/saas.db` (la ruta que los dos `--db-path` de las líneas 67 y 92 ya esperan, sin tocarlos). Si el plano existe (pre-migración), no-op → fallback implícito correcto. Evita la regresión tipo `8409bb9` |

## Verificaciones ejecutadas

1. **Sintaxis YAML**: `yaml.safe_load` sobre ambos workflows → OK (11 steps cada uno, orden correcto).
2. **Sintaxis shell**: `bash -n` sobre los 3 bloques `run:` modificados/nuevos → OK (condicionales bien formados, `fi` correctos).
3. **Simulación funcional** (sandbox con sqlite de juguete + repo git falso):
   - Caso A (1.er run post-merge, rama con plano): restore copia el plano; persist genera `.zst`, y el árbol tras el commit contiene `data/saas.db.zst` + `data/runs/` y **NO** `data/saas.db` → el push dejará de ser rechazado.
   - Caso B (estacionario, rama con `.zst`): restore descomprime y los datos son íntegros; `git rm --ignore-unmatch` sin plano no aborta (idempotencia confirmada).
   - Caso C (tuner, pre y post migración): en ambos escenarios `persist/data/saas.db` acaba existiendo con datos íntegros.
4. **Flags zstd correctos**: `-d` en las descompresiones, `-f` en todas las escrituras (evita fallo "already exists" del 2.º run y el prompt interactivo en CI), `-o` con destino explícito (sin él, zstd escribiría en el directorio origen).
5. **Rutas correctas por working directory**: restore de pipeline corre desde la raíz del repo (rutas `persist/...` → `data/...` OK); el bloque persist hace `cd persist` y a partir de ahí usa rutas relativas al clon (`data/saas.db.zst`) — coherente.
6. **`./.venv/bin/pytest -q`** → exit 0.
7. **`./init.sh`** → exit 0, `[OK] Entorno listo`.
8. **Alcance limpio**: `git status` solo muestra los 2 workflows, `feature_list.json`, `progress/current.md` y el nuevo `progress/impl_...md`. **Nada en `src/` ni `tests/`**; `data/saas.db` intacta (mtime 2026-05-30, anterior a la sesión).
9. **`feature_list.json`**: #26 en `in_progress` (NO `done`); las adiciones (M6, #27, #28) son registro de backlog del leader, coherentes con las observaciones fuera de scope de `current.md`.

## Checkpoints (CHECKPOINTS.md)

- C1 (arnés completo, init.sh verde): [x]
- C2 (estado coherente; una sola feature `in_progress`): [x]
- C3 (arquitectura; `src/` sin cambios en esta feature): [x]
- C4 (verificación real; suite > 0 tests, toda verde): [x]
- C5 (BD heredada intacta y funcional): [x]
- C6 (cierre de sesión): [ ] — pendiente por diseño: el cierre (history.md, `done`, commit) ocurre tras esta review, no antes.

## Observaciones (no bloqueantes)

- El blob de 99 MB permanece en la **historia** de la rama `data` (los commits antiguos). No viola el límite (el hook solo rechaza blobs nuevos) y el workflow usa `fetch-depth: 1`; documentado correctamente en el impl doc con `git filter-repo` como opción futura fuera de scope.
- La verificación definitiva es necesariamente post-merge (workflow_dispatch); el plan manual del impl doc §"Plan de verificación manual" es el correcto y debe ejecutarse tras mergear.
