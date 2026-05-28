# Convenciones de código

> Homogeneidad extrema. La IA predice mejor cuando el repositorio se parece
> a sí mismo en todas partes.

## Estilo Python

- **Versión:** Python 3.11+ (sintaxis `list[str]` permitida, `match` permitido).
- **Formato:** `ruff format`, line-length 120 (heredado del legacy).
- **Lint:** `ruff check` con reglas `E, F, I, B, UP`. Ignora `E501` (lo
  maneja el formatter) y `E701` (se preserva el estilo compacto del legacy:
  `if k in full: scores[...] += 2` en loops sobre listas de keywords).
- **Imports:** stdlib primero, luego third-party (`praw`, `pandas`, …), luego
  internos (`from saas_radar.foo import bar`). Una línea por módulo. `ruff`
  con la regla `I` ordena automáticamente.
- **Strings:** comillas dobles `"..."` siempre. Comillas simples solo para
  escapar comillas dobles dentro.
- **f-strings** para interpolación. Nada de `.format()` ni `%`.
- **NO `sys.path.append`** en ningún sitio. El paquete es pip-installable
  desde la feature #1.

## Nombres

| Tipo                    | Convención        | Ejemplo                  |
|-------------------------|-------------------|--------------------------|
| Módulos                 | `snake_case`      | `pain_filter.py`         |
| Paquete principal       | `snake_case`      | `saas_radar`             |
| Clases                  | `PascalCase`      | `Proposal`               |
| Funciones / variables   | `snake_case`      | `load_pain_posts`        |
| Constantes              | `UPPER_SNAKE`     | `MIN_SEMANTIC_SCORE`     |
| Privadas                | prefijo `_`       | `_semantic_score`        |
| "Dunder" metadata       | prefijo `_`       | `_post_id`, `_subreddit` (en dicts de extracción) |

## Estructura de archivo

Cada archivo en `src/saas_radar/` empieza con:

```python
"""Una línea describiendo el propósito del módulo."""
from __future__ import annotations

# imports stdlib
import json
import logging
from pathlib import Path

# imports third-party
import pandas as pd
from sqlalchemy import text

# imports internos
from saas_radar.config import MIN_SEMANTIC_SCORE
from saas_radar.storage.db import engine

logger = logging.getLogger(__name__)
```

## Tests

- Un archivo de test por módulo: `tests/test_<módulo>.py`.
- Framework: **pytest**. NO usar `unittest`. Estilo: funciones
  `test_xxx_yyy(...)`, no clases (salvo grupos de fixtures).
- Nombres de test descriptivos:
  `test_semantic_score_returns_negative_for_showcase_prefix`.
- Cada test que toca disco usa `tempfile.TemporaryDirectory()` o el fixture
  `tmp_path` de pytest. **No** mockear el filesystem.
- Tests que tocan LLM: `httpx.MockTransport` o `respx`. No llamadas reales.
- Tests que tocan PRAW: mockear `praw.Reddit` con `MagicMock`. No llamadas
  reales.
- Tests que tocan SQLite: BD temporal vía `tmp_path / "test.db"` y `engine =
  create_engine(f"sqlite:///{tmp_path}/test.db")`. NO compartir la BD del
  proyecto.

## Manejo de errores

Excepciones del dominio:

```python
class SaasRadarError(Exception):
    """Base para errores del dominio."""

class LLMError(SaasRadarError):
    """Fallo del LLM tras agotar retries."""

class OpportunityNotFound(SaasRadarError):
    """No existe la opp con ese id."""
```

El CLI captura `SaasRadarError`, imprime mensaje a `stderr` y sale con código
1. Nunca propaga stack traces al usuario.

Las funciones de capa externa (clients LLM, scrapers) **devuelven `None`**
en fallo definitivo + log a `logger.error`. No lanzan al pipeline para que
los loops puedan continuar (circuit breaker decide cuándo cortar).

## Logging

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Cargando %d posts desde la BD", len(rows))
logger.warning("Cache previo corrupto: %s", err)
logger.error("Claude error %d: %s", status, text[:300])
```

- Niveles: `DEBUG` (traza fina), `INFO` (eventos del pipeline), `WARNING`
  (degradaciones recuperables), `ERROR` (fallos definitivos), `CRITICAL`
  (no usar — preferir excepciones).
- **No** usar `print()` para errores / debug.
- **Sí** usar `print()` para el "user output" del CLI: cabeceras de fase
  `── FASE 4: …`, resumen final, etc. Eso lo lee el humano.
- `setup_logging(level, fmt)` se llama UNA vez en `main.py`, no en módulos
  individuales.

## Comentarios

Por defecto **no** se escriben. Solo se permiten cuando explican un *por qué*
no obvio:
- Workaround documentado de una limitación de un servicio.
- Invariante sutil que el código no expresa.
- Trade-off elegido contra una alternativa.

Los nombres deben hacer el resto.

**Excepción educativa**: la regla pedagógica del proyecto (ver `CLAUDE.md`)
exige explicación línea a línea en `progress/impl_<feature>.md`, no en el
código. Los archivos quedan limpios; el aprendizaje vive en `progress/`.

## Convenciones específicas del legacy a respetar

Heredadas tal cual (no cambiar sin justificación):

- **Subreddit en minúsculas siempre**: `save_to_db` normaliza antes de
  insertar. `r/SaaS` y `r/saas` cuentan como uno.
- **JSON serializado como TEXT** en SQLite: campos como `evidence_quotes`,
  `mvp_scope`, `mentioned_competitors`, `pricing_tiers` son `json.dumps()` a
  la escritura y `json.loads()` tolerante a la lectura.
- **INSERT OR IGNORE vía tabla staging** para bulk insert idempotente.
- **Stopwords ricas de dominio** en `_COHERENCE_STOP` (raíces 4-char como
  `manu`, `trac`, `spre`).

## Convenciones específicas del legacy a CAMBIAR

Heredadas con cambio explícito:

- **Logging estructurado desde el día 1** (vs `print` con emojis del legacy).
- **Provider como argumento** (vs mutación de `config.AI_PROVIDER`).
- **Paquete pip-installable** (vs `sys.path.append`).
- **Separación `opportunities` vs `opportunity_state`** si la feature de BD
  lo decide (vs 26 columnas mezclando output LLM + flags humanos).
- **`_clean_extractions` como 4 funciones puras** (vs 86 líneas mezcladas).
- **`requirements.txt` no usado** — todo va en `[project.dependencies]` del
  `pyproject.toml`.
