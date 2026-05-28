# Lecciones aprendidas y deuda técnica — reddit-saas-radar (legacy)

> Lectura obligatoria antes de arrancar `saas-radar`. Lo que aquí se documenta NO
> es cosmético — son trade-offs reales que costaron iteraciones aprender.

---

## 1. Lo que SÍ reproducir tal cual

### 1.1 La arquitectura de validación en dos pasos (LLM + Python)

El LLM **no es de fiar** para reglas estrictas. La estrategia que funciona:

1. **Pides al LLM que aplique reglas** (RULES 1-7 en el prompt v3 de `synthesis.py`).
2. **Verificas en código** lo que el LLM debería haber hecho (`_validate_synthesis`).

Razón concreta: Sonnet 4.6 viola RULE 1 (≥2 evidencias del mismo workflow) en ~15-20% de las opps generadas, incluso con el prompt explícito. La validación en Python descarta esas opps a `disqualified_ideas` sin coste extra.

**Aplicar en saas-radar**: cada vez que pidas al LLM una restricción dura (cantidad, formato, vocabulario), añade un validador Python detrás. NO te fíes del prompt solo.

### 1.2 Cache defensivo con guarda de "estado mejor que el nuevo"

`_save_extractions_cache` no sobrescribe el cache si lo nuevo es estrictamente peor (0 válidas vs >0 válidas viejas). Persiste el nuevo en `<path>.failed.json` para inspección.

Esto salvó al menos 3 sesiones donde Groq agotó TPD a mitad de run. Sin esta guarda, hubiéramos perdido las extracciones buenas del run anterior.

**Aplicar en saas-radar**: cualquier cache que se sobrescriba en mitad de un pipeline costoso debe llevar guarda defensiva. Mejor preservar lo viejo + flag de inspección, que destruirlo con datos malos nuevos.

### 1.3 Circuit breaker explícito

`CIRCUIT_BREAKER_THRESHOLD=3`: si 3 batches consecutivos fallan (todos `_error`), abortar. No reintentar contra un provider caído.

Sin esto, una caída de 30 minutos del provider gastaba 30+ retries inútiles y costaba la TPD del día.

**Aplicar en saas-radar**: cualquier loop que llame a un servicio externo necesita un contador de fallos consecutivos. Más simple que "max global retries", más seguro.

### 1.4 Pre-cluster por subreddit antes de la síntesis

Sin pre-clustering, el LLM mezclaba evidencia de industrias dispares intentando cumplir RULE 7 (diversidad). Pre-clusterear por subreddit + separadores `### CLUSTER ###` reduce ese fallo a casi 0.

**Aplicar en saas-radar**: cuando se le pide al LLM trabajar sobre N items, **ordénalos** y **agrúpalos visualmente** antes. Los LLMs son muy sensibles al orden del input.

### 1.5 Validación contra el texto REAL, no contra la quote del LLM

`_validate_synthesis` usa `problem_description` del item original — NO `evidence_quotes` del output del LLM. Razón: el LLM falsifica quotes (resúmenes que cree representativos). Validar contra su propia historia es validar nada.

**Aplicar en saas-radar**: si el LLM cita algo, valida la cita contra el texto original. Confiar en sus citas es confiar en sí mismo.

### 1.6 Una capa de configuración mutable + recálculo en runtime

Las phrases de `PAIN_SIGNAL_PHRASES` pueden cambiar entre runs. `data_loader.py` **recalcula** `semantic_score` cada vez (no usa el valor persistido en BD). Esto permite iterar el filtro sin re-scrapear.

**Aplicar en saas-radar**: para cualquier campo derivado de config (no de input externo), recalcular en runtime es más barato que migrar. Persiste por velocidad, no por verdad.

### 1.7 Migraciones de schema idempotentes (PRAGMA + ALTER)

`init_db()` añade columnas con `if "semantic_score" not in cols: ALTER TABLE ...`. Sin Alembic, sin Yoyo, sin nada externo. Funciona porque SQLite tolera ALTER incremental.

**Aplicar en saas-radar**: en proyectos one-developer + SQLite, este patrón es suficiente. NO traer Alembic hasta que haya múltiples developers y múltiples entornos.

### 1.8 `INSERT OR IGNORE` vía tabla staging

`save_to_db` hace `df.to_sql('_staging_X')` + `INSERT OR IGNORE INTO X SELECT * FROM _staging_X` + `DROP _staging_X`. Patrón seguro para idempotencia + bulk inserts en SQLite sin upsert nativo.

**Aplicar en saas-radar**: si el dataset es bulk + idempotente y SQLite es el target, este patrón gana a un loop con `INSERT OR IGNORE` post a post (1 query vs N).

### 1.9 Stopwords RICAS en validación de coherencia léxica

`_COHERENCE_STOP` no es la lista clásica de inglés. Incluye raíces 4-char de **dominio**: `manu`, `trac`, `spre`, `exce`. Esto previene que dos extracciones genéricas pasen el filtro solo por compartir "tracking" / "manually" / "spreadsheet".

**Aplicar en saas-radar**: cualquier filtro léxico que opere sobre el dominio del producto necesita stopwords del dominio, no solo del idioma.

### 1.10 `try/except` aislando fases no críticas

`phase_gtm` en `main.py` está envuelta en try/except. Cualquier fallo del GTM agent imprime `[WARN]` y el pipeline continúa. Razón: el cron del tuner depende solo de que `pipeline.yml` termine en verde. Las opps ya están persistidas; el GTM es "nice to have".

**Aplicar en saas-radar**: cada fase del pipeline que no sea estrictamente necesaria para el siguiente paso debe poder fallar sin abortar.

### 1.11 Helpers `*_quota.py` para inspeccionar antes de gastar

`groq_quota.py` y `gemini_quota.py` hacen una llamada de 1 token para ver headers o el status. Coste despreciable, valor: evitar arrancar un run que se va a caer a mitad.

**Aplicar en saas-radar**: para cualquier servicio con cuota, un quota-check rápido antes de operaciones largas se amortiza el primer día.

### 1.12 Persistencia en rama Git del propio repo

`pipeline.yml` hace checkout dual `main` + `data` y commitea `data/saas.db` + `data/runs/` a la rama `data` tras cada run. Sin S3, sin Postgres, sin storage externo.

Bonus: GitHub no suspende crons si hay commits recientes en el repo → cada run mantiene viva la programación.

**Aplicar en saas-radar**: si los datos son < 100MB y el repo es privado / personal, esta solución es muy barata y elegante. Migrar a S3 cuando los datos crezcan, no antes.

---

## 2. Lo que NO reproducir (o cambiar)

### 2.1 `print` con emojis + separadores `──` para todo el logging

Documentado en `CLAUDE.md` como convención del proyecto. Funciona en consola, **rompe en Windows cp1252** sin `sys.stdout.reconfigure(encoding="utf-8")`, hace imposible filtrar por nivel y enturbia los logs de GitHub Actions.

Migración a `logging` estructurado **aplazada** en [plan/backlog.md](../../plan/backlog.md) con plan L1+L2+L3. No se hizo porque el tuner parsea formato fijo del CLI → cambiar el output requiere snapshot test del tuner report primero.

**Cambiar en saas-radar**: arrancar con `logging` estándar desde el día 1. Variables: `LOG_LEVEL`, `LOG_FORMAT` (`text`/`json`). Un `logging_setup.py` con `setup_logging(level, fmt)`. El user output del CLI separado de los logs (cabeceras de fases → stdout, debug/info → logger).

### 2.2 Dependencia de `requests` sin declarar en `requirements.txt`

`helpers/*_quota.py` importa `requests` pero el `requirements.txt` solo lista `httpx`. Funciona porque otra dep lo arrastra transitivamente, pero es un timebomb.

**Cambiar en saas-radar**: o usar `httpx` en todo (incluidos quota checks), o declarar `requests` explícitamente. Idealmente lo primero.

### 2.3 Convivencia de `_init.sh` no existente + `pyproject.toml` sin `[project]`

El proyecto no tiene `pyproject.toml` con metadata de package (`[project]` con name/version/dependencies). Solo configuración de ruff/pytest. No es instalable como package.

**Cambiar en saas-radar**: `pyproject.toml` completo con `[project]` y dependencias. Hace al proyecto pip-installable y compatible con tooling moderno (`uv`, `rye`, `pdm`).

### 2.4 Imports relativos con `sys.path.append(os.path.abspath(...))`

`ai_analyzer.py`, `helpers/audit_filter.py`, `helpers/groq_quota.py`, `scripts/backfill_canonical.py` todos hacen variantes de:
```python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
```

Antipatrón. Solo funciona porque el repo no es un package.

**Cambiar en saas-radar**: arrancar como package (`src/saas_radar/...`) con `pyproject.toml`. Eliminar TODOS los `sys.path.append`.

### 2.5 Mutación de `config.AI_PROVIDER` en runtime

`agents/gtm_agent.py:_make_call_llm_fn` muta `config.AI_PROVIDER` antes de importar el dispatcher para soportar `--provider claude`. Comentado en código como "feo pero el dispatcher actual lee del módulo, no recibe el provider como argumento. Refactorizar eso queda fuera de B1".

**Cambiar en saas-radar**: el dispatcher debe recibir el provider como argumento, no leerlo de un global mutable. Mutar config es un olor a refactor pendiente.

### 2.6 `dashboard/app.py` con 17 líneas que dicen poco

El scaffold no aporta valor. Muestra `len(df)` y top 10 posts por upvotes. No accede a `opportunities`, no muestra GTM, no tiene filtros, no tiene autenticación.

**Cambiar en saas-radar**: o construir el dashboard de verdad (feature backlog), o NO INCLUIRLO hasta que se vaya a hacer en serio. El scaffold induce a pensar "ya está hecho".

### 2.7 Tabla `opportunities` con 26 columnas

Mezcla output determinista del pipeline (campos generados por LLM) con flags humanos (`reviewed`, `starred`, `discarded`, `user_notes`) y metadata (`canonical_id`). Lifecycle distinto, mismo schema.

Justificación histórica: simplicidad de queries. Ya hay una tabla aparte (`opportunity_gtm`) para los outputs del GTM agent porque ahí la separación era más obvia.

**Cambiar en saas-radar**: dos tablas desde el día 1. `opportunities` (output LLM) + `opportunity_state` (flags humanos, idem 1:1 con FK + UNIQUE). Vista combinada para queries.

### 2.8 Helper `audit_filter.py` que escribe a `data/audit_filter.md`

El output va a `data/`, que está en `.gitignore`. Implica que cada vez que el usuario quiere ver el audit, lo regenera (no se versionará). Para un comando "diagnóstico", está bien — pero para un comando "compáralo entre runs", no sirve.

**Cambiar en saas-radar**: si quieres histórico de auditorías, persistir en BD (tabla `audit_runs`) y/o subir a la rama `data` como `data/runs/<ts>_audit.md`.

### 2.9 `_clean_extractions` con criterios mezclados

La función hace 4 cosas: drop who-vago, drop dolor no-SaaS, infer/keep workaround, fix payment_signal. Cada criterio tiene su lógica + log + estadística. La función entera es 86 líneas.

**Cambiar en saas-radar**: separar en 4 funciones puras encadenadas. Más fáciles de testear, más fáciles de tunear cada criterio independiente.

### 2.10 La regla pedagógica del CLAUDE.md original

El proyecto exige que cada cambio se explique línea a línea para enseñar Python/SQL/regex al usuario. Útil al inicio del proyecto, fricción en una sesión de implementación intensiva donde se escriben 500 líneas.

**Re-evaluar en saas-radar**: mantener la regla "explicación de qué/por qué/impacto" siempre. La explicación línea a línea, solo a petición explícita ("explícame línea a línea") o cuando se introduzca un patrón nuevo.

---

## 3. Intentos abandonados / parcialmente terminados

### 3.1 Dedup v1 con limitación documentada (Jaccard solo)

`analysis/dedup.py` implementa Jaccard sobre `evidence_quotes`. Limitación: si dos opps describen el mismo problema con vocabulario disjunto, NO matchean. Documentado en [plan/gtm.md](../../plan/gtm.md) §"Limite conocido del dedup v1".

Ejemplo real en BD: id=8 ("Client Communication and Project Context Tool") debería pertenecer al cluster {2,4,7,9,10} ("Client Communication Tracker for Agencies"). No lo hace porque sus quotes son disjuntas.

**Decisión documentada**: no es deuda, es trade-off por diseño. Aceptamos falsos negativos (clusters duplicados que el GTM agent procesa por separado) para evitar falsos positivos (colapsar opps reales que parecen iguales).

**Trigger v2 (embeddings)**: 2026-06-11. Si ≥3 clusters claramente duplicados conceptualmente.

**Aplicar en saas-radar**: arrancar con el mismo Jaccard simple. NO empezar con embeddings. Cuando v2 entre, hacerlo en una rama con un set de regresión de las opps actuales.

### 3.2 Tuner en modo dry-run desde A3 (2026-04-24), sin pasar a A4

El tuner imprime / notifica / sube artefacto, pero NO modifica `config.py` ni abre PRs. Fase A4 (modo PR real con `--apply` + libcst + `gh pr create`) **pendiente**. ETA original 2026-05-14, hoy es 2026-05-28 → desfase.

Razón: A3 lleva acumulando reports diarios. Antes de pasar a A4 hay que verificar que >=14 reports consecutivos son coherentes. La verificación nunca se cerró en el código actual.

**Aplicar en saas-radar**: el tuner es una de las features de más alto valor (delegación de tuning manual). Reconstruirlo a fondo, pero con un objetivo claro: "modo PR real desde el día N+14, sin excusas".

### 3.3 GTM agent con 1 fila persistida desde B2 (2026-04-25)

`opportunity_gtm` tiene 1 fila después de un mes operativo. Razón: las opps reales con `priority_score >= 7` que no estaban ya en el cluster duplicado son escasas. La cobertura del agente está bajísima.

**Causa raíz**: el pipeline produce ~1-2 opps/semana con priority >=7, de las cuales muchas son duplicadas. Sin más volumen, no hay GTM volumen.

**Aplicar en saas-radar**: o bajar `--min-priority` por defecto (de 7 a 5), o esperar a que el dedup v2 reduzca duplicados y subir el volumen efectivo. Decidir antes de cerrar B2 equivalente.

### 3.4 Dashboard scaffold sin construir

`dashboard/app.py` lleva ~6 meses con 17 líneas. Trigger documentado: B3 cerrado + ≥10 opps canónicas + decisión de hosting (Streamlit Cloud / Oracle Cloud Free / Hugging Face Spaces).

**Aplicar en saas-radar**: NO incluir scaffold de dashboard hasta que se vaya a construir. Si se incluye, que la feature ya tenga acceptance claro y hosting decidido.

### 3.5 Logging estructurado aplazado

Plan L1+L2+L3 detallado en `plan/backlog.md`. Bloqueante: snapshot test del tuner report. Nunca se ejecutó.

**Aplicar en saas-radar**: hacer L1+L2+L3 desde el día 1, no después. Es más barato que migrar.

### 3.6 Migración a Postgres aplazada a A5

No hay locks reales hoy. La migración existe solo en el roadmap del tuner.

**Aplicar en saas-radar**: arrancar con SQLite. Re-evaluar solo si la concurrencia se vuelve real.

### 3.7 Backfill de 7 opps canónicas (B0 deuda)

Las 7 opps pre-B0 tenían `canonical_id=NULL`. Backfill ejecutado el 2026-04-27 contra la rama `data` con `scripts/backfill_canonical.py`. Resultado: 4 canónicas (`{1}`, `{2,4,7,9,10}`, `{3,5,6}`, `{8}`).

Aprendizaje: una migración de schema (B0 añadió `canonical_id`) requiere un backfill explícito + idempotente para que las filas pre-existentes no rompan downstream (en este caso, el GTM agent solo lee opps con `id==canonical_id`).

**Aplicar en saas-radar**: cualquier columna NOT NULL nueva o columna que invariante un patrón query debe llevar su backfill el mismo día que se mergea la migración. Y idempotente, para poder re-ejecutar sin daño.

---

## 4. Decisiones de scope que NO se hicieron

### 4.1 No se hizo `auth` en ningún punto

El radar es uso personal. Si se quiere multi-usuario, hay que arrancar desde cero con auth → multi-tenant → row-level security. Es un proyecto distinto.

### 4.2 No se hizo i18n

Output en español + inglés (los prompts y categorías en inglés porque Reddit es inglés). Si el target son comunidades hispanas, el proyecto cambia.

### 4.3 No se hizo segmentación por geografía

Los posts no se filtran por país / idioma. Implica que `r/restaurantowners` mezcla US, UK, AU. Para algunos nichos esto no importa (workflows operativos), para otros sí (legal, fiscal).

### 4.4 No se hizo análisis temporal de tendencias

Cada run es snapshot. No hay queries del tipo "qué dolor está creciendo mes a mes". Las 10 `analysis_runs` son insuficientes para tendencia, pero la BD lo permitiría.

### 4.5 No se hizo CRM de oportunidades

`opportunities.starred/reviewed/user_notes` existen pero no hay UI para usarlos. Implica que el usuario nunca marca opps como "perseguir activamente" → la información humana se pierde fuera de Reddit Telegram.

### 4.6 No se hizo cost tracking de LLM

No hay tabla / log de "este run costó $X.YY". Para iterar prompts sería útil saber si una optimización ahorra coste neto.

---

## 5. Aprendizajes meta-proyecto

### 5.1 Iterar prompts con `--skip-scrape --use-cached-extractions`

El bucle de iteración de prompts (sintetizador) está perfectamente diseñado: 1 llamada de síntesis es lo único que se gasta. La fase de extracción se cachea. Es lo que permitió hacer ~20 iteraciones de RULES sin quemar TPD.

Sin este bucle, iterar el prompt v3 hubiera sido prohibitivo.

### 5.2 La rama `data` como almacenamiento es excelente para este volumen

19k posts + 12k comments en 79 MB. Git compara binarios de SQLite mal (no diff por filas), pero como el push es 1/día y el tamaño no escala mucho (incremental añade ~100-500 posts/día), el repo crece ~5-10 MB/mes — totalmente manejable.

### 5.3 Los `meta_recommendations` con `recurrence` son la mejor parte del tuner

La idea de incrementar `recurrence` cada vez que la misma recomendación aparece en runs distintos es lo que hace al tuner robusto: una recomendación de un solo run es ruido, una recomendación de 3 runs es señal. **Replicar**.

### 5.4 El dashboard Streamlit es un agujero

Lleva 6 meses sin construir y bloquea B4 (regeneración GTM al marcar starred=1). Cada vez que aparece, hay que decidir si construir o no, y la respuesta siempre es "todavía no". **Decisión binaria temprana**: o se hace, o se borra del PLAN.

### 5.5 Los 3 workflows (pipeline + tuner + reminders) son sostenibles

Configuración total ~150 líneas de YAML. Cero falsos positivos del cron en 6 semanas. Las "reminders" como cron en GitHub Actions son una idea original — bajan la carga mental sin necesitar app externa.

### 5.6 La documentación viva (PLAN.md → plan/*.md → README.md) se desincroniza rápido

Tres fuentes de verdad: PLAN.md (índice), plan/<tema>.md (detalle), README.md (operación). Cada vez que se cierra una fase, las tres se tocan, y a veces una se queda atrás.

**Aplicar en saas-radar**: una sola fuente para cada concepto. `README.md` operativo solo. `feature_list.json` para lo que viene. `progress/history.md` para lo que vino. Sin duplicación.

---

## 6. Riesgos vivos al cierre de este snapshot

| Riesgo | Estado | Mitigación si pasa |
|---|---|---|
| Groq cierra free tier | Activo | Migrar a Gemini o Claude. El dispatcher ya lo soporta. |
| Reddit cambia ToS de PRAW | Activo | Replicar con scraping HTTP directo (más frágil). |
| GitHub limita Actions free para repos privados | Latente | Mover el cron a un VPS gratuito (Oracle Cloud Free). |
| La BD pasa de ~100MB y la rama `data` crece sin control | Latente | Implementar política de retención (drop posts >18 meses). |
| Telegram cambia política de bots | Bajo | Cambiar a email vía SES. |
| El tuner nunca pasa a A4 (modo PR real) | Activo | Reabrir el plan, asumir que dry-run forever no aporta. |
| El dashboard nunca se construye → B4 nunca arranca | Activo | Decidir antes de iniciar el dashboard. |
| El dedup v1 acumula falsos negativos | Activo | El cron de `reminders.yml` dispara la revisión el 2026-06-11. |
