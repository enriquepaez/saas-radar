"""Tests para src/saas_radar/analysis/extraction.py."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

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
