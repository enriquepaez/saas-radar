"""Tests para src/saas_radar/analysis/post_classifier.py."""

from __future__ import annotations

from saas_radar.analysis.post_classifier import (
    EMOTIONAL_KEYWORDS,
    OPERATIONAL_KEYWORDS,
    PAIN_KEYWORDS,
    SHOWCASE_KEYWORDS,
    classify_post,
)

# ── Criterios de aceptación del spec ─────────────────────────────────────────


def test_classify_showcase_from_acceptance():
    """'I built X' → 'showcase' (criterio de aceptación explícito)."""
    result = classify_post("I built X", "...")
    assert result == "showcase"


def test_classify_question_operational_from_acceptance():
    """'How do you handle invoices?' con 'manage track' → 'question_operational'."""
    result = classify_post("How do you handle invoices?", "manage track")
    assert result == "question_operational"


def test_classify_pain_point_from_acceptance():
    """'I hate manual process' → 'pain_point'."""
    result = classify_post("I hate manual process", "...")
    assert result == "pain_point"


def test_classify_empty_returns_other():
    """Título y body vacíos → 'other' (criterio de aceptación)."""
    result = classify_post("", "")
    assert result == "other"


# ── Las 6 categorías ──────────────────────────────────────────────────────────


def test_classify_showcase_with_launched():
    """Título con 'launched' → 'showcase'."""
    result = classify_post("I launched my SaaS today", "got my first users")
    assert result == "showcase"


def test_classify_showcase_with_mrr():
    """'MRR' en el texto → 'showcase'."""
    result = classify_post("First $500 MRR hit!", "so excited revenue keeps growing")
    assert result == "showcase"


def test_classify_showcase_with_show_hn():
    """'Show HN' prefix → 'showcase'."""
    result = classify_post("Show HN: my project is live", "built it in 2 weeks")
    assert result == "showcase"


def test_classify_showcase_i_made():
    """'I made' en el título → 'showcase'."""
    result = classify_post("I made a tool for freelancers", "works great")
    assert result == "showcase"


def test_classify_question_technical():
    """Pregunta con keywords técnicos → 'question_technical'."""
    result = classify_post("How to setup Zapier webhook?", "tutorial configure install api")
    assert result == "question_technical"


def test_classify_question_technical_best_way():
    """'Best way to' con keywords técnicas → 'question_technical'."""
    result = classify_post("Best way to integrate the API?", "configure script debug")
    assert result == "question_technical"


def test_classify_question_operational_handle():
    """'How do you handle' → 'question_operational'."""
    result = classify_post("How do you handle client onboarding?", "workflow process")
    assert result == "question_operational"


def test_classify_question_operational_manage():
    """'How do you manage' + operational keyword → 'question_operational'."""
    result = classify_post("How do you manage your invoices?", "billing track")
    assert result == "question_operational"


def test_classify_pain_point_nightmare():
    """'nightmare' → 'pain_point'."""
    result = classify_post("Invoice tracking nightmare", "this process is so painful")
    assert result == "pain_point"


def test_classify_pain_point_frustrated():
    """'frustrated' → 'pain_point'."""
    result = classify_post("So frustrated with manual process", "broken workflow")
    assert result == "pain_point"


def test_classify_pain_point_tedious():
    """'tedious' → 'pain_point'."""
    result = classify_post("This is so tedious", "manually doing this every day")
    assert result == "pain_point"


def test_classify_discussion_emotional():
    """Keywords emocionales sin dolor operacional → 'discussion'."""
    result = classify_post("Feeling burned out today", "this is tough and hard")
    assert result == "discussion"


def test_classify_discussion_burnt_out():
    """'burnt out' → 'discussion'."""
    result = classify_post("I am burnt out from freelancing", "giving up")
    assert result == "discussion"


def test_classify_other_empty():
    """Post vacío → 'other'."""
    assert classify_post("", "") == "other"


def test_classify_other_none_like():
    """Strings con solo espacios → 'other'."""
    assert classify_post("  ", "  ") == "other"


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_classify_emoji_only():
    """Solo emojis → 'other' (ninguna keyword activa)."""
    result = classify_post("😀🚀", "💡🎯")
    assert result == "other"


def test_classify_url_only():
    """Solo URLs → 'other' (se eliminan en normalize)."""
    result = classify_post("https://example.com", "http://test.org/path")
    assert result == "other"


def test_classify_showcase_beats_pain():
    """Showcase tiene prioridad sobre pain_point."""
    result = classify_post("I built a nightmare tracker", "pain frustrated broken")
    assert result == "showcase"


def test_classify_pain_beats_discussion():
    """Pain_point tiene prioridad sobre discussion."""
    result = classify_post("Burned out AND hate this manual process", "nightmare")
    assert result == "pain_point"


def test_classify_showcase_beats_question():
    """Showcase tiene prioridad sobre question_operational."""
    result = classify_post("I built a tool, how do you handle billing?", "invoice manage")
    assert result == "showcase"


def test_classify_question_without_operational_keyword():
    """Pregunta sin keywords operacionales → no es question_operational."""
    result = classify_post("What is the meaning of life?", "just wondering")
    # Puede ser 'discussion' o 'other', pero no 'question_operational'
    assert result not in ("question_operational",)


def test_classify_accepts_none_title():
    """None como título no crashea."""
    result = classify_post(None, "manual process nightmare")  # type: ignore[arg-type]
    assert result == "pain_point"


def test_classify_accepts_none_text():
    """None como body no crashea."""
    result = classify_post("I launched my SaaS", None)  # type: ignore[arg-type]
    assert result == "showcase"


def test_classify_accepts_both_none():
    """Ambos None → 'other'."""
    result = classify_post(None, None)  # type: ignore[arg-type]
    assert result == "other"


# ── Keyword lists del legacy están presentes ──────────────────────────────────


def test_pain_keywords_has_legacy_words():
    """PAIN_KEYWORDS contiene las palabras clave del legacy."""
    legacy_pain = ["i hate", "pain", "painful", "annoying", "manual process",
                   "doesn't work", "broken", "frustrated", "wish there was",
                   "no tool", "can't find"]
    for kw in legacy_pain:
        assert kw in PAIN_KEYWORDS, f"Legacy keyword '{kw}' missing from PAIN_KEYWORDS"


def test_showcase_keywords_has_legacy_words():
    """SHOWCASE_KEYWORDS contiene las palabras clave del legacy."""
    legacy_showcase = ["launched", "shipped", "built", "made", "$", "mrr",
                       "revenue", "users", "first paying", "growth", "hit"]
    for kw in legacy_showcase:
        assert kw in SHOWCASE_KEYWORDS, f"Legacy keyword '{kw}' missing from SHOWCASE_KEYWORDS"


def test_emotional_keywords_has_legacy_words():
    """EMOTIONAL_KEYWORDS contiene las palabras clave del legacy."""
    legacy_emotional = ["burned out", "burnt out", "lonely", "depressed",
                        "hard", "this is tough", "struggling mentally", "giving up"]
    for kw in legacy_emotional:
        assert kw in EMOTIONAL_KEYWORDS, f"Legacy keyword '{kw}' missing from EMOTIONAL_KEYWORDS"


def test_operational_keywords_has_legacy_words():
    """OPERATIONAL_KEYWORDS contiene las palabras clave del legacy."""
    legacy_operational = ["handle", "manage", "automate", "track", "deal with",
                          "monitor", "refund", "onboarding", "billing", "pricing",
                          "feedback", "support", "churn", "retention"]
    for kw in legacy_operational:
        assert kw in OPERATIONAL_KEYWORDS, f"Legacy keyword '{kw}' missing from OPERATIONAL_KEYWORDS"


def test_keyword_lists_are_lowercase():
    """Todas las listas de keywords están en minúsculas (normalize hace lowercase antes de comparar)."""
    for kw in PAIN_KEYWORDS:
        assert kw == kw.lower(), f"PAIN_KEYWORDS: '{kw}' no está en minúsculas"
    for kw in SHOWCASE_KEYWORDS:
        assert kw == kw.lower(), f"SHOWCASE_KEYWORDS: '{kw}' no está en minúsculas"
    for kw in EMOTIONAL_KEYWORDS:
        assert kw == kw.lower(), f"EMOTIONAL_KEYWORDS: '{kw}' no está en minúsculas"
    for kw in OPERATIONAL_KEYWORDS:
        assert kw == kw.lower(), f"OPERATIONAL_KEYWORDS: '{kw}' no está en minúsculas"
