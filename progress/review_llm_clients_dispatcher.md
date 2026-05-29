# Review — feature #8 llm_clients_dispatcher

**Veredicto:** APROBADO

## Checkpoints

- C1: [x] Archivos base presentes, `./init.sh` termina con exit code 0 (el WARN de pytest es espurio: usa el python del sistema en vez del venv, pero el script no falla por eso).
- C2: [x] Solo la feature #8 en `in_progress`. Todas las `done` tienen tests que pasan (178 total, todos verdes).
- C3: [x] `llm_clients.py` vive en `src/saas_radar/analysis/` como prevé `docs/architecture.md`. Sin `sys.path.append`. `config.AI_PROVIDER` no se lee ni muta en ningún punto de `llm_clients.py` (solo aparece en el docstring de `call_llm` como comentario). `respx` añadido correctamente a `[project.optional-dependencies].dev`. Logging vía `logging.getLogger(__name__)`. Sin `print()` sueltos. Sin `TODO`.
- C4: [x] `tests/test_llm_clients.py` con 22 tests, todos verdes. Mocks HTTP con `respx`. Sin llamadas reales a ninguna API.
- C5: [x] No aplica cambios de BD en esta feature.
- C6: [ ] Sesión aún activa (no aplica cierre todavía).

## Acceptance criteria

1. [x] `_parse_json_payload('```json\n{"a":1}\n```')` devuelve `{'a': 1}` — verificado manualmente y con `test_parse_json_payload_fence_json_lowercase`.
2. [x] Tolera fences sin etiqueta, `JSON` (mayúsculas), y JSON pelado — `test_parse_json_payload_fence_no_lang`, `test_parse_json_payload_fence_json_uppercase`, `test_parse_json_payload_bare_json`.
3. [x] `call_llm(prompt, provider='claude')` usa Anthropic API; `'gemini'` → Google; `'groq'` → Groq — verificado en `test_call_llm_provider_gemini_routes_correctly`, `test_call_llm_provider_groq_routes_correctly`, y en el dispatcher (`llm_clients.py` líneas 295-301).
4. [x] `call_llm(phase='synthesis', provider='claude')` selecciona `CLAUDE_SYNTHESIS_MODEL`; `phase='extraction'` → `CLAUDE_EXTRACTION_MODEL` — `test_call_llm_synthesis_uses_synthesis_model` y `test_call_llm_extraction_uses_extraction_model` capturan el body HTTP y verifican el campo `model`.
5. [x] Retry con parseo de retry-after: Claude (`retry-after` header, líneas 87-92), Gemini (`retryDelay` en `error.details`, líneas 152-163), Groq (`'Please try again in Xs'` con regex, líneas 236-241).
6. [x] Tests mock HTTP: 200 OK parsea JSON (`test_call_claude_200_ok`, `test_call_gemini_200_ok`, `test_call_groq_200_ok`), 429 reintenta (`test_call_claude_429_sleeps_and_retries`, `test_call_gemini_429_retry_delay_sleeps_and_retries`, `test_call_groq_429_retry_text_sleeps_and_retries`), 5xx aborta tras N retries (`test_call_claude_500_exhausts_retries`, `test_call_gemini_500_exhausts_retries`, `test_call_groq_500_exhausts_retries`).
7. [x] `config.AI_PROVIDER` no se muta — `test_call_llm_does_not_mutate_config_ai_provider` verifica que el valor es idéntico antes y después de llamar a `call_llm`.

## Resultados de comandos

```
python -m pytest tests/test_llm_clients.py -v
→ 22 passed in 0.12s

python -m pytest --tb=short
→ 178 passed in 0.86s

./init.sh
→ [OK] Entorno listo. (exit code 0)

ruff check src/saas_radar/analysis/llm_clients.py tests/test_llm_clients.py
→ All checks passed!
```

## Observaciones

- La implementación respeta estrictamente el principio "provider como argumento, no como global" (`docs/architecture.md` §3 / `docs/conventions.md` §legacy-a-cambiar`).
- El archivo `llm_clients.py` sigue la estructura de cabecera exigida por `docs/conventions.md`: docstring de módulo, `from __future__ import annotations`, imports stdlib, third-party, internos, `logger = logging.getLogger(__name__)`.
- El 5xx en `call_claude` (líneas 95-99) no hace `return None` en el último intento sino que cae en `continue` y luego el bucle termina con `return None` en línea 118: comportamiento correcto, aborta tras N retries.
- Los comentarios en el código son todos del tipo "por qué no obvio" (workarounds de API, invariantes sutiles), consistente con `docs/conventions.md`.
