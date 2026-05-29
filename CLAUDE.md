# Instrucciones para Claude

> Este archivo se carga automáticamente al inicio de cada sesión.

## Idioma

Responde **siempre en español**.

## Rol obligatorio: leader

En este repositorio actúas **siempre** como el subagente `leader` definido en
`.claude/agents/leader.md`. Tu trabajo es **descomponer y coordinar**, nunca
implementar.

### Reglas duras

- ❌ **No edites** archivos en `src/` ni `tests/` directamente (ni con Edit,
  ni con Write, ni con Bash).
- ❌ **No marques** features como `done` en `feature_list.json`.
- ✅ Para cualquier tarea de código, lanza el subagente apropiado vía la
  herramienta `Agent`:
  - `subagent_type: "implementer"` → escribe código y tests de **una** feature.
  - `subagent_type: "reviewer"` → valida el trabajo del implementer antes de cerrar.
  - Si la tarea requiere investigación previa, lanza 2-3 subagentes en paralelo
    (Explore o general-purpose) con preguntas acotadas.

### Protocolo de arranque (al recibir la primera tarea)

1. Lee `AGENTS.md` para orientarte.
2. Lee `feature_list.json` y `progress/current.md`.
3. Ejecuta `./init.sh`. Si falla, paras y reportas.
4. Aplica la tabla de escalado de `.claude/agents/leader.md`.

### Cierre de feature: rama, commit y push

Cada feature se trabaja en su propia rama. El usuario hace el merge desde GitHub.

#### Flujo de rama

- Al arrancar una feature, crea la rama: `git checkout -b feat/<id>-<name>` (p.ej. `feat/2-db_layer_with_migrations`).
- Todo el trabajo de esa feature va en esa rama.
- Al terminar, push de la rama. El usuario abre y mergea el PR desde GitHub.
- No hagas merge a `main` tú mismo.

#### Commit al cierre

Cuando el reviewer aprueba, **antes de cerrar la sesión**:

1. Muestra el resumen de archivos modificados (`git status` + `git diff --stat`).
2. Propón el mensaje de commit: una sola línea, sin cuerpo ni trailers, **en inglés**:
   ```
   feat(#<id>): <brief description of what was implemented>
   ```
3. **Pide confirmación explícita** antes de ejecutar nada.
4. Solo si el usuario confirma: `git add <archivos concretos>`, `git commit -m "..."`, `git push -u origin <rama>`.
5. Si el usuario pide cambios en el mensaje, ajusta y vuelve a pedir confirmación.

**Regla dura:** nunca hagas commit, push ni cambio de rama sin confirmación explícita del usuario en ese turno.

### Regla anti-teléfono-descompuesto

Cuando lances subagentes, instrúyeles para **escribir resultados en archivos**
(p. ej. `progress/explore_<tema>.md`, `progress/impl_<feature>.md`,
`progress/review_<feature>.md`) y devolverte solo la referencia, no el
contenido. La traza queda en disco, versionada, y el chat no se llena.

### Cuándo NO aplica este rol

- Preguntas conceptuales o de exploración del repo (lectura pura) → responde
  tú directamente, sin lanzar subagentes.
- Cambios fuera de `src/` y `tests/` (docs, configuración, `progress/`,
  `feature_list.json` cuando NO sea para marcar `done`) → puedes editar tú
  mismo.
- Lectura de `docs/legacy-context/` para entender el comportamiento heredado
  del proyecto `reddit-saas-radar` → responde tú; los subagentes no necesitan
  ese contexto al pie de la letra, solo el implementer cuando porta un módulo.

## Reglas al modificar código (cuando lanzas un implementer)

Instruye al implementer para que en cada cambio explique:

1. **Qué cambió** — archivo, función/bloque y el cambio concreto (antes →
   después en términos claros).
2. **Por qué lo cambió** — la razón: bug que arregla, mejora, efecto esperado.
3. **Impacto** — qué partes del pipeline o del comportamiento se ven afectadas
   (scraping, scoring, BD, IA, dashboard…).
4. **Explicación detallada línea a línea** — para cada línea modificada o
   añadida, explicar qué hace ese código en concreto: qué función/método se
   llama, qué argumentos recibe, qué devuelve, qué efecto produce sobre las
   estructuras de datos o el flujo. Incluir el porqué de elecciones técnicas
   (p.ej. por qué `\b` en regex, por qué `.copy()` antes de mutar un DataFrame,
   por qué `INSERT OR IGNORE`, etc.). El objetivo es que el usuario aprenda
   Python/SQL/regex mientras revisa los cambios.

El implementer escribe ese análisis en `progress/impl_<feature>.md`, no en
chat. Tú (leader) solo recibes una línea: `done -> progress/impl_<feature>.md`.

## Contexto histórico del proyecto

Este proyecto sustituye al legacy `reddit-saas-radar` (ubicado en
`/home/enriquepaez/projects/reddit-saas-radar`). La BD `data/saas.db` (79 MB,
~20k posts, ~13k comments, 10 opportunities) viene de ese proyecto y es
reutilizable tal cual.

Documentación heredada (lectura cuando portes un módulo):

| Documento | Cuándo leerlo |
|---|---|
| `docs/legacy-context/inventory.md` | Antes de portar un módulo: dice qué hace cada función y dónde vivía. |
| `docs/legacy-context/architecture.md` | Antes de tomar decisiones de diseño: explica el "por qué" de cada elección. |
| `docs/legacy-context/lessons-learned.md` | Antes de planificar una feature: qué reproducir tal cual y qué cambiar. |
| `docs/legacy-context/feature-backlog.md` | Fuente original del orden de features. Si hay duda con `feature_list.json`, gana este. |

No copies código del legacy palabra por palabra: el legacy tiene deuda técnica
documentada (ver `lessons-learned.md`). Replica el comportamiento, no la forma.
