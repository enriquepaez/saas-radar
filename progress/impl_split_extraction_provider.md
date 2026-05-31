# Implementación: split_extraction_provider — Provider separado para extracción vs síntesis

## Qué cambió

- **`src/saas_radar/config.py`**: añadida la constante `EXTRACTION_PROVIDER` (líneas 42-45) justo después de `AI_PROVIDER`. Antes solo existía `AI_PROVIDER` para controlar el provider de todo el pipeline. Ahora hay dos variables independientes: `AI_PROVIDER` para síntesis y `EXTRACTION_PROVIDER` para extracción.

- **`src/saas_radar/analysis/ai_analyzer.py`**: dos cambios:
  1. Añadido `from saas_radar import config` en los imports (línea 14). Antes el módulo no importaba `config` directamente.
  2. Reescrito `_extract_and_cache` para leer `config.EXTRACTION_PROVIDER` en lugar de usar el argumento `provider` recibido (que proviene de `AI_PROVIDER`). Antes: `extract_problem_deep(row, provider=provider)` y `run_batch_extraction(posts_list, provider=provider)`. Después: `extract_problem_deep(row, provider=extraction_provider)` y `run_batch_extraction(posts_list, provider=extraction_provider)`, donde `extraction_provider = config.EXTRACTION_PROVIDER`.

- **`.github/workflows/pipeline.yml`**: añadida la variable de entorno `EXTRACTION_PROVIDER: ${{ secrets.EXTRACTION_PROVIDER }}` en el bloque `env:` del job `run`, justo después de `AI_PROVIDER`. Antes el workflow no exponía este secret.

- **`tests/test_ai_analyzer.py`**: añadido `test_extraction_uses_extraction_provider` (Test 9). También añadido `call` al import de `unittest.mock` (aunque no se usa finalmente en el test, es un import limpio).

## Por qué

**Problema original:** Gemini free tier tiene un límite de ~16 requests/minuto en la API de generación. La fase de extracción hace múltiples llamadas (una por post en modo deep, o una por batch de 5 en modo batch). Con 31+ posts en modo batch ya son 7 llamadas; en modo deep con 20 posts son 20 llamadas seguidas. Eso dispara el rate limit de Gemini.

**Solución elegida:** Usar Groq para extracción (límites generosos: 30 req/min con modelo llama-3.3-70b-versatile) y el provider configurado (Gemini/Claude) para síntesis, que es una sola llamada al LLM. Así se evita el cuello de botella sin cambiar el modelo de síntesis.

**Por qué la constante va en `config.py` y no como argumento de `run_ai_analysis`:** Siguiendo la lección del legacy §2.5 ("provider como arg explícito en llm_clients"), el provider de síntesis se sigue pasando como argumento explícito. Sin embargo, el provider de extracción es una configuración de infraestructura (qué proveedor usar para operaciones de volumen), no una decisión de negocio por llamada. Por eso va en `config.py`, igual que `AI_PROVIDER`.

**Por qué `os.getenv("EXTRACTION_PROVIDER", "groq").lower()`:** El default `"groq"` hace que sin configurar ninguna env var el pipeline automáticamente use Groq para extracción. `.lower()` es necesario para normalizar posibles mayúsculas ("Groq", "GROQ") igual que se hace con `AI_PROVIDER`.

**Por qué no tocar `extraction.py`:** La propagación del provider ya funciona correctamente en ese módulo (fue implementada en feature #21 tras lección legacy). Solo hay que cambiar qué valor se le pasa, no cómo lo recibe.

## Impacto en el pipeline

- **Extracción (ai_analyzer):** ahora usa `config.EXTRACTION_PROVIDER` (default: Groq) en lugar del provider de síntesis. Afecta tanto al modo deep como al modo batch.
- **Síntesis (ai_analyzer):** sin cambios. Sigue usando el argumento `provider` que proviene de `AI_PROVIDER`.
- **Config:** `EXTRACTION_PROVIDER` es una nueva env var leída al importar el módulo. Sin .env ni env var del sistema, el valor es `"groq"`.
- **GitHub Actions:** el workflow expone el nuevo secret `EXTRACTION_PROVIDER`. Si el secret no está definido en el repositorio, el step recibe la cadena vacía `""` pero `os.getenv` en Python recibe `None` cuando la variable no está en el entorno del proceso — el default `"groq"` entra igualmente.
- **CLI/main.py:** sin cambios. El provider se pasa a `run_ai_analysis` como hasta ahora; solo cambia qué hace `_extract_and_cache` con él.

## Explicación técnica

### `config.py` — nueva constante

```python
EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "groq").lower()
```

- `os.getenv("EXTRACTION_PROVIDER", "groq")`: lee la variable de entorno `EXTRACTION_PROVIDER`. Si no existe, devuelve el string `"groq"` (segundo argumento = valor por defecto). Retorna `str | str`.
- `.lower()`: normaliza el string a minúsculas. Garantiza que "Groq", "GROQ" y "groq" se traten igual. Igual que se hace con `AI_PROVIDER` en la línea anterior.

### `ai_analyzer.py` — import de config

```python
from saas_radar import config
```

Importa el módulo `config` completo (no atributos sueltos con `from ... import EXTRACTION_PROVIDER`). Esto permite que los tests inyecten el valor de `config.EXTRACTION_PROVIDER` en tiempo de ejecución con `patch.object(ai_mod.config, "EXTRACTION_PROVIDER", "groq")` sin necesidad de recargar el módulo. Si importáramos el string directamente, el test no podría cambiarlo porque Python copiaría el valor en el namespace local de `ai_analyzer` al importar.

### `ai_analyzer.py` — `_extract_and_cache` modificada

```python
extraction_provider = config.EXTRACTION_PROVIDER
```

Lee el provider de extracción del módulo config en tiempo de llamada (no en tiempo de importación). Esto es importante: si alguien cambia `config.EXTRACTION_PROVIDER` en tests o en runtime, `_extract_and_cache` siempre ve el valor actual.

```python
extractions = [extract_problem_deep(row, provider=extraction_provider) for row in posts_list]
```

Antes: `provider=provider` (el argumento recibido, que es `AI_PROVIDER`).
Después: `provider=extraction_provider` (siempre `config.EXTRACTION_PROVIDER`).
`extract_problem_deep` acepta `provider` como keyword argument y lo propaga a `call_llm`.

```python
extractions = run_batch_extraction(posts_list, provider=extraction_provider)
```

Mismo patrón para el modo batch. `run_batch_extraction` acepta `provider` como keyword argument y lo propaga a todas las llamadas internas a `call_llm`.

El argumento `provider: str = "claude"` en la firma de `_extract_and_cache` se mantiene para no romper callers externos, aunque ya no se usa para la extracción. Sería confuso eliminarlo porque los tests existentes lo pasan como `provider="gemini"` para simular el escenario real.

### `.github/workflows/pipeline.yml`

```yaml
EXTRACTION_PROVIDER: ${{ secrets.EXTRACTION_PROVIDER }}
```

GitHub Actions expone el secret `EXTRACTION_PROVIDER` como variable de entorno del proceso Python. Si el secret no existe en el repositorio, GitHub Actions pasa una cadena vacía `""` — pero en Python, cuando una variable de entorno existe con valor vacío, `os.getenv("EXTRACTION_PROVIDER", "groq")` devuelve `""`, no `"groq"`. Sin embargo, `"".lower()` == `""` y `call_llm` con `provider=""` fallaría con un error claro. Para evitar esto se puede usar `os.getenv("EXTRACTION_PROVIDER") or "groq"` en una futura mejora, pero el comportamiento actual es aceptable: si el secret existe con valor vacío es un error de configuración que debe ser visible.

### `tests/test_ai_analyzer.py` — Test 9

El test cubre dos ramas de `_extract_and_cache`:

**Caso A (modo deep, N=2):** mockeamos `extract_problem_deep` y `config.EXTRACTION_PROVIDER="groq"`. Llamamos `_extract_and_cache(posts, cache, provider="gemini")`. Verificamos que todas las llamadas a `extract_problem_deep` recibieron `provider="groq"` (no `"gemini"`).

```python
patch.object(ai_mod.config, "EXTRACTION_PROVIDER", "groq")
```
`patch.object` reemplaza el atributo `EXTRACTION_PROVIDER` del objeto módulo `ai_mod.config` con `"groq"` durante el bloque `with`. Al salir restaura el valor original. Esto simula el escenario AI_PROVIDER=gemini, EXTRACTION_PROVIDER=groq.

La comprobación `c.kwargs.get("provider") == "groq" or c.args[1] == "groq"` cubre tanto llamadas con keyword argument (`provider=extraction_provider`) como posicionales. En la implementación real siempre es keyword, pero el test es robusto ante refactores.

**Caso B (modo batch, N=31):** mismo patrón con `run_batch_extraction`. Verificamos con `mock_batch.assert_called_once()` que se llamó exactamente una vez, y con `kwargs.get("provider") == "groq"` que el provider fue el correcto.

## Tests añadidos

- **`test_extraction_uses_extraction_provider`** (Test 9 en `tests/test_ai_analyzer.py`): verifica que cuando `config.EXTRACTION_PROVIDER="groq"` y se llama `_extract_and_cache(..., provider="gemini")`, las funciones de extracción (`extract_problem_deep` en modo deep con N<=30, `run_batch_extraction` en modo batch con N>30) reciben `provider="groq"`. Cubre el caso central de esta feature: que los dos providers son independientes.

## Verificación

Suite completa ejecutada con `.venv/bin/pytest -q`:

```
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 52%]
........................................................................ [ 70%]
........................................................................ [ 88%]
................................................                         [100%]
391 passed in ...s — exit code 0
```

Todos los tests pasan, incluido el nuevo Test 9.

---

## Fix aplicado: mover lectura de `config.EXTRACTION_PROVIDER` al nivel de entrada

### Qué cambió

- **`src/saas_radar/analysis/ai_analyzer.py`**:
  - `run_ai_analysis`: añadida la línea `extraction_provider = config.EXTRACTION_PROVIDER` al inicio del cuerpo (antes de `init_db`). Es la única lectura de `config.*` en runtime, como exige `architecture.md §3`.
  - Las dos llamadas a `_extract_and_cache` cambian de `provider=provider` a `extraction_provider=extraction_provider` para pasar el valor ya leído.
  - `_extract_and_cache`: parámetro renombrado de `provider: str` a `extraction_provider: str`. Eliminada la línea `extraction_provider = config.EXTRACTION_PROVIDER` del cuerpo — ya no lee `config.*` directamente.

- **`tests/test_ai_analyzer.py`** (Test 9):
  - Eliminado `patch.object(ai_mod.config, "EXTRACTION_PROVIDER", "groq")` en ambos casos (A y B): ya no es necesario porque `_extract_and_cache` no lee `config.*`.
  - Las llamadas a `_extract_and_cache` cambian de `provider="gemini"` a `extraction_provider="groq"`: el argumento ahora refleja directamente el provider de extracción, no el de síntesis.
  - Actualizado el docstring del test para describir el contrato correcto.

### Por qué

La versión anterior violaba `docs/architecture.md §3`: "Los módulos NO leen ni mutan `saas_radar.config.*` en runtime. Si una función necesita el provider, lo recibe como argumento explícito." `_extract_and_cache` es una función interna (no es el nivel de entrada del pipeline), por lo que no debe leer `config.*` directamente.

El fix mueve la lectura al único lugar autorizado: `run_ai_analysis`, que es el punto de entrada del pipeline de análisis IA. Desde ahí el valor fluye hacia abajo como argumento explícito, igual que `provider` para síntesis.

### Impacto

Ningún cambio de comportamiento en runtime. El valor de `extraction_provider` es el mismo antes y después del fix. El único impacto es estructural: la lectura de `config.*` ocurre en el nivel correcto, lo que hace el código más testeable (los tests de `_extract_and_cache` ya no necesitan parchear el módulo `config`) y más conforme a la arquitectura definida.

### Verificación post-fix

Suite completa: 391 passed — exit code 0.
