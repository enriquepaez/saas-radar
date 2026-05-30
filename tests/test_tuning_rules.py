"""Tests unitarios de las reglas deterministas del agente de tuning.

Cubre las 4 reglas del README "Tuning del pipeline" con fixtures sinteticas. Sin I/O,
sin BD, sin red.
"""

from __future__ import annotations

from saas_radar.agents.tuning_rules import (
    propose_all_changes,
    propose_demote_from_high_signal,
    propose_promote_to_high_signal,
    propose_remove_from_subreddits,
    propose_remove_queries,
)


def make_run(subreddit_signal=None, silent_subreddits=None, empty_queries=None):
    return {
        "subreddit_signal": subreddit_signal or [],
        "silent_subreddits": silent_subreddits or [],
        "empty_queries": empty_queries or [],
    }


def make_sub(name, posts, problem, payment):
    return {
        "subreddit": name,
        "posts_analyzed": posts,
        "with_problem": problem,
        "with_payment_signal": payment,
        "hit_rate": problem / posts if posts else 0,
    }


# ── Regla 1: promover a HIGH_SIGNAL ───────────────────────────────────────


class TestPromoteToHighSignal:
    def test_promueve_con_hitrate_alto_y_payment(self):
        runs = [make_run([make_sub("ecommerce", 3, 3, 2)])]
        proposals = propose_promote_to_high_signal(runs, current_high_signal=set())
        assert len(proposals) == 1
        assert proposals[0].action == "add_high_signal"
        assert proposals[0].target == "ecommerce"

    def test_no_promueve_si_ya_es_high_signal(self):
        runs = [make_run([make_sub("msp", 5, 4, 1)])]
        proposals = propose_promote_to_high_signal(runs, current_high_signal={"msp"})
        assert proposals == []

    def test_no_promueve_con_muestra_insuficiente(self):
        runs = [make_run([make_sub("foo", 2, 2, 1)])]
        proposals = propose_promote_to_high_signal(runs, set())
        assert proposals == []

    def test_no_promueve_sin_payment_ni_hitrate_alto(self):
        runs = [make_run([make_sub("bar", 10, 5, 0)])]
        proposals = propose_promote_to_high_signal(runs, set())
        assert proposals == []

    def test_promueve_con_dos_payment_aunque_hitrate_bajo(self):
        runs = [make_run([make_sub("baz", 10, 4, 2)])]
        proposals = propose_promote_to_high_signal(runs, set())
        assert len(proposals) == 1
        assert proposals[0].target == "baz"

    def test_acumula_entre_runs(self):
        runs = [
            make_run([make_sub("abc", 2, 2, 1)]),
            make_run([make_sub("abc", 2, 2, 0)]),
        ]
        proposals = propose_promote_to_high_signal(runs, set())
        assert len(proposals) == 1
        assert proposals[0].target == "abc"

    def test_normaliza_a_minusculas(self):
        runs = [make_run([make_sub("Ecommerce", 3, 3, 2)])]
        proposals = propose_promote_to_high_signal(runs, current_high_signal={"ecommerce"})
        assert proposals == []


# ── Regla 2: quitar de SUBREDDITS ─────────────────────────────────────────


class TestRemoveFromSubreddits:
    def test_quita_por_recurrence_alta(self):
        recs = [{"type": "remove_subreddit", "target": "startups", "recurrence": 3}]
        proposals = propose_remove_from_subreddits(
            runs=[], meta_recommendations=recs, current_subreddits=["startups", "msp"]
        )
        assert len(proposals) == 1
        assert proposals[0].target == "startups"

    def test_no_quita_si_recurrence_menor_a_3(self):
        recs = [{"type": "remove_subreddit", "target": "startups", "recurrence": 2}]
        proposals = propose_remove_from_subreddits(runs=[], meta_recommendations=recs, current_subreddits=["startups"])
        assert proposals == []

    def test_no_quita_si_target_no_esta_configurado(self):
        recs = [{"type": "remove_subreddit", "target": "fantasma", "recurrence": 5}]
        proposals = propose_remove_from_subreddits(runs=[], meta_recommendations=recs, current_subreddits=["msp"])
        assert proposals == []

    def test_quita_por_cero_hit_dos_runs(self):
        runs = [
            make_run([make_sub("startups", 3, 0, 0)]),
            make_run([make_sub("startups", 3, 0, 0)]),
        ]
        proposals = propose_remove_from_subreddits(runs=runs, meta_recommendations=[], current_subreddits=["startups"])
        assert len(proposals) == 1
        assert proposals[0].target == "startups"

    def test_no_quita_con_muestra_total_insuficiente(self):
        runs = [
            make_run([make_sub("x", 2, 0, 0)]),
            make_run([make_sub("x", 2, 0, 0)]),
        ]
        proposals = propose_remove_from_subreddits(runs=runs, meta_recommendations=[], current_subreddits=["x"])
        assert proposals == []

    def test_no_quita_si_silent_en_uno_de_los_dos_runs(self):
        runs = [
            make_run(silent_subreddits=["x"]),
            make_run([make_sub("x", 3, 0, 0)]),
        ]
        proposals = propose_remove_from_subreddits(runs=runs, meta_recommendations=[], current_subreddits=["x"])
        assert proposals == []

    def test_no_duplica_cuando_ambas_causas_se_cumplen(self):
        runs = [
            make_run([make_sub("startups", 3, 0, 0)]),
            make_run([make_sub("startups", 3, 0, 0)]),
        ]
        recs = [{"type": "remove_subreddit", "target": "startups", "recurrence": 5}]
        proposals = propose_remove_from_subreddits(
            runs=runs, meta_recommendations=recs, current_subreddits=["startups"]
        )
        assert len(proposals) == 1


# ── Regla 3: degradar de HIGH_SIGNAL ──────────────────────────────────────


class TestDemoteFromHighSignal:
    def test_degrada_por_5_runs_silent_consecutivos(self):
        runs = [make_run(silent_subreddits=["taxpros"]) for _ in range(5)]
        proposals = propose_demote_from_high_signal(runs, current_high_signal={"taxpros"})
        assert len(proposals) == 1
        assert proposals[0].target == "taxpros"

    def test_no_degrada_con_solo_4_silent(self):
        runs = [make_run(silent_subreddits=["taxpros"]) for _ in range(4)]
        proposals = propose_demote_from_high_signal(runs, current_high_signal={"taxpros"})
        assert proposals == []

    def test_no_degrada_si_silent_no_es_consecutivo(self):
        runs = [
            make_run(silent_subreddits=["taxpros"]),
            make_run(silent_subreddits=["taxpros"]),
            make_run([make_sub("taxpros", 1, 0, 0)]),  # corta la racha
            make_run(silent_subreddits=["taxpros"]),
            make_run(silent_subreddits=["taxpros"]),
        ]
        proposals = propose_demote_from_high_signal(runs, current_high_signal={"taxpros"})
        assert proposals == []

    def test_degrada_por_hitrate_sostenidamente_bajo(self):
        runs = [
            make_run([make_sub("agency", 5, 1, 0)]),
            make_run([make_sub("agency", 5, 1, 0)]),
            make_run([make_sub("agency", 5, 1, 0)]),
        ]
        proposals = propose_demote_from_high_signal(runs, current_high_signal={"agency"})
        assert len(proposals) == 1
        assert proposals[0].target == "agency"

    def test_no_degrada_con_muestra_acumulada_insuficiente(self):
        runs = [
            make_run([make_sub("agency", 2, 0, 0)]),
            make_run([make_sub("agency", 2, 0, 0)]),
            make_run([make_sub("agency", 2, 0, 0)]),
        ]
        proposals = propose_demote_from_high_signal(runs, current_high_signal={"agency"})
        assert proposals == []

    def test_no_duplica_cuando_ambas_causas_se_cumplen(self):
        runs = [
            make_run([make_sub("agency", 4, 0, 0)]),  # 0% hit
            make_run([make_sub("agency", 4, 0, 0)]),
            make_run([make_sub("agency", 4, 0, 0)]),
            make_run(silent_subreddits=["agency"]),
            make_run(silent_subreddits=["agency"]),
            make_run(silent_subreddits=["agency"]),
            make_run(silent_subreddits=["agency"]),
            make_run(silent_subreddits=["agency"]),
        ]
        proposals = propose_demote_from_high_signal(runs, current_high_signal={"agency"})
        assert len(proposals) == 1


# ── Regla 4: quitar query ─────────────────────────────────────────────────


class TestRemoveQueries:
    def test_quita_query_empty_3_runs(self):
        runs = [make_run(empty_queries=["Buildium doesn't"]) for _ in range(3)]
        proposals = propose_remove_queries(runs, current_queries=["Buildium doesn't", "otra"])
        assert len(proposals) == 1
        assert proposals[0].target == "Buildium doesn't"

    def test_no_quita_con_solo_2_runs_empty(self):
        runs = [make_run(empty_queries=["q"]) for _ in range(2)]
        proposals = propose_remove_queries(runs, current_queries=["q"])
        assert proposals == []

    def test_no_quita_si_empty_no_es_consecutivo(self):
        runs = [
            make_run(empty_queries=["q"]),
            make_run(empty_queries=[]),  # rompe la racha
            make_run(empty_queries=["q"]),
            make_run(empty_queries=["q"]),
        ]
        proposals = propose_remove_queries(runs, current_queries=["q"])
        assert proposals == []

    def test_devuelve_vacio_con_historial_corto(self):
        runs = [make_run(empty_queries=["q"]) for _ in range(2)]
        proposals = propose_remove_queries(runs, current_queries=["q"])
        assert proposals == []


# ── Orquestador ───────────────────────────────────────────────────────────


class TestProposeAllChanges:
    def test_combina_reglas_en_el_mismo_historico(self):
        runs = [
            make_run(
                subreddit_signal=[make_sub("ecommerce", 3, 3, 2), make_sub("startups", 3, 0, 0)],
            ),
            make_run(
                subreddit_signal=[make_sub("ecommerce", 2, 2, 1), make_sub("startups", 3, 0, 0)],
            ),
        ]
        proposals = propose_all_changes(
            runs=runs,
            meta_recommendations=[],
            current_high_signal=set(),
            current_subreddits=["startups", "ecommerce"],
            current_queries=[],
        )
        actions = {(p.action, p.target) for p in proposals}
        assert ("add_high_signal", "ecommerce") in actions
        assert ("remove_subreddit", "startups") in actions

    def test_sin_datos_no_propone_nada(self):
        proposals = propose_all_changes(
            runs=[],
            meta_recommendations=[],
            current_high_signal=set(),
            current_subreddits=[],
            current_queries=[],
        )
        assert proposals == []
