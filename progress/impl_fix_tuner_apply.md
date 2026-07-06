# Implementación: fix — tuner --apply ignora acciones A5/A6/A7 y commitea en vacío

## Qué cambió

- **`src/saas_radar/agents/tuner.py`** (modificado):
  1. `apply_proposals()` ahora implementa las 3 acciones LLM que antes caían
     en el `else` "Accion desconocida ignorada": `add_query`, `add_subreddit`
     y `add_phrase`.
  2. Nuevo helper `_normalize_subreddit(target)`: quita el prefijo `r/` de
     los targets de subreddit sugeridos por el LLM.
  3. Nuevo helper `_insert_tuple_into_list(text, var_name, entry, weight)`:
     inserta tuplas `("frase", peso),` en `PAIN_SIGNAL_PHRASES` (el helper
     existente `_insert_into_set` solo sabe insertar strings `"entry",`).
  4. Nueva constante `_DEFAULT_PHRASE_WEIGHT = 2` para el peso de frases nuevas.
  5. `render_config_diff()` previsualiza las 3 acciones nuevas en el dry-run
     (antes imprimía `# accion desconocida: add_subreddit ...`).
  6. Guard en `main()` (modo `--apply`): si tras `apply_proposals()` el texto
     de `config.py` no cambió, imprime aviso y hace `return 0` ANTES de crear
     rama/commit/PR.
- **`tests/test_tuner.py`** (modificado): 8 tests nuevos en
  `TestApplyProposals`, 1 en `TestRenderConfigDiff`, 1 en `TestCliApply`.
  El fixture `_make_config` gana un bloque `PAIN_SIGNAL_PHRASES`.

## Por qué

El workflow "saas-radar tuner" falló el 2026-07-06 con:

```
Accion desconocida ignorada: add_subreddit
Accion desconocida ignorada: add_phrase
Switched to a new branch 'chore/auto-tuning-20260706'
fatal: empty ident name not allowed
```

Dos bugs encadenados:

1. **Acciones sin implementar.** `tuning_rules.py` genera propuestas A5/A6/A7
   (`add_query`/`add_subreddit`/`add_phrase`) desde `meta_recommendations`,
   pero `apply_proposals()` solo conocía las 4 acciones originales. Las
   propuestas LLM se descartaban en silencio (solo un warning en el log).
2. **Commit en vacío.** Como todas las propuestas del run eran de tipo LLM y
   se ignoraron, `config.py` quedó intacto, pero el flujo seguía adelante:
   creaba la rama e intentaba `git commit`. El error visible fue el de
   identidad git (lo arregla el leader en el workflow), pero aunque hubiera
   identidad, el commit habría fallado igualmente con "nothing to commit".
   El guard hace el `--apply` robusto: si no hay diff real, el job termina
   verde sin intentar git.

Decisiones no obvias:

- **Peso 2 para frases nuevas (no 3):** las frases existentes en
  `PAIN_SIGNAL_PHRASES` van de 1 a 3. Una frase sugerida por el LLM aún no
  está validada con datos reales, así que puntuarla al máximo (3) inflaría
  el scoring con señal no contrastada. 2 es el punto medio conservador.
- **Helper nuevo en vez de parametrizar `_insert_into_set`:** parametrizar el
  formato de línea Y el patrón de duplicados en una sola función la habría
  vuelto más difícil de leer y de testear. Duplicar la estructura (30 líneas)
  con formato de tupla es más simple y cada helper queda autoexplicativo.
- **Comparación de texto antes/después en vez de `git diff --quiet`:** no
  depende de que `config.py` esté trackeado ni de tener git disponible
  (los tests usan un `config.py` temporal fuera del repo), y es trivialmente
  testeable sin mockear otro subprocess.

## Impacto en el pipeline

- **Tuning automático (workflow semanal):** las sugerencias del LLM
  (`query_suggestion`, `subreddit_suggestion`, `phrase_suggestion` con
  recurrence >= 2) por fin se materializan como ediciones de `config.py` y
  llegan al PR de auto-tuning. Hasta ahora ese canal estaba muerto.
- **Scraping:** los `add_subreddit` y `add_query` aplicados amplían el
  universo de búsqueda en el siguiente run (una vez mergeado el PR).
- **Scoring:** los `add_phrase` añaden frases al detector de dolor con
  peso 2.
- **Robustez del job:** un run donde todas las propuestas son duplicados ya
  presentes en config termina en verde con mensaje informativo, en vez de
  reventar en `git commit`.
- **Sin cambios** en BD, Telegram, dashboard ni CLI de scraping.

## Explicación técnica

### `_normalize_subreddit(target: str) -> str`

```python
return re.sub(r"^r/", "", target.strip(), flags=re.IGNORECASE)
```

- `target.strip()` — elimina espacios accidentales en los extremos (el target
  viene de texto generado por LLM, que a veces trae padding).
- `re.sub(patrón, "", texto)` — sustituye lo que matchea el patrón por cadena
  vacía, es decir, lo borra.
- `^r/` — el ancla `^` significa "solo al principio de la cadena": borra el
  prefijo `r/` pero NO tocaría una `r/` en medio del nombre. Sin `^`,
  un hipotético `powerr/tools` quedaría corrupto.
- `flags=re.IGNORECASE` — cubre también `R/notion`.
- Por qué existe: `propose_add_subreddits_from_llm` pasa el target tal cual
  viene de `meta_recommendations`, y el LLM suele escribir `r/nombre`, pero
  en `config.py` la lista `SUBREDDITS` guarda nombres pelados (`"notion"`).
  Sin normalizar, insertaríamos `"r/notion"` y PRAW fallaría al scrapear.

### `_DEFAULT_PHRASE_WEIGHT = 2`

Constante de módulo (prefijo `_` = privada por convención) en vez de un
literal `2` enterrado en dos sitios (`apply_proposals` y
`render_config_diff`): un solo punto de cambio y el nombre documenta la
intención.

### `_insert_tuple_into_list(text, var_name, entry, weight) -> str`

Estructura gemela de `_insert_into_set` (misma búsqueda de bloque con
`_find_block_range`, misma detección de indentación), con dos diferencias:

1. **Patrón de duplicados:**

   ```python
   pattern = re.compile(rf'^\s*\(\s*"{re.escape(entry)}"\s*,\s*\d+\s*\)\s*,?\s*$', re.IGNORECASE)
   ```

   - `rf'...'` — f-string cruda: `f` interpola `{re.escape(entry)}` y `r`
     evita que Python interprete los `\s`/`\d` como escapes de string (los
     recibe regex tal cual).
   - `re.escape(entry)` — escapa caracteres especiales de regex dentro de la
     frase (p.ej. un `.` o `?` literal); sin esto, una frase con `?` rompería
     o matchearía de más.
   - `\(\s*"..."\s*,\s*\d+\s*\)` — matchea la tupla completa: paréntesis
     literal escapado `\(`, la frase entre comillas, coma, `\d+` = uno o más
     dígitos (**cualquier peso**, no solo 2 — si la frase ya existe con
     peso 3, tampoco se duplica), y cierre `\)`.
   - `,?\s*$` — coma final opcional y fin de línea anclado con `$`: el patrón
     debe consumir la línea entera, así no matchea líneas que solo
     *contengan* la frase como subcadena.
   - `re.IGNORECASE` — "Copy Paste" duplica a `("copy paste", 3)`.

2. **Línea insertada:**

   ```python
   escaped = entry.replace('"', '\\"')
   lines.insert(i, f'{indent}("{escaped}", {weight}),')
   ```

   - `entry.replace('"', '\\"')` — si la frase del LLM trajera comillas
     dobles, sin escaparlas la línea generada rompería la sintaxis de
     `config.py` (defensa barata contra input no controlado).
   - `lines.insert(i, ...)` — inserta ANTES del índice `i` (la línea de
     cierre `]`), desplazando el cierre hacia abajo: la entrada queda como
     último elemento de la lista.
   - El cierre se busca solo entre `("]", "],")` (no `}`): esta función es
     exclusiva de listas.

   Detalle heredado de `_insert_into_set`: la indentación se copia del último
   elemento real del bloque (saltando comentarios y líneas vacías, iterando
   hacia atrás desde el cierre), para respetar el formato existente aunque no
   sea de 4 espacios.

### Ramas nuevas en `apply_proposals()`

```python
elif p.action == "add_query":
    text = _insert_into_set(text, "PAIN_SEARCH_QUERIES", p.target)
elif p.action == "add_subreddit":
    text = _insert_into_set(text, "SUBREDDITS", _normalize_subreddit(p.target))
elif p.action == "add_phrase":
    text = _insert_tuple_into_list(text, "PAIN_SIGNAL_PHRASES", p.target, _DEFAULT_PHRASE_WEIGHT)
```

- `add_query` y `add_subreddit` reutilizan `_insert_into_set`: aunque el
  nombre dice "set", su detección de cierre ya contempla `]`/`],`, así que
  funciona igual para listas de strings. Su check de duplicados es
  case-insensitive, lo que cubre el caso `r/propertymanagement` vs
  `"PropertyManagement"` ya presente en config.
- Cada helper devuelve el texto (modificado o intacto) y se reasigna a
  `text`: las propuestas se aplican en cadena sobre el mismo string y se
  escribe a disco una sola vez al final (`config_path.write_text`).
- El `else` con el warning se mantiene como red de seguridad para acciones
  futuras realmente desconocidas.

### Casos nuevos en `render_config_diff()`

```python
elif p.action == "add_query":
    escaped = t.replace('"', '\\"')
    lines.append(f'PAIN_SEARCH_QUERIES.append("{escaped}")')
elif p.action == "add_subreddit":
    lines.append(f'SUBREDDITS.append("{_normalize_subreddit(t)}")')
elif p.action == "add_phrase":
    escaped = t.replace('"', '\\"')
    lines.append(f'PAIN_SIGNAL_PHRASES.append(("{escaped}", {_DEFAULT_PHRASE_WEIGHT}))')
```

Es solo pseudo-Python informativo para el dry-run (`--show-diff`), pero se
normaliza el `r/` y se usa la misma constante de peso para que la
previsualización coincida exactamente con lo que `--apply` hará después.

### Guard en `main()`

```python
text_before = config_path.read_text(encoding="utf-8")
apply_proposals(applied, config_path)
text_after = config_path.read_text(encoding="utf-8")

if text_after == text_before:
    print("(las propuestas no produjeron cambios en config.py — no se crea PR)")
    return 0
```

- Se lee el fichero completo a string antes y después; la comparación `==`
  entre strings en Python es byte a byte, así que cualquier cambio real
  (aunque sea un carácter) pasa el guard.
- `return 0` — exit code 0: el job de Actions queda **verde**. "Todas las
  propuestas eran duplicados" es un resultado válido del tuner, no un error.
- Colocación: después de `check_open_pr` (guard de PR ya abierto) y antes
  del bloque `git checkout -b` — exactamente el punto donde el run del
  2026-07-06 explotó.
- Nota: `mark_acted` NO se ejecuta en este camino, a propósito: las
  recomendaciones quedan con `acted=0` y volverán a evaluarse; si en el
  futuro dejan de ser duplicados (p.ej. el subreddit se elimina de config),
  se aplicarán.

## Tests añadidos

En `tests/test_tuner.py`:

- `TestApplyProposals::test_add_query_inserta_en_pain_search_queries` — la
  query nueva aterriza dentro del bloque `PAIN_SEARCH_QUERIES` (no en otro).
- `TestApplyProposals::test_add_query_no_duplica` — query ya existente no se
  inserta dos veces.
- `TestApplyProposals::test_add_subreddit_inserta_en_subreddits` — subreddit
  nuevo aterriza dentro del bloque `SUBREDDITS`.
- `TestApplyProposals::test_add_subreddit_normaliza_prefijo_r` — target
  `r/shopify` se inserta como `"shopify"`, nunca como `"r/shopify"`.
- `TestApplyProposals::test_add_subreddit_no_duplica_case_insensitive` —
  `r/propertymanagement` no duplica al `"PropertyManagement"` existente.
- `TestApplyProposals::test_add_phrase_inserta_tupla_con_peso_2` — formato
  exacto `("retype into", 2),` dentro de `PAIN_SIGNAL_PHRASES`.
- `TestApplyProposals::test_add_phrase_no_duplica_con_otro_peso` — la frase
  existente con peso 3 bloquea la inserción con peso 2 (match contra
  cualquier peso).
- `TestApplyProposals::test_add_phrase_no_duplica_case_insensitive` —
  "Copy Paste" no duplica a `("copy paste", 3)`.
- `TestRenderConfigDiff::test_genera_pseudo_python_acciones_llm` — el
  dry-run previsualiza las 3 acciones (con `r/` normalizado y peso 2) y ya
  no imprime `# accion desconocida`.
- `TestCliApply::test_apply_sin_cambios_reales_no_crea_rama_ni_pr` —
  reproduce el escenario del fallo real: única propuesta LLM que resulta ser
  duplicado → exit 0, mensaje "no produjeron cambios", `config.py` intacto y
  **ningún comando `git` ejecutado** (se inspecciona la lista de llamadas
  capturadas por el mock de `subprocess.run`).

Además, el fixture `_make_config` de `TestApplyProposals` ahora incluye un
bloque `PAIN_SIGNAL_PHRASES` con dos tuplas, sin romper los 7 tests previos
de la clase (que trocean el texto por bloques anteriores).

## Verificación

`./init.sh` → exit 0 (todo `[OK]`; el paso de tests avisa `[WARN] pytest no
instalado` porque el script busca pytest en el PATH del sistema, no en el
venv del proyecto — comportamiento previo a este fix, no introducido aquí).

Suite completa con el venv real del repo (441 tests colectados):

```
$ .venv/bin/pytest -q
........................................................................ [ 48%]
........................................................................ [ 65%]
........................................................................ [ 81%]
........................................................................ [ 97%]
.........                                                                [100%]
```

Exit code 0. (La línea-resumen no aparece porque `pyproject.toml` ya trae
`addopts = "-q"` y el `-q` extra equivale a `-qq`, que la suprime.)

Solo el módulo del tuner:

```
$ .venv/bin/pytest tests/test_tuner.py
45 passed in 0.05s
```

Los 10 tests nuevos pasan y ninguno de los 35 existentes se rompe.

## Correcciones post-review

El reviewer rechazó la primera versión por dos bugs de escapado de comillas
dobles (detalle en `progress/review_fix_tuner_apply.md`). Ambos corregidos.

### Qué cambió

- **`src/saas_radar/agents/tuner.py` — `_insert_into_set()`**: la línea
  insertada ahora escapa comillas dobles, y el patrón de duplicados se
  construye sobre la forma escapada.
- **`src/saas_radar/agents/tuner.py` — `_insert_tuple_into_list()`**: el
  escapado (`entry.replace('"', '\\"')`) se movió de la rama de inserción al
  inicio de la función, ANTES de construir el patrón de duplicados, que ahora
  matchea contra la forma escapada.
- **`tests/test_tuner.py`**: 2 tests nuevos con targets que contienen
  comillas dobles, validando el resultado con `ast.parse` y el dedupe en
  segunda pasada.

### Por qué

**Bug 1 — config.py inválido.** `_insert_into_set` insertaba
`f'{indent}"{entry}",'` sin escapar. Históricamente solo recibía nombres de
subreddit (sin comillas posibles), pero este fix le enruta por primera vez
texto libre del LLM vía `add_query`. Un target plausible como
`'"manual data entry" CRM'` (sintaxis de búsqueda exacta) generaba:

```python
    ""manual data entry" CRM",   # ← SyntaxError
```

Ese config.py roto se habría commiteado y, al mergear el PR, TODO el
pipeline moriría en el `import config`. La primera versión ya escapaba en
`render_config_diff` y en `_insert_tuple_into_list`, pero faltó el camino
de inserción real de `add_query` — la lección: cuando un helper antiguo
pasa a recibir una clase nueva de input (texto libre vs identificadores),
hay que re-auditar sus supuestos implícitos.

**Bug 2 — dedupe de frases roto con comillas.** En `_insert_tuple_into_list`
había una asimetría: la línea ESCRITA contenía la forma escapada
(`("retype \"into\"", 2),`), pero el patrón de duplicados se construía con
`re.escape(entry)` sobre la frase SIN escapar, así que buscaba una línea
que nunca existe en el fichero. Resultado: cada run que re-propusiera la
frase (p.ej. tras `sync_acted_status` revertir `acted=0`) añadía otra
copia, y como el scorer suma todas las frases que matchean, el peso
efectivo se acumulaba (2, 4, 6…) inflando el scoring en silencio.

### Explicación técnica

Ambos fixes siguen el mismo principio: **el patrón de duplicados debe
buscar exactamente lo que la inserción escribe**. En los dos helpers ahora:

```python
escaped = entry.replace('"', '\\"')
pattern = re.compile(rf'... "{re.escape(escaped)}" ...', re.IGNORECASE)
...
lines.insert(i, f'... "{escaped}" ...')
```

- `entry.replace('"', '\\"')` — sustituye cada `"` por la secuencia de DOS
  caracteres `\` + `"`. Ojo con las capas de escapado: en el código fuente
  Python, el literal `'\\"'` representa el string de 2 caracteres `\"`; al
  escribirse en config.py, ese `\"` hace que la comilla sea literal dentro
  del string y no lo cierre prematuramente.
- `re.escape(escaped)` — segunda capa, esta vez para regex: la forma
  escapada contiene backslashes, que en un patrón significan "secuencia de
  escape"; `re.escape` los convierte en `\\` (backslash literal) para que
  el patrón busque el texto tal cual está escrito en el fichero. El orden
  importa: `replace` primero (forma en disco), `re.escape` después (forma
  en regex). Al revés, el patrón buscaría la frase cruda, que jamás aparece.
- `escaped` se calcula una sola vez al inicio de la función y se usa en
  ambos puntos (patrón + inserción), eliminando por construcción la
  asimetría que causó el bug 2: ya no es posible que inserción y dedupe
  diverjan.

Por qué `ast.parse` en los tests: `ast.parse(texto)` compila el texto a un
árbol de sintaxis SIN ejecutarlo — si el config.py generado tuviera un
SyntaxError, lanza excepción y el test falla. Es la validación más fiel a
"el pipeline podrá importar este fichero", sin el riesgo de ejecutar código
generado dentro del test.

### Tests añadidos

- `TestApplyProposals::test_add_query_con_comillas_genera_python_valido_y_no_duplica`
  — reproduce el bug 1: query con comillas dobles → `ast.parse` pasa, la
  línea queda escapada, y una segunda pasada no duplica (dedupe sobre la
  forma escapada en `_insert_into_set`).
- `TestApplyProposals::test_add_phrase_con_comillas_genera_python_valido_y_no_duplica`
  — reproduce el bug 2: frase con comillas → tupla escapada válida y la
  re-propuesta NO añade otra copia (sin acumulación de peso).

### Verificación

```
$ .venv/bin/pytest tests/test_tuner.py
47 passed in 0.09s

$ .venv/bin/pytest
439 passed, 4 skipped in 167.69s (0:02:47)
```

Exit code 0 en ambos.
