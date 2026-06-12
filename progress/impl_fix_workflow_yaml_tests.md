# Implementación: fix — workflow yaml tests alignment

## Qué cambió

- **`tests/test_pipeline_workflow.py`** (modificado):
  - **Renombrado** `test_no_data_branch_checkout` -> `test_has_data_branch_checkout`. Antes el test aseveraba que NO debía existir ningún step de checkout con `ref: data`; ahora asevera lo contrario: que SÍ existe (y además que su `path` es `persist`).
  - **Renombrado** `test_permissions_contents_read` -> `test_permissions_contents_write`. Antes comprobaba `permissions.contents == "read"`; ahora comprueba `permissions.contents == "write"`.
  - **Añadido** `test_has_persist_step`. Nuevo test que asegura que existe un step cuyo `name` contiene la palabra "persist" (case-insensitive). Sirve como regresión-guard del paso "Persist to data branch" introducido por F22.

No se ha modificado `.github/workflows/pipeline.yml` (ya estaba correcto post-F22).

## Por qué

La feature #22 (`pipeline_persistence_restoration`, PR #33) cambió el workflow para persistir `data/saas.db` y `data/runs/` a la rama `data` mediante un segundo checkout y un step `Persist to data branch`, y elevó `permissions.contents` de `read` a `write` para poder hacer push. Los tests existentes seguían reflejando la arquitectura previa (cache-only, contents read-only) y por eso fallaban 2 de ellos en la suite.

Decisiones no obvias:

- **No se borraron los tests**, se invirtió su polaridad (siguiendo la regla del fix: los tests deben seguir validando algo útil). La intención del test cambia, pero la idea — "el workflow declara explícitamente su estrategia de persistencia y sus permisos" — se conserva.
- **`test_has_data_branch_checkout` valida tanto `ref` como `path`**, no solo `ref`. Eso evita falsos positivos si alguien añadiese accidentalmente otro checkout con `ref: data` sin `path: persist` (lo que rompería la lógica de copiar `data/saas.db` a `persist/data/saas.db` en el step de Persist).
- **`test_has_persist_step` mira `name` y no `run`**, porque el `run` puede cambiar mucho (rutas, comandos git, mensajes de commit) pero el `name` del step suele ser estable y semántico. Es la señal más robusta de "el paso de persistencia sigue ahí".
- Se descartó usar `if: success()` como heurística para detectar el step de persistencia: otros steps futuros podrían usar esa condición y daría falsos positivos.

## Impacto en el pipeline

- **Cero impacto** en el código de producción y en el comportamiento del workflow en GitHub Actions. El cambio es solo de tests.
- **Impacto positivo en CI local**: `./init.sh` / `pytest` vuelven a estar verdes; la deuda introducida por F22 queda saldada.
- Futuras regresiones que rompan la persistencia (alguien que vuelva a poner `contents: read`, que borre el segundo checkout o el step `Persist`) ahora fallan en `pytest` antes de mergear, en lugar de descubrirse en producción cuando la rama `data` deje de actualizarse.

## Explicación técnica

### `test_has_data_branch_checkout(workflow: dict)`

```python
def test_has_data_branch_checkout(workflow: dict):
    """Existe un step de checkout con ref: data y path: persist (F22 persistencia)."""
    steps = workflow["jobs"]["run"]["steps"]
    data_checkouts = [
        s for s in steps
        if s.get("uses", "").startswith("actions/checkout")
        and s.get("with", {}).get("ref") == "data"
        and s.get("with", {}).get("path") == "persist"
    ]
    assert len(data_checkouts) >= 1, (
        "Falta checkout de la rama 'data' con path 'persist' (necesario para F22)"
    )
```

- **`workflow`** es el fixture módulo-scope que ya existe en el archivo: carga el YAML una vez y lo entrega a cada test como `dict`.
- **`workflow["jobs"]["run"]["steps"]`**: accede directamente al array de pasos del job `run`. Es el mismo patrón que usan el resto de tests del archivo, así mantenemos homogeneidad.
- **List comprehension con triple guard**:
  - `s.get("uses", "").startswith("actions/checkout")` filtra solo los pasos que usan la action `actions/checkout` (sin importar la versión `@v3` / `@v4`). El `s.get("uses", "")` con default vacío evita un `KeyError` si el step es un `run:` puro sin `uses`.
  - `s.get("with", {}).get("ref") == "data"` exige que el input `ref` sea exactamente la cadena `"data"`. Usar `.get("with", {})` con dict vacío como default es defensa contra steps sin bloque `with`.
  - `s.get("with", {}).get("path") == "persist"` exige que el checkout escriba en el subdirectorio `persist/`. Esto es lo que hace que el step posterior `Persist to data branch` pueda copiar archivos a `persist/data/saas.db` y hacer `cd persist && git push origin data`.
- **`assert len(...) >= 1`** (en lugar de `== 1`): no nos importa si en el futuro hubiese más de un checkout sobre `data`; solo nos importa que exista al menos uno. Es menos quebradizo.

### `test_permissions_contents_write(workflow: dict)`

```python
def test_permissions_contents_write(workflow: dict):
    """permissions.contents debe ser 'write' (F22 persiste a rama data via push)."""
    permissions = workflow.get("permissions")
    assert permissions is not None, "Falta bloque 'permissions'"
    assert permissions.get("contents") == "write", (
        f"permissions.contents debe ser 'write', es: {permissions.get('contents')}"
    )
```

- **`workflow.get("permissions")`** en lugar de indexación directa para no fallar con `KeyError` si falta el bloque: el siguiente assert da un mensaje más útil ("Falta bloque 'permissions'") que un stack trace.
- **`permissions.get("contents") == "write"`** valida el valor exacto. `write` (no `read`, no `read-write`, no `write-all`) es lo que GitHub Actions necesita para que el `git push origin data` del step `Persist to data branch` pueda escribir.
- **f-string en el mensaje de error** con el valor actual para que cuando alguien rompa esto en el futuro vea inmediatamente qué valor encontró el test.

### `test_has_persist_step(workflow: dict)`

```python
def test_has_persist_step(workflow: dict):
    """Existe un step cuyo name contiene 'Persist' (regresión-guard para F22)."""
    steps = workflow["jobs"]["run"]["steps"]
    persist_steps = [
        s for s in steps
        if "persist" in s.get("name", "").lower()
    ]
    assert len(persist_steps) >= 1, (
        "Falta step con 'Persist' en el name (paso de persistencia a rama data)"
    )
```

- **`s.get("name", "")`** con default `""` para que el `in`-check no rompa si un step no tiene `name` (algunos pasos `run:` pueden omitirlo).
- **`.lower()`** sobre el `name` y comparación con `"persist"` en minúsculas: hace el matching case-insensitive. Así el test no se rompe si alguien renombra "Persist to data branch" a "persist run outputs" o "Persistence step".
- **Test deliberadamente laxo**: no exige `run` específico, ni `if: success()`, ni paths concretos. Solo "existe un step con intención clara de persistencia". Esa laxitud es a propósito: queremos detectar borrados del step, no penalizar refactors del contenido.

## Tests añadidos

- `test_has_data_branch_checkout` — verifica que existe un step `actions/checkout` con `ref: data` y `path: persist` (la base de la persistencia introducida por F22).
- `test_permissions_contents_write` — verifica que `permissions.contents == "write"`, requisito de GitHub Actions para que el job pueda hacer `git push`.
- `test_has_persist_step` — verifica que existe un step cuyo `name` contiene "persist" (case-insensitive); regresión-guard barato y robusto contra el borrado accidental del paso de persistencia.

Los dos primeros son renombrados con polaridad invertida de tests que ya existían (`test_no_data_branch_checkout`, `test_permissions_contents_read`); el tercero es completamente nuevo.

## Verificación

`./.venv/bin/pytest -q tests/test_pipeline_workflow.py`:

```
....................                                                     [100%]
```

20 tests del archivo, todos verdes (exit code 0).

`./.venv/bin/pytest -q` (suite completa):

```
........................................................................ [ 17%]
........................................................................ [ 34%]
........................................................................ [ 51%]
........................................................................ [ 68%]
........................................................................ [ 85%]
..............................................................           [100%]
```

422 tests, 0 fallos, 0 errores, 0 skips reportados, exit code 0.
