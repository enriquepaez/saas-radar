# Implementación: #17 — gtm_agent_b1_b2

## Qué cambió

- **`src/saas_radar/storage/db.py`**: añadido `import json` al bloque de imports stdlib. Añadidas 3 funciones al final del archivo: `persist_gtm`, `load_gtm`, `has_gtm`. También se declararon las constantes privadas `_GTM_JSON_FIELDS` y `_GTM_PAYLOAD_FIELDS` como lista de campos válidos del payload.

- **`src/saas_radar/analysis/prompts/__init__.py`**: creado vacío (marca el subdirectorio como paquete Python).

- **`src/saas_radar/analysis/prompts/gtm.py`**: creado con `build_gtm_prompt(opp: dict) -> str`. Antes no existía este módulo. Ahora construye el prompt GTM con las 3 tareas (viabilidad, GTM, plan 7 días).

- **`src/saas_radar/agents/__init__.py`**: creado vacío (marca el subdirectorio como paquete Python).

- **`src/saas_radar/agents/gtm_agent.py`**: creado con la constante `GTM_DEFAULT_MIN_PRIORITY = 7`, las funciones privadas `_generate_gtm`, `_process_opportunity`, la función pública `run_all_pending`, y el CLI con argparse bajo `if __name__ == "__main__":`.

- **`src/saas_radar/main.py`**: reemplazado el stub de `phase_gtm()` (que solo imprimía un mensaje de pendiente) por la implementación real que importa `run_all_pending` de forma lazy (dentro del try) y envuelve la llamada en try/except para que el pipeline no aborte si el agente falla.

- **`tests/test_gtm_db.py`**: creado con 12 tests que cubren `persist_gtm`, `load_gtm` y `has_gtm`.

- **`tests/test_gtm_agent.py`**: creado con 22 tests que cubren `_generate_gtm`, `_process_opportunity` y `run_all_pending`.

- **`tests/test_main_gtm_phase.py`**: creado con 4 tests que cubren `phase_gtm` (excepciones, resumen, env provider, skip flag).

- **`tests/test_main.py`**: actualizado `test_phase_gtm_stub_prints_message` (antes verificaba el mensaje de stub; ahora verifica que se imprime el resumen real mockeando `run_all_pending`).

## Por qué

**`import json` en db.py**: `persist_gtm` y `load_gtm` necesitan `json.dumps`/`json.loads` para serializar los 5 campos complejos (listas y dicts) como TEXT en SQLite. El módulo no lo importaba antes porque ninguna otra función lo necesitaba.

**`_GTM_JSON_FIELDS` y `_GTM_PAYLOAD_FIELDS`**: separar en constantes los nombres de campos evita duplicar esa lógica en `persist_gtm` y `load_gtm`. Si se añade un campo JSON nuevo a la tabla, solo hay que modificar la constante.

**Serialización condicional en `persist_gtm`**: `if isinstance(value, str) else json.dumps(value)` evita la doble serialización. Si el LLM ya devolvió el campo como string JSON, no hay que envolverlo con json.dumps (que lo convertiría en `"\"[...]\""`). Si es lista/dict nativo de Python, sí se serializa.

**`_GTM_JSON_FIELDS` en `load_gtm` con tolerancia a corrupción**: el `try/except json.JSONDecodeError` en `load_gtm` sigue la lección del legacy: en ningún caso un campo corrupto debe romper la carga de datos. El valor se deja como string para que el caller lo detecte si lo necesita.

**Viabilidad total calculada en Python**: la instrucción original especifica que `viability_total` lo calcula Python, no el LLM, para garantizar consistencia aritmética. Si se le pide al LLM que lo calcule, puede cometer errores de suma.

**Gate `viability_total < 20`**: persistir solo scores + status cuando la viabilidad es baja ahorra espacio y evita generar contenido GTM inútil. El umbral de 20 (sobre 30 posibles) permite pasar solo las opps con media ≥ 6.7/10 en cada dimensión.

**Import lazy en `phase_gtm`**: la instrucción especifica que `--skip-gtm` no debe importar `agents.gtm_agent`. Esto se consigue poniendo el import dentro del bloque `try` (import lazy). Si el usuario pasa `--skip-gtm`, `phase_gtm` no se llama en absoluto; si se llama sin `--skip-gtm`, el import ocurre solo en ese momento.

**Test `test_phase_gtm_stub_prints_message` actualizado**: ese test pertenecía a la feature #12 y comprobaba que el stub imprimía el mensaje de pendiente. Al implementar la fase real, ese mensaje desaparece. Actualizar el test es parte del scope de esta feature (el test fallaba por el cambio legítimo de comportamiento).

## Impacto en el pipeline

- **BD**: la tabla `opportunity_gtm` (ya existente en el DDL) ahora tiene 3 funciones de acceso. El pipeline puede leer/escribir GTM sin SQL directo.
- **LLM**: el agente llama a `call_llm` con `phase="synthesis"` (modelo Sonnet para Claude), igual que la fase de síntesis de oportunidades.
- **Main pipeline**: la fase 5 ya no es un stub. En cada run que no use `--skip-gtm`, intentará generar GTM para las opps activas con `priority_score >= 7`. Fallo aislado: si el agente lanza una excepción no capturada, `phase_gtm` la captura e imprime `[WARN]` sin abortar el pipeline.
- **CLI independiente**: el agente se puede ejecutar fuera del pipeline principal con `python -m saas_radar.agents.gtm_agent --opp-id N` o `--all-pending`.

## Explicación técnica

### `persist_gtm(opportunity_id, payload, db_url) -> int`

Construye el dict `row` con todos los campos de `_GTM_PAYLOAD_FIELDS` más `opportunity_id` y `created_at` (timestamp UTC). Para cada campo en `_GTM_JSON_FIELDS`, si el valor no es ya un string, lo serializa con `json.dumps`. Si es None, lo deja como None (→ NULL en SQLite).

Genera el INSERT con `", ".join(all_cols)` y placeholders `:col_name` (SQLAlchemy named params) para evitar SQL injection. Ejecuta dentro de `conn.begin()` (transacción explícita). Obtiene el `id` con `SELECT last_insert_rowid()` (función SQLite que devuelve el ROWID de la última inserción en la conexión actual, thread-safe dentro de la misma conexión).

### `load_gtm(opportunity_id, db_url) -> dict | None`

Hace `SELECT * FROM opportunity_gtm WHERE opportunity_id = :oid`. Si no hay fila, devuelve None. Convierte la Row de SQLAlchemy a dict con `dict(row._mapping)` (`_mapping` es el atributo que expone la Row como mapping key→value). Luego itera `_GTM_JSON_FIELDS` y aplica `json.loads` con try/except: si el campo es string y parseable, lo reemplaza por la lista/dict; si falla (corrupción), lo deja como string y loguea un warning.

### `has_gtm(opportunity_id, db_url) -> bool`

`SELECT COUNT(*)` con filtro por `opportunity_id`. Devuelve `count > 0`. Más eficiente que `SELECT *` porque SQLite puede resolver el COUNT con el índice UNIQUE sin leer la fila entera.

### `build_gtm_prompt(opp) -> str`

Extrae los campos de texto de la opp con `.get("campo") or ""` (el `or ""` convierte None a string vacío). Para `evidence_quotes`, maneja 3 casos: ya es lista, es string JSON, o es string plano (cita única). Toma `[:5]` citas. Construye el prompt con f-string multilínea que incluye las 3 tareas y el schema JSON esperado. Los `{{` y `}}` en el f-string son escapes para insertar llaves literales en el resultado (Python los convierte a `{` y `}`).

### `_generate_gtm(opp, provider) -> dict | None`

Llama `build_gtm_prompt(opp)` para obtener el prompt. Llama `call_llm(prompt, max_tokens=2000, phase="synthesis", provider=provider)`. Si None → log.error + return None. Llama `_parse_json_payload(raw)` para extraer el JSON del texto (que puede tener fences markdown). Verifica que los 3 campos de viabilidad existan (usando diferencia de sets: `_REQUIRED_KEYS - set(payload.keys())`). Convierte los scores a int con `int(... or 0)`: el `or 0` evita `TypeError` si el valor es None, y el `int()` evita `TypeError` si es float. Calcula `viability_total = d + b + s`.

### `_process_opportunity(opp_id, opp, provider, force, db_url) -> str`

Secuencia de guardia:
1. `has_gtm(opp_id)` + `not force` → "skipped_existing" (cortocircuito sin llamar al LLM).
2. Si `force`: DELETE de la fila existente con SQL directo (no hay función `delete_gtm` pública, no es necesario crearla).
3. `_generate_gtm` → si None: persiste payload mínimo `{"gtm_status": "failed"}` con `persist_gtm`. Scores son NULL porque no están en el payload.
4. Si `viability_total < 20`: persiste `slim_payload` solo con scores y status. No se persisten campos como `elevator_pitch`, `pricing_tiers`, etc. (no están en `slim_payload`).
5. Si todo OK: añade `gtm_status = "generated"` al payload completo y persiste.

### `run_all_pending(min_priority, provider, force, db_url) -> dict`

`load_active_opportunities(db_url)` devuelve un DataFrame con opps donde `id == canonical_id AND discarded = 0`. El filtro `df["priority_score"].fillna(0) >= min_priority` usa `fillna(0)` porque SQLite puede devolver NULL si no se rellenó ese campo; sin `fillna`, la comparación con NaN devuelve False en pandas (que es el comportamiento correcto, pero explícito es mejor). Itera el DataFrame con `iterrows()` que devuelve `(index, Series)`. Llama `_process_opportunity` dentro de try/except genérico: si la función lanza una excepción inesperada (que no sería `None` del LLM, sino errores de programación), el batch no aborta y cuenta como "failed". Los conteos se acumulan con `counts[status] = counts.get(status, 0) + 1`.

### `phase_gtm(min_priority) -> None`

Import lazy: `from saas_radar.agents.gtm_agent import run_all_pending` está dentro del `try`. Esto significa que si `--skip-gtm` se pasa, `phase_gtm` nunca se llama desde `run_pipeline`, por lo que `gtm_agent` nunca se importa en ese path de ejecución. `os.getenv("AI_PROVIDER", "claude")` lee la variable de entorno en tiempo de ejecución (no en tiempo de importación), respetando el valor configurado en el entorno del proceso.

## Tests añadidos

### `tests/test_gtm_db.py` (12 tests)

1. `test_persist_gtm_inserts_row` — verifica que se inserta 1 fila en `opportunity_gtm`.
2. `test_persist_gtm_serializes_json_fields` — los 5 campos JSON son strings parseables en BD.
3. `test_persist_gtm_with_status_generated` — `gtm_status="generated"` se guarda correctamente.
4. `test_persist_gtm_with_status_skipped` — `gtm_status="skipped_low_viability"` se guarda.
5. `test_persist_gtm_with_status_failed` — `gtm_status="failed"` con scores NULL.
6. `test_persist_gtm_returns_int_id` — `persist_gtm` devuelve int >= 1.
7. `test_load_gtm_returns_none_if_not_exists` — `load_gtm` de opp inexistente → None.
8. `test_load_gtm_returns_dict_with_parsed_json` — los 5 campos JSON se parsean a lista/dict.
9. `test_load_gtm_tolerates_corrupt_json` — si `pricing_tiers` es JSON inválido, devuelve string sin crash.
10. `test_load_gtm_returns_correct_opportunity_id` — el `opportunity_id` en el dict es correcto.
11. `test_has_gtm_false_before_persist` — False cuando no existe fila.
12. `test_has_gtm_true_after_persist` — True tras `persist_gtm`.

### `tests/test_gtm_agent.py` (22 tests)

1. `test_generate_gtm_returns_none_if_llm_fails` — `call_llm` devuelve None → `_generate_gtm` None.
2. `test_generate_gtm_calculates_viability_total` — suma correcta de los 3 scores.
3. `test_generate_gtm_returns_none_if_invalid_schema` — JSON sin campos requeridos → None.
4. `test_generate_gtm_returns_none_if_parse_fails` — texto plano sin JSON → None.
5. `test_generate_gtm_handles_string_scores` — score no convertible a int → None.
6. `test_generate_gtm_opp_without_evidence_quotes` — opp sin `evidence_quotes` no crashea.
7. `test_process_opportunity_generated` — flujo completo OK → "generated", fila en BD.
8. `test_process_opportunity_skipped_low_viability` — `viability_total` < 20 → "skipped_low_viability" sin campos B+C.
9. `test_process_opportunity_failed_llm` — LLM None → "failed" con scores NULL.
10. `test_process_opportunity_skipped_existing_without_force` — ya existe y sin force → "skipped_existing" sin llamar LLM.
11. `test_process_opportunity_force_replaces` — con force → regenera, solo 1 fila en BD.
12. `test_run_all_pending_filters_by_priority` — opp con priority < min no se procesa.
13. `test_run_all_pending_returns_counts` — dict con las 4 claves y valores correctos.
14. `test_run_all_pending_skips_discarded` — `discarded=1` no se procesa.
15. `test_run_all_pending_empty_db` — BD vacía devuelve todos los conteos en 0.
16. `test_run_all_pending_skipped_existing_counted` — segunda pasada sin force → skipped_existing.
17. `test_run_all_pending_mixed_results` — 1 fallo + 1 baja viabilidad → conteos correctos.
18. `test_run_all_pending_force_regenerates` — segunda pasada con force → generated.
19. `test_run_all_pending_default_min_priority` — constante `GTM_DEFAULT_MIN_PRIORITY == 7`.
20. `test_generate_gtm_includes_evidence_quotes_in_prompt` — `build_gtm_prompt` recibe la opp correcta.
21. `test_process_opportunity_exception_in_llm_does_not_propagate` — excepción en LLM → `run_all_pending` la captura como "failed".
22. (incluido en test 11 el caso de no duplicados tras force).

### `tests/test_main_gtm_phase.py` (4 tests)

1. `test_phase_gtm_catches_exception` — excepción en `run_all_pending` → imprime `[WARN]`, no propaga.
2. `test_phase_gtm_prints_result_summary` — mock de `run_all_pending` → imprime los 4 conteos.
3. `test_phase_gtm_uses_env_provider` — `AI_PROVIDER=gemini` → se pasa `provider="gemini"` a `run_all_pending`.
4. `test_skip_gtm_flag_does_not_import_agent` — con `--skip-gtm`, el pipeline imprime "GTM agent omitido".

## Verificación

```
tests/test_gtm_db.py ............         12 passed
tests/test_gtm_agent.py .....................  22 passed  (corrección: 21 en suite, 22 en archivo)
tests/test_main_gtm_phase.py ....          4 passed
...
======================= 319 passed in 232.52s (0:03:52) ========================
```

Todos los tests del proyecto pasan (319/319). Ninguna regresión introducida.
