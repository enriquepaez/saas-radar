# Exploración runtime: ¿se ejecuta la fase 4 (meta-análisis) en los runs reales?

**Fecha:** 2026-07-04 · **Feature:** #28 · **Fuentes:** BD de producción (release `db-20260704`, copia en scratchpad `rel.db`), logs de GitHub Actions (`pipeline.yml`), código en `main`.

## Respuesta corta

**No. El meta-análisis nunca se ejecuta en producción — ni en GitHub Actions ni en ningún run.** No falla ni se salta con warning: **nadie lo invoca**. `generate_meta_analysis` / `save_meta_analysis` (`src/saas_radar/analysis/meta_analysis.py`) solo tienen callers en `tests/test_meta_analysis.py`. `run_ai_analysis` (`src/saas_radar/analysis/ai_analyzer.py`) no las llama en ningún punto de su flujo (su docstring de flujo, líneas 148-155, ni menciona meta-análisis). Por eso `meta_recommendations` tiene 0 filas tras 27 runs.

Efecto cascada: la **fase 4.5 (heuristic tuner) tampoco se ejecuta nunca**, porque `main.py:264` busca `data/runs/*_meta.json` y ese archivo solo lo escribiría `save_meta_analysis`. Al no existir, `meta_json_path` queda `None` y el bloque de la línea 271 no entra. Silenciosamente: no imprime nada.

## Evidencia 1 — Logs de GitHub Actions

`gh run list --workflow=pipeline.yml --limit 5`: los 3 runs del 2026-07-04 en verde (28703615830, 28703083871, 28701912101); los 2 anteriores (03-jul, 02-jul) en `failure`.

Run **28703615830** (workflow_dispatch, success, el más reciente) — `grep -i 'meta|FASE|recommendation|heuristic'` sobre el log completo:

```
10:46:16 -- FASE 1: Scraping de subreddits
10:46:16 -- FASE 2: Busqueda por keywords de dolor
10:46:16 -- FASE 3: Comentarios (posts con >=100 comentarios)
11:00:07 -- FASE 5: GTM agent (opps canonicas pendientes)
```

Run **28703083871** (schedule, success) — mismo grep:

```
10:22:55 -- FASE 1: Scraping de subreddits
10:22:55 -- FASE 2: Busqueda por keywords de dolor
10:22:55 -- FASE 3: Comentarios (posts con >=100 comentarios)
10:25:34   RESULTADOS DE ANÁLISIS IA
10:25:34 INFO saas_radar.analysis.ai_analyzer: Resultados guardados en data/ai_analysis.json/20260704_1...
10:25:34 -- FASE 5: GTM agent (opps canonicas pendientes)
```

Conclusiones de los logs:

- La **fase 4 de análisis IA sí corre** (extracción con Groq + síntesis; imprime `RESULTADOS DE ANÁLISIS IA` y guarda `..._results.json`), pero **no incluye meta-análisis**: no aparece `META-ANALISIS DEL RUN` (cabecera de `print_meta_summary`), ni ningún log de `meta_analysis.py` (`Meta-análisis guardado en ...`).
- **No aparece nunca `-- FASE 4.5: Sugerencias heurísticas LLM`** ni el warning `Fase 4.5 heuristic_tuner falló` — el pipeline salta de fase 3/IA directamente a FASE 5, sin mensaje alguno.
- Sin errores ni warnings relacionados con meta: la fase no falla, simplemente no existe en el flujo.

## Evidencia 2 — BD de producción

```sql
SELECT COUNT(*) FROM analysis_runs;          -- 27
SELECT COUNT(*) FROM meta_recommendations;   -- 0
```

`analysis_runs` completa (resumen): runs 1-18 `failed` con Gemini (`LLM devolvió None en síntesis`; run 1: `Solo 0 extraccion(es) válida(s)`); runs 19-27 con Groq: 19 y 23 `ok` (1 opp cada uno), 20-22/24-26 `partial`, 27 `failed` (síntesis None). **Incluso en los 2 runs `ok` y los 6 `partial`, `meta_recommendations` quedó a 0** — descarta que sea un problema de runs fallidos: con extracciones válidas (34-61 por run) el meta-análisis habría tenido material de sobra.

`json_path` de los runs recientes confirma dónde van los resultados reales:

```sql
SELECT id, json_path FROM analysis_runs ORDER BY id DESC LIMIT 3;
-- 27 | (vacío, síntesis falló)
-- 26 | data/ai_analysis.json/20260704_102255_results.json
-- 25 | data/ai_analysis.json/20260704_093316_results.json
```

Nota: `main.py` pasa `output="data/ai_analysis.json"` y `_save_results` lo trata como **directorio** (`ai_analyzer.py:126-128`), por lo que los results acaban en `data/ai_analysis.json/<ts>_results.json` y **`data/runs/` queda siempre vacío** (solo lo crea el `mkdir -p data/runs` del workflow, línea 91 de `pipeline.yml`). Esto agrava el bug de la 4.5: aunque alguien llamara a `save_meta_analysis` con el `json_path` real, el meta JSON caería en `data/ai_analysis.json/`, y el glob de `main.py:264` (`data/runs/*_meta.json`) seguiría sin encontrarlo.

Señales que las reglas del meta-análisis sí habrían explotado (los datos existen, solo falta ejecutar la fase):

```sql
SELECT COUNT(*) FROM reddit_posts WHERE search_query IS NOT NULL AND search_query != '';
-- 3986 posts descubiertos vía pain_search

SELECT subreddit, COUNT(*) n FROM reddit_posts
WHERE search_query IS NOT NULL AND search_query != ''
GROUP BY subreddit ORDER BY n DESC LIMIT 5;
-- sideproject 617 | saas 522 | sysadmin 339 | smallbusiness 321 | legaladvice 296
```

Los 38 subreddits distintos de la BD están todos dentro de `config.SUBREDDITS` actual (los "descubiertos" legaladvice/marketing/productivity ya fueron incorporados manualmente en el pasado, según comentario en `config.py:154-157`), pero `_find_discovered_subreddits` y `_find_empty_queries` habrían generado señal en cada run igualmente.

## Evidencia 3 — Artifacts / release

`runs.tar.gz` de la release `db-20260704` (descargado como `runs_snap.tar.gz` en scratchpad): contiene únicamente la entrada `data/runs/` (directorio vacío, 117 bytes). **Ningún `<ts>_meta.json` existe** — coherente con que `save_meta_analysis` jamás se ejecuta y con que los results van a `data/ai_analysis.json/` (que en el último run tampoco se creó porque la síntesis del run 27 falló antes de `_save_results`).

## Diagnóstico final

| Pregunta | Respuesta |
|---|---|
| ¿Se ejecuta la fase 4 (meta-análisis) en los runs reales? | **No, nunca.** No hay llamada a `generate_meta_analysis`/`save_meta_analysis` fuera de tests. |
| ¿Qué imprime? | Nada. No hay cabecera de fase ni warning; el log salta del análisis IA a FASE 5. |
| ¿Genera el meta JSON? | No. `data/runs/` está vacío en la release; ningún `*_meta.json` en ningún sitio. |
| ¿Errores/warnings visibles? | Ninguno relacionado con meta. Es **código muerto en producción** (dead code), no un fallo en runtime. |
| Efecto colateral | Fase 4.5 (heuristic tuner) tampoco corre nunca (`meta_json_path` siempre `None`), y el tuner determinista (`agents/tuner.py`) trabaja sobre una tabla vacía. |

**Fix que necesita la feature #28:** invocar `generate_meta_analysis` + `save_meta_analysis(meta, json_path, run_id, db_url)` al final de `run_ai_analysis` (o desde `main.py` tras la fase IA) pasando el `run_id` persistido, y unificar la ruta de los JSON (o el glob de `main.py:264`) para que la fase 4.5 encuentre el `_meta.json`.
