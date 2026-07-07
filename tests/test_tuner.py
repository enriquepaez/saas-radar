"""Tests del tuning agent (fase A2 + A4).

Cubre loaders, priorizacion + cap, renderizado del report, diff simulado,
wiring end-to-end del CLI (dry-run) y el modo --apply con subprocess mockeado.
Sin red, sin LLM. Usa BD temporal SQLite en disco
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

    def test_genera_pseudo_python_acciones_llm(self):
        proposals = [
            Proposal("add_query", "invoice tracking spreadsheet", ""),
            Proposal("add_subreddit", "r/shopify", ""),
            Proposal("add_phrase", "retype into", ""),
        ]
        diff = render_config_diff(proposals)
        assert 'PAIN_SEARCH_QUERIES.append("invoice tracking spreadsheet")' in diff
        # El prefijo r/ se normaliza tambien en la previsualizacion
        assert 'SUBREDDITS.append("shopify")' in diff
        assert 'PAIN_SIGNAL_PHRASES.append(("retype into", 2))' in diff
        assert "accion desconocida" not in diff

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


# ── TestApplyProposals ───────────────────────────────────────────────────


class TestApplyProposals:
    """Verifica que apply_proposals edita config.py correctamente."""

    def _make_config(self, tmp_path: Path) -> Path:
        """Crea un config.py minimo con las 3 listas mutables."""
        config_py = tmp_path / "config.py"
        config_py.write_text(
            'HIGH_SIGNAL_SUBREDDITS = {\n'
            '    # comment line\n'
            '    "msp",\n'
            '    "sysadmin",\n'
            '}\n'
            '\n'
            'SUBREDDITS = [\n'
            '    "zapier",\n'
            '    "PropertyManagement",\n'
            '    "accounting",\n'
            ']\n'
            '\n'
            'PAIN_SEARCH_QUERIES = [\n'
            '    "Zapier can\'t",\n'
            '    "I use Excel to track",\n'
            ']\n'
            '\n'
            'PAIN_SIGNAL_PHRASES = [\n'
            '    ("manually copy", 3),\n'
            '    ("copy paste", 3),\n'
            ']\n',
            encoding="utf-8",
        )
        return config_py

    def test_add_high_signal_inserts_entry(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_high_signal", "devops", "hit alto")], config)
        text = config.read_text(encoding="utf-8")
        assert '"devops",' in text
        # Debe estar dentro del bloque HIGH_SIGNAL_SUBREDDITS (antes de SUBREDDITS =)
        hss_block = text.split("\nSUBREDDITS = ")[0]
        assert '"devops",' in hss_block

    def test_add_high_signal_no_duplica(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_high_signal", "msp", "ya existe")], config)
        assert config.read_text(encoding="utf-8").count('"msp"') == 1

    def test_demote_high_signal_elimina_entrada(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("demote_high_signal", "sysadmin", "silent")], config)
        # La entrada debe haber desaparecido del bloque HIGH_SIGNAL
        hss_block = config.read_text(encoding="utf-8").split("\nSUBREDDITS = ")[0]
        assert '"sysadmin"' not in hss_block

    def test_remove_subreddit_elimina_entrada_case_insensitive(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        # El target viene en minusculas del tuner; config.py tiene "PropertyManagement"
        apply_proposals([Proposal("remove_subreddit", "propertymanagement", "0% hit")], config)
        text = config.read_text(encoding="utf-8")
        assert '"PropertyManagement"' not in text

    def test_remove_query_elimina_entrada(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("remove_query", "Zapier can't", "vacia")], config)
        text = config.read_text(encoding="utf-8")
        assert "\"Zapier can't\"" not in text

    def test_preserva_comentarios_y_formato(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_high_signal", "newone", "test")], config)
        text = config.read_text(encoding="utf-8")
        assert "# comment line" in text  # comentario preservado

    def test_add_query_inserta_en_pain_search_queries(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_query", "invoice tracking spreadsheet", "LLM")], config)
        text = config.read_text(encoding="utf-8")
        # Debe estar dentro del bloque PAIN_SEARCH_QUERIES (antes de PAIN_SIGNAL_PHRASES)
        queries_block = text.split("\nPAIN_SIGNAL_PHRASES = ")[0].split("\nPAIN_SEARCH_QUERIES = ")[1]
        assert '"invoice tracking spreadsheet",' in queries_block

    def test_add_query_no_duplica(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_query", "I use Excel to track", "ya existe")], config)
        assert config.read_text(encoding="utf-8").count('"I use Excel to track"') == 1

    def test_add_query_con_comillas_genera_python_valido_y_no_duplica(self, tmp_path):
        import ast

        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        # Sintaxis de busqueda exacta, plausible en un query_suggestion del LLM
        target = '"manual data entry" CRM'
        apply_proposals([Proposal("add_query", target, "LLM")], config)
        text = config.read_text(encoding="utf-8")
        # El fichero resultante debe seguir siendo Python valido (comillas escapadas)
        ast.parse(text)
        assert '"\\"manual data entry\\" CRM",' in text
        # Segunda pasada: el dedupe debe matchear la forma escapada ya escrita
        apply_proposals([Proposal("add_query", target, "LLM repetido")], config)
        text = config.read_text(encoding="utf-8")
        ast.parse(text)
        assert text.count("manual data entry") == 1

    def test_add_subreddit_inserta_en_subreddits(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_subreddit", "shopify", "LLM")], config)
        text = config.read_text(encoding="utf-8")
        subs_block = text.split("\nPAIN_SEARCH_QUERIES = ")[0].split("\nSUBREDDITS = ")[1]
        assert '"shopify",' in subs_block

    def test_add_subreddit_normaliza_prefijo_r(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        # El LLM puede sugerir el target con prefijo r/; config.py guarda sin prefijo
        apply_proposals([Proposal("add_subreddit", "r/shopify", "LLM")], config)
        text = config.read_text(encoding="utf-8")
        assert '"shopify",' in text
        assert '"r/shopify"' not in text

    def test_add_subreddit_no_duplica_case_insensitive(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        # "PropertyManagement" ya existe en config.py; el LLM lo sugiere en minusculas
        apply_proposals([Proposal("add_subreddit", "r/propertymanagement", "ya existe")], config)
        text = config.read_text(encoding="utf-8")
        assert text.lower().count('"propertymanagement"') == 1

    def test_add_phrase_inserta_tupla_con_peso_2(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_phrase", "retype into", "LLM")], config)
        text = config.read_text(encoding="utf-8")
        phrases_block = text.split("\nPAIN_SIGNAL_PHRASES = ")[1]
        assert '("retype into", 2),' in phrases_block

    def test_add_phrase_no_duplica_con_otro_peso(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        # "manually copy" ya existe con peso 3: no debe insertarse con peso 2
        apply_proposals([Proposal("add_phrase", "manually copy", "ya existe")], config)
        text = config.read_text(encoding="utf-8")
        assert text.count('"manually copy"') == 1
        assert '("manually copy", 2)' not in text

    def test_add_phrase_no_duplica_case_insensitive(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        apply_proposals([Proposal("add_phrase", "Copy Paste", "ya existe")], config)
        text = config.read_text(encoding="utf-8")
        assert text.lower().count('"copy paste"') == 1

    def test_add_phrase_con_comillas_genera_python_valido_y_no_duplica(self, tmp_path):
        import ast

        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        target = 'retype "into"'
        apply_proposals([Proposal("add_phrase", target, "LLM")], config)
        text = config.read_text(encoding="utf-8")
        # Python valido y con la frase escapada dentro de la tupla
        ast.parse(text)
        assert '("retype \\"into\\"", 2),' in text
        # Re-proponer la misma frase NO debe añadir otra copia (peso acumulado)
        apply_proposals([Proposal("add_phrase", target, "LLM repetido")], config)
        text = config.read_text(encoding="utf-8")
        ast.parse(text)
        assert text.count("retype") == 1

    def test_accion_desconocida_no_modifica(self, tmp_path):
        from saas_radar.agents.tuner import apply_proposals

        config = self._make_config(tmp_path)
        original = config.read_text(encoding="utf-8")
        apply_proposals([Proposal("unknown_action", "foo", "test")], config)
        assert config.read_text(encoding="utf-8") == original


# ── TestCheckOpenPr ──────────────────────────────────────────────────────


class TestCheckOpenPr:
    def test_devuelve_url_cuando_pr_existe(self, monkeypatch):
        import subprocess as sp_module

        from saas_radar.agents.tuner import check_open_pr

        def fake_run(cmd, **kwargs):
            return sp_module.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    [{"headRefName": "chore/auto-tuning-20260530", "url": "https://github.com/x/y/pull/42"}]
                ),
                stderr="",
            )

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)
        url = check_open_pr("chore/auto-tuning-")
        assert url == "https://github.com/x/y/pull/42"

    def test_devuelve_none_cuando_no_hay_pr(self, monkeypatch):
        import subprocess as sp_module

        from saas_radar.agents.tuner import check_open_pr

        def fake_run(cmd, **kwargs):
            return sp_module.CompletedProcess(cmd, 0, stdout="[]", stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)
        assert check_open_pr("chore/auto-tuning-") is None

    def test_devuelve_none_si_gh_falla(self, monkeypatch):
        import subprocess as sp_module

        from saas_radar.agents.tuner import check_open_pr

        def fake_run(cmd, **kwargs):
            return sp_module.CompletedProcess(cmd, 1, stdout="", stderr="error")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)
        assert check_open_pr("chore/auto-tuning-") is None


# ── TestMarkActed y TestSyncActedStatus ──────────────────────────────────


class TestMarkActed:
    def test_sets_acted_1_en_bd(self, tmp_path):
        from saas_radar.agents.tuner import mark_acted

        db = tmp_path / "test.db"
        _make_meta_recs_db(db, [{"type": "remove_subreddit", "target": "startups", "recurrence": 3, "acted": 0}])
        mark_acted(str(db), [Proposal("remove_subreddit", "startups", "r")])
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT acted FROM meta_recommendations WHERE target = 'startups'").fetchone()
        conn.close()
        assert row[0] == 1

    def test_noop_si_db_no_existe(self, tmp_path):
        from saas_radar.agents.tuner import mark_acted

        # No debe lanzar excepcion
        mark_acted(str(tmp_path / "nope.db"), [Proposal("remove_subreddit", "x", "r")])


class TestSyncActedStatus:
    def test_resetea_acted_cuando_pr_cerrado_sin_merge(self, tmp_path, monkeypatch):
        import subprocess as sp_module

        from saas_radar.agents.tuner import sync_acted_status

        state_file = tmp_path / "tuner_state.json"
        state_file.write_text(
            json.dumps({"pr_url": "https://github.com/x/y/pull/1", "date": "20260530"}), encoding="utf-8"
        )

        db = tmp_path / "test.db"
        _make_meta_recs_db(db, [{"type": "remove_subreddit", "target": "startups", "recurrence": 3, "acted": 1}])

        def fake_run(cmd, **kwargs):
            return sp_module.CompletedProcess(cmd, 0, stdout=json.dumps({"state": "CLOSED"}), stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)
        sync_acted_status(str(db), state_file)

        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT acted FROM meta_recommendations WHERE target = 'startups'").fetchone()
        conn.close()
        assert row[0] == 0

    def test_no_resetea_cuando_pr_merged(self, tmp_path, monkeypatch):
        import subprocess as sp_module

        from saas_radar.agents.tuner import sync_acted_status

        state_file = tmp_path / "tuner_state.json"
        state_file.write_text(json.dumps({"pr_url": "https://github.com/x/y/pull/1"}), encoding="utf-8")

        db = tmp_path / "test.db"
        _make_meta_recs_db(db, [{"type": "remove_subreddit", "target": "startups", "recurrence": 3, "acted": 1}])

        def fake_run(cmd, **kwargs):
            return sp_module.CompletedProcess(cmd, 0, stdout=json.dumps({"state": "MERGED"}), stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)
        sync_acted_status(str(db), state_file)

        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT acted FROM meta_recommendations WHERE target = 'startups'").fetchone()
        conn.close()
        assert row[0] == 1  # no se resetea

    def test_noop_sin_state_file(self, tmp_path):
        from saas_radar.agents.tuner import sync_acted_status

        # No debe lanzar excepcion
        sync_acted_status(str(tmp_path / "nope.db"), tmp_path / "nope.json")


# ── TestCliApply ─────────────────────────────────────────────────────────


class TestCliApply:
    """E2E del modo --apply con subprocess mockeado."""

    def _prepare_env(self, tmp_path: Path, monkeypatch):
        """Monta runs-dir + BD temporal + config.py minimo."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # 3 runs con query muerta → triggea remove_query
        for ts in ("2026-04-18T000000", "2026-04-19T000000", "2026-04-20T000000"):
            _write_meta(runs_dir / f"{ts}_meta.json", empty_queries=["dead query"])

        db = tmp_path / "saas.db"
        _make_meta_recs_db(db, [])

        # Config minimo
        config_py = tmp_path / "config.py"
        config_py.write_text(
            'HIGH_SIGNAL_SUBREDDITS = {\n    "msp",\n}\n\n'
            'SUBREDDITS = [\n    "zapier",\n]\n\n'
            'PAIN_SEARCH_QUERIES = [\n    "dead query",\n    "live query",\n]\n',
            encoding="utf-8",
        )

        readme = tmp_path / "README.md"
        readme.write_text("# Test README\n", encoding="utf-8")

        from saas_radar import config as saas_config

        monkeypatch.setattr(saas_config, "HIGH_SIGNAL_SUBREDDITS", set(), raising=False)
        monkeypatch.setattr(saas_config, "SUBREDDITS", ["zapier"], raising=False)
        monkeypatch.setattr(saas_config, "PAIN_SEARCH_QUERIES", ["dead query", "live query"], raising=False)

        return runs_dir, db, config_py, readme

    def test_apply_crea_pr_y_marca_acted(self, tmp_path, monkeypatch):
        import subprocess as sp_module

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
                return sp_module.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
                return sp_module.CompletedProcess(
                    cmd, 0, stdout="https://github.com/x/y/pull/99\n", stderr=""
                )
            return sp_module.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)

        runs_dir, db, config_py, readme = self._prepare_env(tmp_path, monkeypatch)

        rc = main(
            [
                "--runs-dir",
                str(runs_dir),
                "--db-path",
                str(db),
                "--config-path",
                str(config_py),
                "--readme-path",
                str(readme),
                "--lookback",
                "5",
                "--max-changes",
                "5",
                "--apply",
            ]
        )

        assert rc == 0
        # config.py debe haberse modificado
        assert "dead query" not in config_py.read_text(encoding="utf-8")
        # gh pr create fue llamado
        assert any("create" in c for c in calls if "pr" in str(c))
        # README tiene registro de tuning
        readme_text = readme.read_text(encoding="utf-8")
        assert "Registro de tuning automatico" in readme_text or "auto-tuning" in readme_text.lower()

    def test_apply_sin_cambios_reales_no_crea_rama_ni_pr(self, tmp_path, monkeypatch, capsys):
        """Si todas las propuestas son duplicados (config.py no cambia), el
        tuner debe salir con 0 SIN tocar git — antes intentaba commitear y
        fallaba con 'nothing to commit'."""
        import subprocess as sp_module

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
                return sp_module.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            return sp_module.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)

        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # Sin runs con empty_queries: la unica propuesta viene del LLM (A6)
        # y sugiere un subreddit que YA existe en config.py -> no-op.
        db = tmp_path / "saas.db"
        _make_meta_recs_db(
            db,
            [{"type": "subreddit_suggestion", "target": "r/zapier", "recurrence": 2, "acted": 0}],
        )

        config_py = tmp_path / "config.py"
        config_py.write_text(
            'HIGH_SIGNAL_SUBREDDITS = {\n    "msp",\n}\n\n'
            'SUBREDDITS = [\n    "zapier",\n]\n\n'
            'PAIN_SEARCH_QUERIES = [\n    "live query",\n]\n',
            encoding="utf-8",
        )
        original_config = config_py.read_text(encoding="utf-8")

        from saas_radar import config as saas_config

        monkeypatch.setattr(saas_config, "HIGH_SIGNAL_SUBREDDITS", set(), raising=False)
        monkeypatch.setattr(saas_config, "SUBREDDITS", ["zapier"], raising=False)
        monkeypatch.setattr(saas_config, "PAIN_SEARCH_QUERIES", ["live query"], raising=False)

        rc = main(
            [
                "--runs-dir",
                str(runs_dir),
                "--db-path",
                str(db),
                "--config-path",
                str(config_py),
                "--apply",
            ]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "no produjeron cambios" in out
        # config.py intacto y ningun comando git ejecutado
        assert config_py.read_text(encoding="utf-8") == original_config
        assert not any(c and c[0] == "git" for c in calls)

    def test_apply_skip_cuando_pr_ya_abierto(self, tmp_path, monkeypatch, capsys):
        import subprocess as sp_module

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
                return sp_module.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [{"headRefName": "chore/auto-tuning-20260530", "url": "https://github.com/x/y/pull/99"}]
                    ),
                    stderr="",
                )
            return sp_module.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)

        runs_dir, db, config_py, readme = self._prepare_env(tmp_path, monkeypatch)
        original_config = config_py.read_text(encoding="utf-8")

        rc = main(
            [
                "--runs-dir",
                str(runs_dir),
                "--db-path",
                str(db),
                "--config-path",
                str(config_py),
                "--readme-path",
                str(readme),
                "--apply",
            ]
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIP" in out
        # config.py NO debe haberse modificado
        assert config_py.read_text(encoding="utf-8") == original_config

    def test_apply_gh_pr_create_falla_imprime_stderr(self, tmp_path, monkeypatch, capsys):
        """Si `gh pr create` falla, el tuner debe volcar el stderr de gh (el
        motivo real del fallo) y salir con codigo 1 — antes check=True lanzaba
        CalledProcessError y el mensaje de gh quedaba invisible en CI."""
        import subprocess as sp_module

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
                return sp_module.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
                return sp_module.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr="pull request create failed: GraphQL: was submitted too quickly",
                )
            return sp_module.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("saas_radar.agents.tuner.subprocess.run", fake_run)

        runs_dir, db, config_py, readme = self._prepare_env(tmp_path, monkeypatch)

        rc = main(
            [
                "--runs-dir",
                str(runs_dir),
                "--db-path",
                str(db),
                "--config-path",
                str(config_py),
                "--readme-path",
                str(readme),
                "--apply",
            ]
        )

        assert rc == 1
        err = capsys.readouterr().err
        # El motivo real que devuelve gh llega al log de CI
        assert "pull request create failed" in err
        assert "gh pr create fallo" in err
        # No se marca estado como si el PR existiera
        state_file = runs_dir.parent / "tuner_state.json"
        assert not state_file.exists()
        # El README no registra un tuning que no llego a abrir PR
        assert "auto-tuning" not in readme.read_text(encoding="utf-8").lower()
