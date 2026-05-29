# Review — feature #3 config_module

**Veredicto:** APPROVED

## Criterios de aceptación

1. `from saas_radar import config` carga sin error sin `.env`: CUMPLE
   - Evidencia: `.venv/bin/python -c "from saas_radar import config; print('OK')"` → `OK`

2. `config.AI_PROVIDER` lee de env var, default `'claude'`: CUMPLE
   - Evidencia: `config.AI_PROVIDER` devuelve `"claude"` sin env var. Tests `test_ai_provider_default`, `test_ai_provider_env_override`, `test_ai_provider_groq_override` pasan. Línea 40 de `config.py`: `AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").lower()`

3. `config.PAIN_SIGNAL_PHRASES` es lista de tuplas `(str, int)`, longitud >= 100: CUMPLE
   - Evidencia: longitud = 114. Todos los pesos en {1,2,3}. Tests `test_pain_signal_phrases_is_list_of_tuples`, `test_pain_signal_phrases_min_length`, `test_pain_signal_phrases_weights_valid` pasan.

4. `config.SUBREDDITS` contiene los 36 subreddits del legacy: CUMPLE (con desviación documentada)
   - El acceptance dice "36" pero el implementer cuenta 38 en el archivo legacy real. El informe `progress/impl_config_module.md` documenta la discrepancia y justifica usar la fuente real. `len(config.SUBREDDITS)` = 38. Test `test_subreddits_length` comprueba 38 con comentario explicativo. Coherente con la política de la feature ("contar contra el archivo, no el doc").

5. `config.HIGH_SIGNAL_SUBREDDITS` es un `set`, todos sus elementos están en `SUBREDDITS`: CUMPLE
   - Evidencia: `type(config.HIGH_SIGNAL_SUBREDDITS)` = `set`, 16 elementos, todos en `{s.lower() for s in SUBREDDITS}` (verificado con `missing = []`). Todos en minúsculas.

6. Tests cubren env var override con `monkeypatch`, tipos correctos, longitudes esperadas: CUMPLE
   - Evidencia: 32 tests en `tests/test_config.py`, todos pasan. Incluye `monkeypatch.setenv` para `AI_PROVIDER`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DB_URL`, modelos Claude. Tipos y longitudes cubiertos exhaustivamente.

7. Sin `print()` ni side-effects al importar: CUMPLE
   - Evidencia: `grep -n "print(" src/saas_radar/config.py` → vacío. Test `test_no_print_on_import` con `capsys` pasa: stdout y stderr vacíos tras reimportar.

8. Las constantes de `docs/legacy-context/inventory.md §1.2` están presentes: CUMPLE
   - `POST_LIMIT=100`, `PAIN_SEARCH_LIMIT=50`, `COMMENT_MIN_LENGTH=50`, `HIGH_ENGAGEMENT_THRESHOLD=100`, `COMMENT_FETCH_WORKERS=8`, `COMMENT_TARGET_POSTS=200` ✓
   - `AI_PROVIDER`, URLs y modelos por proveedor ✓
   - `MAX_POSTS=80`, `TEXT_SNIPPET_LEN=500`, `MIN_SEMANTIC_SCORE=1.5`, `MAX_POST_AGE_DAYS=365`, `INCREMENTAL_POST_AGE_DAYS=1`, `CIRCUIT_BREAKER_THRESHOLD=3` ✓
   - `PAIN_CATEGORIES`, `PAIN_SIGNAL_PHRASES`, `SHOWCASE_TITLE_PREFIXES`, `OFF_TOPIC_SIGNALS`, `HIGH_SIGNAL_SUBREDDITS`, `POSTS_CAP_HIGH_SIGNAL`, `POSTS_CAP_DEFAULT`, `SUBREDDITS`, `PAIN_SEARCH_QUERIES` ✓

## Checkpoints CHECKPOINTS.md

- C1: [x] — `./init.sh` sale con exit code 0. Los 4 archivos base existen. Los 3 docs del proyecto existen. Legacy context completo.
- C2: [x] — Solo una feature `in_progress` (#3). Features `done` (#1, #2) tienen tests que pasan (52 en total). `progress/current.md` describe la sesión activa. Dependencias respetadas (#3 depende de #1 que es `done`).
- C3: [x] — `config.py` está en `src/saas_radar/` como prevé `docs/architecture.md`. Sin `sys.path.append`. Sin mutación de globales (el módulo solo define constantes). `python-dotenv` ya declarado en `pyproject.toml` (dependencia de feature #1). Sin `print()` sueltos para debug. Sin `TODO`.
- C4: [x] — `tests/test_config.py` existe (1 archivo de test por módulo). 32 tests nuevos + 20 previos = 52 en verde. No hay tests de LLM ni PRAW en este módulo (no aplican). No toca disco con mocks del filesystem.
- C5: [x] — No aplica para cambios en config.py. La BD heredada sigue intacta (no se toca en esta feature).
- C6: [ ] — Sesión aún abierta (el leader cerrará tras este review). `progress/history.md` se actualizará al cierre.

## Output real de pytest

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
collected 32 items

tests/test_config.py ................................                    [100%]
============================== 32 passed in 0.02s ==============================

# Suite completa:
52 passed in 0.39s
```

## Output real de ruff

```
$ .venv/bin/ruff check src/saas_radar/config.py
All checks passed!

$ .venv/bin/ruff check tests/test_config.py
All checks passed!
```

## Notas al leader

- El implementer justifica correctamente la discrepancia "36 vs 38 subreddits" leyendo el archivo real del legacy. No es una desviación problemática; el doc tiene un error documentado y la fuente de verdad es el código.
- `HIGH_SIGNAL_SUBREDDITS` tiene 16 elementos en lugar de 13 que menciona el inventory. El implementer añadió `ecommerce`, `smallbusiness` y `zapier` basándose en datos del meta-analysis (comentarios en el código con fechas de run). Todos sus elementos están en `SUBREDDITS`. No viola ningún criterio de aceptación.
- La convención de `docs/conventions.md` exige `logger = logging.getLogger(__name__)` — pero `config.py` no usa logging (solo define constantes), por lo que la ausencia de logger es correcta.
