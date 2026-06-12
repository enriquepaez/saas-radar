"""Tests del módulo de configuración saas_radar.config."""

from __future__ import annotations

import importlib


def test_import_without_dotenv():
    """config se importa sin error aunque no exista .env."""
    import saas_radar.config  # noqa: F401


def test_ai_provider_default():
    """AI_PROVIDER tiene el valor por defecto 'claude' si no hay env var."""
    from saas_radar import config

    # Si el entorno no tiene AI_PROVIDER seteado, el default es 'claude'.
    # Usamos importlib.reload para garantizar que se lee el estado actual.
    importlib.reload(config)
    import os

    if "AI_PROVIDER" not in os.environ:
        assert config.AI_PROVIDER == "claude"


def test_ai_provider_env_override(monkeypatch):
    """monkeypatch de AI_PROVIDER se refleja tras reimportar el módulo."""
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    from saas_radar import config

    importlib.reload(config)
    assert config.AI_PROVIDER == "gemini"


def test_ai_provider_groq_override(monkeypatch):
    """AI_PROVIDER acepta 'groq' como valor válido."""
    monkeypatch.setenv("AI_PROVIDER", "groq")
    from saas_radar import config

    importlib.reload(config)
    assert config.AI_PROVIDER == "groq"


def test_groq_api_key_override(monkeypatch):
    """GROQ_API_KEY se lee correctamente desde el entorno."""
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key-123")
    from saas_radar import config

    importlib.reload(config)
    assert config.GROQ_API_KEY == "test-groq-key-123"


def test_anthropic_api_key_override(monkeypatch):
    """ANTHROPIC_API_KEY se lee correctamente desde el entorno."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    from saas_radar import config

    importlib.reload(config)
    assert config.ANTHROPIC_API_KEY == "sk-ant-test-key"


def test_gemini_api_key_override(monkeypatch):
    """GEMINI_API_KEY se lee correctamente desde el entorno."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTest")
    from saas_radar import config

    importlib.reload(config)
    assert config.GEMINI_API_KEY == "AIzaTest"


def test_db_url_default():
    """DB_URL tiene el default correcto."""
    from saas_radar import config

    importlib.reload(config)
    import os

    if "DB_URL" not in os.environ:
        assert config.DB_URL == "sqlite:///data/saas.db"


def test_pain_signal_phrases_is_list_of_tuples():
    """PAIN_SIGNAL_PHRASES es una lista de tuplas (str, int)."""
    from saas_radar import config

    assert isinstance(config.PAIN_SIGNAL_PHRASES, list)
    for item in config.PAIN_SIGNAL_PHRASES:
        assert isinstance(item, tuple), f"Esperaba tupla, got {type(item)}: {item}"
        assert len(item) == 2, f"Tupla debe tener 2 elementos: {item}"
        phrase, weight = item
        assert isinstance(phrase, str), f"El primer elemento debe ser str: {phrase}"
        assert isinstance(weight, int), f"El segundo elemento debe ser int: {weight}"


def test_pain_signal_phrases_min_length():
    """PAIN_SIGNAL_PHRASES tiene al menos 100 entradas."""
    from saas_radar import config

    assert len(config.PAIN_SIGNAL_PHRASES) >= 100, (
        f"Se esperaban >= 100 frases, hay {len(config.PAIN_SIGNAL_PHRASES)}"
    )


def test_pain_signal_phrases_weights_valid():
    """Todos los pesos de PAIN_SIGNAL_PHRASES son 1, 2 o 3."""
    from saas_radar import config

    for phrase, weight in config.PAIN_SIGNAL_PHRASES:
        assert weight in (1, 2, 3), f"Peso fuera de rango para '{phrase}': {weight}"


def test_subreddits_is_list_of_strings():
    """SUBREDDITS es una lista de strings."""
    from saas_radar import config

    assert isinstance(config.SUBREDDITS, list)
    for sub in config.SUBREDDITS:
        assert isinstance(sub, str), f"Esperaba str, got {type(sub)}: {sub}"


def test_subreddits_length():
    """SUBREDDITS contiene exactamente 38 subreddits (legacy real, inventory.md dice 36 pero el archivo tiene 38)."""
    from saas_radar import config

    assert len(config.SUBREDDITS) == 38, (
        f"Se esperaban 38 subreddits (conteo real del legacy), hay {len(config.SUBREDDITS)}"
    )


def test_subreddits_contains_known_entries():
    """SUBREDDITS incluye subreddits clave de todos los tiers."""
    from saas_radar import config

    # Tier A
    for sub in ["zapier", "nocode", "notion", "airtable"]:
        assert sub in config.SUBREDDITS, f"Falta subreddit tier A: {sub}"
    # Tier B (algunos tienen mayúsculas como en el legacy: "Accounting", "AmazonSeller", etc.)
    for sub in ["msp", "restaurantowners", "ecommerce"]:
        assert sub in config.SUBREDDITS, f"Falta subreddit tier B: {sub}"
    # "Accounting" aparece con mayúscula en el legacy
    assert any(s.lower() == "accounting" for s in config.SUBREDDITS), "Falta subreddit 'Accounting'"
    # Tier C
    for sub in ["microsaas", "saas"]:
        assert sub in config.SUBREDDITS, f"Falta subreddit tier C: {sub}"
    # Descubiertos
    for sub in ["legaladvice", "marketing", "productivity"]:
        assert sub in config.SUBREDDITS, f"Falta subreddit descubierto: {sub}"


def test_high_signal_subreddits_is_set():
    """HIGH_SIGNAL_SUBREDDITS es un set."""
    from saas_radar import config

    assert isinstance(config.HIGH_SIGNAL_SUBREDDITS, set)


def test_high_signal_subreddits_all_lowercase():
    """Todos los elementos de HIGH_SIGNAL_SUBREDDITS están en minúsculas."""
    from saas_radar import config

    for sub in config.HIGH_SIGNAL_SUBREDDITS:
        assert sub == sub.lower(), f"Subreddit HIGH_SIGNAL no está en minúsculas: {sub}"


def test_high_signal_subreddits_subset_of_subreddits():
    """Todos los elementos de HIGH_SIGNAL_SUBREDDITS están en SUBREDDITS (case-insensitive)."""
    from saas_radar import config

    subreddits_lower = {s.lower() for s in config.SUBREDDITS}
    for sub in config.HIGH_SIGNAL_SUBREDDITS:
        assert sub.lower() in subreddits_lower, (
            f"HIGH_SIGNAL_SUBREDDITS contiene '{sub}' que no está en SUBREDDITS"
        )


def test_indiehackers_in_high_signal_subreddits():
    """indiehackers fue promovido a HIGH_SIGNAL en F24 (tuner recurrence ≥3)."""
    from saas_radar import config

    assert "indiehackers" in config.HIGH_SIGNAL_SUBREDDITS, (
        "'indiehackers' debe estar en HIGH_SIGNAL_SUBREDDITS (añadido en F24)"
    )


def test_pain_search_queries_is_list_of_strings():
    """PAIN_SEARCH_QUERIES es una lista de strings."""
    from saas_radar import config

    assert isinstance(config.PAIN_SEARCH_QUERIES, list)
    for q in config.PAIN_SEARCH_QUERIES:
        assert isinstance(q, str), f"Esperaba str, got {type(q)}: {q}"


def test_pain_search_queries_min_length():
    """PAIN_SEARCH_QUERIES tiene al menos 10 entradas."""
    from saas_radar import config

    assert len(config.PAIN_SEARCH_QUERIES) >= 10, (
        f"Se esperaban >= 10 queries, hay {len(config.PAIN_SEARCH_QUERIES)}"
    )


def test_showcase_title_prefixes_is_list_of_strings():
    """SHOWCASE_TITLE_PREFIXES es una lista de strings."""
    from saas_radar import config

    assert isinstance(config.SHOWCASE_TITLE_PREFIXES, list)
    for prefix in config.SHOWCASE_TITLE_PREFIXES:
        assert isinstance(prefix, str), f"Esperaba str, got {type(prefix)}: {prefix}"


def test_showcase_title_prefixes_min_length():
    """SHOWCASE_TITLE_PREFIXES tiene al menos 5 entradas."""
    from saas_radar import config

    assert len(config.SHOWCASE_TITLE_PREFIXES) >= 5, (
        f"Se esperaban >= 5 prefijos, hay {len(config.SHOWCASE_TITLE_PREFIXES)}"
    )


def test_off_topic_signals_is_list_of_strings():
    """OFF_TOPIC_SIGNALS es una lista de strings."""
    from saas_radar import config

    assert isinstance(config.OFF_TOPIC_SIGNALS, list)
    for signal in config.OFF_TOPIC_SIGNALS:
        assert isinstance(signal, str), f"Esperaba str, got {type(signal)}: {signal}"


def test_off_topic_signals_min_length():
    """OFF_TOPIC_SIGNALS tiene al menos 3 entradas."""
    from saas_radar import config

    assert len(config.OFF_TOPIC_SIGNALS) >= 3, (
        f"Se esperaban >= 3 señales off-topic, hay {len(config.OFF_TOPIC_SIGNALS)}"
    )


def test_min_semantic_score_type_and_value():
    """MIN_SEMANTIC_SCORE es float o int con valor > 0."""
    from saas_radar import config

    assert isinstance(config.MIN_SEMANTIC_SCORE, (float, int)), (
        f"MIN_SEMANTIC_SCORE debe ser float o int, got {type(config.MIN_SEMANTIC_SCORE)}"
    )
    assert config.MIN_SEMANTIC_SCORE > 0, (
        f"MIN_SEMANTIC_SCORE debe ser > 0, got {config.MIN_SEMANTIC_SCORE}"
    )


def test_post_limit_type_and_value():
    """POST_LIMIT es int con valor > 0."""
    from saas_radar import config

    assert isinstance(config.POST_LIMIT, int), (
        f"POST_LIMIT debe ser int, got {type(config.POST_LIMIT)}"
    )
    assert config.POST_LIMIT > 0, f"POST_LIMIT debe ser > 0, got {config.POST_LIMIT}"


def test_scraping_constants_present():
    """Las constantes de scraping del legacy están presentes con los valores correctos."""
    from saas_radar import config

    assert config.POST_LIMIT == 100
    assert config.PAIN_SEARCH_LIMIT == 50
    assert config.COMMENT_MIN_LENGTH == 50
    assert config.HIGH_ENGAGEMENT_THRESHOLD == 100
    assert config.COMMENT_FETCH_WORKERS == 8
    assert config.COMMENT_TARGET_POSTS == 200


def test_ai_constants_present():
    """Las constantes IA del legacy están presentes con los valores correctos."""
    from saas_radar import config

    assert config.MAX_POSTS == 80
    assert config.TEXT_SNIPPET_LEN == 500
    assert config.MIN_SEMANTIC_SCORE == 1.0
    assert config.MAX_POST_AGE_DAYS == 365
    assert config.INCREMENTAL_POST_AGE_DAYS == 1
    assert config.CIRCUIT_BREAKER_THRESHOLD == 3


def test_pain_categories_present():
    """PAIN_CATEGORIES contiene las categorías esperadas."""
    from saas_radar import config

    assert isinstance(config.PAIN_CATEGORIES, list)
    assert "pain_point" in config.PAIN_CATEGORIES
    assert "question_operational" in config.PAIN_CATEGORIES


def test_posts_cap_constants():
    """POSTS_CAP_HIGH_SIGNAL y POSTS_CAP_DEFAULT tienen los valores actuales (F24: HIGH_SIGNAL subió a 15)."""
    from saas_radar import config

    assert config.POSTS_CAP_HIGH_SIGNAL == 15
    assert config.POSTS_CAP_DEFAULT == 4


def test_llm_api_urls_present():
    """Las URLs de API de los proveedores LLM están presentes."""
    from saas_radar import config

    assert config.ANTHROPIC_API_URL == "https://api.anthropic.com/v1/messages"
    assert "generativelanguage.googleapis.com" in config.GEMINI_API_URL
    assert "groq.com" in config.GROQ_API_URL


def test_claude_model_defaults():
    """Los modelos de Claude tienen los defaults correctos del legacy."""
    from saas_radar import config

    importlib.reload(config)
    import os

    if "CLAUDE_EXTRACTION_MODEL" not in os.environ:
        assert config.CLAUDE_EXTRACTION_MODEL == "claude-haiku-4-5-20251001"
    if "CLAUDE_SYNTHESIS_MODEL" not in os.environ:
        assert config.CLAUDE_SYNTHESIS_MODEL == "claude-sonnet-4-6"


def test_extraction_provider_empty_string_falls_back_to_groq(monkeypatch):
    """EXTRACTION_PROVIDER vacío ('') usa el default 'groq', igual que None."""
    monkeypatch.setenv("EXTRACTION_PROVIDER", "")
    from saas_radar import config

    importlib.reload(config)
    assert config.EXTRACTION_PROVIDER == "groq", (
        f"EXTRACTION_PROVIDER='' debe caer en 'groq', got '{config.EXTRACTION_PROVIDER}'"
    )


def test_extraction_provider_env_override(monkeypatch):
    """EXTRACTION_PROVIDER se lee correctamente cuando tiene valor no vacío."""
    monkeypatch.setenv("EXTRACTION_PROVIDER", "gemini")
    from saas_radar import config

    importlib.reload(config)
    assert config.EXTRACTION_PROVIDER == "gemini"


def test_ai_provider_empty_string_falls_back_to_claude(monkeypatch):
    """AI_PROVIDER vacío ('') usa el default 'claude', igual que None."""
    monkeypatch.setenv("AI_PROVIDER", "")
    from saas_radar import config

    importlib.reload(config)
    assert config.AI_PROVIDER == "claude", (
        f"AI_PROVIDER='' debe caer en 'claude', got '{config.AI_PROVIDER}'"
    )


def test_no_print_on_import(capsys):
    """El módulo no produce output en stdout/stderr al importarse."""
    import saas_radar.config  # noqa: F401

    importlib.reload(saas_radar.config)
    captured = capsys.readouterr()
    assert captured.out == "", f"stdout no debería tener output al importar config, got: {captured.out!r}"
    assert captured.err == "", f"stderr no debería tener output al importar config, got: {captured.err!r}"
