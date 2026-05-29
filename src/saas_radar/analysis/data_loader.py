"""Carga, filtrado y ranking de posts/comentarios de Reddit para la fase de extracción IA."""

from __future__ import annotations

import logging
import time

import pandas as pd

from saas_radar.analysis.pain_filter import _semantic_score
from saas_radar.config import (
    HIGH_SIGNAL_SUBREDDITS,
    MAX_POST_AGE_DAYS,
    MAX_POSTS,
    MIN_SEMANTIC_SCORE,
    PAIN_CATEGORIES,
    POSTS_CAP_DEFAULT,
    POSTS_CAP_HIGH_SIGNAL,
    SUBREDDITS,
)
from saas_radar.storage.db import engine

logger = logging.getLogger(__name__)


def _pseudo_title(text: str) -> str:
    """Extrae la primera frase del texto como pseudo-título, máximo 120 caracteres.

    Busca el primer separador de frase ('. ', '? ', '! ', newline) en los
    primeros 120 caracteres. Si no encuentra ninguno, trunca a 120 chars con '...'.
    """
    t = str(text).strip()
    for sep in [". ", "? ", "! ", "\n"]:
        idx = t.find(sep)
        if 0 < idx <= 120:
            return t[: idx + 1]
    return t[:120] + ("..." if len(t) > 120 else "")


def load_pain_comments_as_posts() -> pd.DataFrame:
    """Carga comentarios con señal de dolor y los convierte en 'posts virtuales'.

    Lee de reddit_comments los comentarios con más de 200 caracteres, recalcula
    el semantic_score de cada uno (usando texto vacío como título), filtra por
    MIN_SEMANTIC_SCORE y mapea al mismo esquema de columnas que reddit_posts
    para que puedan concatenarse con load_pain_posts.

    Devuelve un DataFrame vacío si no hay comentarios con señal suficiente.
    """
    df = pd.read_sql(
        "SELECT comment_id, post_id, subreddit, text, score, created_utc "
        "FROM reddit_comments WHERE length(text) > 200",
        con=engine,
    )
    if df.empty:
        return df

    df["semantic_score"] = df.apply(
        lambda r: _semantic_score("", str(r["text"])), axis=1
    )
    df = df[df["semantic_score"] >= MIN_SEMANTIC_SCORE]
    if df.empty:
        return df

    mapped = pd.DataFrame(
        {
            "id": df["comment_id"],
            "source": "comment",
            "subreddit": df["subreddit"],
            "title": df["text"].apply(_pseudo_title),
            "text": df["text"],
            "score": df["score"].fillna(0).astype(int),
            "upvote_ratio": 0.0,
            "num_comments": 0,
            "created_utc": df["created_utc"],
            "url": "",
            "flair": None,
            "search_query": None,
            "clean_text": df["text"],
            "category": "pain_point",
            "semantic_score": df["semantic_score"],
        }
    )
    return mapped


def load_pain_posts(
    min_score: int = 10,
    top_n: int = MAX_POSTS,
    include_comments: bool = True,
    post_age_days: int = MAX_POST_AGE_DAYS,
) -> pd.DataFrame:
    """Carga, filtra y rankea posts de Reddit para la fase de extracción IA.

    Pasos:
    1. Lee todos los posts de reddit_posts.
    2. Filtra por subreddit, categoría, score mínimo, longitud de texto y edad.
    3. Recalcula semantic_score (ignora el valor persistido en BD).
    4. Filtra por MIN_SEMANTIC_SCORE.
    5. (Opcional) Merge con comentarios como posts virtuales.
    6. Rankea con blend 10/15/75 (score_norm, num_comments_norm, sem_norm).
    7. Aplica cap por subreddit (HIGH_SIGNAL → 10, default → 4).
    8. Devuelve los top_n mejores.

    Devuelve DataFrame vacío si no queda ningún post tras los filtros.
    """
    posts = pd.read_sql("SELECT * FROM reddit_posts", con=engine)
    posts["subreddit"] = posts["subreddit"].str.lower()
    posts = posts[posts["subreddit"].isin({s.lower() for s in SUBREDDITS})]
    posts = posts[posts["category"].isin(PAIN_CATEGORIES)]
    posts = posts[posts["score"] >= min_score]
    posts = posts[posts["text"].fillna("").str.len() > 100]

    if "created_utc" in posts.columns:
        cutoff = time.time() - post_age_days * 86400
        before_age = len(posts)
        posts = posts[posts["created_utc"].fillna(0) >= cutoff]
        logger.info("Filtro temporal (<%dd): %d → %d posts", post_age_days, before_age, len(posts))

    posts["semantic_score"] = posts.apply(
        lambda r: _semantic_score(str(r["title"]), str(r["text"])), axis=1
    ).astype(float)
    before_sem = len(posts)
    posts = posts[posts["semantic_score"] >= MIN_SEMANTIC_SCORE]
    logger.info("Pre-filtro semántico posts: %d → %d", before_sem, len(posts))

    if include_comments:
        comments = load_pain_comments_as_posts()
        if not comments.empty:
            comments = comments[
                comments["subreddit"].str.lower().isin({s.lower() for s in SUBREDDITS})
            ]
            logger.info("Comentarios con señal de dolor: %d", len(comments))
            posts = pd.concat([posts, comments], ignore_index=True)

    if posts.empty:
        logger.warning("Sin posts tras el filtro. Prueba --min-score 5 o ejecuta python main.py --skip-ai para scraper más datos.")
        return posts

    for col in ["score", "num_comments"]:
        posts[f"{col}_norm"] = posts.groupby("subreddit")[col].transform(
            lambda x: (x - x.min()) / (x.max() - x.min() + 1)
        )
    sem_max = posts["semantic_score"].max() or 1
    posts["sem_norm"] = posts["semantic_score"] / sem_max
    posts["rank_score"] = (
        posts["score_norm"] * 0.10
        + posts["num_comments_norm"] * 0.15
        + posts["sem_norm"] * 0.75
    )

    def _cap(sub: str) -> int:
        return POSTS_CAP_HIGH_SIGNAL if sub.lower() in HIGH_SIGNAL_SUBREDDITS else POSTS_CAP_DEFAULT

    posts = posts.sort_values("rank_score", ascending=False)
    capped: list[pd.DataFrame] = []
    for sub, group in posts.groupby("subreddit"):
        capped.append(group.head(_cap(sub)))
    posts = pd.concat(capped).sort_values("rank_score", ascending=False).head(top_n)

    logger.info("Top posts seleccionados (sem_score | subreddit | título):")
    for _, r in posts.head(15).iterrows():
        logger.debug("  [%+.0f] r/%s - %s...", r["semantic_score"], r["subreddit"], str(r["title"])[:58])

    return posts.reset_index(drop=True)
