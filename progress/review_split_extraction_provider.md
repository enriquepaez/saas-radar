# Review — feature split_extraction_provider

**Veredicto:** CHANGES_REQUESTED

## Checkpoints

- C1: [x] Archivos base y docs presentes; `./init.sh` termina en verde.
- C2: [x] Una sola feature en `in_progress`; estado coherente.
- C3: [ ] Ver cambio requerido #1 — `ai_analyzer.py` lee `config.EXTRACTION_PROVIDER` directamente desde `_extract_and_cache`, violando `docs/architecture.md` §3.
- C4: [x] Test 9 cubre los dos modos (deep y batch); suite completa pasa (exit code 0, 391 tests).
- C5: [x] BD no tocada.
- C6: [ ] Sesión aún abierta (no aplica para este veredicto).

## Verificación concreta

### config.py ✓
Línea 45: `EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "groq").lower()` — correcto, mismo patrón que `AI_PROVIDER`.

### ai_analyzer.py — extracción ✓ / ✗ (ver C3)
Línea 361: `extraction_provider = config.EXTRACTION_PROVIDER` — la extracción usa el valor correcto.
Línea 266: `call_llm(prompt, ..., provider=provider)` — la síntesis sigue usando el argumento recibido. Correcto.

### pipeline.yml ✓
Línea 35: `EXTRACTION_PROVIDER: ${{ secrets.EXTRACTION_PROVIDER }}` — presente y correcto.

### Tests ✓
`test_extraction_uses_extraction_provider` cubre modo deep (N=2) y modo batch (N=31).
Suite completa: 391 tests, exit code 0.

## Cambios requeridos

### 1. Violación de `architecture.md` §3 — `_extract_and_cache` lee `config.EXTRACTION_PROVIDER` directamente

`docs/architecture.md` §3 dice textualmente: "Los módulos NO leen ni mutan `saas_radar.config.AI_PROVIDER` (u otros) en runtime. Si una función necesita el provider, lo recibe como argumento explícito."

El problema: `_extract_and_cache` (línea 361 de `src/saas_radar/analysis/ai_analyzer.py`) lee `config.EXTRACTION_PROVIDER` en tiempo de llamada en lugar de recibirlo como argumento.

La solución correcta per arquitectura:

1. En `run_ai_analysis`, leer `config.EXTRACTION_PROVIDER` una sola vez y pasarlo a `_extract_and_cache`:
   ```python
   # En run_ai_analysis, antes de llamar a _extract_and_cache:
   extraction_provider = config.EXTRACTION_PROVIDER
   # ... y luego:
   all_extractions = _extract_and_cache(posts_list, extractions_cache_path, provider=provider, extraction_provider=extraction_provider)
   ```

2. La firma de `_extract_and_cache` pasa a:
   ```python
   def _extract_and_cache(posts_list, cache_path, provider="claude", extraction_provider=None):
       extraction_provider = extraction_provider or config.EXTRACTION_PROVIDER
   ```
   O directamente con el valor obligatorio si el caller siempre lo pasa.

   Esto mantiene las dos variables desacopladas, hace el flujo de datos explícito, y permite que los tests inyecten el valor sin necesitar `patch.object` sobre el módulo config.

El único sitio donde está permitido leer `config.*` es el orquestador de nivel superior (`run_ai_analysis` o `main.py`), que luego propaga el valor como argumento explícito hacia abajo.

---

# Review — feature split_extraction_provider (re-review tras fix)

**Veredicto:** APPROVED

## Checkpoints

- C1: [x] `./init.sh` termina verde; suite completa pasa (exit code 0).
- C2: [x] Estado coherente en `progress/current.md`.
- C3: [x] Fix aplicado correctamente: `_extract_and_cache` (línea 349-350) recibe `extraction_provider: str = "claude"` como argumento explícito y NO contiene ninguna llamada a `config.*` en su cuerpo. La única lectura de `config.EXTRACTION_PROVIDER` es en `run_ai_analysis` línea 177 (nivel de entrada, único sitio autorizado por §3).
- C4: [x] `run_ai_analysis` pasa `extraction_provider=extraction_provider` explícitamente en ambas llamadas a `_extract_and_cache` (líneas 228 y 230).
- C5: [x] Tests pasan: 391 tests, exit code 0.
- C6: [x] Sin otras violaciones de `architecture.md` §3 en los archivos modificados.

## Verificación concreta

- `ai_analyzer.py` línea 177: `extraction_provider = config.EXTRACTION_PROVIDER` — única lectura de config.*, dentro de `run_ai_analysis`.
- `ai_analyzer.py` línea 349-350: `def _extract_and_cache(posts_list: list[pd.Series], cache_path: str, extraction_provider: str = "claude")` — firma correcta, argumento explícito.
- `ai_analyzer.py` líneas 228, 230: ambas llamadas pasan `extraction_provider=extraction_provider`.
- `grep -n "config\."` sobre el archivo solo devuelve línea 176 (comentario) y 177 (la lectura autorizada).
- Suite: exit code 0 confirmado dos veces.
