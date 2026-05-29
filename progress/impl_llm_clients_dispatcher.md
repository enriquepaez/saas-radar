# Implementación: #8 — llm_clients_dispatcher

## Qué cambió

- **`src/saas_radar/analysis/llm_clients.py`** (nuevo): módulo creado desde cero con las 5 funciones del acceptance criteria. No existía.
- **`tests/test_llm_clients.py`** (nuevo): 22 tests con mocks HTTP vía `respx`.
- **`pyproject.toml`** (modificado): `respx>=0.21` añadido a `[project.optional-dependencies].dev`.

## Por qué

**Dispatcher con `provider` como argumento explícito (no `config.AI_PROVIDER`)**: el legacy mutaba `config.AI_PROVIDER` globalmente y `call_llm` lo leía como variable de módulo. Esto violaba el principio de "configuración por argumento, no por mutación global" (ver `docs/architecture.md` §3). El nuevo `call_llm` recibe `provider` como parámetro; el caller decide qué provider usar, lo que hace el código testeable sin monkeypatching del módulo y elimina un vector de bugs difíciles de reproducir (estado global mutable compartido entre threads o entre llamadas consecutivas con providers distintos).

**`_parse_json_payload` con `re.sub` en lugar de `lstrip("json")`**: el legacy usaba `lstrip("json")` para quitar la etiqueta de lenguaje del fence. `lstrip` actúa a nivel de caracteres, no de substring: `lstrip("json")` sobre `'{"jobs": 1}'` eliminaría las letras j, o, s, n del inicio, produciendo `'bs": 1}'`. Se usa `re.sub(r"^[Jj][Ss][Oo][Nn]\s*", "", ...)` que hace un match exacto de la secuencia completa "json" (insensible a mayúsculas) al inicio de la cadena.

**`None` en lugar de excepciones**: las funciones de cliente LLM son "borde del sistema" — llamadas a servicios externos. Según `docs/architecture.md` §5, deben devolver `None` en fallo definitivo + log, no propagar excepciones. Esto permite que los loops de extracción en `extraction.py` (feature #9) apliquen el circuit breaker sin `try/except` en cada llamada.

**`respx` para mocks HTTP**: el proyecto usa `httpx` para todas las llamadas HTTP. `respx` es la librería de mocking diseñada específicamente para `httpx`, con API limpia para interceptar por URL y simular respuestas de red sin servidor real. Alternativa descartada: `httpx.MockTransport` requiere más boilerplate y no soporta `side_effect` con listas de respuestas de forma tan concisa.

## Impacto en el pipeline

- **LLM (analysis/)**: este módulo es la capa HTTP para todo el pipeline de IA. Las features #9 (extraction), #10 (synthesis) y #11 (ai_analyzer) lo usarán vía `call_llm(prompt, provider=..., phase=...)`.
- **Configuración**: el módulo lee `config.ANTHROPIC_API_KEY`, `config.GEMINI_API_KEY`, `config.GROQ_API_KEY`, `config.CLAUDE_EXTRACTION_MODEL`, `config.CLAUDE_SYNTHESIS_MODEL`, `config.GEMINI_MODEL`, `config.GROQ_MODEL`. **Nunca lee ni muta `config.AI_PROVIDER`**.
- **Sin impacto en BD, scraping, Telegram**: este módulo es exclusivamente una capa de cliente HTTP.

## Explicación técnica

### `_parse_json_payload(text: str) -> dict | None`

Recibe texto crudo de una respuesta LLM y extrae el JSON que contiene.

1. `text.strip()` — elimina whitespace del contorno para normalizar.
2. `if "```" in text` — detecta si hay fences markdown. Solo si las hay, intentamos separar el contenido.
3. `for part in text.split("```")` — `split("```")` divide el texto en los puntos donde aparecen las tres comillas. Para un texto como ` ```json\n{...}\n``` `, producirá 3 partes: `""`, `"json\n{...}\n"`, `""`.
4. `re.sub(r"^[Jj][Ss][Oo][Nn]\s*", "", part.strip()).strip()` — quita la etiqueta de lenguaje al inicio de cada parte usando un regex que coincide solo con la cadena literal "json" (case-insensitive) seguida de opcional whitespace. Usar `re.sub` en vez de `lstrip` es crítico: `lstrip` actúa a nivel de caracteres del conjunto pasado, no de substring (ver "Por qué" arriba).
5. `if cleaned.startswith("{")` — confirmamos que la parte limpiada es el inicio de un objeto JSON. Guardamos en `text` y salimos del loop.
6. `json.loads(text)` — parsea el JSON. Si falla (texto no es JSON válido), captura `json.JSONDecodeError` y devuelve `None`.

### `call_claude(prompt, max_tokens, model, max_retries) -> dict | None`

1. Guarda temprana si `config.ANTHROPIC_API_KEY` está vacía: log + `return None`.
2. `model = model or config.CLAUDE_EXTRACTION_MODEL` — si no se pasa modelo explícito, usa el modelo de extracción (más barato). El dispatcher `call_llm` es quien elige entre extraction y synthesis.
3. `headers` con `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json` — los 3 headers requeridos por Anthropic Messages API.
4. `body["messages"]` usa el formato Messages API: lista de `{"role": "user", "content": prompt}`.
5. Bucle `for attempt in range(max_retries)`:
   - 429 → lee `response.headers.get("retry-after", 30)` — Claude pone el tiempo en segundos en este header. `int(float(...))` porque puede ser float como "2.5". `time.sleep(wait)` — bloqueante, por eso en tests se mockea con `patch("time.sleep")`.
   - `>= 500` → log de warning + `time.sleep(1)` de backoff mínimo + `continue` para reintentar.
   - `!= 200` (otros errores, p.ej. 401, 403) → `return None` inmediato sin reintentar.
   - 200 → `data["content"][0]["text"]` — estructura de Messages API donde el texto generado está en el primer elemento de `content`. Pasa por `_parse_json_payload` que extrae el dict.
6. Tras agotar retries: log warning + `return None`.

### `call_gemini(prompt, max_tokens, max_retries) -> dict | None`

1. URL construida dinámicamente: `f"{_GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"` — el modelo y la API key van en la URL (convención de Google AI Studio), no en headers.
2. `generationConfig.responseMimeType: "application/json"` — le dice a Gemini que formatee la salida como JSON, reduciendo la necesidad de parsear fences.
3. En 429: itera `error.details` buscando un campo `retryDelay` como `"31s"`. `rd[:-1]` quita la "s" final, `int(float(...))` convierte a entero, `+ 1` añade margen. Si no encuentra el campo, usa backoff lineal `30 * (attempt + 1)`.
4. Extracción de respuesta: `data["candidates"][0]["content"]["parts"][0]["text"]` — estructura anidada de Gemini. Verifica en cada nivel que la lista no esté vacía antes de indexar.

### `call_groq(prompt, max_tokens, max_retries) -> dict | None`

1. `Authorization: Bearer {api_key}` en header — formato OpenAI estándar que usa Groq.
2. Body con `model`, `messages`, `temperature`, `max_tokens` — formato idéntico a OpenAI Chat Completions.
3. En 429: `re.search(r"Please try again in ([0-9.]+)s", msg)` — regex que captura el número (puede ser float como "1.5") del mensaje de error de Groq. Más robusto que el `split` del legacy, que fallaba si el mensaje variaba ligeramente.
4. Extracción: `response.json()["choices"][0]["message"]["content"]` — formato OpenAI Chat Completions.

### `call_llm(prompt, max_tokens, phase, max_retries, provider) -> dict | None`

El dispatcher. Recibe `provider` como argumento explícito (nunca lee `config.AI_PROVIDER`).

- `provider == "claude"` → selecciona modelo según `phase` y llama `call_claude`. La selección `CLAUDE_SYNTHESIS_MODEL if phase == "synthesis" else CLAUDE_EXTRACTION_MODEL` hace que Sonnet se use para síntesis (requiere razonamiento complejo) y Haiku para extracción (alta frecuencia, coste menor).
- `provider == "gemini"` → llama `call_gemini` (modelo viene de `config.GEMINI_MODEL`, no de `phase`).
- `provider == "groq"` → llama `call_groq`.
- Provider desconocido → log error + `return None` (no levanta excepción, consistente con la política de la capa externa).

## Tests añadidos

| Test | Qué cubre |
|------|-----------|
| `test_parse_json_payload_fence_json_lowercase` | Fence ` ```json ` con etiqueta minúscula |
| `test_parse_json_payload_fence_json_uppercase` | Fence ` ```JSON ` con etiqueta mayúscula |
| `test_parse_json_payload_fence_no_lang` | Fence ` ``` ` sin etiqueta de lenguaje |
| `test_parse_json_payload_bare_json` | JSON pelado sin fences |
| `test_parse_json_payload_invalid_returns_none` | Texto no-JSON devuelve None |
| `test_parse_json_payload_empty_fence_no_json` | Fence con contenido no-JSON devuelve None |
| `test_call_claude_200_ok` | 200 OK: respuesta bien formada → devuelve dict |
| `test_call_claude_429_sleeps_and_retries` | 429 + header retry-after → sleep exacto + reintento |
| `test_call_claude_500_exhausts_retries` | 500 × max_retries → devuelve None |
| `test_call_claude_no_api_key_returns_none` | Sin API key → None sin llamada HTTP |
| `test_call_gemini_200_ok` | 200 OK con estructura candidates correcta |
| `test_call_gemini_429_retry_delay_sleeps_and_retries` | 429 + retryDelay "1s" → sleep(2) + reintento |
| `test_call_gemini_500_exhausts_retries` | 500 × max_retries → None |
| `test_call_groq_200_ok` | 200 OK con estructura choices correcta |
| `test_call_groq_429_retry_text_sleeps_and_retries` | 429 + "Please try again in 1s" → sleep(2) + reintento |
| `test_call_groq_500_exhausts_retries` | 500 × max_retries → None |
| `test_call_llm_synthesis_uses_synthesis_model` | phase='synthesis' → CLAUDE_SYNTHESIS_MODEL en el body |
| `test_call_llm_extraction_uses_extraction_model` | phase='extraction' → CLAUDE_EXTRACTION_MODEL en el body |
| `test_call_llm_provider_gemini_routes_correctly` | provider='gemini' enruta a Gemini API |
| `test_call_llm_provider_groq_routes_correctly` | provider='groq' enruta a Groq API |
| `test_call_llm_does_not_mutate_config_ai_provider` | config.AI_PROVIDER sigue igual tras llamar a call_llm |
| `test_call_llm_unknown_provider_returns_none` | Provider desconocido → None sin excepción |

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.23.1, respx-0.23.1
collected 22 items

tests/test_llm_clients.py ......................                         [100%]

============================== 22 passed in 0.13s ==============================

Total suite: 178 passed in 0.85s
```

`ruff check` pasa sin errores en ambos archivos nuevos.
`init.sh` termina con `[OK] Entorno listo.`
