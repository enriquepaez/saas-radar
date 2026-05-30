# Review — feature #16: github_actions_pipeline_workflow

**Veredicto:** CHANGES_REQUESTED

## Acceptance criteria

- AC1 [x]: Workflow disparable con `gh workflow run 'saas-radar pipeline' -f full_scan=true`. El nombre del workflow es exactamente `saas-radar pipeline` (línea 1 de `pipeline.yml`) y el input `full_scan` de tipo `boolean` está definido en `workflow_dispatch.inputs` (líneas 6-12).
- AC2 [x]: El job no tiene `set -e` en el step "Run pipeline" (líneas 83-89). El step se ejecuta directamente con `if/else` y termina con el exit code de `python -m saas_radar.main`, que no llama a `sys.exit(1)` en caso de status `partial`. Sin `set -e` la shell no propaga errores intermedios que no sean el comando final, lo que es correcto.
- AC3 [x]: La guarda `git diff --cached --quiet` está presente en el step "Commit and push to data branch" (línea 105 de `pipeline.yml`): `if ! git diff --cached --quiet; then`.
- AC4 [x]: Bloque `concurrency` con `group: 'saas-radar'` y `cancel-in-progress: false` presente en líneas 14-16.
- AC5 [x]: Secrets documentados en `progress/impl_github_actions_pipeline_workflow.md` (tabla de 9 secrets: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `AI_PROVIDER`). Todos los que exige el acceptance criterion están presentes.
- AC6 [ ]: **"Verificación manual: 1 run real en GitHub con artefactos correctos (documentar en progress/)"** — El impl doc documenta los pasos a seguir pero no evidencia que el run haya ocurrido. Los ejemplos de salida en la sección "Paso 4" son placeholders (`a1b2c3d chore: pipeline run 2026-05-30T08:00:01Z`) sin timestamps reales ni IDs reales. No hay pantallazos, logs copiados ni referencia a un `gh run view` con ID real.

## Problemas críticos (bloquean aprobación)

### P1 — `pyyaml` declarada en la sección incorrecta de `pyproject.toml`

`pyproject.toml` líneas 41-44:

```toml
[dependency-groups]
dev = [
    "pyyaml>=6.0.3",
]
```

`pyyaml` debería estar en `[project.optional-dependencies].dev` (líneas 20-24), junto a `pytest`, `ruff` y `respx`. La sección `[dependency-groups]` es un formato PEP 735 gestionado exclusivamente por `uv` — `pip install -e ".[dev]"` (el comando del workflow en el step "Install dependencies", línea 78) NO lee `[dependency-groups]` y por tanto NO instalaría `pyyaml` en el runner de GitHub Actions.

Consecuencia: `tests/test_pipeline_workflow.py` fallaría con `ModuleNotFoundError: No module named 'yaml'` en cualquier entorno que use pip estándar (incluido el runner de CI). Los tests pasan localmente porque el venv fue creado con `uv` que sí procesa `[dependency-groups]`.

**Corrección requerida:** mover `"pyyaml>=6.0.3"` de `[dependency-groups].dev` a `[project.optional-dependencies].dev`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.4",
    "respx>=0.21",
    "pyyaml>=6.0.3",
]
```

Y eliminar el bloque `[dependency-groups]` completo.

### P2 — AC6 no cumplido: falta evidencia de run real en GitHub

El acceptance criterion 6 exige "1 run real en GitHub con artefactos correctos (documentar en progress/)". El impl doc contiene solo instrucciones sobre cómo ejecutar el run, no evidencia de que se ejecutó.

**Corrección requerida:** ejecutar `gh workflow run 'saas-radar pipeline' -f full_scan=true` en el repo remoto (una vez que la rama esté en GitHub) y añadir al impl doc la salida real de `gh run list --workflow=pipeline.yml --limit=1` con el run ID real y el status final. Si el run falla por falta de secrets en el repo, documentar el error y el estado (`failed` es aceptable, según el propio AC2).

## Checkpoints generales

- C1 [x]: Arnés completo. `./init.sh` termina verde (exit code 0).
- C2 [x]: Solo feature #16 en `in_progress`. Sin incoherencias de dependencias.
- C3 [x]: No hay `sys.path.append` en `src/`. El archivo de test y el workflow no introducen módulos fuera de las capas previstas. El workflow es infraestructura, no un módulo Python, por lo que no aplica la restricción de capas de `src/saas_radar/`.
- C4 [x]: 17 tests en `tests/test_pipeline_workflow.py`, todos pasan (`17 passed in 0.03s`). Suite completa: 280 passed, 0 failed. Sin mocks de filesystem; los tests leen el archivo YAML real desde `Path(__file__).parent.parent / ".github" / "workflows" / "pipeline.yml"` — patrón correcto para tests de estructura de workflow. Sin llamadas reales a GitHub.
- C5 [x]: No aplica directamente (feature #16 no toca la BD). `data/saas.db` existe.
- C6 [ ]: Sesión aún abierta (feature `in_progress`), no aplica hasta cierre.

## Observaciones menores (no bloquean)

1. El workflow no declara `permissions:` explícitas. El `GITHUB_TOKEN` por defecto tiene permisos de escritura en repositorios privados y en algunos públicos, pero la práctica recomendada de GitHub Actions es declarar `permissions: contents: write` explícitamente cuando el workflow hace push. Aunque funcional, puede romperse si el administrador del repositorio cambia los permisos por defecto del token.

2. El impl doc indica "288 tests, 0 failed" pero la suite real produce 280 passed. Discrepancia menor (probablemente el conteo se hizo antes de que alguna feature de tests cambiara el número total), no afecta a la validez de los 17 tests nuevos.
