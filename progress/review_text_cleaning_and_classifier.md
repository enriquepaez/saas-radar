# Review — feature #5: text_cleaning_and_classifier

**Veredicto:** CHANGES_REQUESTED

## Checkpoints

- C1: [x] — arnés completo, init.sh termina verde (warn pytest no instalado en sistema, pero el venv lo tiene)
- C2: [x] — una sola feature in_progress, current.md describe la sesión activa
- C3: [x] — módulos en `src/saas_radar/analysis/`, sin `sys.path.append`, sin `print()` de debug, sin mutación de globales, regex compiladas al import
- C4: [ ] — tests pasan (57/57 feature, 109/109 suite), pero `ruff check` falla en ambos archivos de test (ver abajo)
- C5: [x] — no aplica directamente a esta feature (no toca BD)
- C6: [x] — N/A (sesión aún activa)

## Resultado de pytest

```
collected 57 items

tests/test_text_cleaning.py ........................                     [ 42%]
tests/test_post_classifier.py .................................          [100%]

57 passed in 0.23s
```

Suite completa: 109 passed in 0.54s

## Criterios de aceptación

Todos verificados manualmente:

| Criterio | Resultado |
|---|---|
| `clean_text('HTTP://x.com hola mundo')` → sin URL, stopwords, puntuación | OK — devuelve `'hola mundo'` |
| `classify_post('I built X', '...')` → `'showcase'` | OK |
| `classify_post('How do you handle invoices?', 'manage track')` → `'question_operational'` | OK |
| `classify_post('I hate manual process', '...')` → `'pain_point'` | OK |
| `classify_post('', '')` → `'other'` | OK |
| NLTK con cache (`nltk.download('stopwords', quiet=True)`) | OK — línea 11 de `text_cleaning.py` |
| Tests cubren las 6 categorías + edge cases | OK — 33 tests en `test_post_classifier.py` |
| Listas PAIN_KEYWORDS/SHOWCASE_KEYWORDS/EMOTIONAL_KEYWORDS/OPERATIONAL_KEYWORDS presentes | OK — 21/22/8/23 elementos |

## Cambios requeridos

### 1. `tests/test_text_cleaning.py` línea 5: `import pytest` sin usar (F401)

`pytest` está importado pero no se usa en ninguna parte del archivo. Ruff lo reporta como `F401`. Eliminar la línea 5 (`import pytest`).

### 2. `tests/test_post_classifier.py` línea 5: `import pytest` sin usar (F401)

Mismo problema. Eliminar la línea 5 (`import pytest`).

### 3. `tests/test_text_cleaning.py`: bloque de imports mal ordenado (I001)

Con `from __future__ import annotations` en línea 3, `import pytest` en línea 5 y el import interno en línea 7, ruff reporta `I001` (import block unsorted). Eliminar el `import pytest` resuelve también este error (el bloque queda: `from __future__` + blanco + import interno, que es el orden correcto).

### 4. `tests/test_post_classifier.py`: bloque de imports mal ordenado (I001)

Mismo caso. Eliminar el `import pytest` resuelve el `I001`.

**Verificación:**
```
$ .venv/bin/python -m ruff check tests/test_text_cleaning.py tests/test_post_classifier.py
F401 `pytest` imported but unused  (test_text_cleaning.py:5)
F401 `pytest` imported but unused  (test_post_classifier.py:5)
I001 Import block is un-sorted or un-formatted (test_text_cleaning.py:3)
I001 Import block is un-sorted or un-formatted (test_post_classifier.py:3)
Found 4 errors. [*] 4 fixable with the --fix option.
```

Los 4 errores son fixable con `ruff --fix`. El implementer puede ejecutar:
```
.venv/bin/python -m ruff check --fix tests/test_text_cleaning.py tests/test_post_classifier.py
```

y verificar que `ruff check` queda limpio tras el fix.

## Nota sobre lo que está bien

- Arquitectura respetada: `src/saas_radar/analysis/` con `__init__.py` correcto.
- Regex compiladas al import (líneas 22, 27, 30, 34 de `text_cleaning.py`).
- Sin `sys.path.append`, sin `print()` de debug, sin `TODO` sin contexto.
- `from __future__ import annotations` presente en ambos módulos fuente.
- Prioridad del clasificador explícita y determinista vía `_PRIORITY`.
- `normalize_for_classifier` correctamente separada de `clean_text` (preserva `?`, `$`, stopwords).
- Comentarios solo donde explican decisiones no obvias (por qué no-alpha, por qué `\S+`, etc.).
- Los módulos fuente (`text_cleaning.py`, `post_classifier.py`) pasan `ruff check` y `ruff format --check` sin errores.

## Segunda revisión

**Veredicto:** APPROVED

### Comandos ejecutados

```
$ source .venv/bin/activate && ruff check tests/test_text_cleaning.py tests/test_post_classifier.py
All checks passed!

$ python -m pytest tests/test_text_cleaning.py tests/test_post_classifier.py -v
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 57 items

tests/test_text_cleaning.py ........................                     [ 42%]
tests/test_post_classifier.py .................................          [100%]

57 passed in 0.23s

$ python -m pytest -q
109 passed in 0.53s  (exit code 0)
```

### Cambios verificados

- `import pytest` eliminado de `tests/test_text_cleaning.py` y `tests/test_post_classifier.py`.
- Errores F401 e I001 resueltos en ambos archivos.
- 57/57 tests de la feature pasan.
- Suite completa 109/109 sin regresiones.
