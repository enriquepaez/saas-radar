"""Tests de las funciones GTM en storage/db.py."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from saas_radar.storage.db import _make_engine, has_gtm, init_db, load_gtm, persist_gtm


@pytest.fixture
def tmp_db(tmp_path):
    """BD temporal con schema inicializado para tests de GTM."""
    db_file = tmp_path / "test_gtm.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url=db_url)

    # Insertar una oportunidad mínima para satisfacer la FK de opportunity_gtm.
    # Se usa SQL directo porque persist_run_to_db es complejo (requiere analysis_run).
    engine = _make_engine(db_url)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO opportunities "
                    "(id, product_name, priority_score, discarded, canonical_id) "
                    "VALUES (1, 'TestProduct', 8, 0, 1)"
                )
            )
    return db_url


@pytest.fixture
def minimal_payload():
    """Payload mínimo válido para persist_gtm."""
    return {
        "viability_desperation": 8,
        "viability_build_ease": 7,
        "viability_scalability": 6,
        "viability_total": 21,
        "elevator_pitch": "Automatiza el caos de las facturas",
        "pricing_tiers": [{"name": "Free", "price": "0€", "features": ["1 usuario"]}],
        "acquisition_channels": [{"platform": "HackerNews", "tactic": "Show HN", "cost_estimate": "0€"}],
        "cold_outreach_script": "Hola, vi tu post sobre facturas. Tengo la solución.",
        "organic_post_template": "¿Cansado de hacer facturas en Excel?",
        "validation_plan_7d": [{"day": 1, "action": "Publicar landing", "success_criterion": "10 visitas"}],
        "pivot_signals": ["Ningún usuario activa en semana 1"],
        "kpis": {"cac_target": "<50€", "activation_target": "70%", "mrr_target_m3": "500€"},
        "gtm_status": "generated",
    }


# ---------------------------------------------------------------------------
# Tests de persist_gtm
# ---------------------------------------------------------------------------


def test_persist_gtm_inserts_row(tmp_db, minimal_payload):
    """persist_gtm inserta exactamente 1 fila en opportunity_gtm."""
    persist_gtm(1, minimal_payload, db_url=tmp_db)

    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM opportunity_gtm WHERE opportunity_id = 1")).scalar()
    assert count == 1


def test_persist_gtm_serializes_json_fields(tmp_db, minimal_payload):
    """persist_gtm serializa pricing_tiers, acquisition_channels, etc. como JSON strings en BD."""
    persist_gtm(1, minimal_payload, db_url=tmp_db)

    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT pricing_tiers, acquisition_channels, validation_plan_7d, pivot_signals, kpis FROM opportunity_gtm WHERE opportunity_id = 1")).fetchone()

    # Los campos JSON deben ser strings parseables en la BD.
    pricing = json.loads(row[0])
    channels = json.loads(row[1])
    plan = json.loads(row[2])
    signals = json.loads(row[3])
    kpis = json.loads(row[4])

    assert isinstance(pricing, list)
    assert isinstance(channels, list)
    assert isinstance(plan, list)
    assert isinstance(signals, list)
    assert isinstance(kpis, dict)


def test_persist_gtm_with_status_generated(tmp_db, minimal_payload):
    """gtm_status='generated' se guarda correctamente en la BD."""
    minimal_payload["gtm_status"] = "generated"
    persist_gtm(1, minimal_payload, db_url=tmp_db)

    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        status = conn.execute(text("SELECT gtm_status FROM opportunity_gtm WHERE opportunity_id = 1")).scalar()
    assert status == "generated"


def test_persist_gtm_with_status_skipped(tmp_db):
    """gtm_status='skipped_low_viability' se guarda correctamente."""
    payload = {
        "viability_desperation": 4,
        "viability_build_ease": 5,
        "viability_scalability": 3,
        "viability_total": 12,
        "gtm_status": "skipped_low_viability",
    }
    persist_gtm(1, payload, db_url=tmp_db)

    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        status = conn.execute(text("SELECT gtm_status FROM opportunity_gtm WHERE opportunity_id = 1")).scalar()
    assert status == "skipped_low_viability"


def test_persist_gtm_with_status_failed(tmp_db):
    """gtm_status='failed' se guarda con scores NULL (payload mínimo)."""
    persist_gtm(1, {"gtm_status": "failed"}, db_url=tmp_db)

    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT gtm_status, viability_desperation, viability_total FROM opportunity_gtm WHERE opportunity_id = 1")
        ).fetchone()
    assert row[0] == "failed"
    assert row[1] is None  # scores NULL
    assert row[2] is None


def test_persist_gtm_returns_int_id(tmp_db, minimal_payload):
    """persist_gtm devuelve un int (el id AUTOINCREMENT asignado)."""
    result_id = persist_gtm(1, minimal_payload, db_url=tmp_db)
    assert isinstance(result_id, int)
    assert result_id >= 1


# ---------------------------------------------------------------------------
# Tests de load_gtm
# ---------------------------------------------------------------------------


def test_load_gtm_returns_none_if_not_exists(tmp_db):
    """load_gtm devuelve None si no hay fila para el opportunity_id."""
    result = load_gtm(999, db_url=tmp_db)
    assert result is None


def test_load_gtm_returns_dict_with_parsed_json(tmp_db, minimal_payload):
    """load_gtm parsea los campos JSON correctamente: pricing_tiers es lista, kpis es dict."""
    persist_gtm(1, minimal_payload, db_url=tmp_db)
    result = load_gtm(1, db_url=tmp_db)

    assert result is not None
    assert isinstance(result["pricing_tiers"], list)
    assert isinstance(result["acquisition_channels"], list)
    assert isinstance(result["validation_plan_7d"], list)
    assert isinstance(result["pivot_signals"], list)
    assert isinstance(result["kpis"], dict)


def test_load_gtm_tolerates_corrupt_json(tmp_db):
    """Si pricing_tiers no es JSON válido, load_gtm lo devuelve como string sin crash."""
    # Insertar directamente en BD con pricing_tiers corrupto.
    engine = _make_engine(tmp_db)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO opportunity_gtm (opportunity_id, pricing_tiers, gtm_status) "
                    "VALUES (1, 'INVALID JSON {{{', 'generated')"
                )
            )

    result = load_gtm(1, db_url=tmp_db)
    assert result is not None
    # pricing_tiers debe seguir siendo el string corrupto (no crash).
    assert result["pricing_tiers"] == "INVALID JSON {{{"


def test_load_gtm_returns_correct_opportunity_id(tmp_db, minimal_payload):
    """load_gtm devuelve el opportunity_id correcto en el dict resultante."""
    persist_gtm(1, minimal_payload, db_url=tmp_db)
    result = load_gtm(1, db_url=tmp_db)
    assert result is not None
    assert result["opportunity_id"] == 1


# ---------------------------------------------------------------------------
# Tests de has_gtm
# ---------------------------------------------------------------------------


def test_has_gtm_false_before_persist(tmp_db):
    """has_gtm devuelve False cuando no existe fila para el opportunity_id."""
    assert has_gtm(1, db_url=tmp_db) is False


def test_has_gtm_true_after_persist(tmp_db, minimal_payload):
    """has_gtm devuelve True tras persist_gtm."""
    persist_gtm(1, minimal_payload, db_url=tmp_db)
    assert has_gtm(1, db_url=tmp_db) is True
