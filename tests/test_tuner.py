"""Tests del tuning agent (fase A2).

Cubre loaders, priorizacion + cap, renderizado del report, diff simulado y
wiring end-to-end del CLI. Sin red, sin LLM. Usa BD temporal SQLite en disco
(la mayoria de drivers falla con `:memory:` cuando reabrimos con otro
conexion).
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

from saas_radar.agents.tuner import (
    ReportData,
    load_meta_recommendations,
    load_recent_runs,
    main,
    prioritize_and_cap,
    render_config_diff,
    render_report,
)
from saas_radar.agents.tuning_rules import Proposal

# ── Helpers ──────────────────────────────────────────────────────────────


def _write_meta(path: Path, subreddit_signal=None, silent_subreddits=None, empty_queries=None):
    payload = {
        "subreddit_signal": subreddit_signal or [],
        "silent_subreddits": silent_subreddits or [],
        "empty_queries": empty_queries or [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sub(name, posts, problem, payment):
    return {
        "subreddit": name,
        "posts_analyzed": posts,
        "with_problem": problem,
        "with_payment_signal": payment,
        "hit_rate": problem / posts if posts else 0,
    }


def _make_meta_recs_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE meta_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            type TEXT,
            target TEXT,
            recurrence INTEGER,
            acted INTEGER,
            created_at TEXT
        )"""
    )
    for r in rows:
        conn.execute(
            "INSERT INTO meta_recommendations(run_id, type, target, recurrence, acted, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                r.get("run_id", 1),
                r["type"],
                r["target"],
                r.get("recurrence", 1),
                r.get("acted", 0),
                r.get("created_at", "2026-04-18T00:00:00Z"),
            ),
        )
    conn.commit()
    conn.close()


# ── load_recent_runs ─────────────────────────────────────────────────────


class TestLoadRecentRuns:
    def test_carga_los_mas_recientes_en_orden_asc(self, tmp_path):
        _write_meta(tmp_path / "2026-04-20T000000_meta.json", silent_subreddits=["c"])
        _write_meta(tmp_path / "2026-04-18T000000_meta.json", silent_subreddits=["a"])
        _write_meta(tmp_path / "2026-04-19T000000_meta.json", silent_subreddits=["b"])

        runs = load_recent_runs(str(tmp_path), lookback=10)

        assert len(runs) == 3
        assert [r["silent_subreddits"] for r in runs] == [["a"], ["b"], ["c"]]

    def test_respeta_lookback(self, tmp_path):
        for ts in ("01", "02", "03", "04"):
            _write_meta(tmp_path / f"2026-04-{ts}T000000_meta.json", silent_subreddits=[ts])

        runs = load_recent_runs(str(tmp_path), lookback=2)

        assert [r["silent_subreddits"] for r in runs] == [["03"], ["04"]]

    def test_salta_json_corrupto_y_sigue(self, tmp_path, capsys):
        _write_meta(tmp_path / "2026-04-18T000000_meta.json", silent_subreddits=["ok"])
        (tmp_path / "2026-04-19T000000_meta.json").write_text("{not valid json", encoding="utf-8")

        runs = load_recent_runs(str(tmp_path), lookback=10)

        assert len(runs) == 1
        assert runs[0]["silent_subreddits"] == ["ok"]
        err = capsys.readouterr().err
        assert "corrupto" in err

    def test_dir_vacio_devuelve_lista_vacia(self, tmp_path):
        assert load_recent_runs(str(tmp_path), lookback=5) == []


# ── load_meta_recommendations ────────────────────────────────────────────


class TestLoadMetaRecommendations:
    def test_lee_filas_como_dicts(self, tmp_path):
        db = tmp_path / "saas.db"
        _make_meta_recs_db(
            db,
            [
                {"type": "boost_subreddit", "target": "notion", "recurrence": 2},
                {"type": "remove_subreddit", "target": "startups", "recurrence": 3},
            ],
        )

        rows = load_meta_recommendations(str(db))

        assert len(rows) == 2
        assert rows[0]["type"] == "boost_subreddit"
        assert rows[1]["target"] == "startups"

    def test_bd_inexistente_devuelve_vacio(self, tmp_path):
        assert load_meta_recommendations(str(tmp_path / "nope.db")) == []

    def test_tabla_ausente_devuelve_vacio(self, tmp_path):
        db = tmp_path / "saas.db"
        sqlite3.connect(str(db)).close()
        assert load_meta_recommendations(str(db)) == []


# ── prioritize_and_cap ───────────────────────────────────────────────────


class TestPrioritizeAndCap:
    def test_orden_accion_conservador_primero(self):
        proposals = [
            Proposal("add_high_signal", "notion", "r"),
            Proposal("remove_query", "foo", "r"),
            Proposal("demote_high_signal", "nocode", "r"),
        ]
        ordered = prioritize_and_cap(proposals, meta_recommendations=[], max_changes=10)
        assert [p.action for p in ordered] == [
            "remove_query",
            "demote_high_signal",
            "add_high_signal",
        ]

    def test_recurrence_desc_dentro_del_mismo_tipo(self):
        proposals = [
            Proposal("add_high_signal", "a", "r"),
            Proposal("add_high_signal", "b", "r"),
            Proposal("add_high_signal", "c", "r"),
        ]
        recs = [
            {"type": "boost_subreddit", "target": "b", "recurrence": 5},
            {"type": "boost_subreddit", "target": "a", "recurrence": 2},
            {"type": "boost_subreddit", "target": "c", "recurrence": 2},
        ]
        ordered = prioritize_and_cap(proposals, recs, max_changes=10)
        # b primero (recurrence 5), empate a/c: alfabetico.
        assert [p.target for p in ordered] == ["b", "a", "c"]

    def test_cap_aplica(self):
        proposals = [Proposal("remove_query", f"q{i}", "r") for i in range(10)]
        ordered = prioritize_and_cap(proposals, [], max_changes=3)
        assert len(ordered) == 3

    def test_cap_negativo_devuelve_todo(self):
        proposals = [Proposal("remove_query", f"q{i}", "r") for i in range(5)]
        ordered = prioritize_and_cap(proposals, [], max_changes=-1)
        assert len(ordered) == 5


# ── render_report ────────────────────────────────────────────────────────


class TestRenderReport:
    def test_report_sin_propuestas(self):
        data = ReportData(
            run_count=3,
            first_ts="2026-04-18T000000",
            last_ts="2026-04-20T000000",
            total_proposals=0,
            applied_proposals=[],
            max_changes=5,
        )
        out = render_report(data, runs_ts=["2026-04-18T000000", "2026-04-19T000000", "2026-04-20T000000"])
        assert "runs analizados: 3" in out
        assert "sin propuestas" in out
        assert "RESUMEN: add=0 demote=0 remove_sub=0 remove_query=0" in out

    def test_report_con_varias_propuestas(self):
        applied = [
            Proposal("remove_query", "Zapier can't", "0 resultados"),
            Proposal("demote_high_signal", "nocode", "5+ silent"),
            Proposal("add_high_signal", "notion", "75% hit"),
        ]
        data = ReportData(
            run_count=7,
            first_ts="2026-04-18T134433",
            last_ts="2026-04-24T100356",
            total_proposals=58,
            applied_proposals=applied,
            max_changes=5,
        )
        out = render_report(data, runs_ts=["2026-04-18T134433", "2026-04-24T100356"])
        assert "proposals totales: 58" in out
        assert "aplicadas (cap 5): 3" in out
        assert "[remove_query" in out
        assert "[add_high_signal" in out
        assert "RESUMEN: add=1 demote=1 remove_sub=0 remove_query=1" in out


# ── render_config_diff ───────────────────────────────────────────────────


class TestRenderConfigDiff:
    def test_genera_pseudo_python(self):
        proposals = [
            Proposal("add_high_signal", "notion", ""),
            Proposal("demote_high_signal", "nocode", ""),
            Proposal("remove_subreddit", "startups", ""),
            Proposal("remove_query", "Zapier can't", ""),
        ]
        diff = render_config_diff(proposals)
        assert 'HIGH_SIGNAL_SUBREDDITS.add("notion")' in diff
        assert 'HIGH_SIGNAL_SUBREDDITS.discard("nocode")' in diff
        assert 'SUBREDDITS.remove("startups")' in diff
        assert 'PAIN_SEARCH_QUERIES.remove("Zapier can\\\'t")' in diff or "Zapier can't" in diff

    def test_sin_propuestas(self):
        assert "sin cambios" in render_config_diff([])


# ── CLI end-to-end ───────────────────────────────────────────────────────


class TestCliMain:
    def _prepare_env(self, tmp_path: Path, monkeypatch):
        """Monta runs-dir + BD temporal y ajusta `config` para el test."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # Run 1: ecommerce con payment -> debe promover
        _write_meta(
            runs_dir / "2026-04-18T000000_meta.json",
            subreddit_signal=[_sub("ecommerce", 5, 5, 3)],
            silent_subreddits=[],
            empty_queries=["dead query"],
        )
        # Run 2: misma query dead, ecommerce repite
        _write_meta(
            runs_dir / "2026-04-19T000000_meta.json",
            subreddit_signal=[_sub("ecommerce", 3, 3, 2)],
            silent_subreddits=[],
            empty_queries=["dead query"],
        )
        # Run 3: dead query sigue muerta -> triggera remove_query
        _write_meta(
            runs_dir / "2026-04-20T000000_meta.json",
            subreddit_signal=[_sub("ecommerce", 2, 2, 1)],
            silent_subreddits=[],
            empty_queries=["dead query"],
        )
        db = tmp_path / "saas.db"
        _make_meta_recs_db(db, [])

        # Monkeypatch config para que las reglas evaluen contra sets conocidos.
        from saas_radar import config as saas_config

        monkeypatch.setattr(saas_config, "HIGH_SIGNAL_SUBREDDITS", set(), raising=False)
        monkeypatch.setattr(saas_config, "SUBREDDITS", ["ecommerce"], raising=False)
        monkeypatch.setattr(saas_config, "PAIN_SEARCH_QUERIES", ["dead query"], raising=False)

        return runs_dir, db

    def test_cli_exit_cero_y_reporta_propuestas(self, tmp_path, monkeypatch):
        runs_dir, db = self._prepare_env(tmp_path, monkeypatch)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(
                [
                    "--runs-dir",
                    str(runs_dir),
                    "--db-path",
                    str(db),
                    "--lookback",
                    "5",
                    "--max-changes",
                    "5",
                    "--show-diff",
                ]
            )
        out = buf.getvalue()

        assert rc == 0
        assert "TUNER DRY-RUN" in out
        assert "runs analizados: 3" in out
        # ecommerce debe promoverse (5+3+2=10 posts, 10 hit, 6 payments)
        assert "add_high_signal" in out
        assert "ecommerce" in out
        # remove_query debe aparecer
        assert "remove_query" in out
        assert "dead query" in out
        # diff simulado presente
        assert "config.py diff simulado" in out
        assert 'HIGH_SIGNAL_SUBREDDITS.add("ecommerce")' in out

    def test_cli_sin_runs_sigue_con_exit_cero(self, tmp_path, monkeypatch):
        empty_runs = tmp_path / "empty"
        empty_runs.mkdir()
        db = tmp_path / "saas.db"
        _make_meta_recs_db(db, [])

        from saas_radar import config as saas_config

        monkeypatch.setattr(saas_config, "HIGH_SIGNAL_SUBREDDITS", set(), raising=False)
        monkeypatch.setattr(saas_config, "SUBREDDITS", [], raising=False)
        monkeypatch.setattr(saas_config, "PAIN_SEARCH_QUERIES", [], raising=False)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--runs-dir", str(empty_runs), "--db-path", str(db)])

        assert rc == 0
        assert "runs analizados: 0" in buf.getvalue()
        assert "sin propuestas" in buf.getvalue()


def test_render_report_snapshot(tmp_path):
    """El formato del report es estable (snapshot contra tests/fixtures/tuner_report_expected.txt)."""
    import datetime
    from unittest.mock import patch

    from saas_radar.agents.tuner import ReportData, render_report
    from saas_radar.agents.tuning_rules import Proposal

    fixed_ts = "2026-04-24T10:03:56Z"
    applied = [
        Proposal("remove_query", "Zapier can't do X", "0 resultados en 3 runs consecutivos."),
        Proposal("demote_high_signal", "nocode", "5+ runs silent consecutivos."),
        Proposal("add_high_signal", "ecommerce", "10 posts acumulados, 10 con problema (100% hit), 6 payment signals en 3 runs."),
    ]
    data = ReportData(
        run_count=3,
        first_ts="2026-04-18T000000",
        last_ts="2026-04-20T000000",
        total_proposals=3,
        applied_proposals=applied,
        max_changes=5,
    )
    runs_ts = ["2026-04-18T000000", "2026-04-19T000000", "2026-04-20T000000"]

    with patch("saas_radar.agents.tuner.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.datetime(2026, 4, 24, 10, 3, 56, tzinfo=datetime.timezone.utc)
        mock_dt.timezone = datetime.timezone
        out = render_report(data, runs_ts)

    fixture = Path(__file__).parent / "fixtures" / "tuner_report_expected.txt"
    if not fixture.exists():
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text(out, encoding="utf-8")
        import pytest
        pytest.skip("Fixture generada por primera vez — ejecuta de nuevo para validar.")
    expected = fixture.read_text(encoding="utf-8")
    assert out == expected
