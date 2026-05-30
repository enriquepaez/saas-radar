"""Tests para el agente heurístico con LLM: heuristic_tuner.py.

Cubre los 6 acceptance criteria de la feature #21:
1. Schema válido genera sugerencias correctas.
2. Dedup contra config existente.
3. dry-run no escribe en BD.
4. Recurrence incrementa en segunda llamada con mismo (type, target).
5. Schema inválido no crashea.
6. Reglas A5/A6/A7 en propose_all_changes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from saas_radar.agents.heuristic_tuner import (
    generate_heuristic_suggestions,
    persist_heuristic_suggestions,
)
from saas_radar.agents.tuning_rules import (
    propose_add_phrases_from_llm,
    propose_add_queries_from_llm,
    propose_add_subreddits_from_llm,
    propose_all_changes,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_meta_json(tmp_path: Path, recurring_niches=None, discovered_subreddits=None, empty_queries=None) -> str:
    """Crea un meta-JSON mínimo para tests."""
    meta = {
        "recurring_niches": recurring_niches or [
            {"niche": "accountants", "count": 3},
            {"niche": "property managers", "count": 2},
        ],
        "discovered_subreddits": discovered_subreddits or [
            {"subreddit": "legaltech", "posts": 5},
        ],
        "empty_queries": empty_queries or ["some obsolete query"],
        "subreddit_signal": [],
        "silent_subreddits": [],
        "pain_categories": [],
        "recommendations": [],
        "summary": {},
    }
    p = tmp_path / "test_meta.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return str(p)


def make_top_posts_df() -> pd.DataFrame:
    """Crea un DataFrame de posts de prueba."""
    return pd.DataFrame([
        {
            "title": "We track invoices in Excel",
            "text": "It takes us hours to reconcile every month. We manually copy paste everything.",
            "subreddit": "Accounting",
            "semantic_score": 8.5,
        },
        {
            "title": "Looking for tenant management software",
            "text": "We manage 50 properties and use spreadsheets to track maintenance requests.",
            "subreddit": "PropertyManagement",
            "semantic_score": 7.2,
        },
    ])


def _make_db(tmp_path: Path) -> str:
    """Crea una BD SQLite temporal con la tabla meta_recommendations."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE meta_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            type TEXT NOT NULL,
            target TEXT NOT NULL,
            recurrence INTEGER DEFAULT 1,
            acted INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


_VALID_LLM_RESPONSE = {
    "new_queries": [
        {"query": "how do you track tenant repairs", "rationale": "high frequency pain in propertymanagement"},
        {"query": "invoice reconciliation spreadsheet", "rationale": "accountants manually reconcile"},
    ],
    "new_subreddits": [
        {"name": "legaltech", "rationale": "emerging legal tech pain"},
    ],
    "new_phrases": [
        {"phrase": "manually reconcile", "weight": 3, "rationale": "strong workaround signal"},
    ],
}


# ── Test 1: Schema válido genera sugerencias correctas ────────────────────────


def test_schema_valido_genera_sugerencias(tmp_path):
    """Un LLM que devuelve JSON válido produce el dict de sugerencias correcto."""
    meta_path = make_meta_json(tmp_path)
    top_df = make_top_posts_df()

    with patch("saas_radar.agents.heuristic_tuner.call_llm", return_value=_VALID_LLM_RESPONSE):
        result = generate_heuristic_suggestions(meta_path, top_df, provider="claude")

    # Las queries y subreddits devueltos por el LLM que NO están en config
    assert "new_queries" in result
    assert "new_subreddits" in result
    assert "new_phrases" in result
    # "how do you track tenant repairs" no está en PAIN_SEARCH_QUERIES → debe aparecer
    queries = [q["query"] for q in result["new_queries"]]
    assert "how do you track tenant repairs" in queries
    # "legaltech" puede estar o no en SUBREDDITS; si no está, debe aparecer
    # (en config actual SUBREDDITS no contiene "legaltech" → debe aparecer)
    subs = [s["name"] for s in result["new_subreddits"]]
    assert "legaltech" in subs
    # frase no en config → debe aparecer
    phrases = [p["phrase"] for p in result["new_phrases"]]
    assert "manually reconcile" in phrases


# ── Test 2: Dedup contra config existente ─────────────────────────────────────


def test_dedup_no_incluye_query_existente_en_config(tmp_path):
    """Queries ya en PAIN_SEARCH_QUERIES no aparecen en el resultado."""
    meta_path = make_meta_json(tmp_path)
    top_df = make_top_posts_df()

    # "I use Excel to track" ya está en PAIN_SEARCH_QUERIES
    llm_response = {
        "new_queries": [
            {"query": "I use Excel to track", "rationale": "duplicated"},
            {"query": "unique new query about tenant repairs", "rationale": "new"},
        ],
        "new_subreddits": [],
        "new_phrases": [],
    }

    with patch("saas_radar.agents.heuristic_tuner.call_llm", return_value=llm_response):
        result = generate_heuristic_suggestions(meta_path, top_df, provider="claude")

    queries = [q["query"] for q in result["new_queries"]]
    assert "I use Excel to track" not in queries
    assert "unique new query about tenant repairs" in queries


def test_dedup_no_incluye_subreddit_existente_en_config(tmp_path):
    """Subreddits ya en SUBREDDITS no aparecen en el resultado."""
    meta_path = make_meta_json(tmp_path)
    top_df = make_top_posts_df()

    # "zapier" ya está en SUBREDDITS
    llm_response = {
        "new_queries": [],
        "new_subreddits": [
            {"name": "zapier", "rationale": "already exists"},
            {"name": "totally_new_sub", "rationale": "new"},
        ],
        "new_phrases": [],
    }

    with patch("saas_radar.agents.heuristic_tuner.call_llm", return_value=llm_response):
        result = generate_heuristic_suggestions(meta_path, top_df, provider="claude")

    subs = [s["name"] for s in result["new_subreddits"]]
    assert "zapier" not in subs
    assert "totally_new_sub" in subs


def test_dedup_no_incluye_frase_existente_en_config(tmp_path):
    """Frases ya en PAIN_SIGNAL_PHRASES no aparecen en el resultado."""
    meta_path = make_meta_json(tmp_path)
    top_df = make_top_posts_df()

    # "i use excel to" ya está en PAIN_SIGNAL_PHRASES (lowercase match)
    llm_response = {
        "new_queries": [],
        "new_subreddits": [],
        "new_phrases": [
            {"phrase": "i use excel to", "weight": 3, "rationale": "existing"},
            {"phrase": "completely new pain phrase", "weight": 2, "rationale": "new"},
        ],
    }

    with patch("saas_radar.agents.heuristic_tuner.call_llm", return_value=llm_response):
        result = generate_heuristic_suggestions(meta_path, top_df, provider="claude")

    phrases = [p["phrase"] for p in result["new_phrases"]]
    assert "i use excel to" not in phrases
    assert "completely new pain phrase" in phrases


# ── Test 3: dry-run no escribe en BD ──────────────────────────────────────────


def test_dry_run_no_llama_a_persist(tmp_path):
    """Con --dry-run, persist_heuristic_suggestions no se invoca."""
    meta_path = make_meta_json(tmp_path)

    # Simular llamada CLI con --dry-run
    with patch("saas_radar.agents.heuristic_tuner.call_llm", return_value=_VALID_LLM_RESPONSE), \
         patch("saas_radar.agents.heuristic_tuner.persist_heuristic_suggestions") as mock_persist:
        from saas_radar.agents.heuristic_tuner import main
        exit_code = main(["--meta-json", meta_path, "--provider", "claude", "--dry-run"])

    assert exit_code == 0
    mock_persist.assert_not_called()


# ── Test 4: Recurrence incrementa en segunda llamada ─────────────────────────


def test_recurrence_incrementa_en_segunda_insercion(tmp_path):
    """Segunda llamada con mismo (type, target) sube recurrence de 1 a 2."""
    db_path = _make_db(tmp_path)

    suggestions = {
        "new_queries": [{"query": "how to track repairs", "rationale": "test"}],
        "new_subreddits": [],
        "new_phrases": [],
    }

    # Primera llamada → recurrence = 1
    persist_heuristic_suggestions(suggestions, db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT recurrence FROM meta_recommendations WHERE type = ? AND target = ?",
        ("query_suggestion", "how to track repairs"),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1

    # Segunda llamada → recurrence = 2
    persist_heuristic_suggestions(suggestions, db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT recurrence FROM meta_recommendations WHERE type = ? AND target = ?",
        ("query_suggestion", "how to track repairs"),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 2


def test_persist_inserta_los_tres_tipos(tmp_path):
    """persist_heuristic_suggestions inserta correctamente los 3 tipos de sugerencias."""
    db_path = _make_db(tmp_path)

    suggestions = {
        "new_queries": [{"query": "test query", "rationale": "r"}],
        "new_subreddits": [{"name": "test_sub", "rationale": "r"}],
        "new_phrases": [{"phrase": "test phrase", "weight": 2, "rationale": "r"}],
    }

    count = persist_heuristic_suggestions(suggestions, db_path)
    assert count == 3

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT type, target, recurrence, acted FROM meta_recommendations ORDER BY id"
    ).fetchall()
    conn.close()

    types = {r[0] for r in rows}
    assert "query_suggestion" in types
    assert "subreddit_suggestion" in types
    assert "phrase_suggestion" in types
    for row in rows:
        assert row[2] == 1  # recurrence = 1
        assert row[3] == 0  # acted = 0


# ── Test 5: Schema inválido no crashea ───────────────────────────────────────


def test_schema_invalido_devuelve_vacio_sin_excepcion(tmp_path):
    """LLM con schema inválido retorna dict vacío sin propagar excepción."""
    meta_path = make_meta_json(tmp_path)
    top_df = make_top_posts_df()

    bad_responses = [
        {"bad": "schema"},
        {"new_queries": "not a list", "new_subreddits": [], "new_phrases": []},
        {"new_queries": [{"wrong_key": "foo"}], "new_subreddits": [], "new_phrases": []},
        None,
    ]

    for bad_resp in bad_responses:
        with patch("saas_radar.agents.heuristic_tuner.call_llm", return_value=bad_resp):
            result = generate_heuristic_suggestions(meta_path, top_df, provider="claude")
        assert result == {"new_queries": [], "new_subreddits": [], "new_phrases": []}, \
            f"Esperaba dict vacío para respuesta: {bad_resp!r}"


def test_meta_json_inexistente_devuelve_vacio():
    """Meta-JSON que no existe devuelve dict vacío sin excepción."""
    top_df = make_top_posts_df()
    with patch("saas_radar.agents.heuristic_tuner.call_llm") as mock_llm:
        result = generate_heuristic_suggestions("/nonexistent/path.json", top_df, provider="claude")
    mock_llm.assert_not_called()
    assert result == {"new_queries": [], "new_subreddits": [], "new_phrases": []}


# ── Test 6: Reglas A5/A6/A7 en propose_all_changes ───────────────────────────


def test_regla_a5_propone_add_query_con_recurrence_2():
    """A5: meta_recommendation query_suggestion con recurrence >= 2 genera propuesta add_query."""
    meta_recs = [
        {"type": "query_suggestion", "target": "how to track tenant repairs", "recurrence": 2, "acted": 0},
    ]
    proposals = propose_add_queries_from_llm(meta_recs)
    assert len(proposals) == 1
    assert proposals[0].action == "add_query"
    assert proposals[0].target == "how to track tenant repairs"


def test_regla_a5_no_propone_con_recurrence_1():
    """A5: recurrence < 2 no genera propuesta."""
    meta_recs = [
        {"type": "query_suggestion", "target": "low recurrence query", "recurrence": 1, "acted": 0},
    ]
    proposals = propose_add_queries_from_llm(meta_recs)
    assert len(proposals) == 0


def test_regla_a5_no_propone_con_acted_1():
    """A5: acted=1 no genera propuesta (ya fue actuado)."""
    meta_recs = [
        {"type": "query_suggestion", "target": "already acted query", "recurrence": 3, "acted": 1},
    ]
    proposals = propose_add_queries_from_llm(meta_recs)
    assert len(proposals) == 0


def test_regla_a6_propone_add_subreddit_con_recurrence_2():
    """A6: subreddit_suggestion con recurrence >= 2 genera propuesta add_subreddit."""
    meta_recs = [
        {"type": "subreddit_suggestion", "target": "legaltech", "recurrence": 3, "acted": 0},
    ]
    proposals = propose_add_subreddits_from_llm(meta_recs)
    assert len(proposals) == 1
    assert proposals[0].action == "add_subreddit"
    assert proposals[0].target == "legaltech"


def test_regla_a7_propone_add_phrase_con_recurrence_2():
    """A7: phrase_suggestion con recurrence >= 2 genera propuesta add_phrase."""
    meta_recs = [
        {"type": "phrase_suggestion", "target": "manually reconcile invoices", "recurrence": 2, "acted": 0},
    ]
    proposals = propose_add_phrases_from_llm(meta_recs)
    assert len(proposals) == 1
    assert proposals[0].action == "add_phrase"
    assert proposals[0].target == "manually reconcile invoices"


def test_propose_all_changes_incluye_a5_a6_a7():
    """propose_all_changes integra A5/A6/A7 junto a las reglas deterministas."""
    meta_recs = [
        {"type": "query_suggestion", "target": "new query from llm", "recurrence": 2, "acted": 0},
        {"type": "subreddit_suggestion", "target": "new_sub", "recurrence": 2, "acted": 0},
        {"type": "phrase_suggestion", "target": "new pain phrase", "recurrence": 3, "acted": 0},
    ]
    proposals = propose_all_changes(
        runs=[],
        meta_recommendations=meta_recs,
        current_high_signal=set(),
        current_subreddits=[],
        current_queries=[],
    )
    actions = {p.action for p in proposals}
    assert "add_query" in actions
    assert "add_subreddit" in actions
    assert "add_phrase" in actions


def test_propose_all_changes_orden_conservador():
    """A5-A7 aparecen DESPUÉS de las reglas conservadoras A1-A4."""
    runs = [
        {
            "subreddit_signal": [
                {"subreddit": "noisysub", "posts_analyzed": 5, "with_problem": 0, "with_payment_signal": 0},
                {"subreddit": "noisysub", "posts_analyzed": 5, "with_problem": 0, "with_payment_signal": 0},
            ],
            "silent_subreddits": [],
            "empty_queries": ["dead query"],
        },
        {
            "subreddit_signal": [
                {"subreddit": "noisysub", "posts_analyzed": 5, "with_problem": 0, "with_payment_signal": 0},
            ],
            "silent_subreddits": [],
            "empty_queries": ["dead query"],
        },
        {
            "subreddit_signal": [],
            "silent_subreddits": [],
            "empty_queries": ["dead query"],
        },
    ]
    meta_recs = [
        {"type": "query_suggestion", "target": "new llm query", "recurrence": 2, "acted": 0},
    ]
    proposals = propose_all_changes(
        runs=runs,
        meta_recommendations=meta_recs,
        current_high_signal=set(),
        current_subreddits=["noisysub"],
        current_queries=["dead query"],
    )
    actions = [p.action for p in proposals]
    assert "add_query" in actions
    # remove_query debe aparecer antes que add_query
    if "remove_query" in actions:
        assert actions.index("remove_query") < actions.index("add_query")
