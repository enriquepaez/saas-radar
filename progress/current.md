# Sesión actual

> Este archivo se vacía al cerrar cada sesión y se mueve a `history.md`.
> Mientras trabajas, **mantenlo actualizado en tiempo real**, no al final.

## Plan de continuación (escrito 2026-07-04 al cierre del milestone M6)

No hay feature en curso. M6 completo: #26-#29 done (PRs #39-#42 mergeadas).
La próxima sesión debe empezar por aquí:

### 1. Verificar el cron post-M6 (a partir del 5-jul)

Sobre el run diario más reciente (`gh run list --workflow=pipeline.yml`):

- [ ] Run verde y el log muestra restore **desde la release `db-latest`**
      (primera vez que se ejercita ese camino en el cron).
- [ ] `meta_recommendations` > 0 filas en la BD de la release (verificar
      descargando: `gh release download db-latest -p saas.db.zst`) — efecto
      del fix #28, primera vez en la historia del proyecto.
- [ ] El log muestra la fase 4 (META-ANALISIS) y la fase 4.5 (sugerencias
      heurísticas LLM).
- [ ] Las opportunities tienen `discarded=0` (backfill #27) y el agente GTM
      las procesa (filas en `opportunity_gtm`).
- [ ] El snapshot `db-YYYYMMDD` del día incluye `runs.tar.gz` con contenido
      (results + meta JSON), no vacío.
- [ ] Ninguna alerta Telegram de fallo (o si la hay, investigar).

### 2. Tras ~5-7 runs verdes (hacia el 11-jul)

- [ ] Borrar la rama `data` congelada: `git push origin --delete data`
      (recupera ~1,25 GB de repo). Quitar entonces el step de checkout
      fallback de `pipeline.yml`/`tuner.yml` (feature pequeña).
- [ ] Vigilar el tuner: con `meta_recommendations` poblándose y recurrence
      acumulando, el modo PR (#20) podría abrir su primera PR automática
      `chore/auto-tuning-*`. Revisarla con calma la primera vez.

### 3. Retomar el roadmap estratégico (`docs/improvement_roadmap.md`)

La espera de "recabar más información en la BBDD" es la **Fase 0** del
roadmap. Con 5-7 runs frescos: repetir el análisis de señal sobre la BD de
la release y ejecutar la Fase 1 (cirugía de config) como nueva feature.

⚠️ **Numeración**: el roadmap llama "F26-F30" a sus fases futuras, pero esos
ids ya fueron usados por el milestone M6. Al registrarlas en
`feature_list.json`, usar **#30 en adelante** (ver nota en el roadmap).

### Contexto útil

- BD de producción: release `db-latest` (~24,8 MB zstd, ~101 MB real,
  30.216 posts a 4-jul). Sincronización local:
  `gh release download db-latest -p saas.db.zst -O data/saas.db.zst --clobber && zstd -d -f data/saas.db.zst -o data/saas.db`
- Historia detallada del M6: `progress/history.md` (4 entradas del 2026-07-04).
