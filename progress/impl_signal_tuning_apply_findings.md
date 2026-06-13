# Implementación: #24 — signal_tuning_apply_findings

## Qué cambió

### `src/saas_radar/config.py`

- **`MIN_SEMANTIC_SCORE`**: 1.5 → 1.0. El umbral que debe superar `_semantic_score` para que un post pase al pipeline de IA. Bajarlo captura posts borderline que antes se descartaban silenciosamente.

- **`POSTS_CAP_HIGH_SIGNAL`**: 10 → 15. El tope de posts por subreddit en el ranking final para subreddits de alta señal. Permite meter más posts de subreddits que históricamente producen oro.

- **`HIGH_SIGNAL_SUBREDDITS`**: se añade `"indiehackers"` al set (de 16 a 17 miembros). El tuner llevaba ≥3 runs señalando esta comunidad como de alta señal (fundadores que relatan dolores de producto reales).

- **`PAIN_SEARCH_QUERIES`**: se eliminan 31 queries con yield=0 en los últimos 60 días. La lista pasa de 95 a 64 queries. Queries más largas e ineficientes (p.ej. `"how do you handle payroll for hourly"`) consumen cuota de API sin retorno; quitarlas libera ancho de banda para las queries que sí rinden.

- **`PAIN_SIGNAL_PHRASES`**: se añaden 4 frases nuevas al final (nota: `"drowning in spreadsheets"` ya estaba presente en la lista original y no se duplica). La lista pasa de 114 a 118 frases.

### `tests/test_config.py`

- **`test_ai_constants_present`**: actualizado `MIN_SEMANTIC_SCORE == 1.5` → `== 1.0`.
- **`test_posts_cap_constants`**: actualizado `POSTS_CAP_HIGH_SIGNAL == 10` → `== 15`. Docstring actualizado para reflejar que F24 modificó este valor.
- **`test_indiehackers_in_high_signal_subreddits`**: test nuevo que verifica que `"indiehackers"` está en `HIGH_SIGNAL_SUBREDDITS`.

## Por qué

### MIN_SEMANTIC_SCORE 1.5 → 1.0
Posts con score entre 1.0 y 1.5 contienen señal real (p.ej. un post en r/bookkeeping que menciona un workaround manual pero con vocabulario menos explícito) pero el umbral conservador los dejaba fuera. El análisis post-run de posts oro no capturados mostró varios con semantic_score entre 1.0 y 1.4. El riesgo de ruido adicional es bajo porque el LLM filtra en extracción.

### POSTS_CAP_HIGH_SIGNAL 10 → 15
El cap de 10 era insuficiente para subreddits como `accounting`, `msp` o `smallbusiness` que tienen ~20-30 posts de señal por run. El tuner había detectado que el ranking truncaba posts con rank_score alto en esos subreddits. +5 posts por subreddit de alta señal aumenta el pool de la IA sin explotar el presupuesto de tokens.

### indiehackers en HIGH_SIGNAL
r/indiehackers ya estaba en `SUBREDDITS` (Tier D — Aspiracional) pero con cap=4 (default). La comunidad mezcla showcases con pain real de producto (p.ej. "I'm spending 4 hours/week on invoicing"). El tuner detectó recurrence ≥3. Promoverlo a HIGH_SIGNAL le da cap=15 y normalización preferente en el ranking.

### Purga de 31 queries (yield=0 en 60d)
Las queries eliminadas no devolvieron ningún post relevante en 60 días de runs. Las causas varían:
- Queries muy específicas de herramienta que Reddit no indexa bien (`"Buildium doesn't"`, `"ServiceTitan is too expensive"`).
- Queries de workaround genérico ya cubiertos por otras más concretas (`"we track this in a spreadsheet"` → cubierta por `"I built a spreadsheet to track"` y `"I use Google Sheets to track"`).
- Preguntas operacionales hiper-específicas de industria que rinden en Google pero no en Reddit (`"how do you handle payroll for hourly"`, `"how do you track candidate pipeline"`).
- Alternativa descartada: mantenerlas como "cobertura de largo plazo". Motivo de descarte: con 95 queries el scraper ya consumía cuota en exceso; una lista más corta y precisa mejora el ratio señal/cuota.

### 4 frases nuevas en PAIN_SIGNAL_PHRASES
Extraídas de posts oro que el pipeline no capturó porque su semantic_score era < 1.5 (con el umbral anterior) y las frases no existían en la lista. Con el nuevo umbral 1.0 y las frases añadidas, estos posts entrarán al pipeline.

- `("pdf to csv", 3)`: indica transformación manual de documentos bancarios o contables. Señal fuerte de workaround (peso 3). Frecuente en r/bookkeeping y r/accounting.
- `("spending too much time", 2)`: frase de dolor temporal genérico. Peso 2 porque es menos específica que `"takes me hours"` pero captura posts con vocabulario más coloquial.
- `("converting bank statement", 3)`: workaround manual de ingesta bancaria. Señal de pago fuerte en fintech/contabilidad (peso 3).
- `("manage inventory in shopify", 2)`: combinación de tarea + herramienta concreta. Captura el pain de gestión de stock en Shopify sin herramienta adecuada (peso 2 porque incluye el nombre de producto, lo que reduce ambigüedad).

Nota: `"drowning in spreadsheets"` ya estaba presente en la lista original (línea ~335) y no se añadió de nuevo para evitar duplicado.

## Tabla antes/después del yield esperado

| Parámetro | Antes (F23) | Después (F24) | Efecto esperado |
|---|---|---|---|
| `MIN_SEMANTIC_SCORE` | 1.5 | 1.0 | +10-20% posts que llegan al LLM |
| `POSTS_CAP_HIGH_SIGNAL` | 10 | 15 | +5 posts/subreddit alto en el ranking |
| `HIGH_SIGNAL_SUBREDDITS` | 16 subreddits | 17 (+indiehackers) | Posts de indiehackers con cap 15 en lugar de 4 |
| `PAIN_SEARCH_QUERIES` | 95 queries | 64 queries (−31 muertas) | Menor consumo de cuota, mismo o mejor yield |
| `PAIN_SIGNAL_PHRASES` | 114 frases | 118 frases (+4 nuevas) | Mejor scoring de posts borderline |

## Lista de las 31 queries eliminadas

Todas con yield=0 en los últimos 60 días según consulta SQL a `data/saas.db`:

1. `"Airtable can't"`
2. `"Airtable doesn't"`
3. `"Airtable too expensive alternative"`
4. `"Buildium doesn't"`
5. `"HubSpot is too expensive"`
6. `"I export from QuickBooks and then"`
7. `"I wish there was a tool"`
8. `"IT onboarding manual process"`
9. `"Jobber doesn't"`
10. `"Notion can't"`
11. `"QuickBooks doesn't"`
12. `"Salesforce is too expensive"`
13. `"ServiceTitan is too expensive"`
14. `"Softr limitations"`
15. `"Zapier can't"`
16. `"Zapier is too expensive"`
17. `"collections spreadsheet"`
18. `"construction job costing QuickBooks"`
19. `"how do you handle insurance verification"`
20. `"how do you handle payroll for hourly"`
21. `"how do you handle purchase orders"`
22. `"how do you track billable hours"`
23. `"how do you track candidate pipeline"`
24. `"how do you track technician hours"`
25. `"incident runbook outdated"`
26. `"no-code tool can't do"`
27. `"reconciliation nightmare"`
28. `"restaurant inventory spreadsheet"`
29. `"subcontractor tracking spreadsheet"`
30. `"tip reconciliation"`
31. `"we track this in a spreadsheet"`

### Estado de comentarios de bloque tras la purga

- **"── Herramientas concretas con limitaciones"**: conservado (quedan `"Zapier doesn't support"`, `"Notion doesn't"`, `"doesn't integrate with"`, `"no integration with"`, `"no API for"`, `"no webhook"`).
- **"── Ausencia de herramienta"**: conservado (quedan `"why is there no app for"` y resto de queries del bloque).
- **"── Preguntas operacionales por industria"**: conservado (quedan `"how do you handle job costing"`, `"how do you manage tenant maintenance requests"`, `"how do you manage subcontractors"`, `"how do you manage client reporting"`).
- **Contabilidad/AR**: conservado (quedan 6 queries).
- **Restaurantes**: conservado (quedan 5 queries).
- **MSP/sysadmin**: conservado (quedan 3 queries).
- **Construcción**: conservado (quedan 2 queries: `"job costing spreadsheet"`, `"WIP report Excel"`).
- **No-code tool frustration**: queda solo `"Notion limitations business"` tras eliminar las 3 con yield=0. El bloque se conserva con comentario actualizado.

## Impacto en el pipeline

- **Scraping (fase 2 — pain_search)**: 31 queries menos → ~31 llamadas menos a Reddit por run. Reduce tasa de rate-limiting. Sin pérdida de señal porque las queries eliminadas no devolvían posts relevantes.
- **Scoring (pain_filter.py — `_semantic_score`)**: MIN_SEMANTIC_SCORE más bajo → más posts pasan el filtro. Las 4 frases nuevas aumentan el score de posts que antes quedaban en 0.5-1.0.
- **Ranking (data_loader.py)**: POSTS_CAP_HIGH_SIGNAL más alto → más posts de subreddits de alta señal en el pool de IA. indiehackers con cap 15 en lugar de 4 → hasta 11 posts más de esa comunidad por run.
- **LLM (extracción/síntesis)**: pool de entrada ligeramente mayor (efecto de MIN_SEMANTIC_SCORE y POSTS_CAP). Sin cambios en prompts ni modelos.
- **BD / persistencia**: sin cambios de schema.
- **Telegram / CLI**: sin cambios.
- **GitHub Actions**: sin cambios.

## Explicación técnica

### `MIN_SEMANTIC_SCORE = 1.0`
Constante flotante leída por `data_loader.py:load_pain_posts()` en la línea `df = df[df["semantic_score"] >= MIN_SEMANTIC_SCORE]`. Reducir de 1.5 a 1.0 amplía el intervalo de posts que superan el filtro. El valor 1.0 fue elegido (no 0 ni 0.5) porque una frase de peso 1 ya suma 1 punto, lo que representa señal mínima de "pregunta operacional". Por debajo de 1.0, los posts no tienen ninguna frase de señal — solo ruido puro.

### `POSTS_CAP_HIGH_SIGNAL = 15`
Entero leído por `data_loader.py` en la lógica de cap por subreddit:
```python
cap = config.POSTS_CAP_HIGH_SIGNAL if sub in config.HIGH_SIGNAL_SUBREDDITS else config.POSTS_CAP_DEFAULT
```
El cap limita cuántos posts de un mismo subreddit entran al pool final después del ranking blend (10% score Reddit + 15% num_comments + 75% semantic_score). Subir de 10 a 15 da 5 slots adicionales a cada subreddit de alta señal.

### `HIGH_SIGNAL_SUBREDDITS.add("indiehackers")`
`HIGH_SIGNAL_SUBREDDITS` es un set de Python (tipo `set[str]`). Todos sus miembros son lowercase porque `data_loader.py` compara contra `sub.lower()` al evaluar el cap. `"indiehackers"` ya aparece en `SUBREDDITS` como `"indiehackers"` (lowercase), así que el test `test_high_signal_subreddits_subset_of_subreddits` (que hace comparación case-insensitive) pasa sin modificación.

### Eliminación de queries en `PAIN_SEARCH_QUERIES`
`PAIN_SEARCH_QUERIES` es una lista de strings. Cada string se pasa a `reddit_scraper.search_pain_posts(query)` que ejecuta `multireddit.search(query, limit=PAIN_SEARCH_LIMIT)`. Eliminar una query de la lista hace que Reddit nunca reciba esa búsqueda en los runs siguientes. No hay efecto retroactivo en la BD (los posts ya indexados de esas queries se mantienen en `reddit_posts`).

Los bloques de comentario tipo `# Restaurantes / food service (cluster tip/payroll/inventory)` se conservan si el bloque tiene al menos una query restante. El bloque "No-code tool frustration" pierde 3 de 4 queries, y su comentario se actualiza a `# No-code tool frustration (cluster Softr/Notion limits)` para reflejar lo que queda.

### Frases nuevas en `PAIN_SIGNAL_PHRASES`
Las 4 tuplas nuevas siguen el patrón `(phrase: str, weight: int)` donde `phrase` es lowercase (la función `_semantic_score` en `pain_filter.py` normaliza el texto con `.lower()` antes de buscar) y `weight` es 1, 2 o 3. Se añaden al final de la lista bajo un comentario `# Nuevas frases extraídas de posts oro no capturados (F24)` para facilitar el tracking histórico de qué frases se añadieron y cuándo.

## Tests añadidos

- **`test_indiehackers_in_high_signal_subreddits`** (`tests/test_config.py`): verifica que `"indiehackers"` está en `config.HIGH_SIGNAL_SUBREDDITS`. Cubre la adición F24 que el test genérico de subset no detectaría como error si se hubiera olvidado.

## Tests modificados

- **`test_ai_constants_present`**: assert `MIN_SEMANTIC_SCORE == 1.5` → `== 1.0`. Refleja el valor actual tras F24.
- **`test_posts_cap_constants`**: assert `POSTS_CAP_HIGH_SIGNAL == 10` → `== 15`. Docstring actualizado para indicar que F24 modificó este valor (distingue del "valor del legacy" original).

## Limitación conocida

Los cambios no se pueden validar con tests automáticos de yield real. Para medir el impacto real (cuántos posts adicionales llegan al LLM, cuántas oportunidades más se generan) se necesita un run real del pipeline contra r/indiehackers y la BD viva. Esto está fuera del scope de tests unitarios: los tests de `test_data_loader.py` usan una BD temporal en memoria que no refleja la distribución real de posts.

## Verificación

```
/home/enriquepaez/projects/saas-radar/.venv/bin/pytest tests/test_config.py tests/test_pain_filter.py tests/test_data_loader.py -v

============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 73 items

tests/test_config.py ....................................                [ 49%]
tests/test_pain_filter.py ......................                         [ 79%]
tests/test_data_loader.py ...............                                [100%]

============================== 73 passed in 1.76s ==============================
```
