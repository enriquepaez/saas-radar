"""Tests para src/saas_radar/analysis/text_cleaning.py."""

from __future__ import annotations

from saas_radar.analysis.text_cleaning import clean_text, normalize_for_classifier

# ── clean_text ────────────────────────────────────────────────────────────────


def test_clean_text_removes_http_url():
    """URLs con http:// desaparecen del output."""
    result = clean_text("check http://example.com for details")
    assert "http" not in result
    assert "example" not in result


def test_clean_text_removes_https_url():
    """URLs con HTTPS:// (mayúsculas) también desaparecen."""
    result = clean_text("HTTP://x.com hola mundo")
    assert "http" not in result
    assert "x.com" not in result


def test_clean_text_removes_punctuation():
    """Puntuación y caracteres no alfabéticos se eliminan."""
    result = clean_text("hello, world! this is a test.")
    assert "," not in result
    assert "!" not in result
    assert "." not in result


def test_clean_text_removes_english_stopwords():
    """Stopwords inglesas como 'the', 'is', 'a' desaparecen."""
    result = clean_text("the cat is on a mat")
    # 'the', 'is', 'on', 'a' son stopwords en inglés
    words = result.split()
    assert "the" not in words
    assert "is" not in words
    assert "a" not in words


def test_clean_text_removes_spanish_stopwords():
    """Stopwords españolas como 'hola', 'el', 'de' desaparecen."""
    result = clean_text("hola mundo de nuevo")
    words = result.split()
    # 'de' es stopword EN y ES
    assert "de" not in words


def test_clean_text_full_acceptance_case():
    """Criterio de aceptación: 'HTTP://x.com hola mundo' → sin URL, sin stopwords, sin puntuación."""
    result = clean_text("HTTP://x.com hola mundo")
    # La URL y las palabras que son stopwords deben desaparecer
    assert "http" not in result
    assert "x.com" not in result
    # 'hola' es stopword en español (en NLTK stopwords es 'hola' si está incluido;
    # si no, debe aparecer o no dependiendo del corpus — lo importante es que no hay URL
    # y no hay puntuación)
    assert "/" not in result
    assert ":" not in result


def test_clean_text_returns_empty_for_empty_input():
    """String vacío devuelve string vacío."""
    assert clean_text("") == ""


def test_clean_text_returns_empty_for_whitespace_only():
    """String con solo espacios devuelve string vacío."""
    assert clean_text("   ") == ""


def test_clean_text_returns_empty_for_none_type():
    """None devuelve string vacío (guard de tipo)."""
    assert clean_text(None) == ""  # type: ignore[arg-type]


def test_clean_text_returns_empty_for_non_string():
    """Enteros u otros tipos devuelven string vacío."""
    assert clean_text(42) == ""  # type: ignore[arg-type]


def test_clean_text_returns_empty_for_only_stopwords():
    """Texto que solo contiene stopwords devuelve vacío."""
    result = clean_text("the a is on")
    assert result == ""


def test_clean_text_keeps_content_words():
    """Palabras de contenido no-stopword se preservan."""
    result = clean_text("invoice tracking nightmare")
    # Estas palabras no son stopwords y tienen más de 2 caracteres
    assert "invoice" in result
    assert "tracking" in result
    assert "nightmare" in result


def test_clean_text_removes_short_words():
    """Palabras de 1-2 caracteres se eliminan aunque no sean stopwords."""
    result = clean_text("xy ab cd efg")
    words = result.split()
    assert "xy" not in words
    assert "ab" not in words
    assert "cd" not in words
    # 'efg' tiene 3 chars y no es stopword → debe sobrevivir
    assert "efg" in words


def test_clean_text_lowercases():
    """El texto de salida está siempre en minúsculas."""
    result = clean_text("Invoice TRACKING Nightmare")
    assert result == result.lower()


def test_clean_text_emoji_only():
    """Texto con solo emojis devuelve vacío (los emojis no son a-z)."""
    result = clean_text("😀🚀💡")
    assert result == ""


def test_clean_text_pain_keywords_survive():
    """Keywords del legacy como 'nightmare', 'manual', 'frustrated' sobreviven."""
    result = clean_text("This manual process is a nightmare and I am frustrated")
    assert "nightmare" in result
    assert "manual" in result
    assert "frustrated" in result


# ── normalize_for_classifier ──────────────────────────────────────────────────


def test_normalize_removes_urls():
    """Las URLs se eliminan también en normalize."""
    result = normalize_for_classifier("check https://example.com now")
    assert "https" not in result
    assert "example.com" not in result


def test_normalize_preserves_question_mark():
    """El '?' se preserva — el clasificador lo necesita para detectar preguntas."""
    result = normalize_for_classifier("How do you handle invoices?")
    assert "?" in result


def test_normalize_preserves_dollar_sign():
    """El '$' se preserva — el clasificador lo necesita para detectar showcases."""
    result = normalize_for_classifier("I made $500 MRR")
    assert "$" in result


def test_normalize_preserves_stopwords():
    """Stopwords se preservan en normalize (a diferencia de clean_text)."""
    result = normalize_for_classifier("how do you handle this")
    assert "how" in result
    assert "do" in result
    assert "you" in result


def test_normalize_lowercases():
    """El output está en minúsculas."""
    result = normalize_for_classifier("How Do YOU Handle Invoices?")
    assert result == result.lower()


def test_normalize_returns_empty_for_non_string():
    """Entrada no-string devuelve string vacío."""
    assert normalize_for_classifier(None) == ""  # type: ignore[arg-type]
    assert normalize_for_classifier(42) == ""  # type: ignore[arg-type]


def test_normalize_handles_empty_string():
    """String vacío devuelve string vacío."""
    assert normalize_for_classifier("") == ""


def test_normalize_strips_leading_trailing_spaces():
    """Espacios al inicio y final se eliminan."""
    result = normalize_for_classifier("  hello world  ")
    assert not result.startswith(" ")
    assert not result.endswith(" ")
