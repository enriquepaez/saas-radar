"""Pre-filtro semántico sobre texto de Reddit: scoring por señales de dolor de negocio real."""

from __future__ import annotations

import re

from saas_radar.config import OFF_TOPIC_SIGNALS, PAIN_SIGNAL_PHRASES, SHOWCASE_TITLE_PREFIXES

# \b al inicio de cada phrase evita matches en mitad de palabra (ej. "mishandled" no debe
# matchear "handle"). Los sufijos flexibles quedan cubiertos porque las phrases no terminan
# en word-boundary estricto: "manually enter" cubre "manually entered", "manually entering".
_PAIN_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\b" + re.escape(phrase), re.IGNORECASE), points)
    for phrase, points in PAIN_SIGNAL_PHRASES
]

# Un único patrón alternado para off-topic: más eficiente que iterar señal por señal.
# \b en ambos extremos porque estas señales son palabras/frases discretas (no prefijos).
_OFFTOPIC_PATTERN: re.Pattern[str] = re.compile(
    "|".join(r"\b" + re.escape(s) + r"\b" for s in OFF_TOPIC_SIGNALS),
    re.IGNORECASE,
)


def _semantic_score(title: str, text: str) -> float:
    """Puntúa un post según señales lingüísticas de dolor de negocio real.

    Orden de evaluación:
    1. Penalización dura (-99) si el título empieza por un prefijo showcase/consejo.
    2. Penalización media (-50) si el contenido contiene señales off-topic.
    3. Suma de puntos por phrases de dolor; bonus x0.5 si la señal aparece también en título.
    """
    combined = (title or "") + " " + (text or "")
    title_lc = (title or "").lower().strip()

    for prefix in SHOWCASE_TITLE_PREFIXES:
        if title_lc.startswith(prefix):
            return -99.0

    if _OFFTOPIC_PATTERN.search(combined):
        return -50.0

    score = 0.0
    for pattern, points in _PAIN_PATTERNS:
        if pattern.search(combined):
            score += points
            if pattern.search(title_lc):
                score += points * 0.5

    return score
