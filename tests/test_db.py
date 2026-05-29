"""Tests de la capa de almacenamiento storage/db.py."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from saas_radar.storage.db import (
    _extract_target,
    db_stats,
    has_successful_run,
    init_db,
    load_active_opportunities,
    load_from_db,
    persist_meta_recommendations,
    persist_run_to_db,
    save_to_db,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(db_url)
    return db_url


# ---------------------------------------------------------------------------
# 1. Idempotencia init_db
# ---------------------------------------------------------------------------


def test_init_db_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(db_url)
    init_db(db_url)  # segunda llamada no debe fallar

    engine = create_engine(db_url)
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}

    expected = {"reddit_posts", "reddit_comments", "analysis_runs", "opportunities", "meta_recommendations", "opportunity_gtm"}
    assert expected.issubset(tables)


# ---------------------------------------------------------------------------
# 2. Migración idempotente semantic_score
# ---------------------------------------------------------------------------


def test_migration_semantic_score_added(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(db_url)

    # Crear tabla manualmente SIN semantic_score
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(
                "CREATE TABLE reddit_posts ("
                "id TEXT PRIMARY KEY, source TEXT, subreddit TEXT, title TEXT, text TEXT,"
                "score INTEGER, upvote_ratio REAL, num_comments INTEGER, created_utc REAL,"
                "url TEXT, flair TEXT, search_query TEXT, clean_text TEXT, category TEXT"
                ")"
            ))

    # init_db debe añadir la columna
    init_db(db_url)

    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(reddit_posts)")).fetchall()}

    assert "semantic_score" in cols

    # Segunda llamada no debe fallar
    init_db(db_url)


# ---------------------------------------------------------------------------
# 3. Migración idempotente canonical_id
# ---------------------------------------------------------------------------


def test_migration_canonical_id_added(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(db_url)

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(
                "CREATE TABLE opportunities ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, product_name TEXT,"
                "niche TEXT, core_problem TEXT, why_gap_exists TEXT, concrete_workaround TEXT,"
                "workaround_cost TEXT, mvp_scope TEXT, estimated_price TEXT, monetization TEXT,"
                "competitor_gap TEXT, mentioned_competitors TEXT, payment_signal TEXT,"
                "payment_evidence TEXT, solo_buildable INTEGER, mvp_weeks INTEGER,"
                "priority_score INTEGER, priority_reason TEXT, evidence_items TEXT,"
                "evidence_quotes TEXT, reviewed INTEGER DEFAULT 0, starred INTEGER DEFAULT 0,"
                "discarded INTEGER DEFAULT 0, user_notes TEXT, created_at TEXT"
                ")"
            ))

    init_db(db_url)

    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(opportunities)")).fetchall()}

    assert "canonical_id" in cols

    # Segunda llamada no debe fallar
    init_db(db_url)


# ---------------------------------------------------------------------------
# 4. INSERT OR IGNORE con duplicados
# ---------------------------------------------------------------------------


def test_save_to_db_insert_or_ignore(tmp_db):
    df = pd.DataFrame([
        {"id": "p1", "subreddit": "saas", "title": "Post 1", "score": 10},
        {"id": "p2", "subreddit": "saas", "title": "Post 2", "score": 20},
        {"id": "p3", "subreddit": "saas", "title": "Post 3", "score": 30},
    ])

    inserted = save_to_db(df, "reddit_posts", tmp_db)
    assert inserted == 3

    # Insertar las mismas 3 filas + 1 duplicada
    df_dup = pd.DataFrame([
        {"id": "p1", "subreddit": "saas", "title": "Post 1 dup", "score": 99},
        {"id": "p4", "subreddit": "saas", "title": "Post 4", "score": 40},
    ])
    inserted2 = save_to_db(df_dup, "reddit_posts", tmp_db)
    assert inserted2 == 1

    # Segunda pasada con las mismas filas → 0 insertadas
    inserted3 = save_to_db(df, "reddit_posts", tmp_db)
    assert inserted3 == 0


# ---------------------------------------------------------------------------
# 5. save_to_db normaliza subreddit a minúsculas
# ---------------------------------------------------------------------------


def test_save_to_db_normalizes_subreddit(tmp_db):
    df = pd.DataFrame([{"id": "p10", "subreddit": "Entrepreneur", "title": "Test", "score": 5}])
    save_to_db(df, "reddit_posts", tmp_db)

    result = load_from_db("reddit_posts", tmp_db)
    row = result[result["id"] == "p10"].iloc[0]
    assert row["subreddit"] == "entrepreneur"


# ---------------------------------------------------------------------------
# 6. load_from_db devuelve DataFrame con filas insertadas
# ---------------------------------------------------------------------------


def test_load_from_db_returns_rows(tmp_db):
    df = pd.DataFrame([
        {"comment_id": "c1", "post_id": "p1", "subreddit": "saas", "text": "hello", "score": 1},
    ])
    save_to_db(df, "reddit_comments", tmp_db)

    result = load_from_db("reddit_comments", tmp_db)
    assert len(result) == 1
    assert result.iloc[0]["post_id"] == "p1"


# ---------------------------------------------------------------------------
# 7. db_stats con BD vacía
# ---------------------------------------------------------------------------


def test_db_stats_empty(tmp_db):
    stats = db_stats(tmp_db)
    assert stats == {"reddit_posts": 0, "reddit_comments": 0}


# ---------------------------------------------------------------------------
# 8. db_stats con filas
# ---------------------------------------------------------------------------


def test_db_stats_with_rows(tmp_db):
    df = pd.DataFrame([
        {"id": "x1", "subreddit": "saas", "title": "T1", "score": 1},
        {"id": "x2", "subreddit": "saas", "title": "T2", "score": 2},
    ])
    save_to_db(df, "reddit_posts", tmp_db)

    stats = db_stats(tmp_db)
    assert stats["reddit_posts"] == 2
    assert stats["reddit_comments"] == 0


# ---------------------------------------------------------------------------
# 9. has_successful_run con BD vacía
# ---------------------------------------------------------------------------


def test_has_successful_run_empty(tmp_db):
    assert has_successful_run(tmp_db) is False


# ---------------------------------------------------------------------------
# 10. has_successful_run con run ok
# ---------------------------------------------------------------------------


def test_has_successful_run_with_ok_run(tmp_db):
    engine = create_engine(tmp_db)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(
                "INSERT INTO analysis_runs (run_at, status) VALUES ('2026-01-01T00:00:00', 'ok')"
            ))

    assert has_successful_run(tmp_db) is True


# ---------------------------------------------------------------------------
# 11. persist_run_to_db devuelve run_id y opps tienen run_id correcto
# ---------------------------------------------------------------------------


def test_persist_run_to_db_returns_run_id(tmp_db):
    run_data = {
        "run_at": "2026-01-01T10:00:00",
        "posts_analyzed": 5,
        "valid_extractions": 2,
        "opportunities_count": 2,
        "status": "ok",
        "ai_provider": "claude",
    }
    opps = [
        {"product_name": "OppA", "niche": "SaaS", "priority_score": 8, "evidence_items": "[]", "evidence_quotes": "[]"},
        {"product_name": "OppB", "niche": "FinTech", "priority_score": 7, "evidence_items": "[]", "evidence_quotes": "[]"},
    ]

    run_id = persist_run_to_db(run_data, opps, tmp_db)
    assert run_id >= 1

    engine = create_engine(tmp_db)
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT run_id FROM opportunities WHERE run_id = {run_id}")).fetchall()

    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 12. persist_run_to_db: canonical_id autorreferencial
# ---------------------------------------------------------------------------


def test_persist_run_to_db_canonical_id_self_referential(tmp_db):
    run_data = {"run_at": "2026-01-01T10:00:00", "status": "ok"}
    opps = [
        {"product_name": "OppX", "niche": "DevTools"},
        {"product_name": "OppY", "niche": "HR"},
    ]

    run_id = persist_run_to_db(run_data, opps, tmp_db)

    engine = create_engine(tmp_db)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, canonical_id FROM opportunities WHERE run_id = :rid"), {"rid": run_id}).fetchall()

    for opp_id, canonical_id in rows:
        assert canonical_id == opp_id, f"opp {opp_id} tiene canonical_id={canonical_id}, esperado {opp_id}"


# ---------------------------------------------------------------------------
# 13. load_active_opportunities filtra correctamente
# ---------------------------------------------------------------------------


def test_load_active_opportunities(tmp_db):
    run_data = {"run_at": "2026-01-01T10:00:00", "status": "ok"}
    opps = [
        {"product_name": "Active", "discarded": 0},
        {"product_name": "Discarded", "discarded": 1},
    ]
    run_id = persist_run_to_db(run_data, opps, tmp_db)

    # La opp "Discarded" tiene discarded=1 pero canonical_id=id (autorreferencial)
    # Necesitamos asegurarnos de que discarded=1 se insertó correctamente
    active = load_active_opportunities(tmp_db)

    assert len(active) == 1
    assert active.iloc[0]["product_name"] == "Active"


# ---------------------------------------------------------------------------
# 14. persist_meta_recommendations incrementa recurrence
# ---------------------------------------------------------------------------


def test_persist_meta_recommendations_increments_recurrence(tmp_db):
    recs = [{"type": "add_subreddit", "action": "add r/microsaas"}]

    persist_meta_recommendations(1, recs, tmp_db)
    persist_meta_recommendations(1, recs, tmp_db)

    engine = create_engine(tmp_db)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT recurrence FROM meta_recommendations WHERE type = 'add_subreddit'")).fetchone()

    assert row[0] == 2


# ---------------------------------------------------------------------------
# 15. persist_meta_recommendations no incrementa si acted = 1
# ---------------------------------------------------------------------------


def test_persist_meta_recommendations_no_increment_if_acted(tmp_db):
    recs = [{"type": "remove_query", "action": "remove 'invoices'"}]
    persist_meta_recommendations(1, recs, tmp_db)

    # Marcar como acted = 1
    engine = create_engine(tmp_db)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("UPDATE meta_recommendations SET acted = 1 WHERE type = 'remove_query'"))

    # Segunda llamada debe insertar nueva fila (no incrementar)
    persist_meta_recommendations(1, recs, tmp_db)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM meta_recommendations WHERE type = 'remove_query'")).scalar()

    assert count == 2


# ---------------------------------------------------------------------------
# Extra: _extract_target
# ---------------------------------------------------------------------------


def test_extract_target_subreddit_type_with_r_prefix():
    target = _extract_target("add_subreddit", "Consider adding r/microsaas to the list")
    assert target == "r/microsaas"


def test_extract_target_subreddit_type_no_r_prefix():
    action = "Consider adding microsaas to the list"
    target = _extract_target("add_subreddit", action)
    assert target == action[:50]


def test_extract_target_non_subreddit_type():
    target = _extract_target("remove_query", "remove the query about invoices from the list")
    assert target == "remove the query about invoices from the list"[:50]
