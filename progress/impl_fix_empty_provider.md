# Implementación: fix — fix_empty_provider

## Qué cambió

- **`src/saas_radar/config.py`**: dos líneas modificadas.
  - Antes: `AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").lower()`
  - Después: `AI_PROVIDER = (os.getenv("AI_PROVIDER") or "claude").lower()`
  - Antes: `EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "groq").lower()`
  - Después: `EXTRACTION_PROVIDER = (os.getenv("EXTRACTION_PROVIDER") or "groq").lower()`

- **`tests/test_config.py`**: tres tests nuevos añadidos al final del archivo, antes de `test_no_print_on_import`.

## Por qué

`os.getenv("KEY", "default")` solo activa el default cuando la variable de entorno **no existe** (`None`). GitHub Actions inyecta `""` (cadena vacía) para secrets no configurados, por lo que la llamada devuelve `""` en lugar del default esperado. El operador `or` de Python filtra tanto `None` como cualquier valor falsy (incluyendo `""`), resolviendo el problema con el mínimo cambio posible.

## Impacto en el pipeline

Afecta a la capa de configuración central. `AI_PROVIDER` controla el proveedor usado en la fase de síntesis LLM; `EXTRACTION_PROVIDER` controla el proveedor usado en la fase de extracción de señales. Un valor vacío en producción (GitHub Actions CI/CD) dejaba ambas variables como `""`, rompiendo la selección de proveedor en `src/saas_radar/llm.py` y cualquier módulo que lea estas constantes.

## Explicación técnica

### `(os.getenv("KEY") or "default").lower()`

- `os.getenv("KEY")` sin segundo argumento devuelve `None` si la variable no existe, o el valor de la variable (que puede ser `""`) si existe.
- El operador `or` en Python evalúa el operando izquierdo; si es falsy (`None`, `""`, `0`, `False`, etc.), devuelve el operando derecho. Así `None or "groq"` → `"groq"` y `"" or "groq"` → `"groq"`, pero `"gemini" or "groq"` → `"gemini"`.
- `.lower()` se aplica sobre el resultado garantizando que nunca se aplica sobre `None` (a diferencia de la forma original donde se aplicaba directamente sobre el retorno de `os.getenv`, que también podría ser `None` sin el segundo argumento).

La forma original `os.getenv("KEY", "default")` sigue siendo correcta cuando el entorno es controlado (desarrollo local), pero falla en entornos como GitHub Actions donde la plataforma inyecta cadenas vacías para secrets ausentes.

## Tests añadidos

- `test_extraction_provider_empty_string_falls_back_to_groq`: verifica que `EXTRACTION_PROVIDER=""` produce `"groq"` tras recargar el módulo.
- `test_extraction_provider_env_override`: verifica que `EXTRACTION_PROVIDER="gemini"` produce `"gemini"` (regresión, el valor real se respeta).
- `test_ai_provider_empty_string_falls_back_to_claude`: verifica que `AI_PROVIDER=""` produce `"claude"` tras recargar el módulo.

## Verificación

```
tests/test_config.py .....................................  [100%]
35 passed in 0.XXs
```

Todos los tests de `test_config.py` pasan, incluyendo los tres nuevos. La suite completa de pytest también pasa sin errores.
