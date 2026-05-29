"""Tests para saas_radar.analysis.pain_filter._semantic_score."""

from __future__ import annotations

import re

from saas_radar.analysis.pain_filter import _OFFTOPIC_PATTERN, _PAIN_PATTERNS, _semantic_score
from saas_radar.config import OFF_TOPIC_SIGNALS, PAIN_SIGNAL_PHRASES, SHOWCASE_TITLE_PREFIXES

# ── Showcase prefix ────────────────────────────────────────────────────────────


def test_semantic_score_returns_negative99_for_showcase_prefix():
    # "how i " está en SHOWCASE_TITLE_PREFIXES; el título empieza con ese prefijo.
    assert _semantic_score("how i built my saas", "long text about anything") == -99.0


def test_semantic_score_showcase_prefix_case_insensitive():
    # El prefijo se compara sobre title.lower().strip(), así que mayúsculas no importan.
    assert _semantic_score("HOW I built something", "some text") == -99.0


def test_semantic_score_showcase_i_built_prefix():
    # "i built" es otro prefijo real.
    assert _semantic_score("i built a new tool", "description here") == -99.0


def test_semantic_score_showcase_we_launched_prefix():
    assert _semantic_score("we launched our product today", "more text") == -99.0


# ── Off-topic ──────────────────────────────────────────────────────────────────


def test_semantic_score_returns_negative50_for_offtopic_signal():
    # "burned out" está en OFF_TOPIC_SIGNALS.
    assert _semantic_score("Real pain here", "I'm burned out and considering quitting") == -50.0


def test_semantic_score_returns_negative50_for_burnout_word():
    # "burnout" también está en OFF_TOPIC_SIGNALS.
    assert _semantic_score("Feeling terrible", "dealing with burnout at work") == -50.0


def test_semantic_score_returns_negative50_for_politics():
    # "politics" está en OFF_TOPIC_SIGNALS.
    assert _semantic_score("Office discussion", "we argue about politics all day") == -50.0


# ── Showcase tiene prioridad sobre off-topic ───────────────────────────────────


def test_semantic_score_showcase_beats_offtopic():
    # Cuando el título es showcase Y el contenido es off-topic, retorna -99 (showcase primero).
    title = "how i dealt with burnout"
    text = "I'm burned out and depressed"
    assert _semantic_score(title, text) == -99.0


# ── Pain phrases con bonus de título ──────────────────────────────────────────


def test_semantic_score_pain_phrase_with_title_bonus():
    # "track invoices" no está, pero "i use excel to" (peso +3) aparece en texto.
    # Si ponemos "i use excel to track invoices" en el title también:
    # combined match → +3, title match → +1.5, total = 4.5 >= 3.
    title = "i use excel to track invoices"
    text = "I use Excel to track invoices because there is no better tool"
    score = _semantic_score(title, text)
    assert score >= 3


def test_semantic_score_pain_phrase_title_bonus_additive():
    # "manually track" (peso +3): si está en title Y en text, score = 3 (combined) + 1.5 (bonus).
    title = "manually track everything in a spreadsheet"
    text = "I manually track all my invoices manually track expenses"
    score = _semantic_score(title, text)
    # "manually track" matchea en combined (+3) y en title_lc (+1.5). Mínimo 4.5.
    assert score >= 4.5


# ── Sin bonus (señal solo en texto, no en título) ─────────────────────────────


def test_semantic_score_pain_phrase_no_title_bonus():
    # La phrase aparece solo en el texto, no en el título → score exactamente el peso.
    # "copy paste" (peso +3).
    title = "Having trouble with my workflow"
    text = "I have to copy paste data from one system to another every day"
    score = _semantic_score(title, text)
    # Solo +3 por combined (sin bonus de título). Puede haber otras phrases; al menos +3.
    assert score >= 3


def test_semantic_score_pain_phrase_exact_weight_without_title():
    # "spreadsheet hell" (peso +3) aparece SOLO en texto. Título neutro.
    # Verificamos que el score sea al menos 3 y que no haya regresión a negativo.
    title = "Questions about workflow tools"
    text = "This is spreadsheet hell, I can't manage anymore"
    score = _semantic_score(title, text)
    assert score >= 3
    assert score > 0


# ── Texto vacío / None ─────────────────────────────────────────────────────────


def test_semantic_score_empty_strings():
    assert _semantic_score("", "") == 0.0


def test_semantic_score_none_values_dont_raise():
    # El código hace (title or "") y (text or ""), así que None no lanza excepción.
    result = _semantic_score(None, None)  # type: ignore[arg-type]
    assert result == 0.0


def test_semantic_score_none_title_with_text():
    # Título None pero texto neutro.
    result = _semantic_score(None, "some regular text without any signal")  # type: ignore[arg-type]
    assert result == 0.0


def test_semantic_score_empty_title_with_pain_in_text():
    # Título vacío, señal en texto → no lanza excepción y score > 0.
    result = _semantic_score("", "I manually track all my invoices in Excel")
    assert result > 0


# ── Patrones pre-compilados al nivel de módulo ────────────────────────────────


def test_pain_patterns_compiled_at_module_level():
    # _PAIN_PATTERNS debe tener la misma longitud que PAIN_SIGNAL_PHRASES
    # y contener objetos re.Pattern, no strings.
    assert len(_PAIN_PATTERNS) == len(PAIN_SIGNAL_PHRASES)
    for pattern, points in _PAIN_PATTERNS:
        assert isinstance(pattern, re.Pattern)
        assert isinstance(points, int)


def test_offtopic_pattern_compiled_at_module_level():
    assert isinstance(_OFFTOPIC_PATTERN, re.Pattern)


def test_pain_patterns_use_word_boundary_prefix():
    # Cada patrón debe empezar con \b para evitar matches parciales.
    for pattern, _ in _PAIN_PATTERNS:
        assert pattern.pattern.startswith(r"\b"), f"Patrón sin \\b inicial: {pattern.pattern!r}"


# ── Tests usando listas reales de config ──────────────────────────────────────


def test_all_showcase_prefixes_trigger_negative99():
    # Verifica que TODOS los prefijos del config disparan -99.
    neutral_text = "some irrelevant text without signals"
    for prefix in SHOWCASE_TITLE_PREFIXES:
        title = prefix + " something extra"
        score = _semantic_score(title, neutral_text)
        assert score == -99.0, f"Prefijo {prefix!r} no devolvió -99.0, devolvió {score}"


def test_all_offtopic_signals_trigger_negative50():
    # Verifica que TODAS las señales off-topic disparan -50.
    neutral_title = "A normal question about workflow"
    for signal in OFF_TOPIC_SIGNALS:
        text = f"some context {signal} more context"
        score = _semantic_score(neutral_title, text)
        assert score == -50.0, f"Signal {signal!r} no devolvió -50.0, devolvió {score}"


def test_pain_phrases_produce_positive_scores():
    # Al menos las 5 primeras pain phrases del config deben producir score positivo
    # cuando aparecen en el texto con un título neutro.
    neutral_title = "Question about workflow management"
    for phrase, expected_points in PAIN_SIGNAL_PHRASES[:5]:
        text = f"context {phrase} more context"
        score = _semantic_score(neutral_title, text)
        assert score >= expected_points, (
            f"Phrase {phrase!r} esperaba al menos {expected_points}, obtuvo {score}"
        )
