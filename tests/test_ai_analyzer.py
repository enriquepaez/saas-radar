"""Tests del orquestador ai_analyzer: flujo completo, cache defensivo y abort."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_post(i: int, subreddit: str = "entrepreneur") -> pd.Series:
    """Crea un pd.Series que simula un post de Reddit."""
    return pd.Series(
        {
            "id": f"post_{i}",
            "subreddit": subreddit,
            "title": f"Title {i}",
            "text": f"This is the body of post {i} with enough content to pass the filter.",
            "score": 20 + i,
            "num_comments": 5 + i,
            "url": f"https://reddit.com/r/{subreddit}/comments/post_{i}",
            "source": "post",
            "created_utc": 1700000000.0,
            "rank_score": 0.8 - i * 0.1,
            "semantic_score": 5.0 - i * 0.5,
        }
    )


def _make_extraction(i: int, subreddit: str = "entrepreneur") -> dict:
    """Crea un dict de extracción válida."""
    return {
        "has_problem": True,
        "_error": False,
        "_post_id": f"post_{i}",
        "_subreddit": subreddit,
        "_score": 20 + i,
        "_num_comments": 5 + i,
        "_url": f"https://reddit.com/r/{subreddit}/comments/post_{i}",
        "_title": f"Title {i}",
        "who_has_it": f"freelancer who does invoicing for clients {i}",
        "problem_description": f"They track invoices in spreadsheets manually and lose track of payments {i}",
        "workflow_context": f"Each month they reconcile spreadsheet invoices manually {i}",
        "current_workaround": "spreadsheets and email",
        "payment_signal": True,
        "payment_quote": "I would pay for a better solution",
        "competitor_mentions": [],
        "key_quote": f"I hate tracking invoices in spreadsheets {i}",
    }


def _make_synthesis_response(n_opps: int = 1) -> dict:
    """Genera una respuesta de síntesis válida con n oportunidades."""
    opps = []
    for k in range(1, n_opps + 1):
        opps.append(
            {
                "id": k,
                "product_name": f"InvoiceTracker for Freelancers {k}",
                "niche": "solo freelancers who invoice clients manually",
                "core_problem": "Track invoices in spreadsheets",
                "why_gap_exists": "No tool focuses on solo freelancers specifically",
                "evidence_items": [1, 2],
                "evidence_quotes": [
                    "[item 1] I track invoices in spreadsheets manually and lose track",
                    "[item 2] I reconcile spreadsheet invoices every month manually",
                ],
                "concrete_workaround": "Excel spreadsheets",
                "workaround_cost": "4 hours/week",
                "mvp_scope": "Invoice CRUD, payment status, reminder emails",
                "monetization": "$19/mo — solo freelancers pay for time saving",
                "estimated_price": "$19-29/month",
                "competitor_gap": "QuickBooks is too expensive for solos",
                "mentioned_competitors": ["QuickBooks"],
                "payment_signal": "high",
                "payment_evidence": "I would pay for a better solution",
                "solo_buildable": True,
                "mvp_weeks": 6,
                "priority_score": 8,
                "priority_reason": "Score 8/10 because: 2 evidence items + high payment signal",
            }
        )
    return {
        "opportunities": opps,
        "top_3_recommended": list(range(1, min(n_opps + 1, 4))),
        "disqualified_ideas": [],
    }


def _init_test_db(db_path: str) -> None:
    """Crea las tablas mínimas necesarias en una BD de prueba."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at              TEXT NOT NULL,
            posts_analyzed      INTEGER DEFAULT 0,
            valid_extractions   INTEGER DEFAULT 0,
            opportunities_count INTEGER DEFAULT 0,
            json_path           TEXT,
            status              TEXT,
            duration_sec        INTEGER,
            error_message       TEXT,
            ai_provider         TEXT
        );
        CREATE TABLE IF NOT EXISTS opportunities (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id                INTEGER,
            product_name          TEXT,
            niche                 TEXT,
            core_problem          TEXT,
            why_gap_exists        TEXT,
            concrete_workaround   TEXT,
            workaround_cost       TEXT,
            mvp_scope             TEXT,
            estimated_price       TEXT,
            monetization          TEXT,
            competitor_gap        TEXT,
            mentioned_competitors TEXT,
            payment_signal        TEXT,
            payment_evidence      TEXT,
            solo_buildable        INTEGER,
            mvp_weeks             INTEGER,
            priority_score        INTEGER,
            priority_reason       TEXT,
            evidence_items        TEXT,
            evidence_quotes       TEXT,
            reviewed              INTEGER DEFAULT 0,
            starred               INTEGER DEFAULT 0,
            discarded             INTEGER DEFAULT 0,
            user_notes            TEXT,
            created_at            TEXT,
            canonical_id          INTEGER
        );
        CREATE TABLE IF NOT EXISTS reddit_posts (
            id TEXT PRIMARY KEY,
            source TEXT, subreddit TEXT, title TEXT, text TEXT,
            score INTEGER, upvote_ratio REAL, num_comments INTEGER,
            created_utc REAL, url TEXT, flair TEXT, search_query TEXT,
            clean_text TEXT, category TEXT, semantic_score REAL
        );
        CREATE TABLE IF NOT EXISTS reddit_comments (
            comment_id TEXT PRIMARY KEY, post_id TEXT, subreddit TEXT,
            text TEXT, score INTEGER, created_utc REAL,
            clean_text TEXT, category TEXT
        );
        CREATE TABLE IF NOT EXISTS meta_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER, type TEXT, action TEXT, target TEXT,
            recurrence INTEGER DEFAULT 1, acted INTEGER DEFAULT 0, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS opportunity_gtm (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER
        );
    """)
    conn.close()


# ── Test 1: flujo completo ok ─────────────────────────────────────────────────


def test_full_pipeline_ok(tmp_path):
    """Flujo completo con 3 posts → extracción → síntesis → BD tiene 1 fila ok."""
    db_file = tmp_path / "test.db"
    _init_test_db(str(db_file))

    posts_df = pd.DataFrame([_make_post(i) for i in range(3)])
    extractions = [_make_extraction(i) for i in range(3)]
    synthesis_raw = _make_synthesis_response(n_opps=1)

    with (
        patch("saas_radar.analysis.ai_analyzer.init_db"),
        patch("saas_radar.analysis.ai_analyzer.load_pain_posts", return_value=posts_df),
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", return_value=extractions),
        patch("saas_radar.analysis.ai_analyzer._clean_extractions", return_value=extractions),
        patch("saas_radar.analysis.ai_analyzer.build_synthesis_prompt", return_value=("PROMPT", extractions)),
        patch("saas_radar.analysis.ai_analyzer.call_llm", return_value=synthesis_raw),
        patch("saas_radar.analysis.ai_analyzer._validate_synthesis", return_value=synthesis_raw),
        patch("saas_radar.analysis.ai_analyzer._save_extractions_cache"),
        patch("saas_radar.analysis.ai_analyzer._print_results"),
    ):
        from saas_radar.analysis.ai_analyzer import run_ai_analysis

        result = run_ai_analysis(
            top_n=3,
            provider="claude",
            use_cached_extractions=False,
            output_path=str(tmp_path / "runs"),
            db_path=str(db_file),
        )

    assert result["status"] == "ok"
    assert len(result["opportunities"]) == 1

    # Verificar la BD
    conn = sqlite3.connect(str(db_file))
    rows = conn.execute("SELECT status FROM analysis_runs").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "ok"


# ── Test 2: abort por < 2 extracciones válidas ────────────────────────────────


def test_abort_too_few_valid(tmp_path):
    """Con solo 1 extracción válida, la función retorna status='failed' sin llamar a call_llm."""
    db_file = tmp_path / "test.db"
    _init_test_db(str(db_file))

    posts_df = pd.DataFrame([_make_post(i) for i in range(3)])
    one_extraction = [_make_extraction(0)]  # Solo 1 válida

    mock_call_llm = MagicMock()

    with (
        patch("saas_radar.analysis.ai_analyzer.init_db"),
        patch("saas_radar.analysis.ai_analyzer.load_pain_posts", return_value=posts_df),
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", return_value=one_extraction),
        patch("saas_radar.analysis.ai_analyzer._clean_extractions", return_value=one_extraction),
        patch("saas_radar.analysis.ai_analyzer._save_extractions_cache"),
        patch("saas_radar.analysis.ai_analyzer.call_llm", mock_call_llm),
    ):
        from saas_radar.analysis.ai_analyzer import run_ai_analysis

        result = run_ai_analysis(
            top_n=3,
            provider="claude",
            use_cached_extractions=False,
            output_path=str(tmp_path / "runs"),
            db_path=str(db_file),
        )

    assert result["status"] == "failed"
    assert result["opportunities"] == []
    # call_llm NO debe haberse llamado
    mock_call_llm.assert_not_called()


# ── Test 3: cache defensivo ───────────────────────────────────────────────────


def test_defensive_cache(tmp_path):
    """Cache previo con 2 válidas + _clean_extractions devuelve [] → NO sobrescribe el cache."""
    db_file = tmp_path / "test.db"
    _init_test_db(str(db_file))
    cache_file = tmp_path / "extractions_cache.json"

    # Cache previo con 2 extracciones válidas
    prev_extractions = [_make_extraction(i) for i in range(2)]
    cache_file.write_text(json.dumps(prev_extractions), encoding="utf-8")
    original_content = cache_file.read_text(encoding="utf-8")

    posts_df = pd.DataFrame([_make_post(i) for i in range(3)])

    # La nueva extracción falla (retorna lista de errores)
    failed_extractions = [{"has_problem": False, "_error": True, "_title": "t", "_subreddit": "s"} for _ in range(3)]

    with (
        patch("saas_radar.analysis.ai_analyzer.init_db"),
        patch("saas_radar.analysis.ai_analyzer.load_pain_posts", return_value=posts_df),
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", return_value=failed_extractions),
        patch("saas_radar.analysis.ai_analyzer._clean_extractions", return_value=[]),
    ):
        from saas_radar.analysis.ai_analyzer import _save_extractions_cache

        # Simular la extracción fallida y el intento de guardar el cache
        _save_extractions_cache([], str(cache_file))

    # El archivo original NO debe haberse sobrescrito
    assert cache_file.read_text(encoding="utf-8") == original_content

    # Debe existir el archivo .failed.json
    failed_cache = Path(str(cache_file) + ".failed.json")
    assert failed_cache.exists()
    failed_data = json.loads(failed_cache.read_text(encoding="utf-8"))
    assert failed_data == []


# ── Test 4: use_cached_extractions salta la extracción ───────────────────────


def test_use_cached_extractions(tmp_path):
    """Con use_cached_extractions=True y cache existente, no se llama a extract_problem_deep/batch."""
    db_file = tmp_path / "test.db"
    _init_test_db(str(db_file))
    cache_file = tmp_path / "extractions_cache.json"

    # Cache con 2 extracciones válidas
    cached = [_make_extraction(i) for i in range(2)]
    cache_file.write_text(json.dumps(cached), encoding="utf-8")

    posts_df = pd.DataFrame([_make_post(i) for i in range(2)])
    synthesis_raw = _make_synthesis_response(n_opps=1)

    mock_batch = MagicMock()
    mock_deep = MagicMock()

    with (
        patch("saas_radar.analysis.ai_analyzer.init_db"),
        patch("saas_radar.analysis.ai_analyzer.load_pain_posts", return_value=posts_df),
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", mock_batch),
        patch("saas_radar.analysis.ai_analyzer.extract_problem_deep", mock_deep),
        patch("saas_radar.analysis.ai_analyzer._clean_extractions", return_value=cached),
        patch("saas_radar.analysis.ai_analyzer.build_synthesis_prompt", return_value=("PROMPT", cached)),
        patch("saas_radar.analysis.ai_analyzer.call_llm", return_value=synthesis_raw),
        patch("saas_radar.analysis.ai_analyzer._validate_synthesis", return_value=synthesis_raw),
        patch("saas_radar.analysis.ai_analyzer._print_results"),
    ):
        from saas_radar.analysis.ai_analyzer import run_ai_analysis

        result = run_ai_analysis(
            top_n=2,
            provider="claude",
            use_cached_extractions=True,
            extractions_cache_path=str(cache_file),
            output_path=str(tmp_path / "runs"),
            db_path=str(db_file),
        )

    # Verificar que las funciones de extracción NO se llamaron
    mock_batch.assert_not_called()
    mock_deep.assert_not_called()
    assert result["status"] == "ok"


# ── Test 5: cache defensivo no escribe si no hay cache previo ─────────────────


def test_save_extractions_cache_no_prev(tmp_path):
    """Sin cache previo + new_data vacío → escribe igualmente (no hay nada que proteger)."""
    cache_file = tmp_path / "extractions_cache.json"
    assert not cache_file.exists()

    from saas_radar.analysis.ai_analyzer import _save_extractions_cache

    _save_extractions_cache([], str(cache_file))

    # Debe existir el archivo aunque esté vacío
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert data == []

    # NO debe existir el .failed.json porque no había nada que proteger
    failed = Path(str(cache_file) + ".failed.json")
    assert not failed.exists()


# ── Test 6: cache defensivo sobrescribe cuando hay datos nuevos ───────────────


def test_save_extractions_cache_with_new_data(tmp_path):
    """Con new_data no vacío → siempre sobrescribe el cache."""
    cache_file = tmp_path / "extractions_cache.json"
    cache_file.write_text(json.dumps([{"old": True}]), encoding="utf-8")

    new_data = [_make_extraction(0), _make_extraction(1)]

    from saas_radar.analysis.ai_analyzer import _save_extractions_cache

    _save_extractions_cache(new_data, str(cache_file))

    saved = json.loads(cache_file.read_text())
    assert len(saved) == 2
    assert saved[0]["_post_id"] == "post_0"


# ── Test 7: status 'partial' cuando no hay oportunidades ─────────────────────


def test_partial_status_when_no_opportunities(tmp_path):
    """Con síntesis que devuelve 0 oportunidades válidas → status='partial'."""
    db_file = tmp_path / "test.db"
    _init_test_db(str(db_file))

    posts_df = pd.DataFrame([_make_post(i) for i in range(3)])
    extractions = [_make_extraction(i) for i in range(3)]
    # Síntesis sin oportunidades válidas
    empty_synthesis = {
        "opportunities": [],
        "top_3_recommended": [],
        "disqualified_ideas": [{"idea": "X", "rule_violated": "RULE 1"}],
    }

    with (
        patch("saas_radar.analysis.ai_analyzer.init_db"),
        patch("saas_radar.analysis.ai_analyzer.load_pain_posts", return_value=posts_df),
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", return_value=extractions),
        patch("saas_radar.analysis.ai_analyzer._clean_extractions", return_value=extractions),
        patch("saas_radar.analysis.ai_analyzer.build_synthesis_prompt", return_value=("PROMPT", extractions)),
        patch("saas_radar.analysis.ai_analyzer.call_llm", return_value=empty_synthesis),
        patch("saas_radar.analysis.ai_analyzer._validate_synthesis", return_value=empty_synthesis),
        patch("saas_radar.analysis.ai_analyzer._save_extractions_cache"),
        patch("saas_radar.analysis.ai_analyzer._print_results"),
    ):
        from saas_radar.analysis.ai_analyzer import run_ai_analysis

        result = run_ai_analysis(
            top_n=3,
            provider="claude",
            use_cached_extractions=False,
            output_path=str(tmp_path / "runs"),
            db_path=str(db_file),
        )

    assert result["status"] == "partial"
    assert result["opportunities"] == []

    conn = sqlite3.connect(str(db_file))
    rows = conn.execute("SELECT status FROM analysis_runs").fetchall()
    conn.close()
    assert rows[0][0] == "partial"


# ── Test 8: LLM devuelve None en síntesis ────────────────────────────────────


def test_llm_none_in_synthesis(tmp_path):
    """Cuando call_llm devuelve None en síntesis → status='failed' persistido en BD."""
    db_file = tmp_path / "test.db"
    _init_test_db(str(db_file))

    posts_df = pd.DataFrame([_make_post(i) for i in range(3)])
    extractions = [_make_extraction(i) for i in range(3)]

    with (
        patch("saas_radar.analysis.ai_analyzer.init_db"),
        patch("saas_radar.analysis.ai_analyzer.load_pain_posts", return_value=posts_df),
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", return_value=extractions),
        patch("saas_radar.analysis.ai_analyzer._clean_extractions", return_value=extractions),
        patch("saas_radar.analysis.ai_analyzer.build_synthesis_prompt", return_value=("PROMPT", extractions)),
        patch("saas_radar.analysis.ai_analyzer.call_llm", return_value=None),
        patch("saas_radar.analysis.ai_analyzer._save_extractions_cache"),
    ):
        from saas_radar.analysis.ai_analyzer import run_ai_analysis

        result = run_ai_analysis(
            top_n=3,
            provider="claude",
            use_cached_extractions=False,
            output_path=str(tmp_path / "runs"),
            db_path=str(db_file),
        )

    assert result["status"] == "failed"

    conn = sqlite3.connect(str(db_file))
    rows = conn.execute("SELECT status, error_message FROM analysis_runs").fetchall()
    conn.close()
    assert rows[0][0] == "failed"
    assert "None" in rows[0][1]


# ── Test 9: extracción usa EXTRACTION_PROVIDER aunque AI_PROVIDER sea distinto ──


def test_extraction_uses_extraction_provider(tmp_path):
    """Verifica que extract_problem_deep y run_batch_extraction se llaman con
    el extraction_provider recibido como argumento, independiente del provider de síntesis.

    Escenario: extraction_provider='groq' (leído de config en run_ai_analysis).
    Con 2 posts (≤ DEEP_EXTRACTION_THRESHOLD=30) se usa extract_problem_deep.
    Con 31 posts (> DEEP_EXTRACTION_THRESHOLD=30) se usa run_batch_extraction.
    En ambos casos el provider pasado a la función de extracción debe ser 'groq'.

    _extract_and_cache recibe extraction_provider como argumento explícito;
    NO lee config.* (architecture.md §3: solo run_ai_analysis puede leer config).
    """
    import saas_radar.analysis.ai_analyzer as ai_mod

    # ── Caso A: modo deep (N=2 <= 30) ──────────────────────────────────────
    mock_deep = MagicMock(side_effect=lambda row, provider: _make_extraction(0))

    with (
        patch("saas_radar.analysis.ai_analyzer.extract_problem_deep", mock_deep),
        patch("saas_radar.analysis.ai_analyzer._save_extractions_cache"),
    ):
        posts = [_make_post(i) for i in range(2)]
        ai_mod._extract_and_cache(posts, "cache.json", extraction_provider="groq")

    # Todas las llamadas deben haber usado provider='groq'
    for c in mock_deep.call_args_list:
        assert c.kwargs.get("provider") == "groq" or c.args[1] == "groq", (
            f"extract_problem_deep llamado con provider incorrecto: {c}"
        )

    # ── Caso B: modo batch (N=31 > 30) ──────────────────────────────────────
    mock_batch = MagicMock(return_value=[_make_extraction(0), _make_extraction(1)])

    with (
        patch("saas_radar.analysis.ai_analyzer.run_batch_extraction", mock_batch),
        patch("saas_radar.analysis.ai_analyzer._save_extractions_cache"),
    ):
        posts_batch = [_make_post(i) for i in range(31)]
        ai_mod._extract_and_cache(posts_batch, "cache.json", extraction_provider="groq")

    mock_batch.assert_called_once()
    _, kwargs = mock_batch.call_args
    assert kwargs.get("provider") == "groq", (
        f"run_batch_extraction llamado con provider={kwargs.get('provider')!r}, esperado 'groq'"
    )
