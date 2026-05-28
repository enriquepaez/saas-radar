# CHECKPOINTS — Evaluación del estado final

> En sistemas multi-agente no se evalúa el camino, se evalúa el destino.
> Estos son los checkpoints objetivos que un juez (humano o IA) puede usar
> para decidir si el proyecto está sano.

## C1 — El arnés está completo

- [ ] Existen los 4 archivos base: `AGENTS.md`, `init.sh`,
      `feature_list.json`, `progress/current.md`.
- [ ] Existen los 3 docs del proyecto: `docs/architecture.md`,
      `docs/conventions.md`, `docs/verification.md`.
- [ ] Existe el contexto del legacy en `docs/legacy-context/` con sus 4
      ficheros (`inventory.md`, `architecture.md`, `lessons-learned.md`,
      `feature-backlog.md`).
- [ ] `./init.sh` termina con exit code 0.

## C2 — El estado es coherente

- [ ] Como mucho una feature en `in_progress` en `feature_list.json`.
- [ ] Toda feature `done` tiene tests asociados que pasan.
- [ ] Toda feature `done` tiene una entrada en `progress/history.md` con
      fecha, agente y plan ejecutado.
- [ ] `progress/current.md` está vacío o describe la sesión activa
      (no contiene basura de sesiones anteriores).
- [ ] Las dependencias declaradas en `feature_list.json` están respetadas:
      ninguna feature `done` depende de otra que esté `pending` o `blocked`.

## C3 — El código respeta la arquitectura

- [ ] `src/saas_radar/` solo contiene los módulos previstos en
      `docs/architecture.md` (capas: storage, scrapers, analysis, agents,
      helpers, notifications).
- [ ] **No** hay `sys.path.append` en ningún módulo (anti-patrón del legacy
      documentado en `docs/legacy-context/lessons-learned.md` §2.4).
- [ ] **No** se mutan globales de `config.py` en runtime (anti-patrón del
      legacy §2.5). Si una función necesita el provider, lo recibe como
      argumento.
- [ ] Las dependencias de `pyproject.toml` cubren TODOS los `import` no
      stdlib (no quedan dependencias transitivas sin declarar, anti-patrón
      legacy §2.2).
- [ ] Logging vía `logging.getLogger(__name__)` o vía el `print` decidido
      para CLI; no se mezclan los dos en el mismo módulo sin justificación.
- [ ] No hay `print()` sueltos para debug, ni `TODO` sin contexto.

## C4 — La verificación es real

- [ ] `tests/` tiene al menos un test por módulo público de `src/saas_radar/`.
- [ ] Los tests usan `tempfile.TemporaryDirectory()` o fixtures de pytest,
      no mocks del filesystem.
- [ ] Los tests que tocan LLM usan `httpx.MockTransport` o `respx`, NO hacen
      llamadas reales en CI.
- [ ] Los tests que tocan PRAW mockean el cliente, NO llaman a Reddit en CI.
- [ ] `python -m pytest -q` muestra > 0 tests y todos verdes.

## C5 — La BD heredada funciona

- [ ] `data/saas.db` existe (copiada del legacy).
- [ ] `init_db()` corre sobre la BD existente sin romper datos.
- [ ] Las consultas básicas (`SELECT COUNT(*) FROM reddit_posts` ≈ 19702,
      `... FROM opportunities` ≈ 10) devuelven valores coherentes.
- [ ] Si una feature añade una columna, lo hace con migración idempotente
      (patrón `PRAGMA table_info → ALTER TABLE ADD COLUMN IF MISSING`).

## C6 — La sesión se cerró bien

- [ ] No hay archivos sin trackear sospechosos (`*.tmp`, `__pycache__`
      fuera del `.gitignore`, `data/extractions_cache.json.failed.json`).
- [ ] `progress/history.md` tiene una entrada por la última sesión.
- [ ] La última feature trabajada está reflejada en su estado correcto
      (`done` si pasó review, `blocked` con razón si quedó bloqueada).

---

**Cómo usar este archivo:** un agente revisor (`.claude/agents/reviewer.md`)
recorre cada checkbox, marca `[x]` o `[ ]`, y rechaza el cierre de sesión
si quedan boxes vacíos en C1-C6 que sean aplicables a la feature en curso.

Notas:

- C5 solo aplica desde la feature #2 (que crea `db.py`).
- C3 "no `sys.path.append`" se verifica con `grep -r "sys.path.append" src/`
  (debe devolver vacío).
- C3 "deps en pyproject" se verifica con `pipdeptree --warn fail` o
  inspección manual del `pyproject.toml`.
