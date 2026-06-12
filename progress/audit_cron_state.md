# Audit: Estado del cron de GitHub Actions

Generado por Explore (read-only) + verificación directa del leader. Fecha: 2026-06-12.

## Diagnóstico (CONFIANZA ALTA)

El workflow `pipeline.yml` ejecuta correctamente cada día en cron (`0 8 * * *`) y los runs aparecen como `success` desde el 2026-06-01 hasta hoy. **PERO** ningún dato generado por el cron baja al repositorio local porque:

1. El workflow guarda `data/saas.db` solo en `actions/cache` (key dinámico por `run_id`, restore-keys `saas-db-`). El cache no es persistente: GH puede evictarlo y no produce historia.
2. Los outputs (`data/runs/`) se suben como `upload-artifact` con `retention-days: 30` — no se commitean.
3. **No hay paso `git commit / git push` a la rama `data`**. La rama remota `origin/data` tiene como último commit `3d7380a chore: pipeline run 2026-05-30T12:54:15Z` (13 días sin actualizar).

Consecuencia: el `data/saas.db` local del usuario refleja un estado anterior al 2026-05-30 + lo que el usuario haya scrapeado en local manualmente. Los `analysis_runs` de la BD local terminan en id=12 (30-mayo failed). Los runs success del cron del 1-jun en adelante están en cache de GH Actions y eventualmente se perderán sin dejar traza estructurada.

## Historial reciente del workflow (gh run list)

| Fecha (UTC) | Workflow | Conclusión | Duración | Trigger |
|---|---|---|---|---|
| 2026-06-12 11:48 | pipeline | success | 21m30s | schedule |
| 2026-06-12 12:10 | tuner | success | 36s | workflow_run |
| 2026-06-11 12:14 | pipeline | success | 21m24s | schedule |
| 2026-06-11 12:36 | tuner | success | 28s | workflow_run |
| 2026-06-10 11:43 | pipeline | success | 20m52s | schedule |
| 2026-06-09 11:23 | pipeline | success | 21m31s | schedule |
| 2026-06-08 12:39 | pipeline | success | 21m58s | schedule |
| ... (todos success entre 1-jun y 12-jun) | | | | |
| 2026-06-01 13:47 | pipeline | failure | ~22m | schedule |
| 2026-05-31 11:33 | pipeline | failure | ~5m | schedule |
| 2026-05-31 11:08 | pipeline | failure | ~5m | schedule |
| 2026-05-31 10:24 | pipeline | success | ~40m | schedule |

Los 3 fails de mayo-junio fueron: `TypeError: Object of type int64 is not JSON serializable` (fixed por commit 4759395 el 01-jun) y rate-limit Groq 429.

## Hipótesis evaluadas

| # | Hipótesis | Resultado | Evidencia |
|---|---|---|---|
| 1 | GitHub suspendió cron por inactividad (>60d) | FALSO | Runs `success` diarios en `gh run list` |
| 2 | Workflow falla silenciosamente | FALSO en el sentido literal — los runs salen `success`. CIERTO en sentido funcional: los datos del cron no llegan al usuario porque el workflow no persiste a `data` |
| 3 | Error de sintaxis YAML / condition skip | FALSO | YAML válido y los jobs corren |
| 4 | Falta secret crítico | FALSO | Logs no muestran errores de auth en steps recientes |
| 5 | Rama `data` corrupta | FALSO | Rama existe; simplemente nadie escribe en ella desde 30-mayo |

## Acciones de reactivación propuestas (sin ejecutarlas)

1. **CRÍTICO** — Añadir al final de `pipeline.yml` un step que haga `git checkout data` (dual checkout) y `git add data/saas.db data/runs/ && git commit -m "chore: pipeline run $(date -u +%FT%TZ)" && git push origin data`. Mismo patrón que el legacy (ver `docs/legacy-context/lessons-learned.md §1.12`) y que el feature 16 documentaba en su acceptance.

2. **CRÍTICO** — Verificar tras el primer push exitoso que el usuario puede `git fetch origin data && git checkout origin/data -- data/saas.db` para traer la BD actualizada.

3. **IMPORTANTE** — Verificar `tuner.yml`: si su rol original era commitear a `data` (depende del legacy), confirmar/corregir. En el legacy era `pipeline.yml` quien lo hacía.

4. **OPCIONAL** — Añadir guard: si `data/saas.db` no cambió respecto al check-in anterior, no hacer push (evitar commits ruidosos).

5. **OPCIONAL** — Documentar en README qué pasos manuales hace el usuario para sincronizar la BD local con la rama `data` (`git fetch && git checkout origin/data -- data/saas.db`).

## Riesgos abiertos

- **BD local desactualizada**: cualquier análisis local (incluido el que el leader hizo en esta sesión) parte de datos del 30-mayo + lo que el usuario haya corrido localmente. El diagnóstico de "30 queries con yield 0" puede estar sesgado por la sincronización ausente.
- **Cache de GH Actions evictable**: las queries `restore-keys: saas-db-` recuperan el cache más reciente que coincida con prefijo. Si GH evicta por LRU (los caches viven ≤7d por defecto sin acceso), el cron arrancaría con BD vacía y produciría falsos negativos en el dedup (todas las opps como nuevas).
- **Tuner no actúa**: aunque el cron del tuner corre, sus `meta_recommendations` se persisten en la BD del runner, que no se sube. Por eso los `boost_subreddit indiehackers` con `recurrence=8` que vemos en la BD local son históricos pre-30-mayo, no actualizados.

## Inferencia adicional (leader)

El feature #16 (`github_actions_pipeline_workflow`) en `feature_list.json` lista como acceptance: *"Commit a rama data solo si hay cambios (git diff --cached --quiet)"*. El workflow actual NO cumple ese acceptance. Posibilidades:
- (a) El workflow original cumplía y un commit posterior simplificó a `actions/cache` sin notar la regresión.
- (b) El acceptance se cerró sin verificarlo en su día.

Para confirmar (a) vs (b), revisar `git log -- .github/workflows/pipeline.yml` y buscar el commit que introdujo `actions/cache`.
