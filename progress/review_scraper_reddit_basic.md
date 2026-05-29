# Review — feature #4 (scraper_reddit_basic)

**Veredicto:** CHANGES_REQUESTED

## Acceptance Criteria

- AC1 get_reddit() devuelve el mismo cliente en llamadas sucesivas (singleton): ✅  
  `_reddit` se guarda en global módulo; `test_get_reddit_singleton` verifica que `praw.Reddit` se llama exactamente 1 vez y ambas referencias son idénticas.

- AC2 fetch_posts con incremental=False usa feeds [hot, new(/2), top-month(/2), top-year(/2)]: ✅  
  Líneas 71-76 de `reddit_scraper.py`. Verificado por `test_fetch_posts_full_mode_feeds`.

- AC3 fetch_posts con incremental=True usa feeds [new, hot, top-day(/2)]: ✅  
  Líneas 64-69 de `reddit_scraper.py`. Verificado por `test_fetch_posts_incremental_mode_feeds`.

- AC4 fetch_posts dedup por id dentro del DataFrame devuelto: ✅  
  `seen_ids: set[str]` en líneas 78-88 de `reddit_scraper.py`. Verificado por `test_fetch_posts_dedup`.

- AC5 search_pain_posts(query) ejecuta multireddit '+'.join(SUBREDDITS).search(query): ✅  
  Líneas 99-108 de `reddit_scraper.py`. Verificado por `test_search_pain_posts_uses_multireddit`.

- AC6 search_pain_posts con incremental=True añade time_filter='day': ✅  
  Líneas 100-102 de `reddit_scraper.py`. Verificado por `test_search_pain_posts_incremental_adds_time_filter`.

- AC7 fetch_top_comments aplica replace_more(limit=0) y filtra len(body) >= COMMENT_MIN_LENGTH: ✅  
  Líneas 121-128 de `reddit_scraper.py`. Verificado por `test_fetch_top_comments_calls_replace_more` y `test_fetch_top_comments_filters_short`.

- AC8 Tests con praw.Reddit mockeado vía MagicMock: dedup, modos incremental vs full, filtros de longitud: ✅  
  10 tests, todos usando MagicMock. Sin llamadas reales a Reddit.

- AC9 NO se hacen llamadas reales a Reddit en CI: ✅  
  `_reddit` se sustituye directamente (`scraper_module._reddit = mock_reddit`) en todos los tests. `time.sleep` se parchea en `test_search_pain_posts_uses_multireddit`.

## Checkpoints

- C1 Arnés completo: ✅ (init.sh termina con exit code 0)
- C2 Estado coherente: ✅
- C3 Arquitectura respetada — sin sys.path.append, módulo en capa correcta `scrapers/`: ✅ parcial ❌ ver defectos
- C4 Verificación real — 10 tests verdes, PRAW mockeado: ✅
- C5 BD heredada: ✅ (no aplica directamente a esta feature)
- C6 Sesión cerrada: pendiente de aprobar

## Defectos encontrados

### Defecto 1 — BLOQUEANTE: imports desordenados en `tests/test_reddit_scraper.py` (I001)

`ruff check` con regla `I` falla en `tests/test_reddit_scraper.py`:

```
I001 [*] Import block is un-sorted or un-formatted
  --> tests/test_reddit_scraper.py:2:1
```

El bloque de imports tiene `from unittest.mock import MagicMock, patch` (stdlib) en una línea, luego `import pytest` (third-party) separado con línea en blanco, cuando ruff espera que stdlib y third-party estén en bloques separados pero internamente ordenados. Concretamente: `pytest` debería ir después de `unittest.mock` en su bloque separado, y los internos (`saas_radar.*`) en un tercer bloque. La ordenación actual pone `unittest.mock` y `pytest` en bloques distintos pero en el orden incorrecto para el isort de ruff.

Corrección requerida en `tests/test_reddit_scraper.py` líneas 2-15: reordenar para que quede:
```python
"""Tests del scraper de Reddit con PRAW completamente mockeado."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import saas_radar.scrapers.reddit_scraper as scraper_module
from saas_radar.config import COMMENT_MIN_LENGTH, SUBREDDITS
from saas_radar.scrapers.reddit_scraper import (
    fetch_posts,
    fetch_top_comments,
    get_reddit,
    search_pain_posts,
)
```

Nota: `get_reddit` y `search_pain_posts` también deben ir en orden alfabético dentro del bloque de imports internos.

### Defecto 2 — BLOQUEANTE: formato incorrecto en `src/saas_radar/scrapers/reddit_scraper.py` y `tests/test_reddit_scraper.py`

`ruff format --check` reporta que reformatearía ambos archivos. Las diferencias concretas son:

**`reddit_scraper.py` línea 1-2:** falta línea en blanco entre el docstring y `from __future__ import annotations`:
```python
# actual:
"""Scraper de Reddit con PRAW: singleton, feeds y comentarios."""
from __future__ import annotations

# esperado:
"""Scraper de Reddit con PRAW: singleton, feeds y comentarios."""

from __future__ import annotations
```

**`tests/test_reddit_scraper.py`:** mismo problema en línea 1-2, más falta de doble línea en blanco entre funciones de nivel superior (entre `make_mock_post`, `reset_reddit_singleton`, y cada función `test_*`).

## Output de pytest

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 10 items

tests/test_reddit_scraper.py ..........                                  [100%]

============================== 10 passed in 0.25s ==============================
```

## Output de init.sh

Termina con exit code 0. Todos los checks en verde.

## Resumen

Los acceptance criteria de negocio están todos satisfechos y los tests pasan. Los defectos son exclusivamente de estilo (`ruff format` y `ruff check --select I`), pero `docs/conventions.md` exige explícitamente formato `ruff format` y lint `ruff check` con reglas `E, F, I, B, UP`. La regla `I` (imports) falla, y `ruff format` reporta 2 archivos que reformatearía. Ambos deben corregirse antes de aprobar.

---

# Segunda revision — 2026-05-29

**Veredicto:** APPROVED

## Comandos ejecutados

### 1. ruff format --check

```
2 files already formatted
EXIT: 0
```

### 2. ruff check --select E,F,I,B,UP

```
All checks passed!
EXIT: 0
```

### 3. pytest tests/test_reddit_scraper.py -v

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 10 items

tests/test_reddit_scraper.py ..........                                  [100%]

============================== 10 passed in 0.26s ==============================
EXIT: 0
```

### 4. ./init.sh

```
[OK]    python3 -> Python 3.11.15
[OK]    Versión de Python compatible (>= 3.11)
[OK]    Existe AGENTS.md
[OK]    Existe feature_list.json
[OK]    Existe progress/current.md
[OK]    Existe docs/architecture.md
[OK]    Existe docs/conventions.md
[OK]    Existe docs/verification.md
[OK]    Existe CHECKPOINTS.md
[OK]    Existe docs/legacy-context/inventory.md
[OK]    Existe docs/legacy-context/architecture.md
[OK]    Existe docs/legacy-context/lessons-learned.md
[OK]    Existe docs/legacy-context/feature-backlog.md
[OK]    feature_list.json válido (21 features)
[OK]    pyproject.toml existe
[OK]    src/saas_radar/ existe
..............................................................           [100%]
[OK]    Todos los tests pasan
[OK]    Sin sys.path.append en src/
[OK]    Entorno listo. Puedes empezar a trabajar.
EXIT: 0
```

Los 4 comandos terminan en verde. Los defectos de formato e imports de la primera revision estan corregidos.
