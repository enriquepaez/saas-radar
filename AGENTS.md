# AGENTS.md — Mapa de navegación para agentes de IA

> Este archivo es el **punto de entrada** para cualquier agente que trabaje en
> este repositorio. NO es una biblia de reglas: es un **mapa**. Lee solo lo que
> necesites cuando lo necesites (divulgación progresiva).

---

## 1. Antes de empezar (obligatorio)

1. Ejecuta `./init.sh` y verifica que termina sin errores. Si falla, **para**
   y resuelve el entorno antes de tocar código.
2. Lee `progress/current.md` para entender en qué estado quedó la última sesión.
3. Lee `feature_list.json` y elige **una** tarea con estado `pending` cuyas
   dependencias estén `done`. No trabajes en más de una a la vez.

## 2. Mapa del repositorio

| Archivo / carpeta                     | Qué contiene                                              | Cuándo leerlo |
|---------------------------------------|-----------------------------------------------------------|---------------|
| `feature_list.json`                   | Lista de tareas con estado (pending / in_progress / done / blocked) | Siempre, al empezar |
| `progress/current.md`                 | Estado de la sesión actual                                | Siempre, al empezar |
| `progress/history.md`                 | Bitácora append-only de sesiones anteriores               | Si necesitas contexto histórico |
| `docs/architecture.md`                | Qué significa "hacer un buen trabajo" en este proyecto    | Antes de implementar |
| `docs/conventions.md`                 | Reglas de estilo, nombres, estructura                     | Antes de escribir código |
| `docs/verification.md`                | Cómo verificar que tu trabajo funciona                    | Antes de declarar una tarea como `done` |
| `docs/legacy-context/inventory.md`    | Inventario técnico del proyecto `reddit-saas-radar` legacy | Antes de portar un módulo |
| `docs/legacy-context/architecture.md` | Flujos y decisiones del legacy                            | Antes de decisiones de diseño grandes |
| `docs/legacy-context/lessons-learned.md` | Qué reproducir y qué NO del legacy                     | Antes de planificar una feature |
| `docs/legacy-context/feature-backlog.md` | Backlog original (fuente de `feature_list.json`)       | Si hay duda con `feature_list.json` |
| `CHECKPOINTS.md`                      | Criterios objetivos de "estado final correcto"            | Para auto-evaluarte |
| `.claude/agents/`                     | Definiciones de subagentes (líder, implementador, revisor) | Si orquestas trabajo |
| `src/saas_radar/`                     | Código de la aplicación (creado por feature #1)           | Para implementar |
| `tests/`                              | Tests pytest                                              | Para verificar |
| `data/`                               | BD heredada `saas.db` + outputs runtime (no commitear)    | Si trabajas con persistencia |

## 3. Reglas duras (no negociables)

- **Una sola feature a la vez.** No mezcles cambios de varias features en la
  misma sesión.
- **No declares una tarea `done` sin pruebas verdes.** Ejecuta `./init.sh` y
  asegúrate de que pytest pasa al 100%.
- **Documenta lo que haces** en `progress/current.md` mientras trabajas, no al
  final.
- **Deja el repositorio limpio** antes de cerrar la sesión (ver §5).
- **Si no sabes algo, busca en `docs/`** antes de inventarlo. Para
  comportamiento heredado del legacy, busca en `docs/legacy-context/` antes de
  reinventar.
- **Respeta las dependencias** del `feature_list.json`. Una feature con
  dependencias `pending` está bloqueada — no la elijas.

## 4. Cómo elegir una tarea

```
1. Abre feature_list.json
2. Filtra por status == "pending"
3. Filtra por features cuyas dependencias estén todas "done"
4. Coge la de menor "id"
5. Cambia su status a "in_progress" y guarda
6. Anota en progress/current.md: feature, hora de inicio, plan breve
```

## 5. Cierre de sesión (lifecycle)

Antes de terminar:

1. Ejecuta `./init.sh` — todo verde.
2. Si la tarea está acabada: marca `status: "done"` en `feature_list.json`.
3. Mueve el resumen de `progress/current.md` al final de `progress/history.md`.
4. Vacía `progress/current.md` dejando solo la plantilla.
5. No dejes archivos temporales, ni `print()` de debug, ni TODOs sin contexto.

## 6. Si te bloqueas

- Relee la sección relevante de `docs/`.
- Si la herramienta no hace lo que esperas, **no inventes un workaround**:
  documenta el bloqueo en `progress/current.md`, cambia el status a `blocked`
  en `feature_list.json` y para la sesión.
- Si te encuentras código del legacy que querrías "limpiar" pero no es parte
  de tu feature: anótalo en `progress/current.md` como observación, **no lo
  toques**.

## 7. Contexto del proyecto

Este proyecto reconstruye un pipeline llamado **`reddit-saas-radar`**: escanea
subreddits con PRAW buscando señales de dolor de usuarios (workarounds
manuales, herramientas insuficientes), las pasa por un LLM (Claude / Gemini /
Groq) y extrae oportunidades de micro-SaaS. Persiste todo en SQLite y notifica
por Telegram.

La reconstrucción se hace sobre un **arnés de subagentes** (leader /
implementer / reviewer) inspirado en `ejemplo-harness-subagentes`. La BD
heredada (`data/saas.db`, 79 MB) se reutiliza tal cual; los módulos del legacy
se portan **un módulo por feature**, siguiendo el orden de `feature_list.json`.
