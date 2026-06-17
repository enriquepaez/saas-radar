"""Scraper de Reddit con PRAW: singleton, feeds y comentarios."""

from __future__ import annotations

import logging
import time

import pandas as pd
import praw

from saas_radar.config import (
    COMMENT_MIN_LENGTH,
    PAIN_SEARCH_LIMIT,
    POST_LIMIT,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    SUBREDDITS,
)

logger = logging.getLogger(__name__)

_reddit: praw.Reddit | None = None


def get_reddit() -> praw.Reddit:
    """Devuelve el cliente PRAW reutilizando la instancia ya creada (singleton)."""
    global _reddit
    if _reddit is None:
        _reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
    return _reddit


def _post_to_dict(post: object, source: str, subreddit_name: str, search_query: str | None) -> dict:
    """Convierte un praw.models.Submission a dict normalizado."""
    return {
        "id": getattr(post, "id", None),
        "source": source,
        "subreddit": subreddit_name.lower(),
        "title": getattr(post, "title", ""),
        "text": getattr(post, "selftext", ""),
        "score": getattr(post, "score", 0),
        "upvote_ratio": getattr(post, "upvote_ratio", 0.0),
        "num_comments": getattr(post, "num_comments", 0),
        "created_utc": getattr(post, "created_utc", None),
        "url": getattr(post, "url", ""),
        "flair": getattr(post, "link_flair_text", None),
        "search_query": search_query,
    }


def fetch_posts(subreddit_name: str, limit: int = POST_LIMIT, incremental: bool = False) -> pd.DataFrame:
    """Descarga posts de un subreddit combinando varios feeds y deduplicando por id.

    En modo full (incremental=False): hot + new(//2) + top-month(//2) + top-year(//2).
    En modo incremental (incremental=True): new + hot + top-day(//2).
    """
    reddit = get_reddit()
    sub = reddit.subreddit(subreddit_name)

    if incremental:
        feeds = [
            sub.new(limit=limit),
            sub.hot(limit=limit),
            sub.top(time_filter="day", limit=limit // 2),
        ]
    else:
        feeds = [
            sub.hot(limit=limit),
            sub.new(limit=limit // 2),
            sub.top(time_filter="month", limit=limit // 2),
            sub.top(time_filter="year", limit=limit // 2),
        ]

    seen_ids: set[str] = set()
    posts: list[dict] = []

    for feed in feeds:
        for post in feed:
            pid = getattr(post, "id", None)
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            posts.append(_post_to_dict(post, "subreddit_feed", subreddit_name, None))

    return pd.DataFrame(posts)


def search_pain_posts(query: str, limit: int = PAIN_SEARCH_LIMIT, incremental: bool = False) -> pd.DataFrame:
    """Busca posts de dolor en el multireddit combinado de todos los subreddits configurados.

    Si incremental=True, restringe la búsqueda al último día (time_filter='day').
    Añade un sleep de 0.1s entre posts para respetar el rate limit de la API.
    """
    reddit = get_reddit()
    sub_str = "+".join(SUBREDDITS)
    kwargs: dict = {"sort": "relevance", "limit": limit}
    if incremental:
        kwargs["time_filter"] = "day"

    posts: list[dict] = []
    for post in reddit.subreddit(sub_str).search(query, **kwargs):
        subreddit_name = getattr(post.subreddit, "display_name", "").lower()
        posts.append(_post_to_dict(post, "pain_search", subreddit_name, query))
        time.sleep(0.1)

    return pd.DataFrame(posts)


def fetch_top_comments(post_id: str, limit: int = 30) -> list[dict]:
    """Descarga los comentarios más relevantes de un post dado su id.

    Llama replace_more(limit=0) para evitar objetos MoreComments no resueltos.
    Filtra comentarios sin atributo body o con body más corto que COMMENT_MIN_LENGTH.
    """
    reddit = get_reddit()
    submission = reddit.submission(id=post_id)
    submission.comments.replace_more(limit=0)

    comments: list[dict] = []
    for comment in submission.comments[:limit]:
        if not hasattr(comment, "body"):
            continue
        if len(comment.body) < COMMENT_MIN_LENGTH:
            continue
        comments.append(
            {
                "comment_id": comment.id,
                "post_id": post_id,
                "subreddit": submission.subreddit.display_name.lower(),
                "text": comment.body,
                "score": comment.score,
                "created_utc": comment.created_utc,
            }
        )

    return comments
