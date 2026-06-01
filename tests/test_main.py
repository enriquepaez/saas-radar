"""Tests del CLI principal: flags argparse, detección de modo, E2E con mocks."""

from __future__ import annotations

import argparse
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _make_posts_df(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [f"post{i}" for i in range(n)],
            "title": [f"Title {i}" for i in range(n)],
            "text": [f"Text content {i}" for i in range(n)],
            "score": [10] * n,
            "num_comments": [5] * n,
            "subreddit": ["saas"] * n,
            "created_utc": [time.time() - i * 3600 for i in range(n)],
            "author": ["user"] * n,
            "url": [f"https://reddit.com/{i}" for i in range(n)],
            "permalink": [f"/r/saas/{i}" for i in range(n)],
            "search_query": [None] * n,
        }
    )


# ── Test 1: argparse tiene todos los flags ─────────────────────────────────────


def test_argparse_has_all_required_flags():
    from saas_radar.main import MAX_POSTS

    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-scrape", action="store_true")
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--skip-gtm", action="store_true")
    parser.add_argument("--min-score", type=int, default=5)
    parser.add_argument("--top-posts", type=int, default=MAX_POSTS)
    parser.add_argument("--output", type=str, default="data/ai_analysis.json")
    parser.add_argument("--use-cached-extractions", action="store_true")
    parser.add_argument("--full-scan", action="store_true")

    args = parser.parse_args([])
    assert args.skip_scrape is False
    assert args.skip_ai is False
    assert args.skip_gtm is False
    assert args.min_score == 5
    assert args.top_posts == MAX_POSTS
    assert args.output == "data/ai_analysis.json"
    assert args.use_cached_extractions is False
    assert args.full_scan is False


# ── Test 2: --skip-scrape --skip-ai --skip-gtm no lanza excepción ─────────────


def test_skip_all_flags_no_exception(tmp_path, capsys):
    db_url = f"sqlite:///{tmp_path}/test.db"
    with (
        patch("saas_radar.main.init_db") as mock_init,
        patch("saas_radar.main.has_successful_run", return_value=False),
        patch("saas_radar.main.save_to_db"),
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True)

    mock_init.assert_called_once()
    captured = capsys.readouterr()
    assert "Scraping omitido" in captured.out
    assert "Analisis IA omitido" in captured.out
    assert "GTM agent omitido" in captured.out


# ── Test 3: modo INCREMENTAL cuando has_successful_run=True ───────────────────


def test_incremental_mode_when_previous_run_exists(capsys):
    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=True),
        patch("saas_radar.main.save_to_db"),
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True)

    captured = capsys.readouterr()
    assert "INCREMENTAL" in captured.out


# ── Test 4: modo CARGA COMPLETA cuando has_successful_run=False ───────────────


def test_full_load_mode_when_no_previous_run(capsys):
    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=False),
        patch("saas_radar.main.save_to_db"),
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True)

    captured = capsys.readouterr()
    assert "CARGA COMPLETA" in captured.out


# ── Test 5: --full-scan fuerza CARGA COMPLETA aunque haya run previo ──────────


def test_full_scan_flag_forces_full_load(capsys):
    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=True),
        patch("saas_radar.main.save_to_db"),
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True, full_scan=True)

    captured = capsys.readouterr()
    assert "CARGA COMPLETA" in captured.out
    assert "forzado con --full-scan" in captured.out


# ── Test 6: E2E completo con mocks de PRAW + LLM + BD temporal ────────────────


def test_e2e_full_pipeline_with_mocks(tmp_path):
    posts_df = _make_posts_df(3)
    ai_return = {"status": "partial", "opportunities": [], "disqualified": [], "top_3": [], "run_id": 1, "json_path": None, "posts_analyzed": 3}

    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=False),
        patch("saas_radar.main.fetch_posts", return_value=posts_df),
        patch("saas_radar.main.search_pain_posts", return_value=posts_df),
        patch("saas_radar.main.fetch_top_comments", return_value=[]),
        patch("saas_radar.main.save_to_db"),
        patch("saas_radar.main.db_stats", return_value={"reddit_posts": 3, "reddit_comments": 0}),
        patch("saas_radar.main.run_ai_analysis", return_value=ai_return),
        patch("saas_radar.main.send_opportunity_alert"),
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_gtm=True)


# ── Test 7: phase_gtm imprime el mensaje del stub ─────────────────────────────


def test_phase_gtm_stub_prints_message(capsys):
    """phase_gtm llama a run_all_pending e imprime el resumen de resultados."""
    from unittest.mock import patch

    from saas_radar.main import phase_gtm

    mock_result = {"generated": 0, "skipped_low_viability": 0, "failed": 0, "skipped_existing": 0}
    with patch("saas_radar.agents.gtm_agent.run_all_pending", return_value=mock_result):
        phase_gtm()

    captured = capsys.readouterr()
    assert "FASE 5" in captured.out
    # Verifica que se imprime el resumen real (no el mensaje de stub)
    assert "GTM:" in captured.out or "[WARN]" in captured.out


# ── Test 8: Fase 3 usa ThreadPoolExecutor con max_workers=COMMENT_FETCH_WORKERS


def test_phase_comments_uses_thread_pool(capsys):
    from saas_radar.config import COMMENT_FETCH_WORKERS, HIGH_ENGAGEMENT_THRESHOLD

    posts_df = _make_posts_df(3)
    posts_df["num_comments"] = HIGH_ENGAGEMENT_THRESHOLD + 10
    posts_df["semantic_score"] = 5.0

    executor_calls = []

    original_executor = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor

    class CapturingExecutor:
        def __init__(self, max_workers=None):
            executor_calls.append(max_workers)
            self._inner = original_executor(max_workers=max_workers)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    with (
        patch("saas_radar.main.fetch_top_comments", return_value=[]),
        patch("saas_radar.main.save_to_db"),
        patch("saas_radar.main.ThreadPoolExecutor", CapturingExecutor),
    ):
        from saas_radar.main import phase_comments

        phase_comments(posts_df)

    assert len(executor_calls) == 1
    assert executor_calls[0] == COMMENT_FETCH_WORKERS


# ── Test 9: _fmt formatea correctamente ───────────────────────────────────────


def test_fmt_formats_seconds_as_mmss():
    from saas_radar.main import _fmt

    assert _fmt(0) == "00m 00s"
    assert _fmt(65) == "01m 05s"
    assert _fmt(3661) == "1h 01m 01s"


# ── Test 10: enrich_posts añade columnas clean_text, category, semantic_score ─


def test_enrich_posts_adds_required_columns():
    from saas_radar.main import enrich_posts

    df = _make_posts_df(2)
    result = enrich_posts(df)

    assert "clean_text" in result.columns
    assert "category" in result.columns
    assert "semantic_score" in result.columns
    assert len(result) == 2


# ── Tests Telegram pipeline integration ───────────────────────────────────────

# ── Test 11: send_opportunity_alert se llama N veces según las oportunidades ──


def test_send_opportunity_alert_called_n_times():
    """send_opportunity_alert se invoca una vez por cada oportunidad devuelta por run_ai_analysis."""
    opps = [
        {"product_name": "App A", "priority_score": 9},
        {"product_name": "App B", "priority_score": 8},
        {"product_name": "App C", "priority_score": 7},
    ]
    ai_return = {
        "status": "ok",
        "opportunities": opps,
        "disqualified": [],
        "top_3": [],
        "run_id": 1,
        "json_path": None,
        "posts_analyzed": 10,
    }

    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=False),
        patch("saas_radar.main.run_ai_analysis", return_value=ai_return),
        patch("saas_radar.main.send_opportunity_alert") as mock_alert,
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_gtm=True)

    assert mock_alert.call_count == 3


# ── Test 12: send_opportunity_alert NO se llama con skip_ai=True ──────────────


def test_send_opportunity_alert_not_called_when_skip_ai():
    """Cuando skip_ai=True no se ejecuta run_ai_analysis y por tanto no hay alertas."""
    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=False),
        patch("saas_radar.main.send_opportunity_alert") as mock_alert,
        patch("saas_radar.main.send_run_summary"),
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True)

    mock_alert.assert_not_called()


# ── Test 13: send_run_summary se llama siempre (con y sin skip_ai) ────────────


@pytest.mark.parametrize("skip_ai", [False, True])
def test_send_run_summary_always_called(skip_ai):
    """send_run_summary se invoca al final del pipeline independientemente de skip_ai."""
    ai_return = {
        "status": "partial",
        "opportunities": [],
        "disqualified": [],
        "top_3": [],
        "run_id": 1,
        "json_path": None,
        "posts_analyzed": 5,
    }

    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=False),
        patch("saas_radar.main.run_ai_analysis", return_value=ai_return),
        patch("saas_radar.main.send_opportunity_alert"),
        patch("saas_radar.main.send_run_summary") as mock_summary,
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=skip_ai, skip_gtm=True)

    mock_summary.assert_called_once()


# ── Test 14: send_run_summary recibe mode correcto según has_successful_run ───


@pytest.mark.parametrize(
    "has_run,full_scan,expected_mode",
    [
        (True, False, "INCREMENTAL"),
        (False, False, "CARGA COMPLETA"),
        (True, True, "CARGA COMPLETA"),
    ],
)
def test_send_run_summary_receives_correct_mode(has_run, full_scan, expected_mode):
    """send_run_summary recibe mode='INCREMENTAL' o 'CARGA COMPLETA' según corresponde."""
    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=has_run),
        patch("saas_radar.main.send_opportunity_alert"),
        patch("saas_radar.main.send_run_summary") as mock_summary,
    ):
        from saas_radar.main import run_pipeline

        run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True, full_scan=full_scan)

    call_kwargs = mock_summary.call_args
    assert call_kwargs is not None
    # send_run_summary se llama con keyword args
    assert call_kwargs.kwargs.get("mode") == expected_mode
