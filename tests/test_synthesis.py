"""Tests para saas_radar.analysis.synthesis."""
from __future__ import annotations

from saas_radar.analysis.synthesis import (
    _coherence_words,
    _quotes_are_coherent,
    _validate_synthesis,
    build_synthesis_prompt,
)

# ── Fixtures de extracciones ─────────────────────────────────────────────────


def _make_extraction(subreddit: str, problem: str, workflow: str = "", workaround: str = "", has_problem: bool = True) -> dict:
    return {
        "_subreddit": subreddit,
        "_score": 10,
        "has_problem": has_problem,
        "who_has_it": "someone",
        "problem_description": problem,
        "workflow_context": workflow,
        "current_workaround": workaround,
        "key_quote": "",
        "payment_quote": "",
        "payment_signal": False,
        "competitor_mentions": [],
    }


ACCOUNTING_1 = _make_extraction("accounting", "invoice tracking in quickbooks", "reconcile invoices monthly", "excel spreadsheet")
ACCOUNTING_2 = _make_extraction("accounting", "invoicing clients with quickbooks entries", "billing workflow", "manual entry")
MSP_1 = _make_extraction("msp", "client onboarding documentation for MSP", "onboard new clients", "word documents")


# ── Tests: build_synthesis_prompt ───────────────────────────────────────────


def test_build_synthesis_prompt_cluster_separators():
    """El prompt contiene separadores ### CLUSTER ### con el nombre correcto y count."""
    extractions = [ACCOUNTING_1, ACCOUNTING_2, MSP_1]
    prompt, _ = build_synthesis_prompt(extractions)
    assert "### CLUSTER: r/accounting (2 items) ###" in prompt
    assert "### CLUSTER: r/msp (1 items) ###" in prompt


def test_build_synthesis_prompt_global_numbering():
    """Los items están numerados globalmente [1], [2], [3] aunque cambien de subreddit."""
    extractions = [ACCOUNTING_1, ACCOUNTING_2, MSP_1]
    prompt, _ = build_synthesis_prompt(extractions)
    assert "[1]" in prompt
    assert "[2]" in prompt
    assert "[3]" in prompt


def test_build_synthesis_prompt_ordered_extractions_returned():
    """La lista ordered_extractions devuelta tiene los items de r/accounting antes que r/msp."""
    extractions = [MSP_1, ACCOUNTING_1, ACCOUNTING_2]
    _, ordered = build_synthesis_prompt(extractions)
    subs = [ex["_subreddit"] for ex in ordered]
    # accounting tiene 2 items, msp tiene 1 → accounting va primero
    assert subs.index("accounting") < subs.index("msp")
    # los dos items de accounting van antes del de msp
    assert subs[0] == "accounting"
    assert subs[1] == "accounting"
    assert subs[2] == "msp"


def test_build_synthesis_prompt_only_has_problem():
    """Extracciones con has_problem=False no aparecen en el prompt ni en ordered_extractions."""
    no_problem = _make_extraction("accounting", "this is not a real problem", has_problem=False)
    extractions = [ACCOUNTING_1, no_problem, MSP_1]
    prompt, ordered = build_synthesis_prompt(extractions)
    # El item sin problema no debe aparecer en ordered_extractions
    assert all(ex["has_problem"] for ex in ordered)
    assert len(ordered) == 2
    # Su texto no debe aparecer en el prompt
    assert "this is not a real problem" not in prompt


# ── Tests: _validate_synthesis ──────────────────────────────────────────────


def test_validate_synthesis_drops_insufficient_evidence():
    """Opp con 1 evidence_item y 1 evidence_quote → descartada a disqualified_ideas."""
    results = {
        "opportunities": [
            {
                "id": 1,
                "product_name": "Solo Tool",
                "evidence_items": [1],
                "evidence_quotes": ["[item 1] foo"],
            }
        ],
        "top_3_recommended": [1],
        "disqualified_ideas": [],
    }
    ordered = [ACCOUNTING_1]
    out = _validate_synthesis(results, ordered)
    assert out["opportunities"] == []
    assert len(out["disqualified_ideas"]) == 1
    dq = out["disqualified_ideas"][0]
    assert "RULE 1 cantidad" in dq["rule_violated"]
    assert 1 not in out["top_3_recommended"]


def test_validate_synthesis_drops_incoherent_cluster():
    """2 extracciones con problem_description disjunto → opp descartada con 'coherencia' en rule_violated."""
    ex1 = _make_extraction("accounting", "invoice reconciliation in quickbooks for construction clients")
    ex2 = _make_extraction("msp", "network switch firmware upgrade scheduling for remote sites")
    results = {
        "opportunities": [
            {
                "id": 1,
                "product_name": "Incoherent Tool",
                "evidence_items": [1, 2],
                "evidence_quotes": ["[item 1] quote one", "[item 2] quote two"],
            }
        ],
        "top_3_recommended": [1],
        "disqualified_ideas": [],
    }
    out = _validate_synthesis(results, [ex1, ex2])
    assert out["opportunities"] == []
    assert len(out["disqualified_ideas"]) == 1
    assert "coherencia" in out["disqualified_ideas"][0]["rule_violated"]


def test_validate_synthesis_keeps_coherent_cluster():
    """3 extracciones que comparten raíces de dominio → opp mantenida."""
    ex1 = _make_extraction("accounting", "invoice tracking in quickbooks")
    ex2 = _make_extraction("accounting", "invoicing clients with quickbooks entries")
    ex3 = _make_extraction("accounting", "qbo invoice reconciliation for bookkeepers")
    results = {
        "opportunities": [
            {
                "id": 1,
                "product_name": "QBO Invoice Tracker",
                "evidence_items": [1, 2, 3],
                "evidence_quotes": ["[item 1] q1", "[item 2] q2", "[item 3] q3"],
            }
        ],
        "top_3_recommended": [1],
        "disqualified_ideas": [],
    }
    out = _validate_synthesis(results, [ex1, ex2, ex3])
    assert len(out["opportunities"]) == 1
    assert out["opportunities"][0]["id"] == 1
    assert 1 in out["top_3_recommended"]


def test_validate_synthesis_top3_only_survivors():
    """Si opp 2 es descartada, top_3_recommended final no incluye el id 2."""
    # Items 1-3 comparten vocabulario de dominio (invo, quic, qbo, book) suficiente
    # para pasar el filtro de coherencia.
    ex1 = _make_extraction("accounting", "invoice reconciliation in quickbooks for bookkeepers")
    ex2 = _make_extraction("accounting", "invoicing clients with quickbooks billing for bookkeepers")
    ex3 = _make_extraction("accounting", "qbo invoice reconciliation for bookkeepers billing")
    ex4 = _make_extraction("msp", "network firmware upgrade unrelated problem")
    ex5 = _make_extraction("saas", "completely different domain unrelated idea")
    results = {
        "opportunities": [
            {
                "id": 1,
                "product_name": "QBO Tracker",
                "evidence_items": [1, 2, 3],
                "evidence_quotes": ["[item 1] q1", "[item 2] q2", "[item 3] q3"],
            },
            {
                "id": 2,
                "product_name": "Incoherent Pairing",
                "evidence_items": [4, 5],
                "evidence_quotes": ["[item 4] q4", "[item 5] q5"],
            },
            {
                "id": 3,
                "product_name": "Another QBO Tool",
                "evidence_items": [1, 2, 3],
                "evidence_quotes": ["[item 1] q1", "[item 2] q2", "[item 3] q3"],
            },
        ],
        "top_3_recommended": [1, 2, 3],
        "disqualified_ideas": [],
    }
    out = _validate_synthesis(results, [ex1, ex2, ex3, ex4, ex5])
    assert 2 not in out["top_3_recommended"]
    assert 1 in out["top_3_recommended"]
    assert 3 in out["top_3_recommended"]


def test_validate_synthesis_accumulates_disqualified():
    """disqualified_ideas del LLM se preserva; la validación agrega las nuevas al final."""
    ex1 = _make_extraction("accounting", "invoice tracking in quickbooks")
    existing_dq = [{"idea": "LLM rejected idea", "rule_violated": "RULE 3 — saturated market"}]
    results = {
        "opportunities": [
            {
                "id": 1,
                "product_name": "Solo Tool",
                "evidence_items": [1],
                "evidence_quotes": ["[item 1] one quote"],
            }
        ],
        "top_3_recommended": [1],
        "disqualified_ideas": existing_dq,
    }
    out = _validate_synthesis(results, [ex1])
    assert len(out["disqualified_ideas"]) == 2
    # La entrada original del LLM se preserva
    assert out["disqualified_ideas"][0]["idea"] == "LLM rejected idea"
    # La nueva entrada de validación se acumula
    assert any("RULE 1 cantidad" in d["rule_violated"] for d in out["disqualified_ideas"])


# ── Tests: _coherence_words ──────────────────────────────────────────────────


def test_coherence_words_strips_prefix():
    """El prefijo [item N] se elimina antes de procesar; 'item' no aparece en las raíces."""
    roots = _coherence_words("[item 3] invoice tracking in quickbooks")
    assert "item" not in roots
    assert "invo" in roots
    assert "quic" in roots


def test_coherence_words_excludes_stop_roots():
    """Raíces de dominio (manu, trac, spre, data) no aparecen en el resultado."""
    roots = _coherence_words("manually tracking spreadsheet data")
    assert "manu" not in roots
    assert "trac" not in roots
    assert "spre" not in roots
    assert "data" not in roots


def test_coherence_words_includes_short_tools():
    """Siglas como qbo y csv son señal fuerte y se incluyen aunque tengan <4 chars."""
    roots = _coherence_words("we use qbo and csv exports")
    assert "qbo" in roots
    assert "csv" in roots


# ── Tests: _quotes_are_coherent ──────────────────────────────────────────────


def test_quotes_are_coherent_true():
    """3 quotes que comparten raíces de dominio (invo, quic) → True."""
    quotes = [
        "invoice reconciliation in quickbooks every month",
        "tracking invoices inside quickbooks for clients",
        "qbo invoicing workflow for bookkeepers",
    ]
    assert _quotes_are_coherent(quotes) is True


def test_quotes_are_coherent_false():
    """2 quotes totalmente dispares → False."""
    quotes = [
        "invoice reconciliation in quickbooks every month",
        "network firmware upgrade scheduling for remote office switches",
    ]
    assert _quotes_are_coherent(quotes) is False


def test_quotes_are_coherent_single_quote():
    """Una sola quote → True (no hay par que comparar)."""
    assert _quotes_are_coherent(["just one sentence here"]) is True
