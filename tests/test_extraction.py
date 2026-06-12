"""Tests para src/saas_radar/analysis/extraction.py."""
from __future__ import annotations

import logging
from unittest.mock import patch

import pandas as pd

from saas_radar import config
from saas_radar.analysis.extraction import (
    CIRCUIT_BREAKER_THRESHOLD,
    _clean_extractions,
    _drop_non_saas,
    _drop_who_vago,
    _fix_payment_signal,
    _fix_workaround,
    extract_problem_deep,
    extract_problem_from_post,
    extract_problems_batch,
    run_batch_extraction,
)


def _make_row(**kwargs) -> pd.Series:
    defaults = {
        "id": "post_abc",
        "title": "Test title",
        "text": "Some text about a workflow problem",
        "subreddit": "entrepreneur",
        "score": 42,
        "num_comments": 10,
        "url": "https://reddit.com/r/entrepreneur/comments/post_abc",
        "source": "post",
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


# ── extract_problem_from_post ─────────────────────────────────────────────────


def test_extract_problem_from_post_ok():
    llm_response = {
        "has_problem": True,
        "who_has_it": "freelance accountant",
        "problem_description": "Manually reconciles invoices every month",
        "workflow_context": "end of month reconciliation",
        "current_workaround": "Excel spreadsheet",
        "payment_signal": False,
        "payment_quote": "",
        "competitor_mentions": [],
        "key_quote": "I spend 3 hours every month on this",
    }
    row = _make_row()
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response) as mock_llm:
        result = extract_problem_from_post(row, ["Great post", "I have the same issue"])

    mock_llm.assert_called_once()
    assert result["has_problem"] is True
    assert result["_post_id"] == "post_abc"
    assert result["_subreddit"] == "entrepreneur"
    assert result["_title"] == "Test title"
    assert result["_score"] == 42
    assert result["_num_comments"] == 10
    assert result["_url"] == "https://reddit.com/r/entrepreneur/comments/post_abc"


def test_extract_problem_from_post_llm_none():
    row = _make_row()
    with patch("saas_radar.analysis.extraction.call_llm", return_value=None):
        result = extract_problem_from_post(row, [])

    assert result["has_problem"] is False
    assert result["_title"] == "Test title"
    assert result["_subreddit"] == "entrepreneur"


# ── extract_problem_deep ──────────────────────────────────────────────────────


def test_extract_problem_deep_ok():
    llm_response = {
        "has_problem": True,
        "who_has_it": "solo bookkeeper",
        "problem_description": "Spends hours reconciling bank statements manually",
        "workflow_context": "monthly close",
        "current_workaround": "Excel",
        "payment_signal": False,
        "payment_quote": "",
        "competitor_mentions": ["QuickBooks"],
        "key_quote": "I wish there was a better way",
        "comment_signals": "Others confirm same pain",
        "estimated_frequency": "monthly",
        "tam_clues": "thousands of freelancers",
    }
    row = _make_row(id="deep_post")
    with patch("saas_radar.analysis.extraction._fetch_comments_for_post", return_value=["Comment A", "Comment B"]):
        with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response):
            result = extract_problem_deep(row)

    assert result["_deep"] is True
    assert result["_post_id"] == "deep_post"
    assert result["_subreddit"] == "entrepreneur"
    assert result["_title"] == "Test title"
    assert result["has_problem"] is True
    assert "_error" not in result


def test_extract_problem_deep_llm_none():
    row = _make_row(id="deep_err")
    with patch("saas_radar.analysis.extraction._fetch_comments_for_post", return_value=[]):
        with patch("saas_radar.analysis.extraction.call_llm", return_value=None):
            result = extract_problem_deep(row)

    assert result["has_problem"] is False
    assert result["_error"] is True
    assert result["_title"] == "Test title"
    assert result["_subreddit"] == "entrepreneur"


# ── extract_problems_batch ────────────────────────────────────────────────────


def test_extract_problems_batch_ok():
    llm_response = {
        "results": [
            {
                "post_index": 1,
                "has_problem": True,
                "who_has_it": "freelance bookkeeper",
                "problem_description": "Invoicing is painful",
                "workflow_context": "monthly invoicing",
                "current_workaround": "spreadsheets",
                "payment_signal": False,
                "payment_quote": "",
                "competitor_mentions": [],
                "key_quote": "It takes forever",
            }
        ]
    }
    rows = [_make_row()]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response):
        results = extract_problems_batch(rows)

    assert len(results) == 1
    assert results[0]["has_problem"] is True
    assert results[0]["_post_id"] == "post_abc"
    assert results[0]["_subreddit"] == "entrepreneur"
    assert results[0]["_title"] == "Test title"
    assert results[0]["_score"] == 42


def test_extract_problems_batch_partial_results():
    llm_response = {"results": []}
    rows = [_make_row(id="p1"), _make_row(id="p2")]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response):
        results = extract_problems_batch(rows)

    assert len(results) == 2
    assert all(r["has_problem"] is False for r in results)


def test_extract_problems_batch_llm_none():
    rows = [_make_row(id="p1"), _make_row(id="p2")]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=None):
        results = extract_problems_batch(rows)

    assert len(results) == 2
    assert all(r.get("_error") is True for r in results)


# ── _drop_who_vago ────────────────────────────────────────────────────────────


def test_drop_who_vago():
    valid_ex = {
        "has_problem": True,
        "who_has_it": "freelance bookkeeper who invoices via email",
        "problem_description": "manual reconciliation",
        "current_workaround": "Excel",
    }
    vague_ex = {
        "has_problem": True,
        "who_has_it": "people",
        "problem_description": "some problem",
        "current_workaround": "nothing",
    }
    result, dropped = _drop_who_vago([valid_ex, vague_ex])

    assert len(result) == 1
    assert dropped == 1
    assert result[0]["who_has_it"] == "freelance bookkeeper who invoices via email"


# ── _drop_non_saas ────────────────────────────────────────────────────────────


def test_drop_non_saas():
    physical_pain_ex = {
        "has_problem": True,
        "who_has_it": "office worker",
        "problem_description": "I feel burnout and loneliness every day",
        "workflow_context": "daily work",
        "key_quote": "",
    }
    rescued_ex = {
        "has_problem": True,
        "who_has_it": "accountant",
        "problem_description": "burnout using excel every day for reconciliation",
        "workflow_context": "excel workflow",
        "key_quote": "",
    }
    result, dropped = _drop_non_saas([physical_pain_ex, rescued_ex])

    assert dropped == 1
    assert len(result) == 1
    assert result[0]["who_has_it"] == "accountant"


# ── _fix_workaround ───────────────────────────────────────────────────────────


def test_fix_workaround_inference():
    ex = {
        "has_problem": True,
        "who_has_it": "small business owner",
        "problem_description": "I use spreadsheets manually to track everything",
        "workflow_context": "tracking",
        "key_quote": "",
        "current_workaround": "",
    }
    result, recovered, kept_no_wk = _fix_workaround([ex])

    assert len(result) == 1
    assert recovered == 1
    assert kept_no_wk == 0
    assert result[0]["current_workaround"] == "spreadsheets (inferred)"
    assert "_weak_workaround" not in result[0]


def test_fix_workaround_kept_as_weak():
    ex = {
        "has_problem": True,
        "who_has_it": "freelancer",
        "problem_description": "it is very painful and time consuming",
        "workflow_context": "project management",
        "key_quote": "",
        "current_workaround": "",
    }
    result, recovered, kept_no_wk = _fix_workaround([ex])

    assert len(result) == 1
    assert recovered == 0
    assert kept_no_wk == 1
    assert result[0]["_weak_workaround"] is True
    assert result[0]["current_workaround"] == "no explicit workaround mentioned"


# ── _fix_payment_signal ───────────────────────────────────────────────────────


def test_fix_payment_signal_cleared():
    ex = {
        "has_problem": True,
        "who_has_it": "SaaS founder",
        "problem_description": "paying too much for tools",
        "payment_signal": True,
        "payment_quote": "",
        "current_workaround": "Excel",
    }
    result = _fix_payment_signal([ex])

    assert len(result) == 1
    assert result[0]["payment_signal"] is False


# ── _clean_extractions (pipeline completo) ────────────────────────────────────


def test_clean_extractions_full_pipeline():
    valid_ex = {
        "has_problem": True,
        "who_has_it": "freelance bookkeeper",
        "problem_description": "manually reconciles invoices using excel every month",
        "workflow_context": "month-end close with excel spreadsheets",
        "key_quote": "I spend 4 hours on this every month",
        "current_workaround": "Excel spreadsheet",
        "payment_signal": False,
        "payment_quote": "",
    }
    no_problem_ex = {
        "has_problem": False,
        "who_has_it": "developer",
        "problem_description": "some vague thing",
        "workflow_context": "",
        "key_quote": "",
        "current_workaround": "",
    }
    vague_who_ex = {
        "has_problem": True,
        "who_has_it": "people",
        "problem_description": "people struggle with invoicing",
        "workflow_context": "general",
        "key_quote": "",
        "current_workaround": "nothing",
    }
    physical_pain_ex = {
        "has_problem": True,
        "who_has_it": "remote worker",
        "problem_description": "suffering from back pain and loneliness all day",
        "workflow_context": "remote work",
        "key_quote": "my back pain is unbearable",
        "current_workaround": "stretching",
    }

    result = _clean_extractions([valid_ex, no_problem_ex, vague_who_ex, physical_pain_ex])

    assert len(result) == 1
    assert result[0]["who_has_it"] == "freelance bookkeeper"


# ── run_batch_extraction (circuit breaker) ────────────────────────────────────


def test_circuit_breaker_fires():
    # 20 posts con batch_size=5 → 4 batches posibles.
    # Circuit breaker dispara tras CIRCUIT_BREAKER_THRESHOLD=3 batches con error consecutivos.
    # El 4to batch nunca se ejecuta.
    posts = [_make_row(id=f"p{i}") for i in range(20)]

    with patch("saas_radar.analysis.extraction.call_llm", return_value=None):
        results = run_batch_extraction(posts, batch_size=5)

    # Solo se procesaron 3 batches de 5 posts cada uno = 15 resultados
    assert len(results) == CIRCUIT_BREAKER_THRESHOLD * 5
    assert all(r.get("_error") is True for r in results)


# ── extract_problems (bifurcacion deep vs batch) ──────────────────────────────


def test_extract_problems_uses_deep_when_few_posts():
    """Con ≤30 posts, extract_problems llama extract_problem_deep una vez por post."""
    import pandas as pd
    from unittest.mock import patch, MagicMock
    from saas_radar.analysis.extraction import extract_problems

    rows = [pd.Series({"id": f"t3_{i}", "title": "t", "text": "x", "subreddit": "saas", "score": 1, "num_comments": 0, "url": ""}) for i in range(5)]

    with patch("saas_radar.analysis.extraction.extract_problem_deep", return_value={"has_problem": True}) as mock_deep, \
         patch("saas_radar.analysis.extraction.run_batch_extraction") as mock_batch:
        result = extract_problems(rows)

    assert mock_deep.call_count == 5
    mock_batch.assert_not_called()
    assert len(result) == 5


def test_extract_problems_uses_batch_when_many_posts():
    """Con >30 posts, extract_problems llama run_batch_extraction."""
    import pandas as pd
    from unittest.mock import patch
    from saas_radar.analysis.extraction import extract_problems, DEEP_EXTRACTION_THRESHOLD

    rows = [pd.Series({"id": f"t3_{i}", "title": "t", "text": "x", "subreddit": "saas", "score": 1, "num_comments": 0, "url": ""}) for i in range(DEEP_EXTRACTION_THRESHOLD + 1)]

    with patch("saas_radar.analysis.extraction.extract_problem_deep") as mock_deep, \
         patch("saas_radar.analysis.extraction.run_batch_extraction", return_value=[]) as mock_batch:
        extract_problems(rows)

    mock_batch.assert_called_once()
    mock_deep.assert_not_called()


# ── Propagación de provider ───────────────────────────────────────────────────


def test_extract_problem_from_post_passes_provider():
    """call_llm recibe el provider no-default cuando se especifica en extract_problem_from_post."""
    llm_response = {
        "has_problem": True,
        "who_has_it": "developer",
        "problem_description": "some pain",
        "workflow_context": "daily work",
        "current_workaround": "none",
        "payment_signal": False,
        "payment_quote": "",
        "competitor_mentions": [],
        "key_quote": "it hurts",
    }
    row = _make_row()
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response) as mock_llm:
        extract_problem_from_post(row, [], provider="gemini")

    _, kwargs = mock_llm.call_args
    assert kwargs.get("provider") == "gemini"


def test_extract_problem_deep_passes_provider():
    """call_llm recibe el provider no-default cuando se especifica en extract_problem_deep."""
    llm_response = {
        "has_problem": True,
        "who_has_it": "solo bookkeeper",
        "problem_description": "painful reconciliation",
        "workflow_context": "monthly close",
        "current_workaround": "Excel",
        "payment_signal": False,
        "payment_quote": "",
        "competitor_mentions": [],
        "key_quote": "takes hours",
        "comment_signals": "",
        "estimated_frequency": "monthly",
        "tam_clues": "",
    }
    row = _make_row(id="deep_provider")
    with patch("saas_radar.analysis.extraction._fetch_comments_for_post", return_value=[]):
        with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response) as mock_llm:
            extract_problem_deep(row, provider="gemini")

    _, kwargs = mock_llm.call_args
    assert kwargs.get("provider") == "gemini"


def test_extract_problems_batch_passes_provider():
    """call_llm recibe el provider no-default cuando se especifica en extract_problems_batch."""
    llm_response = {
        "results": [
            {
                "post_index": 1,
                "has_problem": True,
                "who_has_it": "freelancer",
                "problem_description": "invoicing pain",
                "workflow_context": "invoicing",
                "current_workaround": "spreadsheets",
                "payment_signal": False,
                "payment_quote": "",
                "competitor_mentions": [],
                "key_quote": "takes forever",
            }
        ]
    }
    rows = [_make_row()]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response) as mock_llm:
        extract_problems_batch(rows, provider="gemini")

    _, kwargs = mock_llm.call_args
    assert kwargs.get("provider") == "gemini"


# ── Serialización JSON con numpy.int64 ────────────────────────────────────────


def test_extract_problem_from_post_json_serializable_with_numpy_int64():
    """_score y _num_comments se convierten a int nativo: json.dumps no falla con numpy.int64."""
    import json
    import numpy as np

    llm_response = {
        "has_problem": True,
        "who_has_it": "accountant",
        "problem_description": "invoicing pain",
        "workflow_context": "month-end",
        "current_workaround": "Excel",
        "payment_signal": False,
        "payment_quote": "",
        "competitor_mentions": [],
        "key_quote": "takes forever",
    }
    row = _make_row(score=np.int64(55), num_comments=np.int64(7))
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response):
        result = extract_problem_from_post(row, [])

    assert result["_score"] == 55
    assert result["_num_comments"] == 7
    # El resultado debe ser serializable sin TypeError
    serialized = json.dumps(result)
    parsed = json.loads(serialized)
    assert parsed["_score"] == 55
    assert parsed["_num_comments"] == 7


def test_extract_problem_deep_json_serializable_with_numpy_int64():
    """extract_problem_deep convierte numpy.int64 a int nativo para evitar TypeError en json.dumps."""
    import json
    import numpy as np

    llm_response = {
        "has_problem": True,
        "who_has_it": "solo bookkeeper",
        "problem_description": "reconciliation pain",
        "workflow_context": "monthly close",
        "current_workaround": "Excel",
        "payment_signal": False,
        "payment_quote": "",
        "competitor_mentions": [],
        "key_quote": "takes hours",
        "comment_signals": "",
        "estimated_frequency": "monthly",
        "tam_clues": "",
    }
    row = _make_row(id="np_deep", score=np.int64(100), num_comments=np.int64(25))
    with patch("saas_radar.analysis.extraction._fetch_comments_for_post", return_value=[]):
        with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response):
            result = extract_problem_deep(row)

    assert result["_score"] == 100
    assert result["_num_comments"] == 25
    serialized = json.dumps(result)
    parsed = json.loads(serialized)
    assert parsed["_score"] == 100
    assert parsed["_num_comments"] == 25


def test_extract_problems_batch_json_serializable_with_numpy_int64():
    """extract_problems_batch convierte numpy.int64 a int nativo para evitar TypeError en json.dumps."""
    import json
    import numpy as np

    llm_response = {
        "results": [
            {
                "post_index": 1,
                "has_problem": True,
                "who_has_it": "bookkeeper",
                "problem_description": "invoicing pain",
                "workflow_context": "invoicing",
                "current_workaround": "spreadsheets",
                "payment_signal": False,
                "payment_quote": "",
                "competitor_mentions": [],
                "key_quote": "takes forever",
            }
        ]
    }
    rows = [_make_row(score=np.int64(77), num_comments=np.int64(3))]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=llm_response):
        results = extract_problems_batch(rows)

    assert len(results) == 1
    assert results[0]["_score"] == 77
    assert results[0]["_num_comments"] == 3
    serialized = json.dumps(results[0])
    parsed = json.loads(serialized)
    assert parsed["_score"] == 77
    assert parsed["_num_comments"] == 3


# ── Hardening (feature #23): logging defensivo + fallback de provider ─────────


def test_extract_problems_batch_logs_warning_when_call_llm_none(caplog):
    """call_llm devuelve None → WARNING con el provider y todos los items _error=True."""
    rows = [_make_row(id="p1"), _make_row(id="p2")]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=None):
        with caplog.at_level(logging.WARNING, logger="saas_radar.analysis.extraction"):
            results = extract_problems_batch(rows, provider="gemini")

    assert len(results) == 2
    assert all(r.get("_error") is True for r in results)
    assert any("provider=gemini" in rec.message for rec in caplog.records), caplog.text
    assert any("call_llm devolvió None" in rec.message for rec in caplog.records), caplog.text


def test_extract_problems_batch_logs_warning_when_results_key_missing(caplog):
    """call_llm devuelve dict válido pero SIN 'results' → WARNING con repr truncada + items _error=True."""
    malformed = {"foo": "bar", "baz": [1, 2, 3]}
    rows = [_make_row(id="p1"), _make_row(id="p2")]
    with patch("saas_radar.analysis.extraction.call_llm", return_value=malformed):
        with caplog.at_level(logging.WARNING, logger="saas_radar.analysis.extraction"):
            results = extract_problems_batch(rows, provider="gemini")

    assert len(results) == 2
    assert all(r.get("_error") is True for r in results)
    assert any("sin clave 'results'" in rec.message for rec in caplog.records), caplog.text
    # La repr del dict aparece truncada en el log para debug post-mortem.
    assert any("'foo'" in rec.message and "'bar'" in rec.message for rec in caplog.records), caplog.text


def test_run_batch_extraction_fallback_activates_when_circuit_breaker_fires_with_non_groq_provider(monkeypatch):
    """Fallback (gemini → groq): 3 batches de gemini devuelven None y disparan circuit breaker;
    el fallback con groq devuelve dict válido y produce extracciones válidas.

    El test verifica el caso exacto del audit del 30-may: provider="gemini" cae en circuit
    breaker, EXTRACTION_PROVIDER_FALLBACK="groq" → la función reintenta TODOS los posts con groq
    y devuelve resultados con _error=False y has_problem=True.
    """
    monkeypatch.setattr(config, "EXTRACTION_PROVIDER_FALLBACK", "groq")
    posts = [_make_row(id=f"p{i}") for i in range(15)]  # 3 batches de 5

    valid_llm_response = {
        "results": [
            {
                "post_index": i + 1,
                "has_problem": True,
                "who_has_it": "freelance bookkeeper",
                "problem_description": "invoicing pain",
                "workflow_context": "monthly invoicing",
                "current_workaround": "spreadsheets",
                "payment_signal": False,
                "payment_quote": "",
                "competitor_mentions": [],
                "key_quote": "takes forever",
            }
            for i in range(5)
        ]
    }

    def fake_call_llm(prompt, *, provider, **kwargs):  # noqa: ARG001
        if provider == "gemini":
            return None
        if provider == "groq":
            return valid_llm_response
        raise AssertionError(f"provider inesperado: {provider}")

    with patch("saas_radar.analysis.extraction.call_llm", side_effect=fake_call_llm):
        results = run_batch_extraction(posts, batch_size=5, provider="gemini")

    valid_extractions = [r for r in results if not r.get("_error") and r.get("has_problem")]
    # 15 posts → 3 batches con 5 resultados válidos cada uno tras el fallback.
    assert len(valid_extractions) > 0, f"valid_extractions debe ser > 0 tras fallback. results={results}"
    assert len(valid_extractions) == 15


def test_run_batch_extraction_fallback_disabled_when_env_empty(monkeypatch):
    """EXTRACTION_PROVIDER_FALLBACK='' desactiva el fallback: circuit breaker aborta como antes."""
    monkeypatch.setattr(config, "EXTRACTION_PROVIDER_FALLBACK", "")
    posts = [_make_row(id=f"p{i}") for i in range(15)]

    call_counts = {"gemini": 0, "groq": 0}

    def fake_call_llm(prompt, *, provider, **kwargs):  # noqa: ARG001
        call_counts[provider] = call_counts.get(provider, 0) + 1
        return None

    with patch("saas_radar.analysis.extraction.call_llm", side_effect=fake_call_llm):
        results = run_batch_extraction(posts, batch_size=5, provider="gemini")

    # Solo se llamó al provider original (gemini), nunca al fallback (groq).
    assert call_counts["gemini"] == CIRCUIT_BREAKER_THRESHOLD
    assert call_counts["groq"] == 0
    assert all(r.get("_error") is True for r in results)


def test_run_batch_extraction_no_fallback_when_provider_equals_fallback(monkeypatch):
    """Si provider == fallback ya, NO reintenta (evita bucle infinito)."""
    monkeypatch.setattr(config, "EXTRACTION_PROVIDER_FALLBACK", "groq")
    posts = [_make_row(id=f"p{i}") for i in range(15)]

    n_calls = {"n": 0}

    def fake_call_llm(prompt, *, provider, **kwargs):  # noqa: ARG001
        n_calls["n"] += 1
        return None

    with patch("saas_radar.analysis.extraction.call_llm", side_effect=fake_call_llm):
        run_batch_extraction(posts, batch_size=5, provider="groq")

    # Solo el pase original, sin retry.
    assert n_calls["n"] == CIRCUIT_BREAKER_THRESHOLD


def test_run_batch_extraction_fallback_also_fails_returns_fallback_results(monkeypatch):
    """Fallback también dispara circuit breaker → resultado contiene _error=True; no crash."""
    monkeypatch.setattr(config, "EXTRACTION_PROVIDER_FALLBACK", "groq")
    posts = [_make_row(id=f"p{i}") for i in range(15)]

    with patch("saas_radar.analysis.extraction.call_llm", return_value=None):
        results = run_batch_extraction(posts, batch_size=5, provider="gemini")

    # Tras fallback fallido, los resultados son del fallback: 3 batches con _error.
    assert len(results) == CIRCUIT_BREAKER_THRESHOLD * 5
    assert all(r.get("_error") is True for r in results)
