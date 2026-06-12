# Review: Feature #22 — pipeline_persistence_restoration

## Veredicto
APPROVED

## Acceptance checklist

A1. `.github/workflows/pipeline.yml` hace checkout dual: ref main + ref data en path persist/  
    PASS — `Checkout main` con `ref: main` (líneas 38-42) y `Checkout data branch` con `ref: data`, `path: persist` (líneas 44-51).

A2. `permissions` del job incluye `contents: write` (no solo read)  
    PASS — línea 18-19: `permissions: contents: write` a nivel workflow, heredado por el job.

A3. Tras el step "Run pipeline" existe un step "Persist to data branch" que copia data/saas.db y data/runs/ a persist/, configura git user.email y user.name, git add, git diff --cached --quiet (skip si sin cambios), git commit con timestamp, git push origin data  
    PASS — líneas 83-97. Orden correcto: `mkdir -p persist/data/runs`, copia condicional de `data/saas.db` (línea 87-89) y `data/runs/.` (línea 90-92), `cd persist`, `git config user.email/user.name` (94-95), `git add data/saas.db data/runs/` (96), y `git diff --cached --quiet || (git commit -m "chore: pipeline run $(date -u +%FT%TZ)" && git push origin data)` (línea 97). Mensaje de commit con timestamp ISO-8601 UTC correcto.

A4. El push usa GITHUB_TOKEN del workflow (no PAT)  
    PASS — línea 50: `token: ${{ secrets.GITHUB_TOKEN }}` en el checkout de `data`, combinado con `persist-credentials: true` (línea 51). No se referencia ningún PAT en el workflow.

A5. Concurrency "saas-radar" se mantiene; cancel-in-progress: false  
    PASS — líneas 14-16 sin cambios: `group: 'saas-radar'`, `cancel-in-progress: false`.

A6. actions/cache se mantiene como aceleración (no se elimina, solo se complementa con el push)  
    PASS — líneas 53-58: `Restore saas.db from cache` con `actions/cache@v4`, key `saas-db-${{ github.run_id }}`, `restore-keys: saas-db-`. Intacto.

A7. Documentar en progress/impl_pipeline_persistence_restoration.md cómo el usuario sincroniza la BD local tras un run del cron: `git fetch origin data && git checkout origin/data -- data/saas.db data/runs/`  
    PASS — sección "Como sincronizar la BD local tras un run del cron" (líneas 140-155 del impl.md) documenta exactamente esos comandos, incluyendo el aviso de NO hacer `git checkout data` ni `git pull origin data`.

A8. Verificación manual: tras 1 cron real (o 1 workflow_dispatch), origin/data tiene commit nuevo con timestamp de hoy y data/saas.db actualizado  
    PASS (diferida) — esta verificación solo es posible tras mergear la PR y disparar un run real en GitHub Actions. El impl.md lo declara explícitamente como "verificación en runtime tras mergear la PR". Aceptable según el propio acceptance (es una verificación manual, no automatizada).

A9. NO hay tests automatizados — los workflows YAML solo se validan en GitHub Actions. Documentar la limitación en progress/impl_*.md  
    PASS — sección "Tests anadidos" (líneas 157-171 del impl.md) documenta la limitación, justifica el por qué (no existe mock local fiable para checkout/cache/runners), y enumera las alternativas (validación sintáctica YAML local, actionlint opcional, verificación manual en GH).

## Items adicionales (B-G)

B. Sintaxis YAML  
   PASS — `./.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/pipeline.yml'))"` exit 0. Lo mismo para `tuner.yml`. Ambos parsean limpiamente.

C. Coherencia con conventions.md y CLAUDE.md  
   PASS — `git diff main..HEAD --stat -- src/ tests/` devuelve vacío: el implementer no tocó código de producción ni tests. La feature #22 está en `in_progress` (no `done`); el cierre corresponde al leader. El impl.md cumple el formato pedido (qué/por qué/impacto/explicación línea a línea), incluyendo justificación de elecciones técnicas como `cp -r ... data/runs/.` (con el punto), `git diff --cached --quiet || (...)`, `persist-credentials: true`, etc.

D. Seguridad  
   PASS — el push usa `secrets.GITHUB_TOKEN` (token efímero del job, no PAT). `permissions: contents: write` es el alcance mínimo necesario para escribir a la rama `data` del mismo repo. No se eleva a `pull-requests: write` ni se añaden otros scopes — está bien acotado. El bot user de commit (`github-actions[bot]@users.noreply.github.com`) es el convencional y no asocia commits a humanos reales.

E. Casos edge  
   PASS — los guards defensivos están: 
   - `if [ -f data/saas.db ]; then cp ...` protege el caso en que el pipeline no genere la BD.
   - `if [ -d data/runs ]; then cp -r ...` protege el caso de directorio ausente.
   - `git diff --cached --quiet || (...)` evita commits vacíos cuando un run no produjo deltas.
   - `if: success()` evita persistir estado parcial si el pipeline crashea.
   - Rama `data` ya existe (último commit `3d7380a chore: pipeline run 2026-05-30T12:54:15Z` confirmado en `audit_cron_state.md`), por lo que el checkout no falla.
   - Race con tuner.yml: revisado `tuner.yml`, su único checkout de `data` es read-only (no hay step que escriba/push a la rama `data`). No hay race condition por escritura. Además, `concurrency` separa los workflows en grupos distintos.

F. Coherencia conceptual con legacy lessons-learned §1.12  
   PASS — el patrón replicado es exactamente el dual-checkout + run + commit/push a `data` que describe el legacy. El impl.md cita expresamente la lección (líneas 22-27, 152). La diferencia frente al legacy es que aquí se conserva además `actions/cache` como complemento — decisión razonable y documentada (acelera arranque sin sustituir la persistencia).

G. Sin regresiones aparentes  
   PASS — preservados sin tocar: `actions/cache@v4` (líneas 53-58), `actions/upload-artifact@v4` con `retention-days: 30` y `if-no-files-found: ignore` (líneas 99-106), todas las env vars (`REDDIT_*`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_*`, `AI_PROVIDER`, `EXTRACTION_PROVIDER`, líneas 26-35), `concurrency.group` y `cancel-in-progress` (14-16), cron schedule `'0 8 * * *'` (5), `workflow_dispatch` con input `full_scan` (6-12), step `Run pipeline` con condicional `--full-scan` (75-81).

## Notas

- El step de persistencia hace `cd persist` antes de los `git`, lo que aísla las operaciones del clon `main`. Bien.
- `persist-credentials: true` en el segundo checkout es necesario para que el `git push` posterior reuse el token automáticamente; el impl.md lo explica.
- La ausencia de `actionlint` como pre-commit está flaggeada como mejora futura — no bloqueante para esta feature.
- Pendiente de validación en runtime: tras mergear la PR, el siguiente cron (o `workflow_dispatch`) debe producir un nuevo commit en `origin/data` con timestamp de la hora del run. El usuario sincroniza con `git fetch origin data && git checkout origin/data -- data/saas.db data/runs/`.
- `init.sh` termina verde. No hay cambios en `src/` ni `tests/` que pudieran romper la suite (no se ejecutó pytest porque no aplica — la feature es 100% YAML/docs).
