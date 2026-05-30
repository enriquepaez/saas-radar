"""Tests del agente GTM: _generate_gtm, _process_opportunity, run_all_pending."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from saas_radar.agents.gtm_agent import (
    GTM_DEFAULT_MIN_PRIORITY,
    _generate_gtm,
    _process_opportunity,
    run_all_pending,
)
from saas_radar.storage.db import _make_engine, has_gtm, init_db, load_gtm


@pytest.fixture
def tmp_db(tmp_path):
    """BD temporal con schema inicializado para tests del agente GTM."""
    db_file = tmp_path / "test_agent.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url=db_url)
    return db_url


def _insert_opp(db_url: str, opp_id: int, priority: int = 8, discarded: int = 0) -> None:
    """Helper: inserta una oportunidad mínima en la BD."""
    engine = _make_engine(db_url)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO opportunities "
                    "(id, product_name, priority_score, discarded, canonical_id, evidence_quotes) "
                    "VALUES (:id, :name, :priority, :disc, :cid, :eq)"
                ),
                {
                    "id": opp_id,
                    "name": f"Product{opp_id}",
                    "priority": priority,
                    "disc": discarded,
                    "cid": opp_id,
                    "eq": '["Pain quote 1", "Pain quote 2"]',
                },
            )


@pytest.fixture
def llm_payload():
    """Payload LLM válido con los campos requeridos."""
    return {
        "viability_desperation": 8,
        "viability_build_ease": 7,
        "viability_scalability": 6,
        "elevator_pitch": "Automatiza tu caos de facturas",
        "pricing_tiers": [{"name": "Pro", "price": "29€/mes", "features": ["Todo incluido"]}],
        "acquisition_channels": [{"platform": "HackerNews", "tactic": "Show HN", "cost_estimate": "0€"}],
        "cold_outreach_script": "Vi tu post, tengo la solución exacta.",
        "organic_post_template": "¿Cansado de hojas de cálculo para facturas?",
        "validation_plan_7d": [{"day": 1, "action": "Landing page", "success_criterion": "10 visitas"}],
        "pivot_signals": ["Nadie activa en día 3"],
        "kpis": {"cac_target": "<50€", "activation_target": "70%", "mrr_target_m3": "500€"},
    }


# ---------------------------------------------------------------------------
# Tests de _generate_gtm
# ---------------------------------------------------------------------------


def test_generate_gtm_returns_none_if_llm_fails():
    """_generate_gtm devuelve None si call_llm retorna None."""
    opp = {"id": 1, "product_name": "Test", "priority_score": 8}
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=None):
        result = _generate_gtm(opp, provider="claude")
    assert result is None


def test_generate_gtm_calculates_viability_total(llm_payload):
    """_generate_gtm suma correctamente los 3 scores en viability_total."""
    opp = {"id": 1, "product_name": "Test", "priority_score": 8}
    import json

    raw_response = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw_response):
        result = _generate_gtm(opp, provider="claude")

    assert result is not None
    # 8 + 7 + 6 = 21
    assert result["viability_total"] == 21


def test_generate_gtm_returns_none_if_invalid_schema():
    """_generate_gtm devuelve None si el JSON no tiene los campos requeridos."""
    opp = {"id": 1, "product_name": "Test"}
    # JSON válido pero sin campos de viabilidad.
    invalid_response = '{"elevator_pitch": "algo"}'
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=invalid_response):
        result = _generate_gtm(opp, provider="claude")
    assert result is None


def test_generate_gtm_returns_none_if_parse_fails():
    """_generate_gtm devuelve None si _parse_json_payload no puede parsear."""
    opp = {"id": 1, "product_name": "Test"}
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value="texto plano sin JSON"):
        result = _generate_gtm(opp, provider="claude")
    assert result is None


def test_generate_gtm_handles_string_scores():
    """_generate_gtm devuelve None si los scores no son convertibles a int."""
    import json

    opp = {"id": 1, "product_name": "Test"}
    bad_payload = {
        "viability_desperation": "alto",  # string no convertible
        "viability_build_ease": 7,
        "viability_scalability": 6,
    }
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=json.dumps(bad_payload)):
        result = _generate_gtm(opp, provider="claude")
    assert result is None


def test_generate_gtm_opp_without_evidence_quotes(llm_payload):
    """_generate_gtm funciona aunque la opp no tenga evidence_quotes."""
    opp = {"id": 1, "product_name": "Test", "evidence_quotes": None}
    import json

    raw_response = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw_response):
        result = _generate_gtm(opp, provider="claude")
    assert result is not None
    assert "viability_total" in result


# ---------------------------------------------------------------------------
# Tests de _process_opportunity
# ---------------------------------------------------------------------------


def test_process_opportunity_generated(tmp_db, llm_payload):
    """Flujo completo OK: devuelve 'generated' y persiste la fila."""
    _insert_opp(tmp_db, 1)
    opp = {"id": 1, "product_name": "Test", "priority_score": 8}
    import json

    raw = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        status = _process_opportunity(1, opp, provider="claude", db_url=tmp_db)

    assert status == "generated"
    assert has_gtm(1, db_url=tmp_db)
    gtm = load_gtm(1, db_url=tmp_db)
    assert gtm["gtm_status"] == "generated"
    assert gtm["viability_total"] == 21


def test_process_opportunity_skipped_low_viability(tmp_db):
    """viability_total < 20 → 'skipped_low_viability' sin campos B+C."""
    _insert_opp(tmp_db, 1)
    opp = {"id": 1, "product_name": "Test"}
    low_payload = {
        "viability_desperation": 4,
        "viability_build_ease": 5,
        "viability_scalability": 5,
        "elevator_pitch": "algo",
        "pricing_tiers": [],
    }
    import json

    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=json.dumps(low_payload)):
        status = _process_opportunity(1, opp, provider="claude", db_url=tmp_db)

    assert status == "skipped_low_viability"
    gtm = load_gtm(1, db_url=tmp_db)
    assert gtm["gtm_status"] == "skipped_low_viability"
    assert gtm["viability_total"] == 14  # 4+5+5
    # Campos B+C no deben haberse persistido
    assert gtm.get("elevator_pitch") is None


def test_process_opportunity_failed_llm(tmp_db):
    """LLM falla (None) → 'failed' con scores NULL en BD."""
    _insert_opp(tmp_db, 1)
    opp = {"id": 1, "product_name": "Test"}
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=None):
        status = _process_opportunity(1, opp, provider="claude", db_url=tmp_db)

    assert status == "failed"
    gtm = load_gtm(1, db_url=tmp_db)
    assert gtm["gtm_status"] == "failed"
    assert gtm["viability_desperation"] is None


def test_process_opportunity_skipped_existing_without_force(tmp_db, llm_payload):
    """Ya existe GTM y no se pasa force → 'skipped_existing' sin llamar al LLM."""
    _insert_opp(tmp_db, 1)
    opp = {"id": 1, "product_name": "Test"}
    import json

    raw = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        _process_opportunity(1, opp, provider="claude", db_url=tmp_db)

    # Segunda llamada sin force: debe devolver skipped_existing SIN llamar a LLM
    call_count = []
    with patch("saas_radar.agents.gtm_agent.call_llm", side_effect=lambda *a, **kw: call_count.append(1) or raw):
        status = _process_opportunity(1, opp, provider="claude", force=False, db_url=tmp_db)

    assert status == "skipped_existing"
    assert len(call_count) == 0  # LLM no fue llamado


def test_process_opportunity_force_replaces(tmp_db, llm_payload):
    """Con force=True, borra la fila existente y vuelve a procesar → 'generated'."""
    _insert_opp(tmp_db, 1)
    opp = {"id": 1, "product_name": "Test"}
    import json

    raw = json.dumps(llm_payload)

    # Primera inserción
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        _process_opportunity(1, opp, provider="claude", db_url=tmp_db)

    # Segunda con force=True
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        status = _process_opportunity(1, opp, provider="claude", force=True, db_url=tmp_db)

    assert status == "generated"
    # Solo debe haber 1 fila (no duplicados tras force)
    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM opportunity_gtm WHERE opportunity_id = 1")).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Tests de run_all_pending
# ---------------------------------------------------------------------------


def test_run_all_pending_filters_by_priority(tmp_db, llm_payload):
    """Opps con priority_score < min_priority no se procesan."""
    _insert_opp(tmp_db, 1, priority=10)  # Por encima
    _insert_opp(tmp_db, 2, priority=3)   # Por debajo
    import json

    raw = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        result = run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    # Opp 1 procesada, opp 2 ignorada
    assert result["generated"] == 1
    assert has_gtm(1, db_url=tmp_db)
    assert not has_gtm(2, db_url=tmp_db)


def test_run_all_pending_returns_counts(tmp_db, llm_payload):
    """run_all_pending devuelve dict con conteos correctos."""
    _insert_opp(tmp_db, 1, priority=8)
    _insert_opp(tmp_db, 2, priority=8)
    import json

    raw = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        result = run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    assert "generated" in result
    assert "skipped_low_viability" in result
    assert "failed" in result
    assert "skipped_existing" in result
    assert result["generated"] == 2


def test_run_all_pending_skips_discarded(tmp_db, llm_payload):
    """Opps con discarded=1 no se procesan (load_active_opportunities las filtra)."""
    _insert_opp(tmp_db, 1, priority=8, discarded=1)
    import json

    raw = json.dumps(llm_payload)
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        result = run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    assert result["generated"] == 0
    assert not has_gtm(1, db_url=tmp_db)


def test_run_all_pending_empty_db(tmp_db):
    """BD sin oportunidades devuelve conteos en 0."""
    result = run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)
    assert result == {"generated": 0, "skipped_low_viability": 0, "failed": 0, "skipped_existing": 0}


def test_run_all_pending_skipped_existing_counted(tmp_db, llm_payload):
    """Una opp ya procesada sin --force se cuenta como skipped_existing."""
    _insert_opp(tmp_db, 1, priority=8)
    opp = {"id": 1, "product_name": "Test"}
    import json

    raw = json.dumps(llm_payload)

    # Primera pasada
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    # Segunda pasada sin force
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        result = run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    assert result["skipped_existing"] == 1
    assert result["generated"] == 0


def test_run_all_pending_mixed_results(tmp_db, llm_payload):
    """Un LLM falla y otro tiene baja viabilidad — conteos mixtos correctos."""
    _insert_opp(tmp_db, 1, priority=8)  # Fallará
    _insert_opp(tmp_db, 2, priority=8)  # Baja viabilidad
    import json

    low_payload = {
        "viability_desperation": 3,
        "viability_build_ease": 4,
        "viability_scalability": 5,
        "elevator_pitch": "X",
        "pricing_tiers": [],
    }

    call_count = [0]

    def mock_llm(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return None  # Primera opp: fallo
        return json.dumps(low_payload)  # Segunda opp: baja viabilidad

    with patch("saas_radar.agents.gtm_agent.call_llm", side_effect=mock_llm):
        result = run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    assert result["failed"] == 1
    assert result["skipped_low_viability"] == 1
    assert result["generated"] == 0


def test_run_all_pending_force_regenerates(tmp_db, llm_payload):
    """Con force=True, una opp ya existente se regenera y cuenta como 'generated'."""
    _insert_opp(tmp_db, 1, priority=8)
    import json

    raw = json.dumps(llm_payload)

    # Primera pasada normal
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        run_all_pending(min_priority=7, provider="claude", db_url=tmp_db)

    # Segunda pasada con force
    with patch("saas_radar.agents.gtm_agent.call_llm", return_value=raw):
        result = run_all_pending(min_priority=7, provider="claude", force=True, db_url=tmp_db)

    assert result["generated"] == 1
    assert result["skipped_existing"] == 0


def test_run_all_pending_default_min_priority():
    """GTM_DEFAULT_MIN_PRIORITY es 7."""
    assert GTM_DEFAULT_MIN_PRIORITY == 7


def test_generate_gtm_includes_evidence_quotes_in_prompt():
    """build_gtm_prompt se llama con la opp correcta (evidence_quotes incluidas)."""
    import json

    opp = {
        "id": 1,
        "product_name": "Test",
        "evidence_quotes": '["quote1", "quote2"]',
    }
    captured = {}

    def mock_build(o):
        captured["opp"] = o
        return "prompt text"

    valid_payload = {
        "viability_desperation": 8,
        "viability_build_ease": 7,
        "viability_scalability": 6,
        "elevator_pitch": "X",
        "pricing_tiers": [],
        "acquisition_channels": [],
        "cold_outreach_script": "X",
        "organic_post_template": "X",
        "validation_plan_7d": [],
        "pivot_signals": [],
        "kpis": {},
    }

    with patch("saas_radar.agents.gtm_agent.build_gtm_prompt", side_effect=mock_build):
        with patch("saas_radar.agents.gtm_agent.call_llm", return_value=json.dumps(valid_payload)):
            result = _generate_gtm(opp, provider="claude")

    assert "opp" in captured
    assert captured["opp"]["id"] == 1


def test_process_opportunity_exception_in_llm_does_not_propagate(tmp_db):
    """Excepción en _generate_gtm se maneja → status 'failed'."""
    _insert_opp(tmp_db, 1)
    opp = {"id": 1, "product_name": "Test"}

    with patch("saas_radar.agents.gtm_agent.call_llm", side_effect=RuntimeError("API exploded")):
        # _generate_gtm captura el error de call_llm si retorna None,
        # pero si call_llm lanza excepción, _generate_gtm la propaga.
        # _process_opportunity NO captura excepciones de _generate_gtm.
        # El caller (run_all_pending) sí las captura.
        # Por tanto, testeamos que run_all_pending lo gestiona correctamente.
        result = run_all_pending(min_priority=0, provider="claude", db_url=tmp_db)

    # El error se captura en run_all_pending y cuenta como failed
    assert result["failed"] == 1
