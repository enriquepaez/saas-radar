# saas-radar

Pipeline Python que escanea subreddits buscando dolores reales de usuarios y
los analiza con un LLM para detectar oportunidades de micro-SaaS accionables.
Sustituye al legacy [`reddit-saas-radar`](../reddit-saas-radar) reusando su
base de datos (`data/saas.db`).

Este repo aplica los principios de **Harness Engineering**: el código de la
aplicación se construye **una feature a la vez** por subagentes de IA (leader,
implementer, reviewer) con verificación automática.

## Cómo está organizado el arnés

| Pilar | Manifestación en este repo |
|-------|----------------------------|
| **1. El repositorio ES el sistema** | `AGENTS.md`, `init.sh`, `feature_list.json`, `progress/`, `docs/` |
| **2. Orquestación multi-agente**    | `.claude/agents/leader.md`, `implementer.md`, `reviewer.md` |
| **3. Supervisión y mejora**         | `CHECKPOINTS.md`, hooks en `.claude/settings.json`, `tests/` |

## Para empezar

```bash
./init.sh
```

Si todo está verde, abre `AGENTS.md` y sigue desde ahí.

## Reutilización del legacy

El proyecto legacy [`reddit-saas-radar`](../reddit-saas-radar) llevaba ~6
meses operando antes de este repo. Reusamos:

- **`data/saas.db`** — SQLite con 19.702 posts, 12.654 comments, 10
  opportunities y 35 meta_recommendations. Va copiado tal cual desde el
  legacy. Las migraciones idempotentes de `init_db()` añadirán columnas
  nuevas si las features requieren cambios de schema.
- **Documentación heredada** en `docs/legacy-context/`:
  - [`inventory.md`](docs/legacy-context/inventory.md) — mapa técnico
    (módulos, funciones, schema BD, env vars, CI).
  - [`architecture.md`](docs/legacy-context/architecture.md) — flujos del
    pipeline y decisiones técnicas explicadas.
  - [`lessons-learned.md`](docs/legacy-context/lessons-learned.md) — qué
    reproducir tal cual y qué cambiar.
  - [`feature-backlog.md`](docs/legacy-context/feature-backlog.md) —
    backlog priorizado con dependencias (es la fuente de `feature_list.json`).

Lee `lessons-learned.md` **antes** de portar nada del legacy: hay deuda
técnica documentada que no debe reproducirse (ej. `sys.path.append`,
mutación de `config.AI_PROVIDER`, `dashboard/app.py` scaffold).

## Estructura

```
.
├── AGENTS.md                    # Mapa para agentes (divulgación progresiva)
├── CHECKPOINTS.md               # Criterios de "estado final correcto"
├── CLAUDE.md                    # Forzado del rol leader
├── feature_list.json            # Alcance: una feature a la vez
├── init.sh                      # Verificación e inicialización
├── pyproject.toml               # (lo crea la feature #1) — config ruff + pytest + deps
├── progress/
│   ├── current.md               # Sesión activa (estado vivo)
│   └── history.md               # Bitácora append-only
├── docs/
│   ├── architecture.md          # Qué significa "buen trabajo" en este proyecto
│   ├── conventions.md           # Estilo, nombres, errores
│   ├── verification.md          # Cómo demostrar que funciona
│   └── legacy-context/          # Documentación heredada del proyecto reddit-saas-radar
│       ├── inventory.md
│       ├── architecture.md
│       ├── lessons-learned.md
│       └── feature-backlog.md
├── .claude/
│   ├── agents/                  # Definiciones de líder, implementador, revisor
│   └── settings.json            # Hooks que automatizan la verificación
├── src/
│   └── saas_radar/              # (lo crea la feature #1) — paquete principal
└── tests/                       # Tests pytest
```

## Stack objetivo

- Python 3.11+ (declarado en `pyproject.toml` por la feature #1).
- PRAW (Reddit API), pandas, NLTK, httpx, SQLAlchemy sobre SQLite.
- LLM: dispatcher Claude (Anthropic) / Gemini (Google) / Groq, configurable
  por `AI_PROVIDER`. Default: Claude Haiku 4.5 (extracción) + Sonnet 4.6
  (síntesis).
- Concurrencia: `concurrent.futures.ThreadPoolExecutor` para comentarios.
- Tooling: `ruff` (lint + format), `pytest` (tests).

## Probarlo con Claude Code

Si abres Claude Code en la raíz del repo, ya estás dentro del arnés:
`CLAUDE.md` fuerza al modelo a actuar como `leader` (orquesta, no edita
código).

Receta rápida:

1. `./init.sh` — debe terminar verde.
2. Abre `feature_list.json` y verifica que la feature #1 (`bootstrap_package`)
   está `pending`.
3. Lanza Claude Code en la raíz: `claude`.
4. Pídele literalmente: **"implementa la siguiente feature pendiente"**.

Lo que verás:

- El **leader** anuncia el plan, lanza un `implementer` y luego un `reviewer`.
- Por chat **no pasa código** — solo referencias del tipo
  `done -> progress/impl_<feature>.md`.

Trazabilidad de la sesión (visualización persistente en `progress/`):

| Archivo                          | Quién lo escribe | Qué contiene                                        |
|----------------------------------|------------------|-----------------------------------------------------|
| `progress/current.md`            | leader           | Plan vivo de la sesión                              |
| `progress/impl_<feature>.md`     | implementer      | Archivos tocados + output de los tests + explicación línea a línea |
| `progress/review_<feature>.md`   | reviewer         | Checklist contra `docs/` y `CHECKPOINTS.md`         |
| `feature_list.json`              | implementer      | `pending` → `in_progress` → `done`                  |
| `progress/history.md`            | leader           | Resumen append-only al cerrar la sesión             |

## Roadmap

Ver [`feature_list.json`](feature_list.json) para el plan vivo. Resumen:

- **M1 — Foundation**: pyproject + db_layer + config + scraper (#1-#4).
- **M2 — Pipeline IA mínimo**: text/classifier + semantic_score + data_loader + llm_clients + extraction + synthesis + ai_analyzer + main CLI (#5-#12).
- **M3 — Productización**: meta_analysis + telegram + dedup + workflow CI (#13-#16).
- **M4 — Operación avanzada**: GTM agent + tuner + logging + tuner PR mode (#17-#20).

Detalle de cada feature con `acceptance` verificable en
[`docs/legacy-context/feature-backlog.md`](docs/legacy-context/feature-backlog.md).
