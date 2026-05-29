"""Clasificación de posts de Reddit en 6 categorías por keywords."""

from __future__ import annotations

from saas_radar.analysis.text_cleaning import normalize_for_classifier

# ── Constantes de módulo ──────────────────────────────────────────────────────
# Las listas son constantes de módulo (UPPER_SNAKE), calculadas UNA vez al
# importar. No se recalculan en cada llamada a classify_post.

# Palabras/frases que indican un dolor explícito del autor.
# Replicadas del legacy con adiciones que cubren el vocabulario de pain_point.
PAIN_KEYWORDS: list[str] = [
    "i hate",
    "hate",
    "pain",
    "painful",
    "annoying",
    "manual process",
    "doesn't work",
    "broken",
    "frustrated",
    "frustrating",
    "struggling with",
    "wish there was",
    "nobody solves",
    "no tool",
    "can't find",
    "nightmare",
    "tedious",
    "waste time",
    "wasting time",
    "manually",
    "manual",
]

# Palabras/frases que indican que el autor presenta algo que construyó.
# Incluye prefijos de SHOWCASE_TITLE_PREFIXES + keywords del legacy.
SHOWCASE_KEYWORDS: list[str] = [
    "i built",
    "i made",
    "i created",
    "i launched",
    "i shipped",
    "we launched",
    "we built",
    "we shipped",
    "we made",
    "show hn",
    "my project",
    "launched",
    "shipped",
    "built",
    "made",
    "$",
    "mrr",
    "revenue",
    "users",
    "first paying",
    "growth",
    "hit",
]

# Palabras/frases que indican un estado emocional negativo pero no un dolor
# operacional concreto → se mapean a 'discussion' en el clasificador nuevo
# (el legacy las clasificaba como 'emotional', categoría eliminada en esta versión).
EMOTIONAL_KEYWORDS: list[str] = [
    "burned out",
    "burnt out",
    "lonely",
    "depressed",
    "hard",
    "this is tough",
    "struggling mentally",
    "giving up",
]

# Palabras/frases que indican un flujo de trabajo, proceso o gestión operacional.
# Si aparecen en un post con pregunta → question_operational.
OPERATIONAL_KEYWORDS: list[str] = [
    "handle",
    "manage",
    "automate",
    "track",
    "deal with",
    "monitor",
    "refund",
    "onboarding",
    "billing",
    "pricing",
    "feedback",
    "support",
    "churn",
    "retention",
    "workflow",
    "process",
    "invoice",
    "invoices",
    "scheduling",
    "schedule",
    "inventory",
    "contracts",
    "clients",
]

# Palabras que indican que el post es una pregunta.
# La presencia de "?" o de cualquiera de estas palabras al inicio dispara
# la rama de preguntas en el clasificador.
_QUESTION_WORDS: list[str] = ["how", "what", "why", "anyone", "does anyone"]

# Palabras/frases que distinguen preguntas técnicas (setup, configuración, código)
# de preguntas operacionales.
_TECHNICAL_KEYWORDS: list[str] = [
    "how to",
    "best way to",
    "anyone know",
    "tutorial",
    "setup",
    "configure",
    "install",
    "integrate",
    "api",
    "code",
    "script",
    "debug",
    "deploy",
]

# Orden de prioridad del clasificador (de más a menos específico):
# showcase > pain_point > question_operational > question_technical > discussion > other
#
# La prioridad es explícita: en caso de empate de señales, el primer score > 0
# en este orden gana. Esto replica el comportamiento del legacy (max() no es
# suficiente cuando hay empates) y lo hace determinista.
_PRIORITY: list[str] = [
    "showcase",
    "pain_point",
    "question_operational",
    "question_technical",
    "discussion",
    "other",
]


def classify_post(title: str, text: str) -> str:
    """Clasifica un post de Reddit en una de 6 categorías.

    Categorías (en orden de prioridad):
    - 'showcase': el autor presenta algo que construyó.
    - 'pain_point': dolor explícito del autor.
    - 'question_operational': pregunta sobre un flujo de trabajo o proceso.
    - 'question_technical': pregunta técnica (setup, código, configuración).
    - 'discussion': debate general, estado emocional o conversación sin dolor claro.
    - 'other': texto vacío o sin señal.

    El clasificador normaliza el texto con normalize_for_classifier (lowercase,
    sin URLs, pero conservando puntuación y stopwords), luego aplica reglas de
    keyword matching en el orden de prioridad declarado arriba.

    Args:
        title: Título del post. Puede ser "" o None.
        text: Cuerpo del post. Puede ser "" o None.

    Returns:
        Una de las 6 cadenas de categoría. Nunca None ni vacío.
    """
    # Guard: si ambos son vacíos o no son strings, devolver "other" directamente.
    title_str = title if isinstance(title, str) else ""
    text_str = text if isinstance(text, str) else ""

    if not title_str.strip() and not text_str.strip():
        return "other"

    # Concatenar título + body y normalizar: lowercase sin URLs.
    # normalize_for_classifier NO elimina "?", "$", "how", etc. — el clasificador
    # los necesita para detectar preguntas y showcases de revenue.
    full = normalize_for_classifier(f"{title_str} {text_str}")

    # Diccionario de puntuación por categoría. Se inicializa a 0 para todas.
    scores: dict[str, int] = {cat: 0 for cat in _PRIORITY}

    # ── Showcase: palabras de mayor especificidad primero ─────────────────────
    # Las frases multi-palabra ("i built", "i made") se evalúan antes que las
    # mono-palabra ("built", "made") porque el clasificador usa `in`, que hace
    # substring match. Si el texto tiene "i built", lo capturan ambas; pero
    # las frases multi-word dan una señal más fuerte (+2 vs +1).
    for k in SHOWCASE_KEYWORDS:
        if k in full:
            # Frases multi-palabra dan +2, mono-palabra +1.
            scores["showcase"] += 2 if " " in k else 1

    # ── Pain point ────────────────────────────────────────────────────────────
    for k in PAIN_KEYWORDS:
        if k in full:
            scores["pain_point"] += 2

    # ── Detección de preguntas ────────────────────────────────────────────────
    # Un post es "pregunta" si tiene "?" O si empieza con una de las _QUESTION_WORDS.
    # full.startswith(w) comprueba el texto completo (título ya prefijado).
    is_question = "?" in full or any(full.startswith(w) for w in _QUESTION_WORDS)

    if is_question:
        # Contar keywords técnicas para decidir entre question_technical y
        # question_operational.
        tech_score = sum(1 for k in _TECHNICAL_KEYWORDS if k in full)
        op_score = sum(1 for k in OPERATIONAL_KEYWORDS if k in full)

        if op_score >= 1:
            # Con al menos 1 keyword operacional → question_operational.
            # El umbral es 1 (vs 2 del legacy) para ser más sensible al vocabulario
            # operacional que el pipeline necesita detectar (PAIN_CATEGORIES incluye
            # question_operational). Con >=2 keywords técnicas, la técnica gana.
            if tech_score >= 2 and tech_score > op_score:
                scores["question_technical"] += 3
            else:
                scores["question_operational"] += 3
        elif tech_score >= 1:
            scores["question_technical"] += 3
        else:
            # Pregunta sin keywords operacionales ni técnicas → discussion
            # (equivalente al "question_vague" del legacy, mapeado a discussion).
            scores["discussion"] += 2

    # ── Emotional / discussion ─────────────────────────────────────────────────
    # Keywords emocionales sin dolor operacional → discussion (no pain_point).
    for k in EMOTIONAL_KEYWORDS:
        if k in full:
            scores["discussion"] += 2

    # ── Selección por prioridad ───────────────────────────────────────────────
    # Recorrer el orden de prioridad explícito. La primera categoría con score > 0
    # gana. Esto evita ambigüedad cuando múltiples categorías tienen el mismo score
    # (p.ej. un post con "i built" y "frustrated" → showcase gana sobre pain_point).
    for cat in _PRIORITY:
        if scores[cat] > 0:
            return cat

    return "other"
