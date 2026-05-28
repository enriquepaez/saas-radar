# Arquitectura y flujos — reddit-saas-radar (legacy)

> Documento de referencia para entender el "por qué" detrás de cada decisión técnica
> antes de portar el pipeline a `saas-radar`. Contraparte conceptual del [inventory.md](inventory.md).

---

## 1. Visión de conjunto

```
                    ┌──────────────────┐
                    │   Cron diario    │   GitHub Actions
                    │   08:00 UTC      │   (pipeline.yml)
                    └────────┬─────────┘
                             │
                             ▼
        ┌───────────────────────────────────────────────┐
        │                  main.py                      │
        │   Detecta modo (incremental 24h vs full 365d) │
        └───────────────────────────────────────────────┘
              │              │              │            │
       ┌──────┘       ┌──────┘       ┌──────┘            │
       ▼              ▼              ▼                   ▼
   Fase 1         Fase 2         Fase 3              Fase 4 + 5
   subreddits     pain_search    comentarios          IA + GTM
   (PRAW)         (PRAW)         (ThreadPool x8)      (LLM)
       │              │              │                   │
       └──────────────┴──────────────┘                   │
                      ▼                                  │
              ┌──────────────┐                           │
              │  enrich_*    │  clean_text +             │
              │  → save_to_db│  classify_post +          │
              └──────┬───────┘  _semantic_score          │
                     ▼                                   │
              ┌────────────────────────────────┐         │
              │      data/saas.db (SQLite)     │◄────────┤
              │  reddit_posts + reddit_comments│         │
              └──────────────┬─────────────────┘         │
                             │                           │
                             ▼                           ▼
                ┌──────────────────────┐    ┌────────────────────┐
                │  load_pain_posts     │    │   run_ai_analysis  │
                │  (data_loader)       │    │   (ai_analyzer)    │
                │  filtro + ranking    │    │   extract → synth  │
                └──────────────────────┘    └──────────┬─────────┘
                                                       │
                                                       ▼
                                    ┌──────────────────────────────┐
                                    │  persist_run_to_db           │
                                    │  + find_canonical (B0 dedup) │
                                    │  → opportunities table       │
                                    └──────────────┬───────────────┘
                                                   │
                       ┌───────────────────────────┼───────────────────────────┐
                       ▼                           ▼                           ▼
                ┌──────────────┐           ┌──────────────┐           ┌──────────────┐
                │ Telegram     │           │ meta_analysis│           │ GTM agent    │
                │ opp + run    │           │ JSON + BD    │           │ canónicas    │
                └──────────────┘           └──────┬───────┘           └──────────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────────┐
                                       │ tuner.yml (workflow  │
                                       │ run, dry-run hoy)    │
                                       └──────────────────────┘
```

---

## 2. Fases del pipeline en detalle

### 2.1 Fase 1 — Scraping de subreddits

`phase_subreddits(incremental)` itera `SUBREDDITS` (36 subreddits agrupados en tiers A/B/C/D + descubiertos).

**Modo full** (primer run o `--full-scan`):
```python
feeds = [
    subreddit.hot(limit=100),
    subreddit.new(limit=50),
    subreddit.top("month", limit=50),
    subreddit.top("year", limit=50),
]
```

**Modo incremental** (run posterior a uno exitoso):
```python
feeds = [
    subreddit.new(limit=100),
    subreddit.hot(limit=100),
    subreddit.top("day", limit=50),
]
```

Dedup intra-fase por `id`. Después: `enrich_posts(df)` → `save_to_db(combined, "reddit_posts")` con `INSERT OR IGNORE`.

**Por qué este patrón**:
- `hot` + `new` + `top-month/year` en full → máximo recall histórico.
- `new` primero en incremental → captura posts nuevos antes de que entren en hot/top.
- Sleep `time.sleep(1)` entre subreddits → respeto soft de rate limits PRAW.

### 2.2 Fase 2 — Pain search

`search_pain_posts(query)` ejecuta cada query de `PAIN_SEARCH_QUERIES` (~90 queries) contra el multireddit `sub1+sub2+...+subN.search(query)`.

```python
search_kwargs = {"sort": "relevance", "limit": 50}
if incremental:
    search_kwargs["time_filter"] = "day"
```

Por qué **multireddit en vez de N llamadas separadas**: un solo request con `r/a+b+c/search?q=...` es ~N veces más rápido y consume cuota una vez. Limitación: el campo `subreddit` en el post devuelto puede no coincidir con la subreddit-string del search.

### 2.3 Fase 3 — Comentarios

`phase_comments(posts_df)` selecciona posts con `num_comments >= HIGH_ENGAGEMENT_THRESHOLD` (100). Si hay más de `COMMENT_TARGET_POSTS` (200), prioriza por `semantic_score + num_comments`.

```python
with ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS) as executor:
    futures = {executor.submit(fetch_safe, row["id"]): row["id"] for _, row in high.iterrows()}
    for future in as_completed(futures):
        all_comments.extend(future.result())
```

`fetch_top_comments(post_id)` usa `replace_more(limit=0)` (no expande "load more comments") y filtra `len(body) >= COMMENT_MIN_LENGTH` (50 chars).

Concurrencia 8 elegida empíricamente: PRAW + Reddit aceptan ~8 conexiones simultáneas sin disparar 429.

### 2.4 Fase 4 — Análisis IA

Dos sub-fases. La compleja es la **4a (extracción)**: hay dos modos.

**Modo deep** (cuando `len(posts) <= DEEP_EXTRACTION_THRESHOLD=30`):
- 1 post / llamada.
- Texto completo (no truncado a 500 chars).
- Comentarios de la BD adjuntados al prompt (hasta 15 comentarios, 400 chars cada uno).
- Prompt enriquecido pide `comment_signals`, `estimated_frequency`, `tam_clues`.
- `max_tokens=800` por respuesta.

**Modo batch** (cuando `len(posts) > 30`):
- 5 posts por llamada (`EXTRACTION_BATCH_SIZE=5`).
- Texto truncado a `TEXT_SNIPPET_LEN=500` chars.
- Sin comentarios (los comentarios con señal entran como "posts virtuales" vía `load_pain_comments_as_posts`).
- `max_tokens=220 * len(rows)`.

Por qué **dos modos**:
- Deep maximiza señal cuando hay pocos posts (vale la pena gastar tokens).
- Batch optimiza coste/tiempo cuando hay muchos posts y los comentarios ya entraron por la vía de "posts virtuales".

**Circuit breaker**: si `CIRCUIT_BREAKER_THRESHOLD=3` batches consecutivos fallan (todos con `_error`), aborta. Evita gastar tokens reintentando contra un provider caído.

**Cache defensivo** (`_save_extractions_cache`):
- Guarda tras cada batch.
- Si lo nuevo tiene `valid_new=0` y lo viejo tenía `valid_old>0`, NO sobrescribe → guarda el estado nuevo en `<path>.failed.json` para inspección.
- Esto salva sesiones donde TPD se agota a mitad de run.

**Retry individual**: posts con `_error` se reintentan uno a uno tras el batch loop. El retry carga el `text` original desde `reddit_posts` por id.

**Limpieza `_clean_extractions`** antes de síntesis:
1. Descarta `who_has_it` vago (`"unknown"`, `"the user"`, `"people"`…).
2. Descarta dolor no-SaaS (físico, salud mental, soledad…) salvo que mencione tool/spreadsheet.
3. Workaround: si está vacío, intenta inferirlo del texto via `_WORKAROUND_KEYWORDS`. Si no, marca `_weak_workaround=True` pero **no descarta** (un dolor cuantificable sin workaround explícito es válido).
4. Corrige `payment_signal=true && payment_quote=""` → `payment_signal=false`.

**Pre-validación antes de síntesis**: aborta si `len(valid) < 2` — RULE 1 exige ≥2 evidencias, sin eso no hay síntesis posible.

#### Fase 4b — Síntesis

`build_synthesis_prompt(extractions)`:
1. **Pre-clustering por subreddit**: agrupa por `_subreddit`, ordena subreddits con más extracciones primero. Numera global [1..N] para que `evidence_items` siga apuntando a índices únicos.
2. **Separadores visibles** `### CLUSTER: r/<sub> (N items) ###` para guiar al LLM a buscar coherencia intra-cluster.
3. **Prompt v3 con RULES 1-7** (ver tabla en [inventory.md §8](inventory.md)) + schema JSON estricto.

Una sola llamada con `max_tokens=4000`, `max_retries=12` (la síntesis es la llamada cara — vale la pena esperar 12 min de rate limits).

**Validación `_validate_synthesis`** (post-LLM):
- **Check cantidad**: `len(ev_items) >= 2 AND len(ev_quotes) >= 2`.
- **Check coherencia**: extrae "raíces" de 4 chars de los `problem_description` reales de los `evidence_items` referenciados (NO de las quotes, que el LLM puede falsificar). Exige al menos 2 raíces compartidas en >50% de los items. Stopwords ricas de dominio (`_COHERENCE_STOP`) filtran ruido genérico ("track", "manu", "spreadsheet" como raíces no cuentan).

Las opps que no pasan se mueven a `disqualified_ideas`. `top_3_recommended` se reconstruye con sólo IDs supervivientes.

### 2.5 Fase 5 — GTM agent

`phase_gtm(min_priority=7)` invoca `agents.gtm_agent.run_all_pending`. **Todo dentro de `try/except`** — cualquier fallo imprime `[WARN]` pero NO aborta. Razón: el cron del tuner depende solo de que `pipeline.yml` termine en verde.

Por opp:
1. Carga `evidence_quotes` (cap 5) en el prompt para anclar el GTM en dolor real.
2. LLM genera Tarea A (viabilidad 1-10 x 3) + B (pitch + 3 tiers + 3 canales sin Reddit + cold script + organic post) + C (plan 7d + pivot signals + KPIs).
3. **Gate `viability_total < 20`**: persiste solo scores con `gtm_status='skipped_low_viability'` (ahorra 70-80% tokens).
4. JSON inválido / LLM None → `gtm_status='failed'` (NULL scores). El batch continúa.

**Idempotencia**: una opp con fila existente se salta. `--force` hace `DELETE + INSERT`.

---

## 3. Decisiones técnicas clave (con su porqué)

### 3.1 ¿Por qué Claude por defecto y no Groq?

- **Claude Haiku 4.5** para extracción: muy barato (~$0.25/M input, $1.25/M output), latencia baja, instruction-following fuerte para JSON estricto.
- **Claude Sonnet 4.6** para síntesis: la síntesis es la decisión cara — RULES 1-7 + coherencia léxica + descarte de mercados saturados requieren razonamiento. Sonnet 4.6 acierta en ~90% de los casos, Haiku se cae al 70%.
- **Groq** se mantiene como fallback gratuito con `llama-3.3-70b-versatile`. Limitación: cuota TPM ~6k → pausa **30s entre batches** vs 1s para Claude/Gemini.
- **Gemini 2.0 Flash** como alternativa gratis con cuota muy generosa (1500 req/día, 1M TPM). Usado cuando el usuario quiere "free + bueno".

Dispatcher en `analysis/llm_clients.py: call_llm(phase=...)`: elige modelo según `AI_PROVIDER` y la fase (`phase='extraction'` vs `'synthesis'`).

### 3.2 ¿Por qué SQLite y no Postgres?

- Volumen actual (19k posts + 12k comments + 10 opps) cabe en 79 MB → SQLite va sobrado.
- GitHub Actions monta SQLite trivial: `data/saas.db` viaja como un blob en la rama `data`.
- Concurrencia: el pipeline + tuner + GTM corren secuenciales (workflow_run dependencias), no hay locks reales.
- **Migración a Postgres aplazada** a fase A5 del tuner. Trigger: si SQLite empieza a dar locks.

### 3.3 ¿Por qué Jaccard sobre tokens de `evidence_quotes` para dedup?

- **No usar embeddings** en v1: overkill para <50 opps. Inversión de complejidad sin payback.
- **`evidence_quotes` como ancla** porque es el único campo comparable entre runs (los `evidence_items` son índices relativos a cada run).
- **`product_name` como tie-breaker** (no como condición de match): dos opps con mismo nombre pero quotes disjuntas NO matchean (test verificado).
- Threshold 0.3 calibrado contra las 7 opps reales de la BD para colapsar a 3 canónicas sin falsos positivos.
- **Limitación conocida y aceptada**: falsos negativos por evidencia disjunta (id=8 vs cluster {2,4,7,9,10}). Documentado en [plan/gtm.md](../../plan/gtm.md). Revisión 2026-06-11.

### 3.4 ¿Por qué pre-clustering por subreddit antes de la síntesis?

El LLM detecta clusters mejor cuando los posts de la misma industria llegan consecutivos. Sin el pre-clustering, Sonnet 4.6 mezcla evidencia de industrias dispares cuando intenta cumplir RULE 7 (diversidad). Con el pre-clustering, RULES 1 y 7 dejan de competir.

### 3.5 ¿Por qué la validación de coherencia post-síntesis usa los `problem_description` REALES, no las `evidence_quotes` del LLM?

El LLM puede generar `evidence_quotes` que NO son citas literales — son resúmenes que el modelo cree representativos. Si validamos coherencia contra esas quotes "inventadas", validamos su propia historia. Validar contra `problem_description` de los items referenciados es la auditoría real.

### 3.6 ¿Por qué stopwords de dominio (`manu`, `trac`, `spre`, `exce`, `shee`)?

En el espacio SaaS-pain, palabras como "manually", "tracking", "spreadsheet", "Excel" aparecen en CASI CUALQUIER queja sobre dolor operacional. Si las dejásemos contar como evidencia de coherencia, dos extracciones sobre **problemas completamente distintos** (un bookkeeper rastreando facturas vs un freelancer rastreando horas) pasarían el filtro solo por compartir "trac". Las stopwords de raíz (4 chars) filtran familias enteras: `trac` bloquea `track`, `tracking`, `tracked`, `tracker`.

### 3.7 ¿Por qué Telegram y no email?

- Latencia: usuario quiere notificación push.
- Setup trivial: `@BotFather` → token → `getUpdates` → chat_id.
- Sin dependencia de SMTP / SES / SendGrid.
- Limitación: 4096 chars por mensaje → `send_tuner_report` trunca a 3900.

### 3.8 ¿Por qué GitHub Actions y no un VPS?

- Coste: 0€ en repositorios públicos.
- Mantenimiento: 0 (no hay container que cuidar).
- Cron mensual fiable.
- Limitación clave: NO sirve servicios 24/7 → el dashboard Streamlit no puede vivir aquí. Es la razón por la que el dashboard sigue siendo scaffold.
- Persistencia: rama `data` del propio repo (push tras cada run). Truco: GitHub no suspende crons si el repo tiene commits recientes, y cada run commitea automáticamente.

### 3.9 ¿Por qué dos workflows (pipeline + tuner) en vez de uno?

- **Aislamiento de fallos**: el tuner no debe abortar el pipeline. Trigger `workflow_run` + `if conclusion=success` deja al tuner solo cuando todo lo previo fue bien.
- **Concurrencia separada**: el pipeline tarda ~10-15 min, el tuner ~30s. Mezclarlos crearía colas innecesarias.
- **PR separados a futuro**: cuando A4 entre en producción, el tuner abrirá su propio PR sin contaminar los commits del pipeline.

### 3.10 ¿Por qué el GTM agent está envuelto en `try/except` en `phase_gtm`?

El pipeline tiene 5 fases: si la 5 cae, las 1-4 ya escribieron a `data/saas.db` y el run debería contar como exitoso. Aborto en cascada significaría perder horas de scraping por un fallo de LLM. Trade-off explícito: el GTM agent es "nice to have", no crítico.

---

## 4. Flujo de datos detallado (entrada → salida)

```
[Reddit API]
    │ PRAW (singleton _reddit)
    ▼
[fetch_posts / search_pain_posts]
    │ DataFrame con cols: id, source, subreddit, title, text, score,
    │  upvote_ratio, num_comments, created_utc, url, flair, search_query
    ▼
[enrich_posts]
    │ + clean_text  (NLTK stopwords + lower + regex http/non-alpha)
    │ + classify_post  (keywords → 6 categorías)
    │ + _semantic_score  (regex pre-compiladas, -99 showcase, -50 off-topic, +1..3 phrases)
    ▼
[save_to_db('reddit_posts')]
    │ subreddit → lower
    │ INSERT OR IGNORE vía _staging_reddit_posts
    ▼
[reddit_posts]  ◄── fase 3 inserta también reddit_comments
    │
    │ (fase 4 arranca con --skip-scrape o tras fases 1-3)
    ▼
[load_pain_posts]
    │ filtros: SUBREDDITS ∩ PAIN_CATEGORIES ∩ score >= min_score
    │       ∩ len(text) > 100 ∩ created_utc > now - post_age_days*86400
    │ + recalcular _semantic_score (config mutable, valor BD puede estar stale)
    │ + filtro semantic_score >= MIN_SEMANTIC_SCORE (1.5)
    │ + merge load_pain_comments_as_posts (pseudo-título primera frase)
    │ + ranking:
    │     rank_score = 0.10*score_norm + 0.15*num_comments_norm + 0.75*sem_norm
    │ + cap por subreddit (HIGH_SIGNAL: 10, default: 4)
    │ + head(top_n)
    ▼
[posts ordenados]
    │
    │ if len <= 30: extract_problem_deep (1 post/llamada, texto completo, comments)
    │ else:         extract_problems_batch (5 posts/llamada, snippet 500 chars)
    │
    ▼
[extracciones brutas]
    │ retry individual de posts con _error
    │ _clean_extractions:
    │   - drop who_vago
    │   - drop dolor no-SaaS
    │   - infer workaround o marcar _weak_workaround
    │   - corregir payment_signal sin quote
    │
    ▼
[extracciones limpias] (>=2 para continuar)
    │
    ▼
[build_synthesis_prompt]
    │ pre-cluster por subreddit
    │ separadores ### CLUSTER ###
    │ RULES 1-7
    │ schema JSON estricto
    │
    ▼
[LLM síntesis] (max_tokens=4000, max_retries=12)
    │
    ▼
[_validate_synthesis]
    │ check cantidad >=2
    │ check coherencia léxica sobre problem_description
    │ dropped → disqualified_ideas
    │ top_3 reconstruido con ids supervivientes
    │
    ▼
[print_results] → consola
[save_results] → data/ai_analysis.json + data/runs/<ts>.json
[persist_run_to_db] → analysis_runs (run_id) + opportunities (con canonical_id)
    │
    │ find_canonical sobre opps existentes:
    │   - evidence_overlap (Jaccard >= 0.3) — ancla
    │   - name_similarity — tie-breaker
    │   - sort por (overlap desc, name desc, id asc)
    │
    ▼
[opportunities canónicas + duplicadas]
    │
    ├─ send_opportunity_alert (priority_score >= 8) → Telegram
    ├─ send_run_summary → Telegram
    │
    ├─ generate_meta_analysis → JSON + meta_recommendations en BD (incrementa recurrence)
    │
    └─ phase_gtm:
         load_pending_opportunities (id==canonical_id, not discarded, priority>=7, no GTM)
            │
            ▼
         build_gtm_prompt (incluye hasta 5 evidence_quotes)
            │
            ▼
         LLM (Tarea A + B + C)
            │
            ▼
         validate_payload (4 ints viabilidad presentes, viability_total in [3,30])
            │
            ▼
         gate viability_total < 20 → skipped_low_viability (drop B+C)
            │
            ▼
         persist_gtm → opportunity_gtm
```

---

## 5. Diagrama de tablas y relaciones

```
                    ┌──────────────────────┐
                    │   analysis_runs      │
                    │  PK: id (AUTOINC)    │
                    └──────────┬───────────┘
                               │ 1
                               │
                               │ N
                    ┌──────────▼───────────┐         ┌──────────────────┐
                    │   opportunities      │ N    1  │  opportunity_gtm │
                    │   PK: id             ├─────────┤ FK opportunity_id│
                    │   FK run_id          │         │  UNIQUE          │
                    │   FK canonical_id    │         │  ON DELETE CASC. │
                    │       (self-ref)     │         └──────────────────┘
                    └──────────┬───────────┘
                               │ N
                               │
                               │ 1
                    ┌──────────▼───────────┐
                    │ meta_recommendations │
                    │ FK run_id            │
                    └──────────────────────┘


    ┌──────────────────┐         ┌──────────────────┐
    │   reddit_posts   │ 1     N │ reddit_comments  │
    │   PK: id (text)  ├─────────┤  PK: comment_id  │
    │                  │         │  FK post_id      │
    └──────────────────┘         └──────────────────┘
        (sin FK lógica con opportunities — los evidence_items
         son índices relativos a cada run, no IDs de posts)
```

**Observación importante**: la relación entre `opportunities.evidence_items` y `reddit_posts.id` NO es una FK SQL. `evidence_items` guarda índices 1-based del orden en que las extracciones llegaron a la síntesis. La trazabilidad post→opp se mantiene vía `data/runs/<ts>.json` (que sí lleva `_post_id` en cada extracción).

---

## 6. Modos de ejecución (cómo lo usa el usuario)

| Comando | Cuándo |
|---|---|
| `python main.py` | Run normal. Detecta modo automáticamente. |
| `python main.py --full-scan` | Tras añadir subreddits nuevos, o rellenar huecos históricos. |
| `python main.py --skip-scrape` | Iterar prompts del LLM sin re-scrapear (típico tras editar `synthesis.py`). |
| `python main.py --skip-ai` | Solo scrape, sin gastar tokens. Útil para acumular volumen antes de iterar prompts. |
| `python main.py --skip-scrape --use-cached-extractions` | Re-síntesis con extracciones cacheadas. Tras error TPD en síntesis. |
| `python main.py --top-posts 20 --min-score 5` | Debug rápido / iteración de filtros. |
| `python main.py --skip-gtm` | Pipeline sin gastar tokens en GTM (también útil para A/B testing del GTM agent). |
| `python -m agents.tuner --lookback 7 --show-diff` | Diagnóstico manual del tuner (dry-run). |
| `python -m agents.gtm_agent --opp-id 1 --force` | Regenerar GTM de una opp concreta. |
| `python -m scripts.backfill_canonical --dry-run` | Verificar qué pasaría con un backfill de canonical_id. |
| `python helpers/audit_filter.py` | Auditoría offline del filtro semántico (no consume API). |
| `python helpers/groq_quota.py` | Check de cuota antes de un run grande. |
| `streamlit run dashboard/app.py` | Solo stats de BD (scaffold). |
| `python -m pytest tests/` | Suite completa. |
| `python -m ruff check .` | Linting. |

---

## 7. Lo que el sistema NO hace (límites explícitos)

- **No persiste el texto histórico de `reddit_posts.title/text`** cifrado o anonimizado: queda como descargó PRAW. Implica que si Reddit borra el post, la BD conserva el contenido.
- **No deduplica entre runs a nivel de post**: si un mismo post entra por `subreddit_feed` y luego por `pain_search` con `search_query` distinta, queda con un único id (UPSERT por PK) pero pierde la traza de qué query lo trajo.
- **No tiene política de retención**: la BD crece indefinidamente. Vivimos con ello hasta los ~100MB.
- **No tiene autenticación**: el dashboard es público si lo expones.
- **No tiene métricas de drift** del filtro semántico: si las phrases cambian, el `semantic_score` cambia, pero no hay alerta automática de "este sub baja 30% de hit rate respecto a 2 semanas".
- **No respeta robots.txt fuera de Reddit**: nunca scrappea otra cosa, pero PRAW es la única fuente.
- **No tiene `__init__.py` con re-exports en `analysis/`**: cada submódulo se importa con su path completo. Sin shortcut `from analysis import foo`.

---

## 8. Recetario de operación

### 8.1 Run típico (incremental)

```bash
# 1. Verificar cuota antes de gastar (opcional)
python helpers/groq_quota.py  # solo si AI_PROVIDER=groq

# 2. Lanzar
python main.py

# 3. Inspeccionar
sqlite3 data/saas.db "SELECT id, product_name, priority_score FROM opportunities WHERE run_id = (SELECT MAX(id) FROM analysis_runs)"
```

### 8.2 Re-síntesis tras editar prompts

```bash
# Reutiliza extracciones, gasta solo en síntesis
python main.py --skip-scrape --use-cached-extractions
```

### 8.3 Onboarding de un subreddit nuevo

1. Añadir a `SUBREDDITS` en `config.py`.
2. `python main.py --full-scan` (fuerza 365 días).
3. Revisar meta-análisis del run: hit rate del nuevo sub.
4. Si hit rate >= 75% y >= 1 payment signal → considerar añadir a `HIGH_SIGNAL_SUBREDDITS` (regla 1 del tuner).

### 8.4 Debugging de "el LLM no devuelve oportunidades"

1. Verificar `len(valid)` en el output de `_clean_extractions` (logs de consola).
2. Si `< 2`: pre-validación aborta. Subir `--top-posts`, bajar `--min-score`.
3. Si síntesis devuelve `{}`: rate limit en la llamada síntesis. Reintentar con `--use-cached-extractions`.
4. Si síntesis devuelve opps pero `_validate_synthesis` las descarta: ver el log `[coherencia] rechazada '<name>'` con las raíces. Probable LLM forzando un cluster falso para hit minimum 2 items.

### 8.5 Inspeccionar BD remota (rama `data`)

```bash
git fetch origin data
git checkout data -- data/saas.db
sqlite3 data/saas.db "SELECT COUNT(*) FROM reddit_posts"
```
