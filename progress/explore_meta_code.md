# Exploración feature #28 — `meta_recommendations` con 0 filas tras 27 runs

## TL;DR (causa raíz)
**La fase de meta-análisis (fase 4) NUNCA se ejecuta en el pipeline.** Ni
`generate_meta_analysis` ni `save_meta_analysis` (los únicos que producen el
`*_meta.json` y llaman a `persist_meta_recommendations`) tienen ningún llamador en
`src/`. Solo se invocan desde tests. Como consecuencia, **ninguno de los dos caminos
de inserción en `meta_recommendations` llega a ejecutarse jamás** en un run real.

---

## Los dos únicos caminos de inserción en `meta_recommendations`

### Camino A — determinista (`persist_meta_recommendations`)
- Definido en `src/saas_radar/storage/db.py:420`.
- Único llamador de producción: `save_meta_analysis` en
  `src/saas_radar/analysis/meta_analysis.py:150`, y SOLO si
  `run_id is not None AND meta.get("recommendations")` (línea 149).
- `save_meta_analysis` / `generate_meta_analysis` **no se llaman desde ningún sitio
  de `src/`** (grep confirmado: solo aparecen en su propio módulo y en
  `tests/test_meta_analysis.py`). En particular `run_ai_analysis`
  (`src/saas_radar/analysis/ai_analyzer.py`) NO invoca el meta-análisis: guarda
  `<ts>_results.json` vía `_save_results` (ai_analyzer.py:120) pero nunca escribe
  `_meta.json` ni llama a persist.
- => Camino A muerto en el pipeline.

### Camino B — heurístico LLM (`persist_heuristic_suggestions`)
- Definido en `src/saas_radar/agents/heuristic_tuner.py:251`.
- Se invoca desde `phase_heuristic_tuner` (`src/saas_radar/main.py:160-181`),
  que a su vez SOLO se ejecuta si `meta_json_path is not None`
  (`main.py:271`).
- `meta_json_path` se obtiene en `main.py:263-266` con
  `glob.glob("data/runs/*_meta.json")`. Como el `*_meta.json` **nunca se genera**
  (Camino A muerto), el glob siempre devuelve `[]` -> `meta_json_path` queda `None`
  -> **la fase 4.5 se salta por completo** -> `persist_heuristic_suggestions` nunca
  corre.
- => Camino B también muerto, dependiente del mismo eslabón roto.

**Resultado neto:** 27 runs, 0 inserciones. Consistente con lo observado.

---

## 1. Condiciones necesarias para que se inserte ≥1 fila (estado actual del código)

Para el Camino A (determinista):
1. Alguien debe llamar a `generate_meta_analysis(...)` con las extractions/opps del run.
   **HOY NADIE LO HACE.**
2. Luego llamar a `save_meta_analysis(meta, run_json_path, run_id=<int>, db_url=...)`
   con `run_id` no-None. **HOY NADIE LO HACE.**
3. `meta["recommendations"]` debe ser no-vacía (`meta_analysis.py:149`).
4. `_build_recommendations` (`meta_analysis.py:231`) debe producir ≥1 rec. Condiciones
   por tipo:
   - `remove_subreddit`: subreddit con `posts_analyzed >= 3` y `hit_rate == 0`.
   - `boost_subreddit`: `hit_rate >= 0.6` y `with_payment_signal >= 1`.
   - `check_silent`: `silent_subreddits` no vacío (casi siempre lo es — subreddits
     configurados sin extracciones en el run).
   - `add_subreddit`: hay `discovered_subs` (pain_search con >=2 hits fuera de config).
   - `prune_queries`: `len(empty_queries) > 10`.
   - `emerging_niche`: nicho con `count >= 3`.
   Nota: con runs reales (80 posts, 30-60 extracciones, 0-1 opps) `check_silent`
   normalmente dispararía (siempre hay subreddits silenciosos), así que la lista de
   recomendaciones RARA VEZ estaría vacía **si el meta-análisis se ejecutara**. Es
   decir, la lista vacía NO es la causa principal; la causa es que la fase no corre.

Para el Camino B (heurístico LLM):
5. Debe existir un `data/runs/*_meta.json` (lo genera Camino A -> hoy inexistente).
6. `call_llm(prompt)` (`heuristic_tuner.py:227`) debe devolver algo != None y con
   schema válido (`_validate_schema`), si no -> listas vacías (silencioso, WARNING).
7. Tras `_dedup_against_config`, debe quedar ≥1 sugerencia; si `rows` vacío,
   `persist_heuristic_suggestions` retorna 0 sin insertar (`heuristic_tuner.py:277`).
8. La BD debe existir en `db_path` (`heuristic_tuner.py:265`), si no -> return 0.

---

## 2. Hipótesis concretas (archivo:línea) de por qué NO se inserta nada

- **H1 (causa raíz, alta confianza):** La fase de meta-análisis nunca se cablea al
  pipeline. `run_ai_analysis` (`src/saas_radar/analysis/ai_analyzer.py:137-341`) no
  llama a `generate_meta_analysis`/`save_meta_analysis`. Grep repo-wide: cero
  llamadores fuera de tests. Por tanto Camino A nunca inserta y nunca escribe
  `_meta.json`.

- **H2 (consecuencia de H1, alta confianza):** En `src/saas_radar/main.py:263-266`,
  `glob("data/runs/*_meta.json")` siempre `[]` (nadie escribe ese fichero) ->
  `meta_json_path = None` -> condición `main.py:271 if meta_json_path is not None`
  falsa -> fase 4.5 y `persist_heuristic_suggestions` nunca corren. Camino B muerto.

- **H2b (agravante, mismatch de rutas):** `main.py:352-360` pasa
  `output="data/ai_analysis.json"` a `run_ai_analysis(output_path=...)`, cuyo
  `_save_results` lo trata como DIRECTORIO (ai_analyzer.py:120-131), escribiendo en
  `data/ai_analysis.json/<ts>_results.json`. Pero el glob de fase 4.5 busca en
  `data/runs/`. Aun si el meta-análisis se cableara con esa ruta, el `_meta.json`
  caería fuera de `data/runs/` y el glob seguiría vacío. Doble desalineación.

- **H3 (secundaria, solo relevante si se arreglara H1/H2):** `save_meta_analysis`
  (`meta_analysis.py:149`) exige `run_id is not None AND recommendations` no-vacía.
  Con `recommendations == []` no persiste (llamada con lista vacía nunca ocurre; el
  `if` la bloquea antes). No es el problema actual pero sería un gate futuro.

- **H4 (tragado de excepciones, no es la causa pero enmascara):**
  `phase_heuristic_tuner` (`main.py:166-180`) envuelve TODO en try/except que solo
  loguea `logger.warning` y sigue. Si la fase 4.5 llegara a correr y fallara (LLM,
  BD, schema), NO se vería en la salida estándar del pipeline salvo por un WARNING
  fácil de pasar por alto. Similar: `generate_heuristic_suggestions` degrada a listas
  vacías con WARNING en varios puntos (`heuristic_tuner.py:216,230,234`).

---

## 3. Comportamiento esperado según tests

- `tests/test_meta_analysis.py:222` (`test_generate_meta_analysis_summary_keys`)
  verifica que `generate_meta_analysis` devuelve las 8 claves — pero llama la función
  DIRECTAMENTE, no vía pipeline. No hay test end-to-end que verifique que un run del
  pipeline puebla `meta_recommendations`.
- `tests/test_meta_analysis.py:53-59` y `tests/test_db.py:301-333` prueban
  `persist_meta_recommendations` en aislamiento (recurrence, acted). Pasan porque
  invocan la función directa. Confirman que la función funciona; el fallo es que
  nadie la invoca en producción.
- Ausencia de test de integración main.py -> meta_recommendations explica por qué el
  bug (fase desconectada) no fue detectado.

---

## Referencias de archivo clave
- `src/saas_radar/main.py:249-281` (fase 4 IA + fase 4.5, glob de `_meta.json`)
- `src/saas_radar/analysis/ai_analyzer.py:137-341` (run_ai_analysis — SIN meta-análisis)
- `src/saas_radar/analysis/meta_analysis.py:20,131,149-150` (generate/save/persist gate)
- `src/saas_radar/agents/heuristic_tuner.py:251-306` (persist_heuristic_suggestions; return 0 si rows vacío en :277)
- `src/saas_radar/storage/db.py:420-467` (persist_meta_recommendations — sin filtro que descarte todo; dedup por type/target/acted)
