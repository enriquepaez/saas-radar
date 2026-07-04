"""Tests de src/saas_radar/analysis/meta_analysis.py."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, text

from saas_radar.analysis.meta_analysis import (
    _build_recommendations,
    _find_discovered_subreddits,
    _find_empty_queries,
    generate_meta_analysis,
    print_meta_summary,
    save_meta_analysis,
)
from saas_radar.storage.db import init_db, persist_meta_recommendations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """BD temporal SQLite con el schema completo."""
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(db_url)
    return db_url


@pytest.fixture
def tmp_db_engine(tmp_db):
    """Engine de SQLAlchemy para la BD temporal."""
    return create_engine(tmp_db, connect_args={"check_same_thread": False})


# ---------------------------------------------------------------------------
# Test 1 — recurrence incrementa en segunda llamada con mismo (type, target)
# ---------------------------------------------------------------------------


def test_recurrence_increments(tmp_db):
    """Segunda llamada con la misma (type, target) y acted=0 incrementa recurrence a 2."""
    rec = [
        {
            "type": "remove_subreddit",
            "target": "r/testsub",
            "action": "Considerar quitar r/testsub — 5 posts analizados, 0 con problema real.",
        }
    ]
    persist_meta_recommendations(run_id=1, recs=rec, db_url=tmp_db)
    persist_meta_recommendations(run_id=2, recs=rec, db_url=tmp_db)

    engine = create_engine(tmp_db, connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT recurrence FROM meta_recommendations WHERE type='remove_subreddit'")
        ).fetchone()
    assert row is not None
    assert row[0] == 2


# ---------------------------------------------------------------------------
# Test 2 — _build_recommendations genera remove_subreddit
# ---------------------------------------------------------------------------


def test_recommendations_remove_subreddit():
    """Sub con posts_analyzed>=3 y hit_rate==0 genera rec de tipo remove_subreddit."""
    subreddit_signal = [
        {
            "subreddit": "lowsignal",
            "posts_analyzed": 5,
            "with_problem": 0,
            "with_payment_signal": 0,
            "hit_rate": 0,
        }
    ]
    recs = _build_recommendations(
        subreddit_signal=subreddit_signal,
        silent_subreddits=[],
        empty_queries=[],
        recurring_niches=[],
        discovered_subs=[],
    )
    types = [r["type"] for r in recs]
    assert "remove_subreddit" in types


# ---------------------------------------------------------------------------
# Test 3 — _build_recommendations genera boost_subreddit
# ---------------------------------------------------------------------------


def test_recommendations_boost_subreddit():
    """Sub con hit_rate>=0.6 y with_payment_signal>=1 genera rec de tipo boost_subreddit."""
    subreddit_signal = [
        {
            "subreddit": "highsignal",
            "posts_analyzed": 10,
            "with_problem": 7,
            "with_payment_signal": 2,
            "hit_rate": 0.7,
        }
    ]
    recs = _build_recommendations(
        subreddit_signal=subreddit_signal,
        silent_subreddits=[],
        empty_queries=[],
        recurring_niches=[],
        discovered_subs=[],
    )
    types = [r["type"] for r in recs]
    assert "boost_subreddit" in types


# ---------------------------------------------------------------------------
# Test 4 — _find_empty_queries devuelve solo las queries sin posts
# ---------------------------------------------------------------------------


def test_find_empty_queries(tmp_db, tmp_db_engine):
    """Queries con 0 posts en el rango temporal aparecen en la lista; las que tienen posts no."""
    from saas_radar import config

    # Tomamos las dos primeras queries de config para el test
    q_with_posts = config.PAIN_SEARCH_QUERIES[0]
    q_without_posts = config.PAIN_SEARCH_QUERIES[1]

    # Insertar 1 post para la primera query, nada para la segunda
    now = time.time()
    with tmp_db_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO reddit_posts (id, search_query, created_utc, source) "
                    "VALUES (:id, :q, :utc, 'pain_search')"
                ),
                {"id": "post_test_1", "q": q_with_posts, "utc": now},
            )

    result = _find_empty_queries(post_age_days=7, db_url=tmp_db)

    assert q_without_posts in result
    assert q_with_posts not in result


# ---------------------------------------------------------------------------
# Test 5 — _find_discovered_subreddits excluye subs en SUBREDDITS config
# ---------------------------------------------------------------------------


def test_find_discovered_subreddits(tmp_db, tmp_db_engine):
    """Subs no en SUBREDDITS con >=2 posts en pain_search se devuelven; los configurados no."""
    from saas_radar import config

    now = time.time()
    known_sub = config.SUBREDDITS[0].lower()  # sub ya en la config
    unknown_sub = "some_unknown_subreddit_xyz"

    with tmp_db_engine.connect() as conn:
        with conn.begin():
            # 2 posts del sub conocido (debe ser excluido)
            for i in range(2):
                conn.execute(
                    text(
                        "INSERT INTO reddit_posts (id, subreddit, source, created_utc) "
                        "VALUES (:id, :sub, 'pain_search', :utc)"
                    ),
                    {"id": f"known_{i}", "sub": known_sub, "utc": now},
                )
            # 3 posts del sub desconocido (debe aparecer)
            for i in range(3):
                conn.execute(
                    text(
                        "INSERT INTO reddit_posts (id, subreddit, source, created_utc) "
                        "VALUES (:id, :sub, 'pain_search', :utc)"
                    ),
                    {"id": f"unknown_{i}", "sub": unknown_sub, "utc": now},
                )

    result = _find_discovered_subreddits(post_age_days=7, db_url=tmp_db)

    discovered_names = [r["subreddit"] for r in result]
    assert unknown_sub in discovered_names
    assert known_sub not in discovered_names


# ---------------------------------------------------------------------------
# Test 6 — print_meta_summary formato snapshot
# ---------------------------------------------------------------------------


def test_print_meta_summary_format(tmp_db, capsys):
    """La salida de print_meta_summary incluye la cabecera y el separador '='*70."""
    meta = {
        "summary": {
            "total_extractions": 5,
            "with_problem": 3,
            "with_payment": 1,
            "opportunities": 2,
            "subreddits_active": 4,
            "subreddits_silent": 1,
            "empty_queries_count": 2,
        },
        "pain_categories": [{"category": "excel", "count": 3}],
        "recommendations": [],
    }
    print_meta_summary(meta, db_url=tmp_db)
    captured = capsys.readouterr()
    assert "META-ANALISIS DEL RUN" in captured.out
    assert "=" * 70 in captured.out


# ---------------------------------------------------------------------------
# Test 7 — generate_meta_analysis devuelve las 8 claves esperadas
# ---------------------------------------------------------------------------


def test_generate_meta_analysis_summary_keys(tmp_db):
    """generate_meta_analysis con extractions mínimas devuelve las 8 claves del schema."""
    extractions = [
        {
            "_subreddit": "saas",
            "has_problem": True,
            "payment_signal": False,
            "who_has_it": "freelancers",
            "problem_description": "manual invoice tracking",
            "workflow_context": "use excel spreadsheet",
            "current_workaround": "copy paste",
        }
    ]
    opportunities: list[dict] = []

    meta = generate_meta_analysis(
        extractions=extractions,
        opportunities=opportunities,
        post_age_days=7,
        db_url=tmp_db,
    )

    expected_keys = {
        "subreddit_signal",
        "silent_subreddits",
        "empty_queries",
        "recurring_niches",
        "pain_categories",
        "discovered_subreddits",
        "recommendations",
        "summary",
    }
    assert expected_keys == set(meta.keys())


# ---------------------------------------------------------------------------
# Test 8 — save_meta_analysis no corrompe directorios con '.json' en el nombre
# ---------------------------------------------------------------------------


def test_save_meta_analysis_path_inside_dir_named_json(tmp_path):
    """Con results en <dir>.json/<ts>_results.json, el meta va al MISMO dir como <ts>_meta.json."""
    out_dir = tmp_path / "ai_analysis.json"  # nombre real del output de producción
    out_dir.mkdir()
    run_json = out_dir / "20260101_000000_results.json"
    run_json.write_text("{}", encoding="utf-8")

    meta_path = save_meta_analysis({"recommendations": []}, str(run_json), run_id=None)

    assert meta_path == str(out_dir / "20260101_000000_meta.json")
    assert (out_dir / "20260101_000000_meta.json").exists()


# ---------------------------------------------------------------------------
# Test 9 — save_meta_analysis con nombre genérico (sin sufijo _results)
# ---------------------------------------------------------------------------


def test_save_meta_analysis_generic_json_name(tmp_path):
    """Un results 'run.json' produce 'run_meta.json' en el mismo directorio."""
    run_json = tmp_path / "run.json"
    run_json.write_text("{}", encoding="utf-8")

    meta_path = save_meta_analysis({"recommendations": []}, str(run_json), run_id=None)

    assert meta_path == str(tmp_path / "run_meta.json")
    assert (tmp_path / "run_meta.json").exists()
