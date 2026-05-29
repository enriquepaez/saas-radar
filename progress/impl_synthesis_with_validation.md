# Implementación: #10 — synthesis_with_validation

## Qué cambió

- **`src/saas_radar/analysis/synthesis.py`** (nuevo): módulo completo creado desde cero portando el comportamiento del legacy. Contiene `build_synthesis_prompt`, `_validate_synthesis`, `_coherence_words`, `_quotes_are_coherent`, `_COHERENCE_STOP` y `_SHORT_TOOL_NAMES`.
- **`tests/test_synthesis.py`** (nuevo): 15 tests cubriendo todos los acceptance criteria.

## Por qué

**Por qué pre-clustering**: la lección §1.4 del legacy documenta que ordenar los subreddits por count desc antes de enumerar los items mejora la calidad de la síntesis. El LLM ve posts de la misma industria juntos y detecta clusters reales en lugar de pares aleatorios. Sin este orden, el LLM puede armar clusters falsos combinando items de industrias distintas que usan palabras similares.

**Por qué `_validate_synthesis` valida `problem_description` y no `evidence_quotes`**: lección §1.5. El LLM puede truncar o parafrasear las quotes de evidencia de manera que parezcan más coherentes de lo que son. El texto original de `problem_description` + `workflow_context` + `current_workaround` es inmutable y viene de la extracción previa, por lo que es la única fuente fiable para medir coherencia.

**Por qué `_COHERENCE_STOP` incluye raíces de dominio**: lección §1.9. Palabras como "manual", "tracking", "spreadsheet", "excel" aparecen en CUALQUIER queja de pain en SaaS. Si el filtro de coherencia aceptara estas raíces como señal positiva, casi cualquier par de extracciones pasaría el filtro aunque sean de dominios completamente distintos. Las raíces de 4 chars ("manu", "trac", "spre", "exce") permiten bloquear familias enteras de una sola entrada.

**Por qué `Counter` separado en `_quotes_are_coherent`**: importado en el módulo (no dentro de la función como el legacy) para cumplir las convenciones del proyecto (imports al nivel de módulo). El `defaultdict` de `build_synthesis_prompt` sigue el mismo patrón.

**Por qué `print()` de debug se mantienen**: las instrucciones de implementación indicaban explícitamente preservarlos siguiendo el legacy — son útiles para diagnóstico en pipeline (permiten ver qué opp fue rechazada y por qué raíces).

## Impacto en el pipeline

- **Fase de síntesis (ai_analyzer.py, feature #11)**: este módulo será llamado por el orquestador. `build_synthesis_prompt` produce el prompt y el `ordered_extractions` alineado. `_validate_synthesis` se aplica sobre el JSON devuelto por el LLM antes de persistir.
- **Scoring de oportunidades**: las opps que no superan los checks de cantidad o coherencia nunca llegan a `persist_run_to_db`. Esto evita que oportunidades fabricadas por el LLM (que viola RULE 1 silenciosamente) se guarden en BD.
- **`top_3_recommended`**: se reconstruye solo con ids supervivientes, lo que garantiza que las recomendaciones finales al usuario apunten a oportunidades válidas.

## Explicación técnica

### `build_synthesis_prompt(extractions)`

**Pre-clustering:**
```python
groups: dict[str, list] = defaultdict(list)
for ex in extractions:
    if not ex.get("has_problem"):
        continue
    groups[ex.get("_subreddit", "?")].append(ex)
```
`defaultdict(list)` crea automáticamente una lista vacía la primera vez que se accede a una clave nueva. El `if not ex.get("has_problem")` filtra extracciones sin problema antes de incluirlas; `get` con valor por defecto evita KeyError si la clave falta.

```python
ordered_subs = sorted(groups.keys(), key=lambda s: -len(groups[s]))
```
Ordena los nombres de subreddit por número de items desc (negativo para invertir el orden natural asc). Subreddits con más evidencia acumulada van primero.

```python
ordered_extractions = [ex for s in ordered_subs for ex in groups[s]]
```
List comprehension de doble nivel: para cada subreddit en el orden calculado, añade todos sus items. Produce la lista aplanada final respetando el orden de clusters.

**Separadores:**
```python
if sub != current_sub:
    items_text += f"\n\n### CLUSTER: r/{sub} ({len(groups[sub])} items) ###"
    current_sub = sub
```
Comparación de identidad de string: en cuanto cambia el subreddit (que ya están agrupados consecutivos por la ordenación anterior), inserta el separador visible con el count exacto.

**Numeración global:** `for i, ex in enumerate(ordered_extractions, 1)` — `enumerate` con `start=1` produce índices [1..N] que el LLM usa en `evidence_items`. Esta numeración es global y no se resetea entre clusters.

**n_industries:** `n_industries = len(groups)` — número de subreddits distintos, usado en RULE 7 del prompt para informar al LLM cuántas industrias hay disponibles.

### `_COHERENCE_STOP`

Set plano con dos tipos de entradas:
1. Palabras completas (`"about"`, `"their"`, `"using"`…): el filtro en `_coherence_words` las elimina antes del stemming (`w not in _COHERENCE_STOP`).
2. Raíces de 4 chars (`"manu"`, `"trac"`, `"spre"`, `"exce"`…): el filtro las elimina tras el stemming (`w[:4] not in _COHERENCE_STOP`). Esto permite bloquear familias enteras con una sola entrada — "tracking" → `trac` → bloqueado, "tracker" → `trac` → bloqueado.

### `_SHORT_TOOL_NAMES`

```python
_SHORT_TOOL_NAMES = {"qbo", "crm", "erp", "sap", "csv", "api", "etl", "pos", "ar", "ap"}
```
El regex `[a-z]{4,}` descarta palabras de menos de 4 chars. Pero siglas como "qbo", "crm", "csv" son señal fuerte de dominio específico. Se añaden por separado buscándolas como palabras completas con `re.findall(r"[a-z]+", q)` y haciendo intersección con el set.

### `_coherence_words(quote)`

```python
q = re.sub(r"^\[item\s+\d+\]\s*", "", str(quote).lower())
```
`re.sub` elimina el prefijo `[item N]` al principio de la quote (el `^` ancla al inicio). `\s+` acepta uno o más espacios entre "item" y el número. `\d+` acepta cualquier número. Sin esto, "item" entraría como raíz no bloqueada y contaminaría el cálculo de coherencia.

```python
roots = {w[:4] for w in re.findall(r"[a-z]{4,}", q) if w not in _COHERENCE_STOP and w[:4] not in _COHERENCE_STOP}
```
Set comprehension: `re.findall(r"[a-z]{4,}", q)` extrae todas las palabras de 4+ chars en minúsculas. Para cada una: primero comprueba que la palabra completa no esté en `_COHERENCE_STOP`, luego que su raíz de 4 chars tampoco esté. Si pasa ambos filtros, añade `w[:4]` al set. Al retornar raíces (no palabras completas) se colapsan variantes morfológicas: "invoice"/"invoices"/"invoicing" → "invo".

```python
words = set(re.findall(r"[a-z]+", q))
roots |= words & _SHORT_TOOL_NAMES
```
`re.findall(r"[a-z]+", q)` extrae TODAS las palabras (incluyendo las de <4 chars). La intersección `& _SHORT_TOOL_NAMES` filtra solo las que son siglas conocidas. `|=` agrega al set de raíces.

### `_quotes_are_coherent(quotes, min_shared=2)`

```python
if len(quotes) < 2:
    return True
```
Una sola quote no tiene par con quien comparar → coherente por definición.

```python
word_sets = [_coherence_words(q) for q in quotes]
all_words: Counter[str] = Counter()
for ws in word_sets:
    for w in ws:
        all_words[w] += 1
```
Construye un Counter de cuántas quotes contienen cada raíz. No cuenta ocurrencias dentro de una quote (ya son sets), sino en cuántas quotes distintas aparece cada raíz.

```python
threshold = len(quotes) / 2
majority_words = {w for w, c in all_words.items() if c > threshold}
```
`threshold = N/2` — con 2 quotes: threshold=1.0, necesita `c > 1.0` = aparecer en ambas. Con 3 quotes: threshold=1.5, necesita `c > 1.5` = aparecer en 2 de 3. El `>` (estricto) implementa ">50%".

```python
return len(majority_words) >= min_shared
```
Con `min_shared=2`: necesita al menos 2 raíces que aparezcan en mayoría de quotes. Una raíz compartida podría ser casualidad; dos raíces específicas de dominio compartidas indican que ambas quotes hablan del mismo workflow.

### `_validate_synthesis(results, ordered_extractions, min_evidence=2)`

```python
idx_to_text: dict[int, str] = {}
if ordered_extractions:
    for i, ex in enumerate(ordered_extractions, 1):
        idx_to_text[i] = " ".join([...])
```
Construye un mapa `{1: "texto del item 1", 2: "texto del item 2", ...}` usando índices 1-based (alineados con los que el LLM usa en `evidence_items`). Concatena `problem_description + workflow_context + current_workaround` para maximizar el vocabulario disponible sin depender del `key_quote`.

**Check 1 (cantidad):**
```python
if len(ev_items) < min_evidence or len(ev_quotes) < min_evidence:
```
OR: basta con que CUALQUIERA de los dos campos sea insuficiente para descartar. El mensaje de `rule_violated` incluye los counts actuales para que sea debuggeable.

**Check 2 (coherencia):**
```python
pairs = [(i, idx_to_text.get(int(i), "")) for i in ev_items if str(i).lstrip("-").isdigit()]
```
Filtra ids no numéricos que el LLM emite ocasionalmente (strings, floats). `str(i).lstrip("-").isdigit()` acepta enteros positivos y negativos. `int(i)` convierte para lookup en `idx_to_text`. `idx_to_text.get(int(i), "")` devuelve string vacío si el índice no existe (el LLM a veces cita un item fuera de rango).

```python
texts = [t for _, t in pairs]
coherent = _quotes_are_coherent(texts)
```
Aplica el filtro de coherencia sobre los textos REALES de las extracciones, no sobre las quotes del LLM.

**Reconstrucción de top_3:**
```python
kept_ids = {opp.get("id") for opp in kept}
top3 = [i for i in (results.get("top_3_recommended") or []) if i in kept_ids]
```
Set comprehension para lookup O(1). List comprehension que preserva el orden original de `top_3_recommended` pero elimina los ids de opps descartadas.

**Acumulación de disqualified_ideas:**
```python
results["disqualified_ideas"] = (results.get("disqualified_ideas") or []) + dropped
```
`or []` cubre el caso en que el LLM devuelva `None` en ese campo. La concatenación preserva las entradas del LLM al inicio y agrega las nuevas al final.

## Tests añadidos

1. **`test_build_synthesis_prompt_cluster_separators`**: verifica que el prompt contiene `### CLUSTER: r/accounting (2 items) ###` y `### CLUSTER: r/msp (1 items) ###`.
2. **`test_build_synthesis_prompt_global_numbering`**: verifica que aparecen `[1]`, `[2]`, `[3]` en el prompt con 3 extracciones de 2 subreddits distintos.
3. **`test_build_synthesis_prompt_ordered_extractions_returned`**: verifica que la lista devuelta tiene los 2 items de accounting antes del de msp, incluso cuando el input los entrega en orden inverso.
4. **`test_build_synthesis_prompt_only_has_problem`**: verifica que una extracción con `has_problem=False` no aparece en `ordered_extractions` ni en el texto del prompt.
5. **`test_validate_synthesis_drops_insufficient_evidence`**: opp con 1 item y 1 quote → va a `disqualified_ideas` con "RULE 1 cantidad" en el texto.
6. **`test_validate_synthesis_drops_incoherent_cluster`**: 2 extracciones de dominios completamente distintos (contabilidad vs redes) → opp descartada con "coherencia" en `rule_violated`.
7. **`test_validate_synthesis_keeps_coherent_cluster`**: 3 extracciones sobre QBO/facturas → opp mantenida, aparece en `opportunities` y en `top_3_recommended`.
8. **`test_validate_synthesis_top3_only_survivors`**: de 3 opps, la #2 es incoherente → `top_3_recommended` final contiene [1, 3] pero no 2.
9. **`test_validate_synthesis_accumulates_disqualified`**: el LLM ya trae una entrada en `disqualified_ideas` → tras la validación hay 2 entradas, no 1.
10. **`test_coherence_words_strips_prefix`**: `[item 3] invoice tracking in quickbooks` → "item" no aparece, "invo" y "quic" sí.
11. **`test_coherence_words_excludes_stop_roots`**: "manually tracking spreadsheet data" → ninguno de manu/trac/spre/data aparece en el resultado.
12. **`test_coherence_words_includes_short_tools`**: "we use qbo and csv exports" → qbo y csv aparecen en las raíces.
13. **`test_quotes_are_coherent_true`**: 3 quotes sobre facturas y quickbooks → True.
14. **`test_quotes_are_coherent_false`**: 2 quotes de dominios disjuntos → False.
15. **`test_quotes_are_coherent_single_quote`**: una sola quote → True.

## Verificación

```
source .venv/bin/activate && python -m pytest tests/ 2>&1 | tail -5

........................................................................ [ 34%]
........................................................................ [ 68%]
.................................................................        [100%]
209 passed in 0.88s
```

```
ruff check src/saas_radar/analysis/synthesis.py tests/test_synthesis.py
All checks passed!
```

## Correcciones post-review

### Cambios aplicados

**`src/saas_radar/analysis/synthesis.py`** — se añadió logger y se reemplazaron los 7 `print()` en `_validate_synthesis`.

#### Corrección 1 — logger

Añadidos en el bloque de imports:
```python
import logging
```
Tras todos los imports (antes de la primera función):
```python
logger = logging.getLogger(__name__)
```
`logging.getLogger(__name__)` crea (o recupera) un logger cuyo nombre es el path del módulo (`saas_radar.analysis.synthesis`). Esto permite al caller configurar el nivel de log (`DEBUG`, `INFO`) de forma centralizada sin modificar el módulo.

#### Corrección 2 — 7 print() → logger calls

Los `print()` se clasificaron en dos niveles según su naturaleza:

**Diagnóstico de coherencia → `logger.debug(...)`** (3 prints por bloque × 2 bloques = 6 calls):
- `print(f"  [coherencia] rechazada '{name}':")` → `logger.debug(...)`
- `print(f"     [item {i}] {txt[:100]}")` → `logger.debug(...)`
- `print(f"       raices: {sorted(roots)[:10]}")` → `logger.debug(...)`
- Repetidos en el bloque `else` (fallback a `ev_quotes` cuando no hay `ordered_extractions`).

Se usa `DEBUG` porque son mensajes de diagnóstico interno del algoritmo de validación — útiles para desarrollo y debugging, no para producción.

**Resumen de descarte → `logger.info(...)`** (1 print):
- `print(f"  [validacion] descartada: {d['idea']} => {d['rule_violated']}")` → `logger.info(...)`

Se usa `INFO` porque es un evento de negocio relevante (una oportunidad fue descartada) que el operador del pipeline puede querer ver en logs normales de producción.

### Verificación post-corrección

```
209 passed in 0.88s
ruff check src/saas_radar/analysis/synthesis.py → All checks passed!
```
