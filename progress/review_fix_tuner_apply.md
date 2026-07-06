# Review — fix: tuner --apply acciones A5/A6/A7 + guard de commit vacío

**Veredicto:** APROBADO (segunda pasada, tras correcciones post-review)

## Historial

- **Pasada 1: RECHAZADO** por dos defectos de corrección:
  1. `_insert_into_set` insertaba el entry sin escapar comillas dobles →
     un `add_query` con comillas (p.ej. `"manual data entry" CRM`) generaba
     un `config.py` con SyntaxError.
  2. El patrón de dedupe de `_insert_tuple_into_list` se construía sobre la
     frase SIN escapar, pero la línea escrita en fichero contiene `\"` →
     reaplicar un `add_phrase` con comillas duplicaba la tupla.
- **Pasada 2:** el implementer corrigió ambos puntos (ver
  `progress/impl_fix_tuner_apply.md`, sección "Correcciones post-review").

## Verificación de las correcciones (pasada 2)

Reproduje exactamente los snippets que destaparon los bugs, sobre copias
frescas del `config.py` real:

1. **Escapado en `_insert_into_set`** (tuner.py, `escaped = entry.replace('"', '\\"')`
   antes del patrón y de la inserción):
   `apply_proposals([Proposal("add_query", '"manual data entry" CRM', ...)])`
   → `ast.parse` OK. **config.py VÁLIDO.**
2. **Dedupe sobre forma escapada en `_insert_tuple_into_list`**:
   doble aplicación de `Proposal("add_phrase", 'retype "into"', ...)` →
   una sola línea `("retype \"into\"", 2),`; texto idéntico tras la segunda
   aplicación. **Sin duplicados.**
3. Validación completa del snippet original: las 4 propuestas (query,
   subreddit `r/Shopify`, 2 frases —una con comillas—) producen config.py
   válido (`ast.parse` + `exec`), valores correctos en las tres listas,
   dedupe idempotente al reaplicar y dedupe case-insensitive contra entradas
   preexistentes (`I use Excel to track`, `r/ZAPIER`, `Manually Copy`).

Tests nuevos que cubren ambos fixes:
- `tests/test_tuner.py::TestApplyProposals::test_add_query_con_comillas_genera_python_valido_y_no_duplica`
- `tests/test_tuner.py::TestApplyProposals::test_add_phrase_con_comillas_genera_python_valido_y_no_duplica`

## Suite

- `./.venv/bin/pytest -q` → **exit 0** (4 skipped, resto verde).
- `./.venv/bin/pytest tests/test_tuner.py` → **47 passed**.
- `./init.sh` → exit 0 (verificado en pasada 1; sin cambios de arnés desde entonces).

## Recordatorio de la pasada 1 (sigue vigente)

- Alcance limpio: solo `src/saas_radar/agents/tuner.py` y
  `tests/test_tuner.py` (workflows = leader, fuera de review).
- Guard de `main()` correcto (return 0 sin tocar git si config.py no cambia),
  con test que inspecciona las llamadas a subprocess.
- Normalización `r/` y dedupe case-insensitive verificados.
- Estilo conforme a docs/conventions.md; sin violaciones de
  docs/architecture.md.

## Checkpoints aplicables

- C1 (`./init.sh` verde): [x]
- C3 (arquitectura, sin sys.path.append, sin mutar globales en runtime): [x]
- C4 (tests reales con tmp_path, subprocess mockeado, suite verde): [x]
- Corrección funcional del fix (config.py generado Python válido; dedupe
  correcto incluso con comillas): [x]
