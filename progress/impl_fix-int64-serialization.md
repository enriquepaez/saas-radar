# Implementación: fix — numpy int64 no serializable a JSON

## Qué cambió

- **`src/saas_radar/analysis/extraction.py`**: tres asignaciones de `_score` y `_num_comments` envueltas en `int()`.
  - Función `extract_problem_from_post` (~línea 271-272): antes `row.get("score", 0)` y `row.get("num_comments", 0)` → después `int(row.get("score", 0))` y `int(row.get("num_comments", 0))`.
  - Función `extract_problem_deep` (~línea 318-319): mismo cambio.
  - Función `extract_problems_batch` (~línea 368-369): mismo cambio en el bucle de construcción de extractions.

- **`tests/test_extraction.py`**: tres tests nuevos añadidos al final del archivo, en la sección "Serialización JSON con numpy.int64":
  - `test_extract_problem_from_post_json_serializable_with_numpy_int64`
  - `test_extract_problem_deep_json_serializable_with_numpy_int64`
  - `test_extract_problems_batch_json_serializable_with_numpy_int64`

## Por qué

Cuando pandas lee un DataFrame desde SQLite (vía SQLAlchemy), los campos enteros de la BD (`score`, `num_comments`) llegan como `numpy.int64`, no como `int` nativo de Python. `json.dumps()` no sabe serializar `numpy.int64` y lanza `TypeError: Object of type int64 is not JSON serializable`. Envolver con `int()` fuerza la conversión al tipo nativo de Python antes de que el dict llegue a `json.dumps`.

`int(numpy.int64(x))` es la conversión estándar y segura: numpy.int64 implementa `__int__` y devuelve un `int` Python limpio. No hay pérdida de precisión para los rangos normales de score/num_comments de Reddit.

## Impacto en el pipeline

La función `_save_extractions_cache` en `ai_analyzer.py` (línea ~47) llama a `json.dumps(new_data, ...)` donde `new_data` contiene los dicts devueltos por estas tres funciones. Con el fix, el cache ya no falla al intentar serializar. El pipeline de producción (GitHub Actions) dejará de romperse en la fase de guardado de cache de extracciones.

## Explicación técnica

- `int(row.get("score", 0))`: `row.get("score", 0)` devuelve el valor del campo `score` del `pd.Series`, que cuando viene de SQLite vía pandas es `numpy.int64`. `int(...)` llama a `numpy.int64.__int__()`, que devuelve un `int` nativo de Python. El valor `0` default ya es `int`, así que `int(0)` es un no-op seguro.
- El mismo razonamiento aplica a `num_comments`.
- Las tres funciones tienen el mismo patrón de asignación de metadatos desde el `pd.Series`, por lo que el fix es simétrico en las tres.

## Tests añadidos

- `test_extract_problem_from_post_json_serializable_with_numpy_int64`: construye un `pd.Series` con `score=np.int64(55)` y `num_comments=np.int64(7)`, llama a `extract_problem_from_post`, verifica que `json.dumps` no lanza `TypeError` y que los valores parseados son correctos.
- `test_extract_problem_deep_json_serializable_with_numpy_int64`: mismo patrón para `extract_problem_deep` con `score=np.int64(100)` y `num_comments=np.int64(25)`.
- `test_extract_problems_batch_json_serializable_with_numpy_int64`: mismo patrón para `extract_problems_batch` con `score=np.int64(77)` y `num_comments=np.int64(3)`.

Todos los tests usan `numpy` directamente (`import numpy as np`) para construir valores `int64` explícitos, haciendo el caso de fallo de regresión imposible de ignorar.

## Verificación

```
/home/enriquepaez/projects/saas-radar/.venv/bin/pytest tests/test_extraction.py -v -q
......................
22 passed in 0.40s
```
