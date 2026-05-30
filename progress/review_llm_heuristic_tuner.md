# Review — feature #21 llm_heuristic_tuner

**Veredicto:** APPROVED

## Checkpoints

- C1: [x] — `AGENTS.md`, `init.sh`, `feature_list.json`, `progress/current.md`, `docs/architecture.md`, `docs/conventions.md`, `docs/verification.md`, `CHECKPOINTS.md`, los 4 ficheros de `docs/legacy-context/` existen. `init.sh` termina verde (exit code 0).
- C2: [x] — Una sola feature `in_progress` (#21). `progress/current.md` describe la sesión activa. Dependencias (#8, #13, #18) están en estado `done`.
- C3: [x] — Módulo nuevo en `src/saas_radar/agents/` (capa correcta para agentes). Sin `sys.path.append`. Sin mutación de globals de `config`. Logging vía `logger = logging.getLogger(__name__)` en ambos archivos. `print()` solo en la función `main()` del CLI (`heuristic_tuner.py:347-373`) y en `run_pipeline` de `main.py`, que es user output de fase, todo explícitamente permitido por `docs/conventions.md`. Sin `print()` en módulos internos (`tuning_rules.py` limpio). `from __future__ import annotations` presente en `heuristic_tuner.py:8` y `tuning_rules.py:20`.
- C4: [x] — 16 tests nuevos en `tests/test_heuristic_tuner.py` cubriendo los 6 acceptance criteria. Tests usan `tmp_path` de pytest para BD temporal. Mock de `call_llm` vía `patch("saas_radar.agents.heuristic_tuner.call_llm")` sin llamadas reales al LLM. Suite completa: 404 tests, 404 passed, exit code 0.
- C5: [x] — `persist_heuristic_suggestions` usa `sqlite3.connect(db_path)` con `INSERT` y `UPDATE` idempotentes sobre `meta_recommendations`. No rompe la BD existente.
- C6: [ ] — Sesión aún en curso (feature `in_progress`). Aplazado hasta cierre.

## Acceptance criteria verificados

1. **`generate_heuristic_suggestions` recibe meta_json_path, top_posts_df, provider**: `heuristic_tuner.py:195`. Lee `recurring_niches` con `count >= 2` del meta-JSON (no de `meta_recommendations` directamente, sino del JSON que es el output del meta-análisis). Llama a `call_llm(prompt, provider=provider, phase="synthesis")` en línea 229. Correcto: el AC dice "datos del run" y el meta-JSON es precisamente eso.

2. **Schema de salida validado**: `_validate_schema` (`heuristic_tuner.py:31-65`) valida las tres listas con sus tipos exactos. `call_llm` ya invoca `_parse_json_payload` internamente para extraer el dict del texto del LLM. La cadena es: raw text → `_parse_json_payload` (en `llm_clients.py`) → dict → `_validate_schema` (en `heuristic_tuner.py`). Cumple el AC.

3. **`persist_heuristic_suggestions` con los tres tipos**: `heuristic_tuner.py:253-308`. Inserta `query_suggestion`, `subreddit_suggestion`, `phrase_suggestion`. UPDATE de `recurrence` si ya existe el mismo `(type, target)`. Test `test_persist_inserta_los_tres_tipos` y `test_recurrence_incrementa_en_segunda_insercion` lo verifican.

4. **Dedup contra config actual**: `_dedup_against_config` (`heuristic_tuner.py:152-192`) con import lazy de `saas_radar.config` para leer el estado en el momento de la llamada. Comparación case-insensitive. Tests `test_dedup_no_incluye_*` lo verifican.

5. **CLI con `--meta-json`, `--provider`, `--dry-run`**: `_parse_args` en `heuristic_tuner.py:314-327`. `main()` en `heuristic_tuner.py:330-378`. `--dry-run` retorna sin llamar a `persist_heuristic_suggestions`. Test `test_dry_run_no_llama_a_persist` lo verifica.

6. **Reglas A5/A6/A7 en `tuning_rules.py`**: `propose_add_queries_from_llm` (`tuning_rules.py:285-315`), `propose_add_subreddits_from_llm` (`tuning_rules.py:321-351`), `propose_add_phrases_from_llm` (`tuning_rules.py:357-387`). Las tres exigen `recurrence >= 2` y `acted == 0`. `propose_all_changes` (`tuning_rules.py:393-414`) las llama al final (orden conservador). Tests `test_regla_a5_*`, `test_regla_a6_*`, `test_regla_a7_*`, `test_propose_all_changes_*` los verifican.

7. **Fase 4.5 en `main.py` envuelta en try/except**: `phase_heuristic_tuner` (`main.py:159-180`) con import lazy de `heuristic_tuner` dentro del try. Llamada en `run_pipeline` (`main.py:269-280`) solo si `meta_json_path is not None`. Import local de `glob` en `main.py:261` para buscar el meta-JSON más reciente — justificado en `impl_llm_heuristic_tuner.md` (uso único).

8. **Schema inválido → log WARNING + skip sin crash**: `generate_heuristic_suggestions:235-240`. Cuatro escenarios de respuesta inválida probados en `test_schema_invalido_devuelve_vacio_sin_excepcion`.

9. **Tests cubren todos los criteria**: 16 tests en `tests/test_heuristic_tuner.py`, todos pasando.

## Cambios requeridos

Ninguno.
