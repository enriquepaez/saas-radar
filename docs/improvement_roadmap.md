# Roadmap de mejora post-MVP

> Objetivo: convertir saas-radar en una herramienta que genere leads accionables
> de micro-SaaS — suficientemente específicos, validados y con plan de acción
> para decidir en 5 minutos si vale la pena perseguir cada oportunidad.

> ⚠️ **Nota de numeración (2026-07-04):** las "F26-F30" de este documento se
> escribieron antes del milestone M6, que ocupó los ids 26-29 de
> `feature_list.json` con fixes operativos (compresión BD, storage en
> Releases, fix discarded=NULL, cableado del meta-análisis). Al registrar las
> fases de este roadmap como features, usar **ids #30 en adelante**.

---

## Estado actual (actualizado 2026-07-04, post-M6)

| Métrica | Valor | Observación |
|---|---|---|
| Posts scrapeados | 30.216 | BD en release `db-latest` (GitHub Releases) |
| Oportunidades totales | 2 | Groq-only desde 19-jun; 0-1 opps/run |
| Oportunidades activas (canonical) | 2 | Visibles tras backfill #27 (1er run post-merge) |
| Últimos runs exitosos | 4-jul | Persistencia en Releases operativa (#26/#29) |
| meta_recommendations | 0 → poblándose | Fase 4 cableada en #28; primer dato real ≥5-jul |
| Alerta de fallo | Activa | Telegram en `if: failure()` en ambos workflows |

---

## Fase 0 — Datos frescos (bloqueante para todo lo demás)

El análisis de señal previo se basa en datos del 30-may con runs fallados.
No sirve de base para ajustar umbrales ni evaluar prompts.

**Acciones**:
1. ✅ Pipeline corre y persiste correctamente (M6: Releases + alertas).
2. Esperar 5-7 runs (≈1 semana de cron diario, desde el 5-jul).
3. Sincronizar BD local:
   `gh release download db-latest -p saas.db.zst -O data/saas.db.zst --clobber && zstd -d -f data/saas.db.zst -o data/saas.db`
4. Repetir el análisis de señal de `progress/explore_signal_analysis.md`
   con los datos frescos como punto de partida real para las fases siguientes.
   Incluir por primera vez el contenido real de `meta_recommendations`
   (fase 4 recién cableada) en el análisis.

---

## Fase 1 — Limpieza de señal

**Meta**: que solo entren al LLM posts con dolor real y explícito.
Calidad > cantidad. Un run que analice 30 posts buenos es mejor que 80 mezclados.

### F26 — Cirugía de config (basado en datos frescos)

Esperar Fase 0, luego:

**Subreddits a eliminar** (señal histórica nula o near-nula):
- `veterinaryprofession` — 293 posts, 0 señal alta en toda la historia
- `dentalhygiene` — 429 posts, 2 señal alta (0,5% yield)
- `legaladvice` — 1.788 posts, 24 señal (1,3% yield) — enorme ruido
- `physicaltherapy` — revisar con datos frescos

**Queries a eliminar** (avg semantic_score < -20 sobre BD histórica):
- "I built a spreadsheet to track" (avg -57,9 — patrón showcase puro)
- "no API for" (-30,5)
- "I use Google Sheets to track" (-25,6)
- "copy paste every" (-25,5)
- "I export to CSV and then" (-29,1)
- "Salesforce is too expensive" (-29,6)
- "I use Excel to track" (-15,2)
- "is there a tool that" (-21,4)
- "looking for a SaaS that" (-22,9)
- ~10 más a confirmar con datos frescos

**Threshold ajuste**:
- Subir `MIN_SEMANTIC_SCORE` de 1.0 a 2.5 — a 1.0 pasan posts que no tienen
  ninguna frase de dolor clara, solo el subreddit les da puntos

**Showcase leakage**:
- Añadir a `SHOWCASE_TITLE_PREFIXES` patrones tipo "N ideas", "N problems",
  "N things" — hoy llegan con sem=10 porque el cuerpo cita dolores ajenos,
  no el dolor propio del autor
- En `data_loader`: filtrar `category=showcase` salvo `semantic_score >= 7`
  (los "I built X because I had this pain" sí son señal real)

**Nuevos subreddits a considerar** (confirmar con datos frescos):
- `quickbooks`, `xero` — alta concentración de workarounds de facturación
- `shopify` (distinto de shopifyecommerce) — inventario, fulfillment
- `hubspot` — CRM pain de agencias pequeñas
- `automation` — intención de automatizar explícita

---

## Fase 2 — Mejora de prompts de extracción

**Meta**: que cada extracción sea tan específica que no puedas confundirla
con otra. "Solo bookkeeper que factura a 12 clientes de construcción via email
y reconcilia en Excel los viernes" en vez de "pequeño negocio con problemas
de facturación".

### F27 — Extracción v2: más especificidad forzada

Cambios al `EXTRACTION_PROMPT` y `DEEP_EXTRACTION_PROMPT`:

1. **Forzar industria + tamaño + herramienta actual** en `who_has_it`:
   ```
   "who_has_it" debe incluir: (a) profesión exacta, (b) tamaño aproximado
   del negocio o equipo si se menciona, (c) herramienta actual si se menciona.
   MALO: "small business owner"
   BUENO: "solo bookkeeper managing 8-12 construction clients, currently
   reconciling in Excel every Friday afternoon"
   ```

2. **Forzar cuantificación del dolor** cuando existe:
   ```
   Si el post menciona tiempo ("3 hours", "every morning", "takes my whole day")
   o dinero ("$400/month", "pays a VA"), inclúyelo literalmente en
   "problem_description". No lo omitas ni lo parafrasees.
   ```

3. **Campo nuevo: `urgency_signal`** (string) — evidencia concreta de que
   el problema es urgente HOY, no hipotético:
   - "Lost a client because of this"
   - "Hiring a VA just to do this"
   - "Considering quitting because of this"
   - "Looking to buy a solution right now"
   Si no hay urgency signal, seguir — pero priorizarlos en síntesis.

4. **Rechazar más agresivamente análisis de terceros**:
   Ampliar la regla de "analyzing/summarizing OTHER people's problems" para
   cubrir posts tipo "aquí hay 11 problemas que la gente tiene" — aunque el
   texto contenga frases de dolor, no es el dolor del autor.

### F28 — Síntesis v2: contexto de mercado

Cambios al prompt de síntesis (`synthesis.py`):

1. **RULE 8 — MARKET SIZE SIGNAL**: cada oportunidad debe incluir estimación
   del tamaño de audiencia basada en los posts:
   - Número de subreddits distintos donde aparece el problema
   - Upvotes + comentarios acumulados de los posts de evidencia
   - Si el problema se ve solo en 1 subreddit con posts de score=0, marcar
     como "nicho estrecho — validar antes de construir"

2. **RULE 9 — COMPETITOR SPECIFICITY**: si hay herramientas mencionadas
   en los posts de evidencia, el campo `competitor_gap` debe describir
   exactamente por qué esas herramientas no resuelven el problema
   (precio, falta de integración, curva de aprendizaje, etc.).
   No aceptar "existing tools are too expensive" como respuesta — forzar
   el nombre y el motivo concreto.

3. **Campo nuevo en el output**: `validation_difficulty` (low/medium/high)
   — estimación de lo difícil que sería validar el problema antes de construir:
   - `low`: el autor ya está buscando comprar, hay replies con "+1 mismo problema"
   - `medium`: el problema es claro pero no hay señal de compra
   - `high`: el problema existe pero la audiencia es pequeña o resistente a pagar

---

## Fase 3 — GTM v2: de teórico a accionable

**Meta**: que el plan GTM sea lo suficientemente concreto para empezar a
ejecutarlo esta semana, no "validar la idea en el mes 1".

El GTM actual genera planes coherentes pero genéricos. Para que sirvan de
verdad necesitan:

### F29 — GTM v2: outreach real + validación concreta

1. **Cold outreach personalizado por industria**:
   El `cold_outreach_script` actual es genérico. Añadir al prompt:
   - El subreddit de origen de cada evidence quote
   - Instrucción: "el script de outreach debe poder enviarse como DM a alguien
     que escribió uno de los posts de evidencia — usa su contexto exacto"

2. **Canales de adquisición con comunidades reales**:
   Añadir al contexto del GTM: los subreddits donde apareció el problema.
   Instruir al LLM que proponga tácticas específicas en esas comunidades
   (no "post en foros relevantes" sino "publica en r/bookkeeping con este título
   específico y esta pregunta de validación").

3. **Semana de validación con criterio binario**:
   El `validation_plan_7d` actual da acciones pero no criterios duros.
   Cambiar a:
   - Cada día tiene un criterio pass/fail: "si no consigues X, el día 3 pivota"
   - El criterio de éxito es concreto: "3 respuestas afirmativas a la cold DM",
     "1 persona dispuesta a hacer una llamada de 15 min", "5 upvotes en el
     post de validación"

4. **Campo nuevo: `reddit_outreach_targets`** — lista de 2-3 posts reales
   (los del evidence) donde el LLM puede sugerir exactamente qué responder
   para arrancar una conversación de validación sin vender.

5. **Pricing con benchmarks reales**:
   Añadir al contexto del prompt las herramientas mencionadas como competidores
   con sus precios reales (una tabla estática en el prompt con 20-30 SaaS
   comunes y sus tier de precio). El LLM hoy inventa precios — con referencia
   concreta los tiers serán más realistas.

---

## Fase 4 — Bucle de feedback

**Meta**: que el sistema aprenda de qué oportunidades perseguiste, cuáles
funcionaron y cuáles no, para mejorar los filtros automáticamente.

### F30 — Tabla de seguimiento de oportunidades

Nueva tabla `opportunity_tracking`:
```
opportunity_id  → FK a opportunities
status          → enum: 'new', 'investigating', 'validating', 'building',
                        'abandoned', 'launched'
abandonment_reason → si status=abandoned: 'market_too_small', 'too_complex',
                     'already_exists', 'no_replies', 'other'
validation_result → resumen de lo que descubriste al validar
updated_at
```

CLI simple: `python -m saas_radar.agents.tracker --opp-id N --status validating`

**Por qué importa**: si las oportunidades en status=abandoned tienen en común
ciertos patrones (mismo subreddit, mismo tipo de workaround, mismo rango de
priority_score), ese patrón puede convertirse en una regla de filtrado.

### F31 — Tuner que actúa automáticamente

El tuner actual propone cambios (recurrence≥3) pero nunca los aplica (todos
tienen `acted=0`). Con el modo `--apply` de F20 ya existe la infraestructura.

Lo que falta:
- Que el workflow de GitHub aplique automáticamente los cambios con
  recurrence≥5 (umbral alto para evitar falsos positivos)
- Que las sugerencias de F30 (abandonment patterns) alimenten el tuner
  como nuevas reglas del tipo `add_off_topic_signal` o `remove_subreddit`

---

## Fase 5 — Superficie de salida (lo que realmente usarás)

**Meta**: que no tengas que abrir la BD para saber qué oportunidades hay.

### F32 — Digest semanal por Telegram

Un resumen semanal (no diario — hay demasiado ruido diario) con:
- Las N oportunidades nuevas de la semana, ordenadas por priority_score
- Para cada una: product_name + niche + elevator_pitch + validation_difficulty
- Un link directo al post de Reddit de mayor score de evidencia
- Botones inline de Telegram para marcar `investigating` / `discard` sin
  abrir la BD

### F33 — Vista web mínima (opcional)

Un dashboard HTML estático (generado por el pipeline, pusheado a rama data)
con la tabla de oportunidades filtrada y ordenada, con el GTM desplegable
por oportunidad. Sin backend, sin auth, solo un archivo `index.html` que
puedes abrir localmente o servir desde GitHub Pages.

---

## Orden de implementación sugerido

```
Fase 0   (ahora)          → Esperar datos frescos (1 semana)
F26      (semana 2)       → Limpieza de config basada en datos reales
F27      (semana 3)       → Extracción v2 más específica
F28      (semana 4)       → Síntesis v2 con contexto de mercado
F29      (semana 5-6)     → GTM v2 accionable
F30      (semana 7)       → Tracking de oportunidades
F31      (semana 8)       → Tuner automático
F32      (semana 9)       → Digest semanal Telegram
F33      (cuando quieras) → Dashboard HTML (bajo impacto, cómodo)
```

**Criterio de prioridad**: F26-F28 son de señal y son bloqueantes para todo
lo demás — no tiene sentido mejorar el GTM si los leads que entran son malos.
F29 (GTM v2) es lo que más directamente impacta en que puedas ganar dinero:
sin un plan de validación concreto y outreach específico, las oportunidades
quedan como análisis teórico.

---

## Qué medir para saber que esto está funcionando

| KPI | Hoy | Target en 2 meses |
|---|---|---|
| Oportunidades con `validation_difficulty=low` por mes | ~0 | ≥2 |
| Posts que llegan al LLM con sem≥3 | ~5% del batch | ≥30% del batch |
| Runs fallados / runs totales | 2/12 (17%) | <5% |
| Oportunidades en status != 'new' | 0 | ≥3 |
| Leads que llegaron a validación real | 0 | ≥1 |
