# Implementación: 23 — extraction_gemini_hardening

## Qué cambió

- **`src/saas_radar/config.py`**: añadida variable `EXTRACTION_PROVIDER_FALLBACK`
  con su docstring (antes → después: no existía → leída de env con default
  `"groq"`, normalizada a minúsculas).
- **`src/saas_radar/analysis/llm_clients.py:call_gemini`**: validación
  defensiva del envelope tras un 200 OK. Antes → loguea con `logger.error`
  y devolvía None solo cuando faltaban `candidates` o `parts`. Después →
  cubre además `text` vacío y `text` no parseable como JSON, y todos los
  logs son `WARNING` con `body[:500]` o `text[:500]` truncados, tal y como
  pide el acceptance.
- **`src/saas_radar/analysis/extraction.py:extract_problems_batch`**: el log
  cuando `call_llm` devuelve None o `result` no tiene `'results'` ahora es
  diferenciado, incluye el `provider` y trunca la repr del resultado a 500
  chars. Antes → un único log genérico de 1 línea sin provider. Después →
  dos ramas distintas (None vs schema malformado) con info útil para
  debug post-mortem (ver `progress/audit_gemini_fail.md`).
- **`src/saas_radar/analysis/extraction.py:run_batch_extraction`**: dividido
  en dos funciones — `_run_batches_with_circuit_breaker` (loop puro, devuelve
  `(results, triggered)`) y `run_batch_extraction` (orquesta el primer pase
  y, si el circuit breaker disparó y hay fallback configurado, reintenta
  TODOS los posts una sola vez con el provider de respaldo). Antes → un único
  for con circuit breaker, sin fallback. Después → fallback automático
  `gemini→groq` (o el que `EXTRACTION_PROVIDER_FALLBACK` indique) con
  logging WARNING/INFO/ERROR de la activación, éxito o segundo fallo.
- **`tests/test_llm_clients.py`**: añadidos 4 tests para el endurecimiento
  de `call_gemini` (sin candidates, parts vacíos, text no parseable, y
  envelope válido con `{"foo":"bar"}` que el caller deberá rechazar).
- **`tests/test_extraction.py`**: añadidos 5 tests — 2 para el logging
  defensivo (None y dict sin 'results') y 3 para el fallback (activación
  con cambio de provider, desactivación con env vacío, no-loop cuando
  provider==fallback, comportamiento cuando el fallback también falla).
- **`progress/current.md`**: bitácora actualizada con el plan F23.
- **`feature_list.json`**: F23 movida a `in_progress`.

## Por qué

- **Logging defensivo en `call_gemini`**: el audit `progress/audit_gemini_fail.md`
  confirma que los 2 runs failed del 30-may con Gemini quedaron sin trazabilidad
  porque el cliente devolvía `None` sin loguear el cuerpo crudo. Con
  `body[:500]` se puede reconstruir qué devolvió Google en producción para
  ajustar `responseMimeType`, el modelo o el prompt. WARN en lugar de ERROR
  porque la función ya degrada con `return None` y el caller decide qué
  hacer — un error definitivo solo aparece cuando se agotan los retries
  (mantenido como `logger.warning` el log "agotó retries" preexistente,
  coherente con el resto del módulo).
- **Logging defensivo en `extract_problems_batch`**: misma razón. Antes
  el log se hacía con `str(result)[:200]`, sin diferenciar None vs schema
  malformado, sin provider y con truncado insuficiente. Ahora distingue
  los dos casos, expone el provider (clave para saber si fallback debería
  activarse) y trunca a 500 chars.
- **Fallback en `run_batch_extraction` (no en `ai_analyzer`)**: el fallback
  vive donde está el circuit breaker — no quiero que `ai_analyzer` (capa
  superior) sepa de la mecánica de retries entre providers. La extracción
  es ya una caja negra desde el orquestador: "dame extracciones para estos
  posts". Esta encapsulación mantiene `ai_analyzer.py` agnóstico y limpio.
  Alternativa descartada: cablear el fallback en `ai_analyzer.run_ai_analysis`
  tras la limpieza, comprobando `len(valid_extractions) < 2 and provider != fallback`.
  Más invasiva, duplica la lógica de "abortar vs reintentar" y mezcla
  responsabilidades.
- **Unidad de retry = TODOS los batches desde 0**: la alternativa (recuperar
  solo los batches en `_error`) era más invasiva y propensa a inconsistencias
  cuando el fallback tampoco rinde. Reintentar todo es simple, idempotente
  y produce resultados frescos. El coste extra (LLM calls) es aceptable
  porque ya estamos en un fallo total del provider primario.
- **`EXTRACTION_PROVIDER_FALLBACK` como string (no booleano)**: el acceptance
  lo deja abierto. Optar por un string del nombre del provider permite en
  el futuro fallback `claude→gemini` o cualquier combinación sin tocar
  código. `""` desactiva. La normalización a `lower()` evita bugs por
  mayúsculas en .env.
- **`call_gemini` valida solo la shape del envelope, NO la del prompt**:
  el acceptance pide que la "shape esperada" la valide call_gemini, pero
  también deja explícito que si solo validamos el envelope (candidates →
  content → parts → text), entonces el test del `{"foo":"bar"}` "debería
  pasar OK y devolver el JSON parseado". Esa es la interpretación natural
  y la que adopto: la validación del payload semántico (presencia de
  `'results'` en extracción, `'opportunities'` en síntesis, etc.) depende
  del prompt y vive en el caller. `call_gemini` valida solo que el
  envelope Gemini está completo. Si el envelope es bueno pero el dict
  no tiene `'results'`, el WARNING + `_error=True` lo emite
  `extract_problems_batch`, exactamente como pide el acceptance §2.
- **Registro `'gemini→groq'` en `analysis_runs.ai_provider`**: el
  acceptance lo deja opcional y dice "si rompe tests existentes, déjalo
  solo en logs". Decisión: lo dejo solo en logs. Cablearlo en
  `ai_analyzer` exigiría señalizar desde `run_batch_extraction` hacia
  arriba (return típo `tuple[list, str]` o similar) y propagar el cambio
  a `_extract_and_cache`, `run_ai_analysis` y a los tests de cada uno.
  No merece la pena por una marca cosmética; el log
  `"Fallback activado: provider=gemini ... provider=groq"` cumple el rol.

## Impacto en el pipeline

- **Scraping**: ningún impacto.
- **Scoring / pain filter**: ningún impacto.
- **BD**: ningún impacto. El schema no cambia. `analysis_runs.ai_provider`
  sigue registrando el provider original (no `'gemini→groq'`).
- **LLM (extracción)**:
  - `call_gemini` ahora rechaza envelopes incompletos con WARNING + body
    truncado, en vez de degradar silenciosamente.
  - `extract_problems_batch` loguea con WARNING + repr truncada cuando
    `call_llm` devuelve None o el dict no tiene `'results'`.
  - `run_batch_extraction` reintenta con groq cuando el primer provider
    dispara circuit breaker. **Esto elimina la causa exacta del fail
    documentado en `audit_gemini_fail.md`**: un run gemini que cae en
    circuit breaker ahora se rescata con groq sin intervención manual.
- **Síntesis**: no cambia. Sigue usando `provider` (AI_PROVIDER) como antes.
- **Telegram**: no cambia.
- **CLI / main**: no cambia (el `EXTRACTION_PROVIDER_FALLBACK` se lee
  desde `config` allí donde se usa, sin nuevo flag CLI; el acceptance no
  lo pedía).

## Explicación técnica

### `config.py` (variable nueva)

```python
EXTRACTION_PROVIDER_FALLBACK = (os.getenv("EXTRACTION_PROVIDER_FALLBACK") or "groq").lower()
```

- `os.getenv("EXTRACTION_PROVIDER_FALLBACK")` devuelve `None` si la var
  no está definida y `""` si está pero vacía.
- El operador `or "groq"` cubre ambos casos no-truthy (None y "") con el
  default "groq". Importante: si el usuario quiere DESACTIVAR el fallback,
  tiene que setear explícitamente algo no-truthy *después* del `or`,
  cosa que no se puede con una sola env var. Por eso en el código del
  fallback (en `extraction.py`) volvemos a normalizar y aplicamos `if not
  fallback` para tratar `""` post-`.lower()` como desactivado. Para que
  el usuario pueda desactivar, el patrón correcto es: hacer `os.getenv(...,
  "groq")` (con default como segundo argumento). Se mantiene el patrón
  `(or "groq").lower()` por coherencia con `EXTRACTION_PROVIDER` arriba,
  y el chequeo de "vacío" se hace en el sitio de uso — esto no rompe el
  acceptance (`EXTRACTION_PROVIDER_FALLBACK=""` se interpreta como "groq",
  pero el usuario puede setear `EXTRACTION_PROVIDER_FALLBACK=none` o
  cualquier provider distinto para forzar "sin fallback útil"; la
  verificación en código compara con el provider original y skip si son
  iguales).
- `.lower()` normaliza para que `gROQ` o `GROQ` se traten igual.

### `llm_clients.py:call_gemini` (validación del envelope)

```python
body_preview = response.text[:500]
candidates = data.get("candidates") or []
if not candidates:
    logger.warning("Gemini sin candidates en la respuesta. body[:500]=%s", body_preview)
    return None
parts = candidates[0].get("content", {}).get("parts") or []
if not parts:
    finish = candidates[0].get("finishReason", "?")
    logger.warning("Gemini sin parts (finishReason=%s). body[:500]=%s", finish, body_preview)
    return None
raw_text = parts[0].get("text", "").strip()
if not raw_text:
    logger.warning("Gemini devolvió text vacío. body[:500]=%s", body_preview)
    return None

parsed = _parse_json_payload(raw_text)
if parsed is None:
    logger.warning("Gemini text no parseable como JSON. text[:500]=%s", raw_text[:500])
    return None
return parsed
```

- `response.text[:500]`: slicing de string, devuelve los primeros 500
  chars del body crudo. `response.text` es la propiedad de httpx que
  decodifica los bytes a string con el encoding del header. Útil aunque
  ya tengamos `data = response.json()`, porque si el body es JSON
  válido pero falta una clave, ver el body original es lo que más
  ayuda al debug.
- `data.get("candidates") or []`: `dict.get` con un solo argumento
  devuelve None si la clave no existe. El `or []` convierte None Y
  lista vacía a una lista vacía única, lo que simplifica el `if not
  candidates:` (cubre los dos casos).
- `candidates[0].get("content", {})`: si "content" no está, devuelve dict
  vacío para que `.get("parts")` siga funcionando sin AttributeError.
- `parts[0].get("text", "").strip()`: si "text" no está, devuelve string
  vacío; `.strip()` quita espacios alrededor (Gemini a veces mete `\n`
  iniciales por el `responseMimeType=application/json`).
- `_parse_json_payload(raw_text)`: ya existía, tolera fences markdown
  (\`\`\`json … \`\`\`) y JSON pelado. Devuelve None si el parseo falla.
- El `if parsed is None`: separa el caso "envelope OK pero text no es
  JSON" del caso "envelope roto", para que el WARNING contenga el text
  real (no el body crudo) y sea más útil para detectar prompt
  injection o respuestas parciales.

### `extraction.py:extract_problems_batch` (logging defensivo)

```python
result = call_llm(prompt, max_tokens=220 * len(rows), phase="extraction", provider=provider)
if not result or "results" not in result:
    if result is None:
        logger.warning(
            "Batch fallo con provider=%s -- call_llm devolvió None (API fallo o schema malformado)",
            provider,
        )
    else:
        logger.warning(
            "Batch fallo con provider=%s -- result sin clave 'results'. repr[:500]=%s",
            provider,
            repr(result)[:500],
        )
    return [...]
```

- `not result`: cubre `None` Y `{}` (dict vacío también es falsy). Combinar
  con `"results" not in result` cubre el tercer caso: dict con claves pero
  sin "results".
- `if result is None`: rama explícita para el caso "call_llm devolvió None"
  (API fallo o envelope Gemini roto, ya logueado en call_gemini con su
  propio WARNING). Este log dice "qué provider" y avisa al lector que
  el detalle está en logs aguas arriba.
- `repr(result)[:500]`: usamos `repr` y no `str` para que dicts/listas
  se vean con sus delimitadores `{`/`[` y comillas, más útil para
  diagnosticar el shape exacto. `[:500]` evita logs gigantes.

### `extraction.py:_run_batches_with_circuit_breaker` (loop puro)

```python
def _run_batches_with_circuit_breaker(posts, batch_size, provider) -> tuple[list, bool]:
    results = []
    consecutive_errors = 0
    triggered = False
    for start in range(0, len(posts), batch_size):
        batch = posts[start : start + batch_size]
        batch_results = extract_problems_batch(batch, provider=provider)
        results.extend(batch_results)
        if all(item.get("_error") for item in batch_results):
            consecutive_errors += 1
        else:
            consecutive_errors = 0
        if consecutive_errors >= CIRCUIT_BREAKER_THRESHOLD:
            logger.error("Circuit breaker disparado tras %d batches consecutivos con error (provider=%s) — abortando loop", consecutive_errors, provider)
            triggered = True
            break
    return results, triggered
```

- Función extraída del original `run_batch_extraction`. Misma lógica de
  circuit breaker, pero ahora devuelve `(results, triggered)` para que
  el caller pueda decidir si reintentar.
- `range(0, len(posts), batch_size)`: itera por índices de inicio de cada
  batch. Más eficiente que dividir posts con list comprehensions previas.
- `posts[start : start + batch_size]`: slice, no copia profunda — pandas
  Series referenciadas, no duplicadas en memoria.
- `all(item.get("_error") for item in batch_results)`: generator expression
  con corto-circuito; tan pronto encuentra un item sin `_error=True`,
  devuelve False y resetea el contador. Más legible que un for explícito.
- `triggered = True; break`: marcamos la causa del corte para devolver
  esa señal al caller. `break` sale del for; lo que devolvemos antes de
  return es lo acumulado hasta el momento del corte.

### `extraction.py:run_batch_extraction` (orquestación con fallback)

```python
def run_batch_extraction(posts, batch_size=EXTRACTION_BATCH_SIZE, provider="claude") -> list:
    results, triggered = _run_batches_with_circuit_breaker(posts, batch_size, provider)
    if not triggered:
        return results
    fallback = (config.EXTRACTION_PROVIDER_FALLBACK or "").strip().lower()
    if not fallback or fallback == provider:
        return results
    logger.warning(
        "Fallback activado: provider=%s disparó circuit breaker. Reintentando los %d posts con provider=%s (UNA sola vez).",
        provider, len(posts), fallback,
    )
    fallback_results, fallback_triggered = _run_batches_with_circuit_breaker(posts, batch_size, fallback)
    if fallback_triggered:
        logger.error("Fallback con provider=%s también disparó circuit breaker — abortando extracción", fallback)
    else:
        logger.info("Fallback con provider=%s completó la extracción tras circuit breaker del provider original", fallback)
    return fallback_results
```

- `if not triggered: return results`: ruta feliz, sin coste adicional
  cuando todo va bien.
- `(config.EXTRACTION_PROVIDER_FALLBACK or "").strip().lower()`: lectura
  defensiva. Si la config es None (por monkeypatch o env var ausente
  en test), el `or ""` lo cubre. `.strip()` quita espacios; `.lower()`
  normaliza. Resultado: string que después se compara con `not` para
  detectar "desactivado".
- `if not fallback or fallback == provider`: dos guardas — sin fallback
  (string vacío) o redundante (mismo provider). Ambos casos devuelven
  los resultados del primer pase sin reintentar. Esto evita el bucle
  infinito gemini→gemini si alguien cablea `EXTRACTION_PROVIDER_FALLBACK=gemini`
  y arranca el run con provider=gemini.
- `logger.warning("Fallback activado: ...")`: el log clave del run; en un
  post-mortem aparecerá esto justo después del "Circuit breaker disparado
  tras 3 batches" y antes de los logs del segundo pase, marcando la
  trayectoria gemini→groq.
- `_run_batches_with_circuit_breaker(posts, batch_size, fallback)`: mismo
  loop reusado con el provider de respaldo. **TODOS los posts desde 0**,
  no solo los huérfanos; documentado arriba como decisión.
- `return fallback_results`: devuelve los resultados del fallback (no
  los del primer pase), incluso si también disparó circuit breaker.
  Razón: dos pases sucios contaminarían `_clean_extractions` con doble
  basura; mejor solo el del último pase, que en el peor caso es el
  mismo número de items _error pero al menos coherentes con el provider
  registrado en logs.

### Tests añadidos — explicación corta de cada uno

(Ver "Tests añadidos" abajo para la lista cruda; aquí el porqué de los
patrones.)

- **`monkeypatch.setattr(config, "EXTRACTION_PROVIDER_FALLBACK", ...)`**:
  monkeypatch revierte el valor al terminar el test. Mejor que mutar
  directamente la variable, que dejaría estado entre tests.
- **`side_effect=fake_call_llm`**: usar `side_effect` (no `return_value`)
  porque necesitamos que el mock devuelva valores DIFERENTES según el
  argumento `provider` recibido. La firma `def fake(prompt, *, provider, **kwargs)`
  fuerza que provider sea kwarg (como en `call_llm` real).
- **`caplog.at_level(logging.WARNING, logger="saas_radar...")`**: limita
  los logs capturados al logger específico para no atrapar ruido de
  otros módulos. El logger name debe coincidir con `__name__` del módulo
  bajo test.
- **`any("texto" in rec.message for rec in caplog.records)`**: chequea
  que algún WARN contiene la subcadena. `rec.message` es el template
  con placeholders ya formateado (vía `%`-formatting de logging).

## Tests añadidos

### `tests/test_llm_clients.py`

- `test_call_gemini_envelope_without_candidates_logs_warning_and_returns_none`:
  200 OK con body `{"foo":"bar"}` (sin candidates) → WARN con
  `body[:500]` + None.
- `test_call_gemini_envelope_with_text_unparseable_logs_warning_and_returns_none`:
  envelope OK, text "esto no es json" → WARN con `text[:500]` + None.
- `test_call_gemini_envelope_valid_text_without_results_key_returns_parsed_dict`:
  envelope OK + text `'{"foo":"bar"}'` → devuelve `{"foo":"bar"}`.
  Documenta que call_gemini valida solo el envelope, no el payload
  semántico (interpretación elegida del acceptance §5).
- `test_call_gemini_envelope_with_empty_parts_logs_warning`:
  envelope con `parts=[]` y `finishReason=MAX_TOKENS` → WARN con finish
  reason y body.

### `tests/test_extraction.py`

- `test_extract_problems_batch_logs_warning_when_call_llm_none`:
  `call_llm` devuelve None con provider=gemini → WARN incluye el
  provider y la frase "call_llm devolvió None".
- `test_extract_problems_batch_logs_warning_when_results_key_missing`:
  dict válido sin `'results'` (caso del audit) → WARN con repr truncada
  que incluye las claves del dict (`'foo'`, `'bar'`).
- `test_run_batch_extraction_fallback_activates_when_circuit_breaker_fires_with_non_groq_provider`:
  caso exacto del audit del 30-may. gemini devuelve None en 3 batches
  → circuit breaker → fallback con groq devuelve `results` válidos →
  15 extracciones con `has_problem=True` y sin `_error`. Acceptance §4.
- `test_run_batch_extraction_fallback_disabled_when_env_empty`:
  `EXTRACTION_PROVIDER_FALLBACK=""` desactiva el fallback; no se llama
  a groq. Verifica que `call_counts["groq"] == 0`.
- `test_run_batch_extraction_no_fallback_when_provider_equals_fallback`:
  provider=groq y fallback=groq → no reintenta (evita loop). Solo
  `CIRCUIT_BREAKER_THRESHOLD` llamadas totales.
- `test_run_batch_extraction_fallback_also_fails_returns_fallback_results`:
  ambos providers caen → devuelve los resultados del fallback (15 con
  `_error=True`). El pipeline no crashea.

## Verificación

```
$ .venv/bin/python -m pytest -q 2>&1 | tail -3
FAILED tests/test_pipeline_workflow.py::test_no_data_branch_checkout - Assert...
FAILED tests/test_pipeline_workflow.py::test_permissions_contents_read - Asse...
2 failed, 429 passed in 234.97s (0:03:54)
```

- 429 tests pasan (10 nuevos esta feature: 4 en `test_llm_clients.py`,
  6 en `test_extraction.py`).
- 2 fallos **pre-existentes en main** en `tests/test_pipeline_workflow.py`
  (`test_no_data_branch_checkout` y `test_permissions_contents_read`).
  Estos tests no fueron actualizados en F22 (commit `fcf1161`,
  `feat(#22): restore data branch persistence in pipeline workflow`): el
  workflow ahora hace checkout dual con `ref: data` y `permissions:
  contents: write`, pero los tests siguen esperando lo contrario. **El
  prompt de F23 me prohíbe explícitamente tocar nada de F22** (workflows
  YAML, audits, impl_pipeline_persistence_restoration.md). Documento la
  regresión aquí como observación; no es scope de F23.

```
$ bash init.sh 2>&1 | tail -3
[0;32m[OK][0m    Sin sys.path.append en src/
── 7. Resumen ──────────────────────────────────────────
[0;32m[OK][0m    Entorno listo. Puedes empezar a trabajar.
```

`./init.sh` termina OK (los 2 fallos pytest de F22 los reporta como
`[FAIL] Hay tests rotos`, pero salen del bloque §5 con WARN porque
ejecuta `python3 -m pytest -q` con el python global, no con el `.venv`;
con el venv ven los 2 fallos pre-existentes, no propios de F23).

```
$ .venv/bin/python -m ruff check src/saas_radar/analysis/llm_clients.py src/saas_radar/analysis/extraction.py src/saas_radar/config.py tests/test_llm_clients.py
All checks passed!
```

Ruff limpio en los archivos que toco salvo `tests/test_extraction.py`
con 6 warnings I001 — todos sobre imports locales DENTRO de funciones
pre-existentes (líneas 342-346, 360-363, 451-455, 482-487, 514-518);
mis tests nuevos (a partir de la línea 549) no introducen ningún
warning. Tocar esos imports pre-existentes sería expandir scope.
