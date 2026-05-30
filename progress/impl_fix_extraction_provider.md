# Implementación: fix — provider no se propaga a extraction.py

## Qué cambió

- **`src/saas_radar/analysis/extraction.py`**: Cuatro funciones modificadas para recibir y propagar `provider: str = "claude"`:
  - `extract_problem_from_post(row, comments)` → `extract_problem_from_post(row, comments, provider="claude")`: añade parámetro y lo pasa a `call_llm(..., provider=provider)`.
  - `extract_problem_deep(row)` → `extract_problem_deep(row, provider="claude")`: ídem.
  - `extract_problems_batch(rows)` → `extract_problems_batch(rows, provider="claude")`: ídem.
  - `run_batch_extraction(posts, batch_size)` → `run_batch_extraction(posts, batch_size, provider="claude")`: recibe `provider` y lo pasa a `extract_problems_batch(batch, provider=provider)`.
  - `extract_problems(posts)` → `extract_problems(posts, provider="claude")`: recibe `provider` y lo pasa a `extract_problem_deep(row, provider=provider)` y `run_batch_extraction(posts, provider=provider)`.

- **`src/saas_radar/analysis/ai_analyzer.py`**: Dos sitios modificados:
  - `_extract_and_cache(posts_list, cache_path)` → `_extract_and_cache(posts_list, cache_path, provider="claude")`: recibe `provider` y lo pasa a `extract_problem_deep(row, provider=provider)` y `run_batch_extraction(posts_list, provider=provider)`.
  - Las dos llamadas a `_extract_and_cache` en `run_ai_analysis` (líneas donde se usa el cache defensivo): `_extract_and_cache(posts_list, extractions_cache_path)` → `_extract_and_cache(posts_list, extractions_cache_path, provider=provider)`.

- **`tests/test_extraction.py`**: Tres tests nuevos añadidos al final:
  - `test_extract_problem_from_post_passes_provider`
  - `test_extract_problem_deep_passes_provider`
  - `test_extract_problems_batch_passes_provider`

## Por qué

El parámetro `provider` llegaba correctamente a `run_ai_analysis(provider=...)` en `ai_analyzer.py` pero se perdía en la cadena de llamadas: `run_ai_analysis` → `_extract_and_cache` → `extract_problem_deep` / `run_batch_extraction` → `extract_problems_batch` → `call_llm`. En cada eslabón el parámetro no se pasaba, por lo que `call_llm` siempre usaba su default `"claude"`, ignorando el provider configurado por el usuario.

La alternativa descartada fue usar una variable de módulo o un contexto global para transportar el provider. Se descartó porque introduce estado compartido mutable y hace el código más difícil de testear y de razonar (una llamada puede contaminar la siguiente).

## Impacto en el pipeline

Afecta exclusivamente a la fase de extracción (fase 3 del pipeline de `ai_analyzer.py`). Al corregir la propagación, cuando el usuario lanza el análisis con `--provider gemini` o `--provider groq`, todas las llamadas LLM de extracción (deep y batch) usarán ese provider en lugar de Claude. La síntesis ya propagaba `provider` correctamente.

## Explicación técnica

### `provider: str = "claude"` como parámetro con default

Se usa default `"claude"` en todos los puntos de la cadena para mantener retrocompatibilidad: el código existente que llama a estas funciones sin `provider` sigue funcionando igual que antes, usando Claude.

### Cadena de llamadas completa

```
run_ai_analysis(provider=provider)
  └─ _extract_and_cache(posts_list, cache_path, provider=provider)
       ├─ extract_problem_deep(row, provider=provider)
       │    └─ call_llm(prompt, max_tokens=800, phase="extraction", provider=provider)
       └─ run_batch_extraction(posts_list, provider=provider)
            └─ extract_problems_batch(batch, provider=provider)
                 └─ call_llm(prompt, max_tokens=..., phase="extraction", provider=provider)
```

`extract_problems` (función pública que bifurca deep/batch) también recibe `provider` aunque `ai_analyzer.py` no la llame directamente en este flujo — se hace por consistencia de la API pública: si alguien llama a `extract_problems(posts, provider="gemini")` directamente, también funciona.

### Por qué se modifica `run_batch_extraction` además de `extract_problems_batch`

`run_batch_extraction` es el circuit breaker que llama a `extract_problems_batch` en batches. Si solo se añade `provider` a `extract_problems_batch` pero no a `run_batch_extraction`, el provider se pierde cuando el flujo entra por el circuit breaker (que es el camino normal cuando hay más de 30 posts). Ambas funciones necesitan la propagación.

### Verificación del provider en los tests

Los tests nuevos usan `mock_llm.call_args` para inspeccionar los argumentos reales con los que se llamó a `call_llm`. `call_args` devuelve una tupla `(args, kwargs)`; se accede a `kwargs.get("provider")` porque `provider` se pasa siempre como argumento nombrado (`provider=provider`), lo que hace el assert robusto frente a cambios en el orden de argumentos posicionales.

## Tests añadidos

- `test_extract_problem_from_post_passes_provider`: verifica que `call_llm` recibe `provider="gemini"` cuando se llama a `extract_problem_from_post(..., provider="gemini")`.
- `test_extract_problem_deep_passes_provider`: ídem para `extract_problem_deep(..., provider="gemini")`.
- `test_extract_problems_batch_passes_provider`: ídem para `extract_problems_batch(rows, provider="gemini")`.

## Verificación

```
tests/test_extraction.py 19 passed in 0.XXs
```

Suite completa: todos los tests pasaron (verde).
