"""Tests del scraper de Reddit con PRAW completamente mockeado."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import saas_radar.scrapers.reddit_scraper as scraper_module
from saas_radar.config import COMMENT_MIN_LENGTH, SUBREDDITS
from saas_radar.scrapers.reddit_scraper import (
    fetch_posts,
    fetch_top_comments,
    get_reddit,
    search_pain_posts,
)

# ── Fixture compartida ─────────────────────────────────────────────────────────


def make_mock_post(post_id: str, body: str | None = None) -> MagicMock:
    """Construye un MagicMock con los atributos mínimos de un Submission."""
    post = MagicMock()
    post.id = post_id
    post.title = f"Title {post_id}"
    post.selftext = body if body is not None else f"Body of {post_id}"
    post.score = 10
    post.upvote_ratio = 0.95
    post.num_comments = 5
    post.created_utc = 1700000000.0
    post.url = f"https://reddit.com/{post_id}"
    post.link_flair_text = None
    post.subreddit.display_name = "SaaS"
    return post


@pytest.fixture(autouse=True)
def reset_reddit_singleton():
    """Resetea el singleton _reddit antes y después de cada test."""
    scraper_module._reddit = None
    yield
    scraper_module._reddit = None


# ── Tests del singleton ────────────────────────────────────────────────────────


def test_get_reddit_singleton():
    """get_reddit() debe devolver la misma instancia en llamadas sucesivas."""
    with patch("saas_radar.scrapers.reddit_scraper.praw.Reddit") as mock_reddit_cls:
        mock_instance = MagicMock()
        mock_reddit_cls.return_value = mock_instance

        r1 = get_reddit()
        r2 = get_reddit()

        assert r1 is r2
        assert mock_reddit_cls.call_count == 1


# ── Tests de fetch_posts ───────────────────────────────────────────────────────


def test_fetch_posts_full_mode_feeds():
    """En modo full, se deben llamar hot, new, top('month') y top('year')."""
    post_a = make_mock_post("aaa")
    post_b = make_mock_post("bbb")
    post_c = make_mock_post("ccc")
    post_d = make_mock_post("ddd")

    mock_sub = MagicMock()
    mock_sub.hot.return_value = [post_a]
    mock_sub.new.return_value = [post_b]
    mock_sub.top.side_effect = lambda period, limit: [post_c] if period == "month" else [post_d]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    df = fetch_posts("saas", incremental=False)

    mock_sub.hot.assert_called_once()
    mock_sub.new.assert_called_once()
    top_calls = [call[0][0] for call in mock_sub.top.call_args_list]
    assert "month" in top_calls
    assert "year" in top_calls
    assert len(df) == 4


def test_fetch_posts_incremental_mode_feeds():
    """En modo incremental, se deben llamar new, hot y top('day'). No month ni year."""
    post_a = make_mock_post("aaa")
    post_b = make_mock_post("bbb")
    post_c = make_mock_post("ccc")

    mock_sub = MagicMock()
    mock_sub.new.return_value = [post_a]
    mock_sub.hot.return_value = [post_b]
    mock_sub.top.return_value = [post_c]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    df = fetch_posts("saas", incremental=True)

    mock_sub.new.assert_called_once()
    mock_sub.hot.assert_called_once()
    top_calls = [call[0][0] for call in mock_sub.top.call_args_list]
    assert "day" in top_calls
    assert "month" not in top_calls
    assert "year" not in top_calls
    assert len(df) == 3


def test_fetch_posts_dedup():
    """Un post con el mismo id en dos feeds solo aparece una vez en el DataFrame."""
    post_dup = make_mock_post("dup")
    post_unique = make_mock_post("unique")

    mock_sub = MagicMock()
    mock_sub.hot.return_value = [post_dup]
    mock_sub.new.return_value = [post_dup, post_unique]
    mock_sub.top.return_value = []

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    df = fetch_posts("saas", incremental=False)

    assert len(df) == 2
    assert df["id"].tolist().count("dup") == 1


def test_fetch_posts_returns_empty_dataframe_when_no_posts():
    """Si todos los feeds están vacíos, debe devolver un DataFrame vacío sin error."""
    mock_sub = MagicMock()
    mock_sub.hot.return_value = []
    mock_sub.new.return_value = []
    mock_sub.top.return_value = []

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    df = fetch_posts("saas", incremental=False)

    assert df.empty


# ── Tests de search_pain_posts ─────────────────────────────────────────────────


def test_search_pain_posts_uses_multireddit():
    """search_pain_posts debe llamar a reddit.subreddit con el multireddit '+'.join(SUBREDDITS)."""
    post_a = make_mock_post("aaa")

    mock_sub = MagicMock()
    mock_sub.search.return_value = [post_a]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    with patch("saas_radar.scrapers.reddit_scraper.time.sleep"):
        df = search_pain_posts("invoice automation")

    expected_multireddit = "+".join(SUBREDDITS)
    mock_reddit.subreddit.assert_called_once_with(expected_multireddit)
    call_kwargs = mock_sub.search.call_args
    assert call_kwargs[0][0] == "invoice automation"
    assert len(df) == 1


def test_search_pain_posts_incremental_adds_time_filter():
    """En modo incremental, .search debe recibir time_filter='day'."""
    mock_sub = MagicMock()
    mock_sub.search.return_value = []

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    search_pain_posts("query", incremental=True)

    call_kwargs = mock_sub.search.call_args[1]
    assert call_kwargs.get("time_filter") == "day"


# ── Tests de fetch_top_comments ────────────────────────────────────────────────


def test_fetch_top_comments_filters_short():
    """Comentarios con len(body) < COMMENT_MIN_LENGTH no deben aparecer en el resultado."""
    short_comment = MagicMock()
    short_comment.body = "x" * (COMMENT_MIN_LENGTH - 1)
    short_comment.id = "short1"
    short_comment.score = 1
    short_comment.created_utc = 1700000000.0

    long_comment = MagicMock()
    long_comment.body = "y" * COMMENT_MIN_LENGTH
    long_comment.id = "long1"
    long_comment.score = 5
    long_comment.created_utc = 1700000001.0

    mock_submission = MagicMock()
    mock_submission.comments.__getitem__ = lambda self, s: [short_comment, long_comment]
    mock_submission.comments.replace_more = MagicMock()
    mock_submission.subreddit.display_name = "SaaS"

    mock_reddit = MagicMock()
    mock_reddit.submission.return_value = mock_submission
    scraper_module._reddit = mock_reddit

    result = fetch_top_comments("post123")

    ids = [c["comment_id"] for c in result]
    assert "long1" in ids
    assert "short1" not in ids


def test_fetch_top_comments_calls_replace_more():
    """fetch_top_comments debe llamar a submission.comments.replace_more(limit=0)."""
    mock_submission = MagicMock()
    mock_submission.comments.__getitem__ = lambda self, s: []
    mock_submission.subreddit.display_name = "SaaS"

    mock_reddit = MagicMock()
    mock_reddit.submission.return_value = mock_submission
    scraper_module._reddit = mock_reddit

    fetch_top_comments("post123")

    mock_submission.comments.replace_more.assert_called_once_with(limit=0)


# ── Test de normalización de subreddit ─────────────────────────────────────────


def test_subreddit_name_lowercased_in_fetch_posts():
    """El campo subreddit en el DataFrame debe ser el nombre pasado a fetch_posts (ya lowercase por _post_to_dict)."""
    post_a = make_mock_post("aaa")

    mock_sub = MagicMock()
    mock_sub.hot.return_value = [post_a]
    mock_sub.new.return_value = []
    mock_sub.top.return_value = []

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    scraper_module._reddit = mock_reddit

    df = fetch_posts("SaaS", incremental=False)

    assert df["subreddit"].iloc[0] == "saas"
