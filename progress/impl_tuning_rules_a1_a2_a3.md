# Implementación: #18 — tuning_rules_a1_a2_a3

## Qué cambió

- **`src/saas_radar/agents/tuning_rules.py`**: creado desde cero (port del legacy). Contiene el dataclass `Proposal`, los 3 helpers privados y las 4 funciones de reglas + el orquestador `propose_all_changes`. Antes: no existía. Después: módulo completo con header estándar (`from __future__ import annotations`, `logging.getLogger(__name__)`).

- **`src/saas_radar/agents/tuner.py`**: creado desde cero (port del legacy). Contiene loaders (`load_recent_runs`, `load_meta_recommendations`), priorizador (`prioritize_and_cap`), renderers (`render_report`, `render_config_diff`) y CLI (`_parse_args`, `main`). Cambio crítico: `import config` tardío sustituido por `from saas_radar import config` dentro de `main()`.

- **`.github/workflows/tuner.yml`**: creado desde cero (port del legacy). Trigger `workflow_run` sobre `"saas-radar pipeline"` en lugar de `"Reddit SaaS Radar pipeline"`. Actions actualizadas a v4/v5. Install con `pip install -e .[dev]`. Añadido step NLTK. Comando con `python -m saas_radar.agents.tuner`. Telegram con `python -m saas_radar.notifications.telegram tuner-report`.

- **`tests/test_tuning_rules.py`**: creado desde cero (port del legacy). Import cambiado de `from agents.tuning_rules import ...` a `from saas_radar.agents.tuning_rules import ...`. 26 tests idénticos al legacy en lógica.

- **`tests/test_tuner.py`**: creado desde cero (port del legacy). Imports cambiados a `saas_radar.agents.tuner` y `saas_radar.agents.tuning_rules`. Monkeypatch de config cambiado de `import config; monkeypatch.setattr(config, ...)` a `from saas_radar import config as saas_config; monkeypatch.setattr(saas_config, ...)`. Añadido test extra `test_render_report_snapshot`. Total: 18 tests.

- **`tests/fixtures/tuner_report_expected.txt`**: generado automáticamente por el snapshot test en la primera ejecución. Contiene el output estable de `render_report` con datos fijos y timestamp mockeado.

## Por qué

### Port del legacy sin cambiar lógica

Las 4 reglas (promote, remove_subreddit, demote, remove_query) y el orquestador tienen lógica determinista probada en el legacy. No hay razón para cambiarla: replicar el comportamiento exacto garantiza compatibilidad con los meta-JSONs que ya existen en `data/runs/`.

### Import tardío de config en `main()`

El legacy hace `import config` dentro de `main()` (no en el top-level). Esto no es un error — es una decisión deliberada para que los tests puedan monkeypatchear `config.HIGH_SIGNAL_SUBREDDITS` etc. antes de que `main()` los lea. Si el import fuera a nivel de módulo, el import se ejecutaría al cargar `tuner.py` y el monkeypatch llegaría tarde.

En el nuevo proyecto el módulo se llama `saas_radar.config`, no `config`, así que el import tardío se convierte en `from saas_radar import config`. La semántica es idéntica: se importa el módulo al invocar `main()`, no al cargar `tuner.py`.

### Monkeypatch en tests

En el legacy los tests hacen:
```python
import config
monkeypatch.setattr(config, "HIGH_SIGNAL_SUBREDDITS", set(), raising=False)
```
Esto funciona porque `import config` y `import config` dentro de `main()` resuelven al mismo objeto en `sys.modules`. En el nuevo proyecto hay que usar el mismo módulo objeto:
```python
from saas_radar import config as saas_config
monkeypatch.setattr(saas_config, "HIGH_SIGNAL_SUBREDDITS", set(), raising=False)
```
Así cuando `main()` hace `from saas_radar import config`, Python devuelve el objeto ya parcheado (es el mismo en `sys.modules["saas_radar.config"]`).

### Actions actualizadas en el workflow

El legacy usa `actions/checkout@v6` y `actions/setup-python@v6`, que en el momento de la implementación no existen (la última estable es v4/v5 respectivamente). Usar versiones inexistentes hace fallar el workflow.

### Step NLTK

El paquete `saas_radar` importa módulos que dependen de NLTK stopwords (text_cleaning). Aunque `tuner.py` no los usa directamente, `pip install -e .[dev]` instala el paquete completo y cualquier import indirecto fallaría sin el recurso descargado. El step es preventivo y sin coste significativo.

## Impacto en el pipeline

- **Tuner**: se añade la fase A2 (dry-run) del ciclo de tuning automático. El workflow `tuner.yml` corre después de cada ejecución exitosa del pipeline principal y genera un report de propuestas de cambio a `config.py`.
- **Telegram**: el report se envía al canal configurado vía `send_tuner_report` (ya implementado en feature #14).
- **config.py**: no se modifica (fase A2 es solo lectura). La modificación real viene en feature #20 (modo `--apply`).
- **BD**: se lee `meta_recommendations` para priorizar propuestas, pero no se escribe nada.
- **GitHub Actions**: se añade un segundo workflow. La cadena completa queda: `pipeline.yml` → on success → `tuner.yml`.

## Explicación técnica

### `tuning_rules.py`

**`_aggregate_subreddit_stats(runs)`**: itera todos los runs y acumula `posts`, `with_problem`, `payment`, `runs_seen` por subreddit en un dict. Usa `dict.setdefault(key, default)` para inicializar el contador la primera vez que aparece un subreddit sin necesidad de un `if key not in d`. Normaliza a minúsculas con `.lower()` antes de usar como clave, garantizando que `r/SaaS` y `r/saas` se cuenten juntos.

**`_count_consecutive_silent(runs, subreddit)`**: itera los runs en orden inverso (`reversed(runs)`) y cuenta cuántos consecutivos desde el más reciente tienen el subreddit en `silent_subreddits`. Rompe el loop en cuanto encuentra uno que no lo tiene. El `reversed()` no copia la lista, devuelve un iterador.

**`_count_consecutive_empty_query(runs, query)`**: análogo al anterior pero para `empty_queries`. Compara strings exactos (no normaliza), porque las queries son texto libre.

**`propose_promote_to_high_signal`**: aplica regla 1. Dos condiciones OR: `(posts >= 3 AND hit_rate >= 0.75 AND payment >= 1)` OR `(payment >= 2)`. La segunda condición permite promover subreddits con payment alto aunque la muestra sea pequeña. Guarda `current_lower = {s.lower() for s in current_high_signal}` para evitar normalizar en cada iteración.

**`propose_remove_from_subreddits`**: aplica regla 2 en dos sub-reglas (2a y 2b). Usa `seen_targets: set[str]` para evitar duplicados cuando ambas causas se cumplen para el mismo subreddit. La sub-regla 2b usa `runs[-2:]` (slice, no copia el iterador) para acceder a los últimos 2 runs.

**`propose_demote_from_high_signal`**: aplica regla 3 en dos sub-reglas (3a y 3b). `seen: set[str]` evita que un subreddit aparezca dos veces si cumple ambas condiciones. En 3b, el `if sub in seen: continue` salta subreddits ya degradados por 3a.

**`propose_remove_queries`**: aplica regla 4. Retorno anticipado `if len(runs) < 3: return []` porque no tiene sentido buscar 3 runs consecutivos si no hay 3 runs.

**`propose_all_changes`**: orquestador que llama a las 4 reglas en orden conservador: remove_query → demote → remove_subreddit → add_high_signal. El orden importa porque el consumidor puede capear con `max_changes` y queremos aplicar primero los cambios menos arriesgados.

### `tuner.py`

**`load_recent_runs`**: usa `glob.glob` con patrón `*_meta.json` y `sorted()`. Como los nombres de fichero empiezan por ISO timestamp (`2026-04-20T000000_meta.json`), el orden lexicográfico coincide con el cronológico. `paths[-lookback:]` selecciona los N más recientes. El bloque `try/except (json.JSONDecodeError, OSError)` tolera ficheros corruptos o sin permisos de lectura, escribiendo un warning a `stderr` con `print(..., file=sys.stderr)` (no `logger.warning` porque el tuner es un CLI standalone que puede usarse sin logging configurado).

**`load_meta_recommendations`**: comprueba `os.path.exists(db_path)` antes de conectar para evitar que SQLite cree el fichero si no existe. Usa `conn.row_factory = sqlite3.Row` para poder acceder a columnas por nombre. El `try/except sqlite3.OperationalError` captura el caso de tabla ausente (primera ejecución antes de `init_db`). El `finally: conn.close()` garantiza que la conexión se cierra incluso si hay excepción.

**`prioritize_and_cap`**: construye `rec_by_target: dict[tuple[str, str], int]` con el máximo recurrence visto por par `(action, target)`. La función `sort_key` devuelve una tupla de 3 elementos: `(orden_accion, -recurrence, target_lowercase)`. El signo negativo en recurrence invierte el orden natural (queremos desc). Python ordena tuplas lexicográficamente, así que el orden de acción tiene prioridad, luego recurrence descendente, luego target alfabético.

**`render_report`**: construye el texto línea a línea en una lista y al final hace `"\n".join(lines)`. El ancho de columna `width = max(len(p.action) for p in data.applied_proposals)` calcula el ancho mínimo para alinear las acciones. El format spec `{p.action:<{width}}` alinea a la izquierda con el ancho calculado. `{target:<40}` trunca targets largos (>40 chars) con `...`.

**`render_config_diff`**: genera pseudo-Python para visualizar qué cambiaría en `config.py`. Para `remove_query`, escapa las comillas dobles con `t.replace('"', '\\"')` para que el string generado sea Python válido. El tipo `Iterable[Proposal]` (en lugar de `list[Proposal]`) permite pasar cualquier iterable, incluyendo generadores.

**Import tardío de config en `main()`**: la línea `from saas_radar import config` está dentro de `main()`, no en el top-level. Esto es intencional para que los tests puedan monkeypatchear `config.HIGH_SIGNAL_SUBREDDITS` etc. antes de que `main()` los lea. Si estuviera en el top-level, Python cargaría el módulo al importar `tuner` y el monkeypatch llegaría tarde.

### `tuner.yml`

**`workflow_run`**: se dispara cuando el workflow `"saas-radar pipeline"` completa. El `if: ${{ ... || github.event.workflow_run.conclusion == 'success' }}` garantiza que el tuner solo corre si el pipeline terminó bien (o si es disparo manual). Sin este guard, el tuner correría incluso tras un pipeline fallido.

**`concurrency: cancel-in-progress: false`**: a diferencia de otros workflows donde es `true`, aquí es `false` porque no queremos cancelar un análisis en curso si llega otro trigger. Cada análisis es independiente y puede generar un artefacto diferente.

**NLTK step**: `python -c "import nltk; nltk.download('stopwords', quiet=True)"` descarga el recurso antes de ejecutar el tuner. `quiet=True` suprime el output verbose de NLTK en los logs de CI.

### Snapshot test

**`test_render_report_snapshot`**: usa `unittest.mock.patch("saas_radar.agents.tuner.datetime")` para fijar el timestamp de `datetime.now(timezone.utc)`. El path del patch es el módulo donde `datetime` se usa (`saas_radar.agents.tuner`), no donde está definida (`datetime`). Esto es la regla de patching de Python: se parchea el nombre en el namespace donde se usa, no donde se define.

Si el fixture no existe, el test lo crea con `fixture.write_text(out, ...)` y llama a `pytest.skip(...)` para informar al usuario. En la segunda ejecución, el fixture ya existe y el test compara `out == expected`.

## Tests añadidos

### `tests/test_tuning_rules.py` (26 tests)

- `TestPromoteToHighSignal::test_promueve_con_hitrate_alto_y_payment`: regla 1 caso base (3 posts, 100% hit, 2 payments).
- `TestPromoteToHighSignal::test_no_promueve_si_ya_es_high_signal`: subreddit ya en el set no genera propuesta.
- `TestPromoteToHighSignal::test_no_promueve_con_muestra_insuficiente`: 2 posts < 3 mínimo.
- `TestPromoteToHighSignal::test_no_promueve_sin_payment_ni_hitrate_alto`: 50% hit y 0 payments.
- `TestPromoteToHighSignal::test_promueve_con_dos_payment_aunque_hitrate_bajo`: 2 payments activan la condición OR.
- `TestPromoteToHighSignal::test_acumula_entre_runs`: 2+2=4 posts acumulados superan el umbral.
- `TestPromoteToHighSignal::test_normaliza_a_minusculas`: `Ecommerce` == `ecommerce`.
- `TestRemoveFromSubreddits::test_quita_por_recurrence_alta`: recurrence=3 activa la regla 2a.
- `TestRemoveFromSubreddits::test_no_quita_si_recurrence_menor_a_3`: recurrence=2 no es suficiente.
- `TestRemoveFromSubreddits::test_no_quita_si_target_no_esta_configurado`: subreddit no en SUBREDDITS se ignora.
- `TestRemoveFromSubreddits::test_quita_por_cero_hit_dos_runs`: 0% hit en 2 runs consecutivos (regla 2b).
- `TestRemoveFromSubreddits::test_no_quita_con_muestra_total_insuficiente`: 4 posts < 5 mínimo.
- `TestRemoveFromSubreddits::test_no_quita_si_silent_en_uno_de_los_dos_runs`: silent != 0 posts en el run.
- `TestRemoveFromSubreddits::test_no_duplica_cuando_ambas_causas_se_cumplen`: seen_targets evita doble propuesta.
- `TestDemoteFromHighSignal::test_degrada_por_5_runs_silent_consecutivos`: regla 3a.
- `TestDemoteFromHighSignal::test_no_degrada_con_solo_4_silent`: 4 < 5 mínimo.
- `TestDemoteFromHighSignal::test_no_degrada_si_silent_no_es_consecutivo`: racha rota no cuenta.
- `TestDemoteFromHighSignal::test_degrada_por_hitrate_sostenidamente_bajo`: 20% < 25% en 3 runs (regla 3b).
- `TestDemoteFromHighSignal::test_no_degrada_con_muestra_acumulada_insuficiente`: 6 posts < 10 mínimo.
- `TestDemoteFromHighSignal::test_no_duplica_cuando_ambas_causas_se_cumplen`: seen evita doble propuesta.
- `TestRemoveQueries::test_quita_query_empty_3_runs`: regla 4 caso base.
- `TestRemoveQueries::test_no_quita_con_solo_2_runs_empty`: 2 < 3 mínimo.
- `TestRemoveQueries::test_no_quita_si_empty_no_es_consecutivo`: racha rota no cuenta.
- `TestRemoveQueries::test_devuelve_vacio_con_historial_corto`: menos de 3 runs total.
- `TestProposeAllChanges::test_combina_reglas_en_el_mismo_historico`: orquestador aplica varias reglas.
- `TestProposeAllChanges::test_sin_datos_no_propone_nada`: inputs vacíos → sin propuestas.

### `tests/test_tuner.py` (18 tests)

- `TestLoadRecentRuns::test_carga_los_mas_recientes_en_orden_asc`: 3 ficheros cargados en orden temporal.
- `TestLoadRecentRuns::test_respeta_lookback`: lookback=2 devuelve solo los 2 más recientes.
- `TestLoadRecentRuns::test_salta_json_corrupto_y_sigue`: JSON inválido → warning en stderr, sigue.
- `TestLoadRecentRuns::test_dir_vacio_devuelve_lista_vacia`: sin ficheros → lista vacía.
- `TestLoadMetaRecommendations::test_lee_filas_como_dicts`: 2 filas en BD → 2 dicts.
- `TestLoadMetaRecommendations::test_bd_inexistente_devuelve_vacio`: path no existe → lista vacía.
- `TestLoadMetaRecommendations::test_tabla_ausente_devuelve_vacio`: BD sin tabla → lista vacía.
- `TestPrioritizeAndCap::test_orden_accion_conservador_primero`: remove_query < demote < add.
- `TestPrioritizeAndCap::test_recurrence_desc_dentro_del_mismo_tipo`: mayor recurrence → antes.
- `TestPrioritizeAndCap::test_cap_aplica`: max_changes=3 trunca a 3.
- `TestPrioritizeAndCap::test_cap_negativo_devuelve_todo`: max_changes=-1 devuelve todo.
- `TestRenderReport::test_report_sin_propuestas`: sin propuestas → mensaje "sin propuestas".
- `TestRenderReport::test_report_con_varias_propuestas`: formato con proposals y RESUMEN correcto.
- `TestRenderConfigDiff::test_genera_pseudo_python`: 4 acciones → 4 líneas de pseudo-Python.
- `TestRenderConfigDiff::test_sin_propuestas`: lista vacía → mensaje "sin cambios".
- `TestCliMain::test_cli_exit_cero_y_reporta_propuestas`: E2E con 3 runs → report con add+remove.
- `TestCliMain::test_cli_sin_runs_sigue_con_exit_cero`: sin runs → exit 0 + "sin propuestas".
- `test_render_report_snapshot`: snapshot del formato del report contra fixture en disco.

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 54 items

tests/test_tuning_rules.py ..........................                    [ 48%]
tests/test_tuner.py ..................                                   [ 81%]
tests/test_telegram.py ..........                                        [100%]

============================== 54 passed in 0.06s ==============================
```

Suite completa: exit code 0 (todos los tests pasan, sin regresiones).
