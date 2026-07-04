# Review — feature #27 `fix_opportunities_null_defaults`

**Veredicto:** APROBADO

Rama: `feat/27-fix_opportunities_null_defaults` (working tree sin commitear).
Archivos revisados: `src/saas_radar/storage/db.py`, `tests/test_db.py`,
`feature_list.json`, `progress/current.md`,
`progress/impl_fix_opportunities_null_defaults.md`.

## Acceptance criteria (feature_list.json #27)

1. **Defaults en persist_run_to_db** — [x]
   `src/saas_radar/storage/db.py:362` define
   `flag_defaults = {"reviewed": 0, "starred": 0, "discarded": 0}` y
   `db.py:372-374` rellena con 0 tras la dict comprehension. Como
   `opp.get(f)` devuelve `None` tanto para clave ausente como para valor
   `None` explícito, el chequeo `is None` cubre ambos casos del acceptance.
   Verificado además que NO pisa valores legítimos: `is None` (no truthiness)
   deja pasar `discarded=1` intacto — cubierto por
   `test_persist_run_to_db_flags_respect_explicit_values` (tests/test_db.py:374)
   y por el test 13 preexistente (`discarded=1`), que sigue verde.

2. **Migración idempotente en init_db** — [x]
   `db.py:221-224`: `UPDATE opportunities SET {flag} = 0 WHERE {flag} IS NULL`
   para las 3 columnas. Usa `IS NULL` (no `= NULL`), va dentro del
   `with conn.begin()` existente tras las migraciones de `semantic_score` y
   `canonical_id` (mismo patrón del módulo), y sobre BD vacía/recién creada
   afecta 0 filas sin fallar (la fixture `tmp_db` ejecuta `init_db` sobre BD
   nueva en todos los tests; `test_init_db_idempotent` sigue verde).
   Idempotencia N ejecuciones: `test_init_db_backfill_is_idempotent`
   (tests/test_db.py:429) ejecuta `init_db` dos veces y verifica estado final
   `[(1, 1), (2, 0)]` — el NULL migra a 0 y el `discarded=1` legítimo no se toca.
   La f-string con `{flag}` es aceptable: tupla literal hardcodeada, y los
   identificadores no son parametrizables; mismo patrón que `_column_exists`.

3. **Tras la migración, load_active_opportunities devuelve las opps** — [x]
   `test_init_db_backfills_null_flags_and_restores_active_opportunities`
   (tests/test_db.py:392) reproduce el bug (fila con flags NULL y
   `canonical_id` autorreferencial → 0 filas), ejecuta `init_db` y verifica
   que la opp reaparece. Sobre la BD real, el backfill corre automáticamente
   en el próximo arranque del pipeline.

4. **Test: opp dict sin claves → 0, no NULL, con SELECT directo** — [x]
   `test_persist_run_to_db_flags_default_to_zero_when_keys_missing`
   (tests/test_db.py:343): SELECT directo sobre `opportunities`, assert
   `row == (0, 0, 0)`. Extra: variante con `None` explícito
   (`..._when_keys_are_none`, tests/test_db.py:359), también con SELECT directo.

5. **Test: BD fixture con discarded=NULL → init_db migra y load_active la devuelve** — [x]
   Cubierto por el test del punto 3, con verificación adicional por SELECT
   directo de las 3 columnas (`row == (0, 0, 0)`).

6. **Suite completa verde** — [x]
   `.venv/bin/pytest -q` → exit 0 (353 pass, 4 skips preexistentes).
   `./init.sh` → exit 0.

## Verificaciones adicionales

- **Sin cambios de comportamiento colaterales:** el diff de
  `persist_run_to_db` es solo aditivo (dict de defaults + loop de 3 líneas);
  dedup/canonical_id, `opp_fields` y el INSERT no cambian. Todos los tests
  preexistentes de db (dedup, canonical, load_active) siguen verdes.
- **Scope de archivos:** solo `src/saas_radar/storage/db.py`, `tests/test_db.py`,
  `progress/` y `feature_list.json` (status `pending` → `in_progress`, NO `done`). Correcto.
- **Convenciones:** logging con lazy `%s`, comillas dobles, sin prints,
  comentarios solo de "por qué" (invariante NULL/DEFAULT no obvia — permitido).
  `ruff check`: el único aviso (F841 en tests/test_db.py:286) es preexistente
  en main (verificado con `git stash`). `ruff format --check` ya fallaba en
  main para ambos archivos; el código nuevo sigue el estilo compacto real del
  repo, sin regresión.

## Checkpoints (CHECKPOINTS.md)

- C1: [x] arnés completo; `./init.sh` exit 0.
- C2: [x] una sola feature `in_progress` (#27); `current.md` describe la sesión activa.
- C3: [x] cambio en capa storage prevista; sin `sys.path.append`; sin prints de debug.
- C4: [x] 5 tests nuevos en `tests/test_db.py` con BD temporal (`tmp_path`); suite > 0 tests, toda verde.
- C5: [x] migración idempotente siguiendo el patrón del módulo; no destruye datos (solo NULL → 0).
- C6: [ ] N/A aún — sesión abierta, pendiente de commit/cierre por el leader.

## Cambios requeridos

Ninguno.

## Notas (no bloqueantes)

- `init.sh` ejecutado con el python del sistema salta los tests
  ("pytest no instalado"); la verificación real de tests es `.venv/bin/pytest -q`
  (exit 0). El acceptance menciona `./venv/bin/pytest`, pero el venv del repo
  es `.venv/` — ya documentado por el implementer en `current.md`.
