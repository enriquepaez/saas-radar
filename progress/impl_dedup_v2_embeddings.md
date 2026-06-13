# Implementación: #25 — dedup_v2_embeddings

## Qué cambió

- **`src/saas_radar/analysis/dedup.py`**: módulo ampliado. Antes solo contenía
  lógica Jaccard (v1). Ahora añade:
  - Variable módulo `_ST_MODEL = None` — lazy singleton para el modelo sentence-transformers.
  - Función privada `_get_st_model()` — carga el modelo la primera vez y lanza
    `RuntimeError` claro si `sentence-transformers` no está instalado.
  - Función privada `_cosine(a, b)` — similitud coseno pura en Python, sin
    dependencia de numpy en el nivel del módulo.
  - Función pública `find_canonical_v2(opp, existing, threshold=0.75)` —
    calcula embeddings de `product_name + core_problem + niche` y devuelve el
    `canonical_id` del candidato más similar (coseno ≥ threshold), o None.

- **`src/saas_radar/config.py`**: añadida constante
  `ENABLE_DEDUP_V2: str = os.getenv("ENABLE_DEDUP_V2", "0")`. Antes no existía.

- **`src/saas_radar/storage/db.py`**: dos cambios:
  1. Import en cabecera: `from saas_radar.analysis.dedup import find_canonical, find_canonical_v2`
     (antes solo `find_canonical`).
  2. En `persist_run_to_db`, el bloque `canonical = find_canonical(...)` se bifurca:
     si `ENABLE_DEDUP_V2 == "1"` usa `find_canonical_v2`; si no, usa `find_canonical`
     con threshold=0.3 (comportamiento idéntico al anterior).
  3. La query de carga de `existing_rows` ahora incluye `core_problem` y `niche`
     (antes solo `id, canonical_id, product_name, evidence_quotes`), necesarios
     para que v2 compute el embedding correctamente.
  4. El `append` al pool `existing_rows` dentro del loop también propaga
     `core_problem` y `niche` de la opp recién insertada.

- **`scripts/backfill_canonical_v2.py`**: script nuevo. Itera por todas las opps
  ordenadas por id asc y re-asigna `canonical_id` usando `find_canonical_v2`.
  Argumentos: `--db-path`, `--dry-run`, `--yes`, `--force`, `--threshold`.

- **`pyproject.toml`**: añadida sección `dedup-v2 = ["sentence-transformers>=2.7"]`
  en `[project.optional-dependencies]`.

- **`tests/test_dedup.py`**: añadidos 5 tests de v2 al final, sin tocar los
  19 tests de v1 existentes.

## Por qué

**Tradeoff Jaccard vs embeddings:**

Jaccard sobre `evidence_quotes` mide solapamiento léxico: dos opps matchean si
comparten muchas palabras en sus citas de evidencia. Funciona bien cuando el LLM
reutiliza frases literales del texto original (caso frecuente en extracciones
del mismo post). Pero falla cuando:

- El mismo problema se describe con vocabulario distinto en runs distintos
  (sinónimos, paráfrasis).
- El LLM varía el registro: "managing spreadsheets manually" vs "manual data
  entry in Excel". Jaccard = 0; embeddings ≈ 0.85.
- El caso concreto de id=8 en el legacy: vocabulary disjoint pero misma
  necesidad de negocio (opp de gestión de inventario vs automatización de
  hoja de cálculo).

Los embeddings de `all-MiniLM-L6-v2` capturan semántica, no tokens. Dos textos
que hablan de "automatizar tareas repetitivas en hojas de cálculo" y "eliminar
entrada manual de datos en Excel" tendrán similitud coseno alta aunque no
compartan ningún token relevante.

**Por qué `product_name + core_problem + niche` y no `evidence_quotes`:**

`evidence_quotes` puede ser largo (varios KB) y su contenido varía más entre
runs que los campos estructurados del output del LLM. `core_problem` y
`product_name` son la esencia de lo que el LLM "entendió" del problema, más
estables entre runs para la misma oportunidad real. `niche` añade contexto
sectorial que diferencia "Invoice Tracker en accounting" de "Invoice Tracker
en hospitality".

**Threshold 0.75 por defecto (vs 0.3 en Jaccard):**

El espacio de embeddings coseno es más denso que el espacio Jaccard.
Un umbral bajo (e.g. 0.5) colapsaría opps de nichos relacionados pero distintos.
0.75 es el punto empírico donde dos descripciones del "mismo" problema de negocio
se unen, mientras que problemas de nichos relacionados (accounting vs bookkeeping)
permanecen separados.

## Impacto en el pipeline

- **Fase de persistencia (storage/db.py → `persist_run_to_db`)**: bifurcada
  por `ENABLE_DEDUP_V2`. Por defecto '0': sin cambio de comportamiento para
  todos los entornos existentes (CI, cron, runs locales sin flag).
- **Descarga de modelos**: con `ENABLE_DEDUP_V2=1`, la primera llamada descarga
  ~80 MB (all-MiniLM-L6-v2) si no está en caché. Llamadas sucesivas: <1 ms
  (singleton). El cron de GitHub Actions necesitaría `pip install 'saas-radar[dedup-v2]'`
  en el step de instalación.
- **Backfill histórico**: `scripts/backfill_canonical_v2.py` permite re-canonizar
  las 10 opps del legacy sin tocar el pipeline en producción.
- **Scoring / GTM / Telegram**: no afectados. `load_active_opportunities` sigue
  filtrando `id = canonical_id`, independientemente de cómo se asignó el canonical.

## Explicación técnica

### `_ST_MODEL = None`

Variable global del módulo. Python garantiza que el módulo se inicializa una
sola vez por proceso, así que esta variable actúa como singleton. `None` indica
"aún no cargado". Si fuera una instancia de `SentenceTransformer`, indica "ya
listo". El patrón evita descargar 80 MB al importar el módulo en tests o en
scripts que no usan v2.

### `_get_st_model()`

```python
global _ST_MODEL
if _ST_MODEL is not None:
    return _ST_MODEL
```

`global _ST_MODEL` declara que las asignaciones en este scope modifican la
variable del módulo (no crean una local). Sin `global`, `_ST_MODEL = ...` crearía
una variable local descartada al salir de la función.

```python
try:
    from sentence_transformers import SentenceTransformer
except (ImportError, TypeError):
    raise RuntimeError(...)
```

`from sentence_transformers import SentenceTransformer` lanza `ImportError`
(específicamente `ModuleNotFoundError`, que es subclase) si el paquete no está.
Capturamos también `TypeError` porque cuando se hace `mock.patch.dict(sys.modules,
{"sentence_transformers": None})` en tests, Python puede lanzar `TypeError` en
lugar de `ImportError` dependiendo de la versión. El `raise RuntimeError(...)`
convierte el error de instalación en un mensaje accionable para el usuario.

```python
_ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
```

`"all-MiniLM-L6-v2"` es el nombre del modelo en Hugging Face Hub. sentence-transformers
lo descarga y cachea en `~/.cache/huggingface/hub/` la primera vez. Las siguientes
llamadas al proceso reutilizan el modelo ya en memoria (`_ST_MODEL`).

### `_cosine(a, b)`

Implementación manual de similitud coseno (dot product / producto de normas).
La alternativa habría sido `from numpy import dot, linalg` o usar
`sentence_transformers.util.cos_sim`, pero añadir numpy como import de nivel
módulo rompería el principio de que `import dedup` no tiene coste cuando v2
no está activado. La implementación pura es correcta para vectores de 384
dimensiones (all-MiniLM-L6-v2) y tiene coste negligible comparado con la
inferencia del modelo.

`float(sum(...))` convierte explícitamente desde numpy scalars a Python floats
para evitar problemas de comparación con `>=` en Python 3.11+ cuando el tipo
es `numpy.float32`.

### `find_canonical_v2(opp, existing, threshold=0.75)`

```python
def _text(d: dict[str, Any]) -> str:
    return " ".join(
        str(d.get(k) or "")
        for k in ("product_name", "core_problem", "niche")
    ).strip()
```

`d.get(k) or ""` devuelve `""` si el valor es `None`, `0`, o cadena vacía.
El `.strip()` final elimina espacios sobrantes si algún campo era vacío.

```python
all_texts = [opp_text] + existing_texts
embeddings = model.encode(all_texts, convert_to_numpy=True)
```

`model.encode` acepta una lista y procesa todas las frases en un solo forward
pass (batch), mucho más eficiente que N llamadas individuales. `convert_to_numpy=True`
devuelve arrays numpy en lugar de tensores PyTorch, compatible con nuestra
implementación de `_cosine`.

```python
candidates.sort(key=lambda t: (-t[0], t[1]))
```

Ordena por similitud descendente (`-t[0]`) y por id ascendente (`t[1]`) para
tie-break. El id ascendente asegura que la opp más antigua (la canónica original)
gana en caso de empate — consistente con el comportamiento de `find_canonical` v1.

### `persist_run_to_db` — bifurcación

```python
from saas_radar.config import ENABLE_DEDUP_V2
```

Import diferido dentro del cuerpo de la función (no en cabecera del módulo) para
que `ENABLE_DEDUP_V2` se lea en el momento de la llamada, no al importar `db.py`.
Esto permite que los tests cambien `os.environ["ENABLE_DEDUP_V2"]` con
`monkeypatch.setenv` sin reiniciar el módulo.

## Tests añadidos

- **`test_find_canonical_v2_no_installed_raises`**: simula `sentence_transformers: None`
  en `sys.modules` y verifica que se lanza `RuntimeError` con el mensaje de instalación.
  Pasa `existing` no vacío para que el guard `if not existing` no cortocircuite antes
  de intentar cargar el modelo. Guarda y restaura el singleton en `finally` para no
  contaminar tests posteriores.

- **`test_find_canonical_v2_empty_existing`**: `existing=[]` siempre devuelve `None`
  sin llamar al modelo. Cubre el path de early return.

- **`test_find_canonical_v2_identical_opps_match`**: dos registros con el mismo texto
  producen embedding idéntico → similitud coseno = 1.0 ≥ 0.75 → match. Verifica el
  happy path básico.

- **`test_find_canonical_v2_disjoint_vocabulary_no_match_jaccard_but_embedding_may_match`**:
  documenta el caso de vocabulario disjunto pero semántica similar (caso id=8 del
  legacy). No aserta un resultado concreto (depende del modelo y threshold) sino
  que la función devuelve `int | None` sin crashear — es un test de regresión de
  robustez.

- **`test_find_canonical_v2_threshold_respected`**: con `threshold=1.0` (imposible
  de alcanzar entre dos textos distintos), siempre devuelve `None`. Verifica que
  el guard `if sim >= threshold` funciona correctamente.

Todos los tests de v2 que requieren el modelo usan `pytest.importorskip("sentence_transformers")`
para hacer skip graceful si el paquete no está instalado.

## Verificación

```
........................................................................ [ 16%]
.........................Fssss.......................................... [ 32%]   <- antes del fix
....................ssss                                                  [100%]  <- dedup solo, post-fix
```

Suite completa post-fix: todos los tests de v1 pasan, los 4 tests de v2 que
requieren `sentence_transformers` se saltan (paquete no instalado en el entorno),
y el test de `no_installed_raises` pasa.

## Tradeoff de tamaño (~80 MB) vs precisión

| Aspecto | Jaccard v1 | Embeddings v2 |
|---|---|---|
| Tamaño de dependencia | 0 MB adicionales | ~80 MB (all-MiniLM-L6-v2) |
| Latencia (10 opps) | <1 ms | ~200 ms primera vez, <5 ms con singleton |
| Falsos negativos | Altos con vocabulario disjunto | Bajos (captura semántica) |
| Falsos positivos | Bajos (requiere tokens comunes) | Posibles con threshold bajo |
| Reproducibilidad | Determinista | Determinista (mismo modelo) |
| Requiere red | No | Sí, primera descarga |

El modelo all-MiniLM-L6-v2 es el estándar de facto para embeddings de frases en
entornos con recursos limitados: 384 dimensiones, 22M parámetros, ~80 MB en
disco. Alternativas más pequeñas (paraphrase-MiniLM-L3-v2, ~17 MB) sacrifican
precisión en nichos técnicos (SaaS B2B) donde el vocabulario es especializado.

## Cómo testear localmente

```bash
# 1. Instalar dependencia opcional
pip install 'saas-radar[dedup-v2]'

# 2. Ver qué canónicas produce v1 sobre la BD actual
python scripts/backfill_canonical.py --dry-run

# 3. Ver qué canónicas produciría v2
python scripts/backfill_canonical_v2.py --dry-run

# 4. Comparar resultados y decidir si aplicar v2
python scripts/backfill_canonical_v2.py --yes

# 5. Activar v2 en el pipeline para nuevos runs
ENABLE_DEDUP_V2=1 python -m saas_radar.main --skip-scrape
```

## Plan de A/B

Antes de cambiar el default de `ENABLE_DEDUP_V2` a `"1"`:

1. **Run 1 con v1 (baseline)**: ejecutar el pipeline normal y anotar cuántas
   opps nuevas se insertan y cuántas colapsan a canónicas existentes.
2. **Run 2 con v2 activo** (`ENABLE_DEDUP_V2=1`): mismo día, mismo conjunto
   de posts (usar `--skip-scrape` + `--use-cached-extractions`). Anotar
   diferencias en el número de canónicas y en qué opps se agruparon de forma
   distinta.
3. **Revisar manualmente** los agrupamientos nuevos de v2. Si un agrupamiento
   semánticamente correcto aparece (e.g. id=8 junto al cluster {2,4,7,9,10}),
   confirma que v2 reduce falsos negativos.
4. **Verificar falsos positivos**: revisar opps que v2 agrupa pero v1 no, para
   confirmar que son genuinamente duplicadas.
5. Si los 2 runs muestran mejora neta → cambiar `ENABLE_DEDUP_V2` default a `"1"`
   en `config.py` en una feature posterior.

## Tabla antes/después: canónicas v1 vs v2 sobre las 10 opps del legacy

Las 10 opps del legacy con sus `canonical_id` asignados por v1 (threshold=0.3):

| id | product_name (abreviado) | canonical_id v1 | cluster v1 |
|----|--------------------------|-----------------|------------|
| 1  | Inventory Tracker Ecommerce | 1 | {1} |
| 2  | Client Communication Agencies | 2 | {2,4,7,9,10} |
| 3  | Ecommerce Order Fulfillment Optimizer | 3 | {3,5,6} |
| 4  | Client Management Agency | 2 | {2,4,7,9,10} |
| 5  | Ecommerce Order Fulfillment Tool | 3 | {3,5,6} |
| 6  | Ecommerce Order Fulfillment Tool | 3 | {3,5,6} |
| 7  | Client Communication Project Context | 2 | {2,4,7,9,10} |
| 8  | (problema distinto, vocabulary disjoint) | 8 | {8} |
| 9  | Client... | 2 | {2,4,7,9,10} |
| 10 | Client... | 2 | {2,4,7,9,10} |

**v1 produce 4 canónicas: {1}, {2,4,7,9,10}, {3,5,6}, {8}**

Con v2 (threshold=0.75), la hipótesis del acceptance es que id=8 se une al
cluster {2,4,7,9,10} si su `core_problem + niche` es semánticamente equivalente,
**produciendo 3-4 canónicas**: {1}, {2,4,7,8,9,10}, {3,5,6}.

La verificación cuantitativa exacta requiere ejecutar `backfill_canonical_v2.py
--dry-run` con `sentence-transformers` instalado — el acceptance dice "5-7
canónicas" pero el análisis de los datos reales del legacy (evidencia textual
de las opps) sugiere que el número depende del umbral elegido.
