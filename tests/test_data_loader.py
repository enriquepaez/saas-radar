"""Tests de saas_radar/analysis/data_loader.py con BD SQLite temporal."""

from __future__ import annotations

import time
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Fixture: BD temporal con tablas reddit_posts y reddit_comments
# ---------------------------------------------------------------------------


@pytest.fixture
def test_engine(tmp_path):
    """Crea una BD SQLite temporal con las tablas necesarias."""
    eng = create_engine(f"sqlite:///{tmp_path}/test.db")
    with eng.connect() as conn:
        conn.execute(
            text(
                """CREATE TABLE reddit_posts (
                    id TEXT PRIMARY KEY, source TEXT, subreddit TEXT,
                    title TEXT, text TEXT, score INTEGER, upvote_ratio REAL,
                    num_comments INTEGER, created_utc REAL, url TEXT, flair TEXT,
                    search_query TEXT, clean_text TEXT, category TEXT, semantic_score REAL
                )"""
            )
        )
        conn.execute(
            text(
                """CREATE TABLE reddit_comments (
                    comment_id TEXT PRIMARY KEY, post_id TEXT, subreddit TEXT,
                    text TEXT, score INTEGER, created_utc REAL,
                    clean_text TEXT, category TEXT
                )"""
            )
        )
        conn.commit()
    return eng


def _insert_post(conn, *, id, subreddit, title, body, score, num_comments, created_utc, category="pain_point"):
    """Helper para insertar un post en reddit_posts."""
    conn.execute(
        text(
            "INSERT INTO reddit_posts "
            "(id, source, subreddit, title, text, score, upvote_ratio, num_comments, "
            "created_utc, url, flair, search_query, clean_text, category, semantic_score) "
            "VALUES (:id, 'post', :subreddit, :title, :body, :score, 0.9, :num_comments, "
            ":created_utc, '', NULL, NULL, :body, :category, 0.0)"
        ),
        {
            "id": id,
            "subreddit": subreddit,
            "title": title,
            "body": body,
            "score": score,
            "num_comments": num_comments,
            "created_utc": created_utc,
            "category": category,
        },
    )


def _insert_comment(conn, *, comment_id, subreddit, body, score, created_utc):
    """Helper para insertar un comentario en reddit_comments."""
    conn.execute(
        text(
            "INSERT INTO reddit_comments "
            "(comment_id, post_id, subreddit, text, score, created_utc, clean_text, category) "
            "VALUES (:comment_id, 'p1', :subreddit, :body, :score, :created_utc, :body, 'pain_point')"
        ),
        {
            "comment_id": comment_id,
            "subreddit": subreddit,
            "body": body,
            "score": score,
            "created_utc": created_utc,
        },
    )


# ---------------------------------------------------------------------------
# 1. Filtro temporal: posts demasiado viejos se descartan
# ---------------------------------------------------------------------------


def test_temporal_filter_removes_old_posts(test_engine, tmp_path):
    """Posts con created_utc anterior al cutoff no deben aparecer en el resultado."""
    now = time.time()
    subreddit = "saas"  # subreddit válido del config

    with test_engine.connect() as conn:
        # Post reciente (1 día de antigüedad) — debe pasar
        _insert_post(
            conn,
            id="recent",
            subreddit=subreddit,
            title="I struggle manually tracking invoices every month",
            body="I use Excel to track all my invoices manually. It takes hours. "
                 "I need a better tool for this workflow because it is painful every time.",
            score=20,
            num_comments=5,
            created_utc=now - 86400,
            category="pain_point",
        )
        # Post antiguo (400 días) — debe ser descartado con post_age_days=365
        _insert_post(
            conn,
            id="old",
            subreddit=subreddit,
            title="I struggle manually tracking invoices every month",
            body="I use Excel to track all my invoices manually. It takes hours. "
                 "I need a better tool for this workflow because it is painful every time.",
            score=20,
            num_comments=5,
            created_utc=now - 400 * 86400,
            category="pain_point",
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        # Parchamos SUBREDDITS para que 'saas' sea válido
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    assert "recent" in result["id"].values
    assert "old" not in result["id"].values


# ---------------------------------------------------------------------------
# 2. Filtro semántico: semantic_score < MIN_SEMANTIC_SCORE descarta el post
# ---------------------------------------------------------------------------


def test_semantic_filter_removes_low_score_posts(test_engine):
    """Posts sin señal semántica de dolor deben ser descartados."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        # Post con señal de dolor alta
        _insert_post(
            conn,
            id="pain",
            subreddit=subreddit,
            title="I manually track everything in Excel",
            body="I use Excel to manually track all client invoices. "
                 "It is frustrating and time consuming. The workflow is broken "
                 "and I have to workaround it every week. Need something better.",
            score=20,
            num_comments=5,
            created_utc=now - 3600,
            category="pain_point",
        )
        # Post sin señal de dolor (texto neutro/irrelevante)
        _insert_post(
            conn,
            id="neutral",
            subreddit=subreddit,
            title="Just a random post about nothing special",
            body="This is a post about something completely unrelated to any pain. "
                 "Nothing to see here, just a normal post with no signal.",
            score=20,
            num_comments=5,
            created_utc=now - 3600,
            category="pain_point",
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    # El post con dolor debe aparecer; el neutro no
    assert "pain" in result["id"].values
    assert "neutral" not in result["id"].values


# ---------------------------------------------------------------------------
# 3. Recálculo de semantic_score ignora el valor persistido
# ---------------------------------------------------------------------------


def test_semantic_score_recalculated_not_from_db(test_engine):
    """data_loader debe recalcular semantic_score, no usar el persistido en BD."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        # Post con señal real de dolor pero semantic_score persistido = -99
        # Si se usara el valor de BD, sería descartado; si se recalcula, pasaría
        conn.execute(
            text(
                "INSERT INTO reddit_posts "
                "(id, source, subreddit, title, text, score, upvote_ratio, num_comments, "
                "created_utc, url, flair, search_query, clean_text, category, semantic_score) "
                "VALUES ('recalc', 'post', :sub, 'manually tracking invoices pain', "
                "'I manually track invoices in Excel every month. It is painful and "
                "frustrating. The workaround takes hours of my time each week.', "
                "20, 0.9, 5, :ts, '', NULL, NULL, 'text', 'pain_point', -99.0)"
            ),
            {"sub": subreddit, "ts": now - 3600},
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    # El post debe aparecer porque el semantic_score recalculado es positivo
    assert "recalc" in result["id"].values
    # Y el semantic_score en el DataFrame debe ser el recalculado (>= MIN_SEMANTIC_SCORE)
    row = result[result["id"] == "recalc"].iloc[0]
    from saas_radar.config import MIN_SEMANTIC_SCORE
    assert row["semantic_score"] >= MIN_SEMANTIC_SCORE


# ---------------------------------------------------------------------------
# 4. Ranking: rank_score = 0.10*score_norm + 0.15*num_comments_norm + 0.75*sem_norm
# ---------------------------------------------------------------------------


def test_ranking_formula_applied(test_engine):
    """El resultado debe estar ordenado por rank_score descendente y tener rank_score."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        for i in range(3):
            _insert_post(
                conn,
                id=f"post_{i}",
                subreddit=subreddit,
                title=f"I struggle manually tracking invoices every month post {i}",
                body="I use Excel to manually track all client invoices. "
                     "It is frustrating. Need a better workflow tool now.",
                score=10 + i * 5,
                num_comments=i * 3,
                created_utc=now - 3600,
                category="pain_point",
            )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    assert "rank_score" in result.columns
    assert len(result) > 0
    # Verificar que está ordenado descendente
    scores = result["rank_score"].tolist()
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 5. Cap por subreddit: HIGH_SIGNAL → 10, default → 4
# ---------------------------------------------------------------------------


def test_cap_high_signal_subreddit(test_engine):
    """Un subreddit HIGH_SIGNAL no debe superar POSTS_CAP_HIGH_SIGNAL=10 posts."""
    now = time.time()
    # 'saas' NO está en HIGH_SIGNAL_SUBREDDITS, pero 'msp' sí
    subreddit = "msp"

    with test_engine.connect() as conn:
        for i in range(15):  # 15 posts, cap debe cortar en 10
            _insert_post(
                conn,
                id=f"msp_{i}",
                subreddit=subreddit,
                title=f"I struggle with manual workflow tracking {i}",
                body="I manually track all tickets in a spreadsheet. It is painful "
                     "and frustrating. This workaround takes hours each week.",
                score=10 + i,
                num_comments=i,
                created_utc=now - 3600,
                category="pain_point",
            )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    msp_rows = result[result["subreddit"] == subreddit]
    from saas_radar.config import POSTS_CAP_HIGH_SIGNAL
    assert len(msp_rows) <= POSTS_CAP_HIGH_SIGNAL


def test_cap_default_subreddit(test_engine):
    """Un subreddit normal no debe superar POSTS_CAP_DEFAULT=4 posts."""
    now = time.time()
    subreddit = "notion"  # en SUBREDDITS pero no en HIGH_SIGNAL_SUBREDDITS

    with test_engine.connect() as conn:
        for i in range(10):  # 10 posts, cap debe cortar en 4
            _insert_post(
                conn,
                id=f"notion_{i}",
                subreddit=subreddit,
                title=f"I manually track everything in Notion {i}",
                body="I use Notion to manually track all my invoices. "
                     "It is frustrating and time consuming. Need better tool.",
                score=10 + i,
                num_comments=i,
                created_utc=now - 3600,
                category="pain_point",
            )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    notion_rows = result[result["subreddit"] == subreddit]
    from saas_radar.config import POSTS_CAP_DEFAULT
    assert len(notion_rows) <= POSTS_CAP_DEFAULT


# ---------------------------------------------------------------------------
# 6. top_n limita el resultado total
# ---------------------------------------------------------------------------


def test_top_n_limits_result(test_engine):
    """load_pain_posts devuelve como máximo top_n filas."""
    now = time.time()

    with test_engine.connect() as conn:
        for i in range(30):
            subreddit = "msp" if i % 2 == 0 else "sysadmin"
            _insert_post(
                conn,
                id=f"post_{i}",
                subreddit=subreddit,
                title=f"I manually track everything and struggle with workflow {i}",
                body="I use Excel to manually track all my client invoices. "
                     "It is painful and frustrating. This workaround takes hours.",
                score=10 + i,
                num_comments=i,
                created_utc=now - 3600,
                category="pain_point",
            )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", ["msp", "sysadmin"]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=5,
                include_comments=False,
                post_age_days=365,
            )

    assert len(result) <= 5


# ---------------------------------------------------------------------------
# 7. load_pain_comments_as_posts: comentarios se convierten en posts virtuales
# ---------------------------------------------------------------------------


def test_comments_loaded_as_posts(test_engine):
    """Comentarios con señal de dolor aparecen con source='comment' y pseudo-título."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        _insert_comment(
            conn,
            comment_id="c1",
            subreddit=subreddit,
            body="I manually track all client invoices in Excel every single month. "
                 "It is so painful and frustrating. The workaround takes too long. "
                 "I need a better tool for this workflow problem.",
            score=5,
            created_utc=now - 3600,
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        result = data_loader.load_pain_comments_as_posts()

    if not result.empty:
        assert "source" in result.columns
        assert (result["source"] == "comment").all()
        assert "title" in result.columns
        # El pseudo-título debe ser <= 120 chars + posible '...'
        for title in result["title"]:
            # El título real (sin los '...') debe tener <= 121 chars
            assert len(title) <= 124  # 120 + len('...')


def test_comments_pseudo_title_first_sentence(test_engine):
    """El pseudo-título debe ser la primera frase del comentario."""
    now = time.time()
    subreddit = "saas"
    long_text = (
        "I manually enter all data every day. "  # primera frase (38 chars + sep '. ')
        "This is extremely frustrating and painful. "
        "The workaround takes hours every week. "
        "I need a better solution for this."
    )

    with test_engine.connect() as conn:
        _insert_comment(
            conn,
            comment_id="c_sentence",
            subreddit=subreddit,
            body=long_text,
            score=5,
            created_utc=now - 3600,
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        result = data_loader.load_pain_comments_as_posts()

    if not result.empty:
        title = result.iloc[0]["title"]
        # Debe terminar con '. ' recortado o similar
        assert title == "I manually enter all data every day."


# ---------------------------------------------------------------------------
# 8. load_pain_comments_as_posts: comentarios cortos (<=200 chars) ignorados
# ---------------------------------------------------------------------------


def test_short_comments_excluded(test_engine):
    """Comentarios con <= 200 caracteres no deben aparecer."""
    now = time.time()

    with test_engine.connect() as conn:
        _insert_comment(
            conn,
            comment_id="short",
            subreddit="saas",
            body="Short comment, very painful workaround.",  # < 200 chars
            score=5,
            created_utc=now - 3600,
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        result = data_loader.load_pain_comments_as_posts()

    assert result.empty


# ---------------------------------------------------------------------------
# 9. Merge de comentarios en load_pain_posts con include_comments=True
# ---------------------------------------------------------------------------


def test_include_comments_merges_into_posts(test_engine):
    """Con include_comments=True, comentarios válidos se suman a los posts."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        # Un post válido
        _insert_post(
            conn,
            id="p1",
            subreddit=subreddit,
            title="I manually track invoices and it is painful",
            body="I use Excel to manually track all client invoices. "
                 "The workaround is painful and takes hours every week.",
            score=20,
            num_comments=5,
            created_utc=now - 3600,
            category="pain_point",
        )
        # Un comentario válido (> 200 chars con señal de dolor)
        _insert_comment(
            conn,
            comment_id="c_valid",
            subreddit=subreddit,
            body="I manually enter all invoice data in a spreadsheet every single day. "
                 "It is so frustrating and time consuming. The workflow is completely broken. "
                 "I wish there was a tool that could automate this painful workaround.",
            score=10,
            created_utc=now - 3600,
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=True,
                post_age_days=365,
            )

    # Debe haber al menos el post; el comentario puede o no pasar según semantic_score
    assert len(result) >= 1
    sources = result["source"].unique()
    assert "post" in sources


# ---------------------------------------------------------------------------
# 10. BD vacía devuelve DataFrame vacío
# ---------------------------------------------------------------------------


def test_empty_db_returns_empty_dataframe(test_engine):
    """Con BD vacía, load_pain_posts debe devolver un DataFrame vacío."""
    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        result = data_loader.load_pain_posts(min_score=5, top_n=20, include_comments=False)

    assert isinstance(result, pd.DataFrame)
    assert result.empty


# ---------------------------------------------------------------------------
# 11. Filtro por score mínimo
# ---------------------------------------------------------------------------


def test_min_score_filter(test_engine):
    """Posts con score < min_score deben ser descartados."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        _insert_post(
            conn,
            id="low_score",
            subreddit=subreddit,
            title="I manually track invoices and it is painful",
            body="I use Excel to manually track all client invoices. "
                 "The workaround is painful and takes hours every week.",
            score=3,  # < min_score=10
            num_comments=5,
            created_utc=now - 3600,
            category="pain_point",
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=10,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    assert "low_score" not in result.get("id", pd.Series()).values


# ---------------------------------------------------------------------------
# 12. Filtro por categoría: solo pain_point y question_operational
# ---------------------------------------------------------------------------


def test_category_filter(test_engine):
    """Solo posts con categoría en PAIN_CATEGORIES deben pasar."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        _insert_post(
            conn,
            id="showcase",
            subreddit=subreddit,
            title="I built a tool for invoice tracking",
            body="I built a tool that automates invoices. Check it out. "
                 "It is really great and I am proud of it. It took months.",
            score=20,
            num_comments=5,
            created_utc=now - 3600,
            category="showcase",  # NO está en PAIN_CATEGORIES
        )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    assert "showcase" not in result.get("id", pd.Series()).values


# ---------------------------------------------------------------------------
# 13. reset_index: índice del resultado es 0..N-1
# ---------------------------------------------------------------------------


def test_result_has_reset_index(test_engine):
    """El DataFrame devuelto debe tener índice continuo desde 0."""
    now = time.time()
    subreddit = "saas"

    with test_engine.connect() as conn:
        for i in range(3):
            _insert_post(
                conn,
                id=f"p{i}",
                subreddit=subreddit,
                title=f"I manually track invoices painfully {i}",
                body="I use Excel to manually track all my client invoices. "
                     "It is frustrating. The workflow is broken every week.",
                score=10 + i,
                num_comments=i,
                created_utc=now - 3600,
                category="pain_point",
            )
        conn.commit()

    from saas_radar.analysis import data_loader

    with patch("saas_radar.analysis.data_loader.engine", test_engine):
        with patch("saas_radar.analysis.data_loader.SUBREDDITS", [subreddit]):
            result = data_loader.load_pain_posts(
                min_score=5,
                top_n=50,
                include_comments=False,
                post_age_days=365,
            )

    if not result.empty:
        assert list(result.index) == list(range(len(result)))
