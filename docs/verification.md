# Verificación — Cómo demostrar que el trabajo funciona

> Regla de oro: **el agente no dice "funciona", lo demuestra**.
> Toda feature termina con evidencia ejecutable, no con afirmaciones.

## Niveles de verificación

### Nivel 1 — Tests unitarios (obligatorio)

Toda función pública en `src/saas_radar/` tiene al menos un test en `tests/`
que:

1. Cubre el camino feliz.
2. Cubre al menos un camino de error si la función puede fallar.

Comando:
```bash
python -m pytest -q
```

Comando verbose para debug:
```bash
python -m pytest -v --tb=short
```

### Nivel 2 — Tests con servicios externos mockeados (obligatorio para
features de scraping, LLM, Telegram)

Las features que tocan servicios externos se verifican con mocks:

**LLM clients**:
```python
import httpx
from saas_radar.analysis.llm_clients import call_claude

def test_call_claude_returns_parsed_json(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"text": '{"a": 1}'}]})
    transport = httpx.MockTransport(handler)
    # ... inject transport into httpx.Client used by call_claude
    result = call_claude("prompt")
    assert result == {"a": 1}
```

**PRAW**:
```python
from unittest.mock import MagicMock
from saas_radar.scrapers.reddit_scraper import fetch_posts

def test_fetch_posts_dedups_by_id(monkeypatch):
    fake_post = MagicMock(id="abc", title="t", selftext="x", score=10, ...)
    fake_sub = MagicMock()
    fake_sub.hot.return_value = [fake_post, fake_post]  # mismo post 2 veces
    fake_reddit = MagicMock()
    fake_reddit.subreddit.return_value = fake_sub
    monkeypatch.setattr("saas_radar.scrapers.reddit_scraper.get_reddit", lambda: fake_reddit)
    df = fetch_posts("nocode", limit=10)
    assert len(df) == 1
```

**Telegram**:
```python
def test_send_opportunity_alert_skips_below_threshold(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    monkeypatch.setenv("TELEGRAM_ALERT_THRESHOLD", "8")
    sent = send_opportunity_alert({"priority_score": 5, ...})
    assert sent is False
```

### Nivel 3 — Tests de integración del CLI (obligatorio para features que
extienden `main.py`)

Las features que añaden flags / fases al CLI se verifican ejecutando el CLI
real contra una BD temporal:

```python
import subprocess, os

def test_main_skip_scrape_skip_ai_does_not_call_external(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path}/test.db")
    result = subprocess.run(
        ["python", "-m", "saas_radar.main", "--skip-scrape", "--skip-ai", "--skip-gtm"],
        capture_output=True, text=True, env={**os.environ},
    )
    assert result.returncode == 0
    assert "Scraping omitido" in result.stdout
    assert "Analisis IA omitido" in result.stdout
```

### Nivel 4 — Smoke test manual (opcional pero recomendado al cerrar M2 y M4)

Antes de cerrar una feature que cambia el pipeline observable, ejecutar un
flujo end-to-end real (consume tokens) sobre `data/saas.db` legacy:

```bash
# Re-síntesis usando extracciones cacheadas (no consume tokens de extracción)
python -m saas_radar.main --skip-scrape --use-cached-extractions --top-posts 20
```

Documentar el resultado en `progress/impl_<feature>.md`:
- Tokens consumidos (aprox).
- Nº de oportunidades generadas.
- Si pasaron `_validate_synthesis` o cuántas se descartaron.

## Anti-patrones (no hacer)

- ❌ "He añadido la función, debería funcionar." → falta test ejecutable.
- ❌ Test que solo verifica que la función no lanza excepción. → tiene que
  comprobar el resultado concreto.
- ❌ `mock` del filesystem. → usa `tempfile.TemporaryDirectory()` o
  `tmp_path` de pytest.
- ❌ Llamadas reales a LLM, PRAW, Telegram en tests. → mock siempre.
- ❌ Compartir `data/saas.db` real con los tests. → BD temporal por test.
- ❌ Marcar la feature como `done` sin pasar `./init.sh`.

## Verificación final antes de cerrar

```bash
./init.sh           # debe terminar con [OK] Entorno listo
```

`init.sh` valida:
1. Python >= 3.11.
2. Archivos base del arnés existen (incluidos los 4 de `legacy-context/`).
3. `feature_list.json` es válido (1 in_progress max, status válidos, deps
   coherentes).
4. `pyproject.toml` y `src/saas_radar/` existen (desde feature #1).
5. `pytest -q` pasa al 100%.
6. **No hay `sys.path.append`** en `src/` (anti-patrón legacy §2.4).

Si `./init.sh` está rojo, **no** marques nada como `done`. Anota el bloqueo
en `progress/current.md` con estado `blocked` en `feature_list.json`.

## Verificación de comportamiento heredado

Para features que portan un módulo del legacy, además del Nivel 1-3,
el reviewer debe comprobar:

- El comportamiento observable del módulo coincide con lo descrito en
  `docs/legacy-context/inventory.md` y `docs/legacy-context/architecture.md`.
- Cualquier diferencia respecto al legacy está justificada por
  `docs/legacy-context/lessons-learned.md` (sección "Lo que NO reproducir")
  o por una decisión nueva documentada en `progress/impl_<feature>.md`.
- Si la feature modifica el schema de la BD, una migración idempotente
  permite abrir `data/saas.db` heredada sin pérdida de datos.
