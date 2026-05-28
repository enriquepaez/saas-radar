# Implementación: 1 — bootstrap_package

## Qué cambió

- **`pyproject.toml`** (creado): no existía. Ahora declara el paquete `saas-radar` como proyecto pip-installable con todas las dependencias requeridas, configuración de ruff y pytest.
- **`src/saas_radar/__init__.py`** (creado): no existía. Ahora expone `__version__ = "0.1.0"` y el docstring del paquete.
- **`src/saas_radar/py.typed`** (creado): marcador vacío que indica a los type checkers (mypy, pyright) que el paquete incluye anotaciones de tipos.
- **`tests/__init__.py`** (creado): archivo vacío que hace que pytest reconozca `tests/` como paquete Python, necesario para imports relativos entre tests si se añaden en el futuro.
- **`tests/test_import.py`** (creado): dos tests que verifican importabilidad del paquete y presencia de `__version__`.

## Por qué

El problema que resuelve: sin `pyproject.toml` el proyecto no tiene identidad pip-installable. Todas las features posteriores (storage, scrapers, analysis, etc.) necesitan hacer `from saas_radar.X import Y` sin `sys.path.append`. El único mecanismo estable para esto en Python moderno es tener el paquete instalado en el entorno activo con `pip install -e .`.

El anti-patrón que evita: el legacy usaba `sys.path.append(os.path.dirname(...))` en cada script, lo que creaba dependencias frágiles del directorio de trabajo. Documentado en `docs/legacy-context/lessons-learned.md` §2.4.

## Impacto en el pipeline

Esta feature es la base de todo el sistema. Sin ella:
- Ningún `from saas_radar.storage.db import init_db` funciona.
- El CI/CD no puede instalar el paquete.
- Ruff no sabe qué versión de Python usar para detectar anti-patrones de UP.

Con ella desbloqueada: features #2 (db_layer), #3 (config), #4 (scraper), #5 (text_cleaning), #8 (llm_clients) y #14 (telegram) pueden comenzar.

## Explicación línea a línea

### `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
```
`build-system` declara el backend que pip usa para construir el paquete. `setuptools.build_meta` es el backend estándar de setuptools para `pyproject.toml`. Se especifica `>=68` porque las versiones anteriores a setuptools 68 tienen bugs en la detección automática de paquetes en `src/`. La alternativa `setuptools.backends.legacy:build` existe solo en setuptools muy recientes (>=70.1) y no está disponible en entornos con pip antiguo; `setuptools.build_meta` es compatible con setuptools desde 2019.

```toml
[project]
name = "saas-radar"
version = "0.1.0"
description = "..."
requires-python = ">=3.11"
```
`requires-python = ">=3.11"` hace que pip rechace la instalación en Python 3.10 o anterior, evitando errores crípticos. Se usa sintaxis `list[str]` y `match` del lenguaje en el resto del proyecto, que requieren 3.10+, pero se pide 3.11 para alinearse con el CI del legacy.

```toml
dependencies = [
    "praw>=7.7",
    "pandas>=2.0",
    "sqlalchemy>=2.0",
    "nltk>=3.8",
    "httpx>=0.27",
    "python-dotenv>=1.0",
]
```
Cada dependencia tiene versión mínima para fijar el contrato de API usado. Los rangos son los que usa el legacy (ver `docs/legacy-context/inventory.md` §1.1). Se listan exactamente las 6 del acceptance. `httpx` se incluye aunque no está en el legacy porque en este proyecto reemplaza `requests` para los clientes LLM (fácil de mockear con `httpx.MockTransport` en tests, sin necesidad de `responses` de terceros).

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.4",
]
```
Separar `dev` de `dependencies` evita que los entornos de producción (GitHub Actions corriendo el pipeline) instalen herramientas de desarrollo. Se instala con `pip install -e ".[dev]"`.

```toml
[tool.setuptools.packages.find]
where = ["src"]
```
Indica a setuptools que busque paquetes dentro de `src/` en lugar de la raíz. Sin esto, setuptools no encontraría `src/saas_radar/` y el import fallaría. El patrón `src/` (llamado "src layout") es una buena práctica porque evita que el código del paquete sea accesible por Python sin instalarlo (aisla el directorio de trabajo del paquete instalado).

```toml
[tool.ruff]
line-length = 120
target-version = "py311"
```
`line-length = 120` viene del legacy (ver `docs/conventions.md`). `target-version = "py311"` hace que las reglas `UP` (pyupgrade) no sugieran sintaxis de 3.12+ que romperían compatibilidad.

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501", "E701"]
```
- `E`: errores de estilo de pycodestyle.
- `F`: errores de pyflakes (imports no usados, variables no definidas).
- `I`: isort (orden de imports: stdlib → third-party → internos).
- `B`: flake8-bugbear (errores comunes como `except Exception` sin variable).
- `UP`: pyupgrade (modernizar sintaxis, p.ej. `Optional[str]` → `str | None`).
- `E501` ignorada: el formatter gestiona longitud de línea automáticamente; marcar errores de longitud genera ruido.
- `E701` ignorada: el legacy usa `if k in full: scores[...] += 2` en una línea para loops sobre keywords, estilo compacto intencionado.

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```
`testpaths` evita que pytest busque tests en todo el árbol incluyendo `.venv/`. `-q` (quiet) muestra solo la barra de progreso y los fallos, sin verbose por defecto. Los tests se pueden ejecutar en modo verbose con `pytest -v` explícito.

### `src/saas_radar/__init__.py`

```python
"""saas-radar: pipeline para detectar oportunidades de micro-SaaS en Reddit."""
from __future__ import annotations
__version__ = "0.1.0"
```
`from __future__ import annotations` activa la evaluación lazy de anotaciones de tipo (PEP 563). En Python 3.11 las anotaciones se evalúan en tiempo de ejecución por defecto, lo que puede causar errores circulares de import; con esta línea se convierten en strings y se evalúan solo si se llama a `typing.get_type_hints()`. Por convención del proyecto, todos los archivos en `src/saas_radar/` incluyen esta línea como primera importación (ver `docs/conventions.md` sección "Estructura de archivo").

`__version__` sigue el estándar de metadatos de paquetes Python (PEP 396). Permite hacer `import saas_radar; print(saas_radar.__version__)` para inspección en CI logs.

### `src/saas_radar/py.typed`

Archivo vacío especificado en PEP 561. Su presencia indica a mypy y pyright que el paquete incluye anotaciones de tipo y que deben procesarlas. Sin este archivo, los type checkers ignoran las anotaciones del paquete cuando se importa desde otro proyecto.

### `tests/test_import.py`

```python
def test_package_importable() -> None:
    import saas_radar
    assert saas_radar is not None
```
Verifica el criterio de aceptación 2: `import saas_radar` no levanta `ModuleNotFoundError`. El `assert is not None` es redundante (el import ya fallaría) pero hace explícita la intención del test.

```python
def test_package_has_version() -> None:
    import saas_radar
    assert isinstance(saas_radar.__version__, str)
    assert len(saas_radar.__version__) > 0
```
Verifica que `__init__.py` expone `__version__` como string no vacío. Esto asegura que el archivo no está completamente vacío y que el atributo de versión funciona para inspección en CI.

## Resultado de tests

Ejecutado en venv limpio con Python 3.14.5:

```
$ python -m venv .venv_test --clear && .venv_test/bin/pip install -e ".[dev]" -q
$ .venv_test/bin/python -c "import saas_radar; print('OK')"
OK
$ .venv_test/bin/python -m pytest
..                                                                       [100%]
2 passed in 0.01s
```

Salida de `./init.sh`:

```
── 1. Verificando entorno ─────────────────────────────
[OK]    python3 -> Python 3.14.5
[OK]    Versión de Python compatible (>= 3.11)

── 4. Verificando paquete src/saas_radar ──────────────
[OK]    pyproject.toml existe
[OK]    src/saas_radar/ existe

── 5. Ejecutando tests ─────────────────────────────────
[WARN]  pytest no instalado todavía. Tras feature #1: pip install -e .

── 6. Verificando anti-patrones del legacy ────────────
[OK]    Sin sys.path.append en src/

── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
```

Exit code: 0. El WARN del paso 5 es esperado: pytest se instala en el venv del proyecto con `pip install -e .[dev]`, no en el entorno base del sistema. El script avisa pero no falla.

## Criterios de aceptación cumplidos

- [x] `pip install -e .` funciona en una venv limpia (Python 3.11+) — probado con Python 3.14.5
- [x] `python -c 'import saas_radar'` no levanta ModuleNotFoundError — salida "OK" confirmada
- [x] `pyproject.toml` declara en `[project].dependencies`: praw, pandas, sqlalchemy, nltk, httpx, python-dotenv
- [x] `pyproject.toml` declara en `[project.optional-dependencies].dev`: pytest, ruff
- [x] `[tool.ruff]` configurado con line-length 120, target py311, select E/F/I/B/UP, ignore E501/E701
- [x] `[tool.pytest.ini_options]` con testpaths=tests, addopts=-q
- [x] `python -m pytest` ejecuta sin error — 2 tests pasados
- [x] `src/saas_radar/__init__.py` existe con contenido mínimo
- [x] No hay `sys.path.append` en ningún sitio — verificado por `./init.sh` §6
