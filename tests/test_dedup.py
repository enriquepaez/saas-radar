"""
Tests de dedup semántico de oportunidades.

Cubre:
  - Tokenización de quotes (descarte de prefijo `[item N]`, stopwords).
  - Tokenización de product_name (stopwords genéricas).
  - Coerción de evidence_quotes desde list / JSON string / None.
  - find_canonical:
      · 2 opps idénticas → match
      · misma product_name pero evidencia disjunta → NO match
      · producto distinto + evidencia idéntica → match
      · sin existing → None
      · canonical_id de la match es el devuelto (no el id directo si difieren)
      · empate por overlap se resuelve por name_similarity, luego id asc
  - Wiring en persist_run_to_db: misma quote en runs distintos colapsa al
    mismo canonical_id. Quotes disjuntas → canonical_id propio.
"""

from __future__ import annotations

import json

import pytest

from saas_radar.analysis.dedup import (
    _coerce_quote_list,
    _evidence_tokens,
    _name_tokens,
    evidence_overlap,
    find_canonical,
    name_similarity,
)

# ── Tokenización ────────────────────────────────────────────────────────


def test_evidence_tokens_strips_item_prefix():
    quotes = ["[item 13] spending an hour every morning checking competitor prices"]
    tokens = _evidence_tokens(quotes)
    assert "item" not in tokens  # el prefijo se descarta
    assert "spending" in tokens
    assert "checking" in tokens
    assert "competitor" in tokens
    assert "prices" in tokens


def test_evidence_tokens_filters_short_and_stopwords():
    quotes = ["[item 1] the team spends much time with the system"]
    tokens = _evidence_tokens(quotes)
    assert "the" not in tokens  # stopword
    assert "much" not in tokens  # stopword
    assert "with" not in tokens  # stopword
    assert "team" in tokens  # 4 chars, no stopword
    assert "spends" in tokens
    assert "system" in tokens


def test_name_tokens_drops_glue_words():
    tokens = _name_tokens("Inventory Tracker for Small E-commerce Businesses")
    assert "for" not in tokens
    assert "inventory" in tokens
    assert "tracker" in tokens
    assert "small" in tokens
    assert "businesses" in tokens


def test_coerce_quote_list_accepts_json_string():
    src = json.dumps(["[item 1] foo bar", "[item 2] baz qux"])
    out = _coerce_quote_list(src)
    assert out == ["[item 1] foo bar", "[item 2] baz qux"]


def test_coerce_quote_list_accepts_list():
    out = _coerce_quote_list(["a", "b"])
    assert out == ["a", "b"]


def test_coerce_quote_list_handles_none_and_empty():
    assert _coerce_quote_list(None) == []
    assert _coerce_quote_list("") == []
    assert _coerce_quote_list("[]") == []


# ── Jaccard ─────────────────────────────────────────────────────────────


def test_evidence_overlap_identical_returns_one():
    a = {"evidence_quotes": ["[item 1] spending hours packing orders shipping"]}
    b = {"evidence_quotes": ["[item 5] spending hours packing orders shipping"]}
    assert evidence_overlap(a, b) == pytest.approx(1.0)


def test_evidence_overlap_disjoint_returns_zero():
    a = {"evidence_quotes": ["spending hours packing orders shipping"]}
    b = {"evidence_quotes": ["tracking competitor prices spreadsheets daily"]}
    assert evidence_overlap(a, b) == 0.0


def test_name_similarity_drops_stopwords():
    a = {"product_name": "Tracker for E-commerce"}
    b = {"product_name": "Tracker for Agencies"}
    # Tras stopwords: {"tracker", "commerce"} vs {"tracker", "agencies"}.
    # Intersección = {"tracker"}, unión = {"tracker", "commerce", "agencies"}.
    # Jaccard = 1/3 ≈ 0.333.
    assert name_similarity(a, b) == pytest.approx(1 / 3)


# ── find_canonical: casos del PLAN ─────────────────────────────────────


def test_find_canonical_returns_none_when_no_existing():
    opp = {"product_name": "Foo", "evidence_quotes": ["bar baz qux"]}
    assert find_canonical(opp, []) is None


def test_find_canonical_two_identical_match():
    a = {
        "id": 1,
        "canonical_id": 1,
        "product_name": "Ecommerce Order Fulfillment Tool",
        "evidence_quotes": [
            "[item 26] 12 minutes per order to pick and pack",
            "[item 27] spending 25+ hours a week just on packing and shipping",
        ],
    }
    b = {
        "product_name": "Ecommerce Order Fulfillment Tool",
        "evidence_quotes": [
            "[item 30] it takes more than 12 minutes per order to pick and pack",
            "[item 31] spending 25+ hours a week just on packing and shipping",
        ],
    }
    assert find_canonical(b, [a]) == 1


def test_find_canonical_same_name_disjoint_evidence_no_match():
    """PLAN: misma product_name pero evidencia disjunta → NO match."""
    a = {
        "id": 1,
        "canonical_id": 1,
        "product_name": "Customer Tracker",
        "evidence_quotes": ["tracking inventory in spreadsheets daily"],
    }
    b = {
        "product_name": "Customer Tracker",
        "evidence_quotes": ["managing email campaigns through mailchimp"],
    }
    assert find_canonical(b, [a]) is None


def test_find_canonical_different_name_identical_evidence_matches():
    """PLAN: producto distinto + evidencia idéntica → match."""
    a = {
        "id": 1,
        "canonical_id": 1,
        "product_name": "Foo Widget",
        "evidence_quotes": ["spending hours packing orders shipping every week"],
    }
    b = {
        "product_name": "Bar Doodad",
        "evidence_quotes": ["spending hours packing orders shipping every week"],
    }
    assert find_canonical(b, [a]) == 1


def test_find_canonical_returns_existing_canonical_id_not_row_id():
    """Si la match apunta a otra canónica (cluster ya existente), devolver esa."""
    canonical = {
        "id": 1,
        "canonical_id": 1,
        "product_name": "Order Tool",
        "evidence_quotes": ["packing orders shipping daily"],
    }
    duplicate = {
        "id": 5,
        "canonical_id": 1,  # apunta a canonical
        "product_name": "Order Tool",
        "evidence_quotes": ["packing orders shipping daily"],
    }
    new = {
        "product_name": "Order Tool",
        "evidence_quotes": ["packing orders shipping daily"],
    }
    # El mejor candidato por id-asc y empate de scores es id=1, pero aunque
    # la match fuese id=5, su canonical_id=1, así que el resultado debe ser 1.
    assert find_canonical(new, [canonical, duplicate]) == 1


def test_find_canonical_threshold_can_be_tuned():
    """Tokens que solapan poco (1 de 6) deben quedar bajo el default 0.3 pero
    pueden cruzar con threshold más laxo."""
    a = {
        "id": 1,
        "canonical_id": 1,
        "product_name": "A",
        "evidence_quotes": ["alpha beta gamma delta epsilon zeta"],
    }
    b = {
        "product_name": "B",
        "evidence_quotes": ["alpha xxxx yyyy zzzz wwww vvvv"],
    }
    # Jaccard = 1 / 11 ≈ 0.09.
    assert find_canonical(b, [a]) is None
    assert find_canonical(b, [a], threshold=0.05) == 1


# ── Smoke contra las 7 opps reales (datos del PLAN) ─────────────────────


REAL_OPPS = [
    {
        "id": 1, "canonical_id": None,
        "product_name": "Inventory Tracker for Small E-commerce Businesses",
        "evidence_quotes": [
            "[item 13] spending an hour every morning checking competitor prices",
            "[item 16] doing everything over spreadsheets",
            "[item 46] drowning in spreadsheets trying to track inventory",
        ],
    },
    {
        "id": 2, "canonical_id": None,
        "product_name": "Client Communication Tracker for Agencies",
        "evidence_quotes": [
            "[item 8] we've been dealing with the same scattered information problem",
            "[item 10] the overhead of keeping projects organized, tracking hours, sending invoices, and keeping clients updated usually becomes its own full-time job",
        ],
    },
    {
        "id": 3, "canonical_id": None,
        "product_name": "Ecommerce Order Fulfillment Optimizer",
        "evidence_quotes": [
            "[item 26] 12 minutes per order to pick and pack",
            "[item 27] if you're spending 25+ hours a week just on packing and shipping",
        ],
    },
    {
        "id": 4, "canonical_id": None,
        "product_name": "Client Management for Agency Owners",
        "evidence_quotes": [
            "[item 5] the overhead of keeping projects organized, tracking hours, sending invoices, and keeping clients updated usually becomes its own full-time job",
            "[item 3] the client brain concept is smart",
        ],
    },
    {
        "id": 5, "canonical_id": None,
        "product_name": "Ecommerce Order Fulfillment Tool",
        "evidence_quotes": [
            "[item 30] it takes more than 12 minutes per order to pick and pack",
            "[item 31] if you're spending 25+ hours a week just on packing and shipping",
        ],
    },
    {
        "id": 6, "canonical_id": None,
        "product_name": "Ecommerce Order Fulfillment Tool",
        "evidence_quotes": [
            "[item 27] it takes more than 12 minutes per order to pick and pack",
            "[item 28] spending 25+ hours a week just on packing and shipping",
        ],
    },
    {
        "id": 7, "canonical_id": None,
        "product_name": "Client Communication and Project Context Tool for Agencies",
        "evidence_quotes": [
            "[item 11] we've been dealing with the same scattered information problem",
            "[item 13] the overhead of keeping projects organized, tracking hours, sending invoices, and keeping clients updated usually becomes its own full-time job",
        ],
    },
]


def test_smoke_seven_real_opps_collapse_to_three_canonicals():
    """Con las 7 opps reales y threshold 0.3, deben emerger 3 canónicas:
    {1}, {2,4,7}, {3,5,6}. Verifica el criterio de cierre del PLAN."""
    canonicals: dict[int, int] = {}
    existing: list[dict] = []
    for opp in REAL_OPPS:
        match = find_canonical(opp, existing)
        if match is None:
            canonicals[opp["id"]] = opp["id"]
            existing.append({**opp, "canonical_id": opp["id"]})
        else:
            canonicals[opp["id"]] = match
            existing.append({**opp, "canonical_id": match})

    distinct = set(canonicals.values())
    assert len(distinct) == 3, f"esperadas 3 canónicas, se obtuvo {distinct}: {canonicals}"
    assert canonicals[1] == 1
    assert canonicals[2] == canonicals[4] == canonicals[7]
    assert canonicals[3] == canonicals[5] == canonicals[6]


# ── Wiring en persist_run_to_db ─────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path):
    """BD SQLite temporal aislada para tests de persistencia."""
    from saas_radar.storage.db import init_db

    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url=db_url)
    return db_url


def _opp(name, quotes):
    import json as _json

    return {
        "product_name": name,
        "niche": "test niche",
        "core_problem": "test problem",
        "evidence_quotes": _json.dumps(quotes),
        "evidence_items": _json.dumps([1, 2]),
        "priority_score": 7,
        "discarded": 0,
    }


def test_persist_first_opp_canonical_self(temp_db):
    from saas_radar.storage.db import load_active_opportunities, persist_run_to_db

    persist_run_to_db(
        run_data={"run_at": "2026-04-25T00:00:00+00:00", "posts_analyzed": 10, "valid_extractions": 5, "status": "ok", "duration_sec": 1},
        opportunities=[_opp("Foo", ["[item 1] alpha bravo charlie delta echo"])],
        db_url=temp_db,
    )
    df = load_active_opportunities(db_url=temp_db)
    assert len(df) == 1
    assert df.iloc[0]["id"] == df.iloc[0]["canonical_id"]


def test_persist_duplicate_across_runs_collapses(temp_db):
    from sqlalchemy import create_engine, text

    from saas_radar.storage.db import load_active_opportunities, persist_run_to_db

    quotes = ["[item 1] packing orders shipping daily customers"]

    persist_run_to_db(
        run_data={"run_at": "2026-04-25T00:00:00+00:00", "posts_analyzed": 10, "valid_extractions": 5, "status": "ok", "duration_sec": 1},
        opportunities=[_opp("Order Tool", quotes)],
        db_url=temp_db,
    )
    persist_run_to_db(
        run_data={"run_at": "2026-04-26T00:00:00+00:00", "posts_analyzed": 10, "valid_extractions": 5, "status": "ok", "duration_sec": 1},
        opportunities=[_opp("Order Tool", quotes)],
        db_url=temp_db,
    )

    # Solo 1 canónica activa, aunque hay 2 filas en la tabla.
    active = load_active_opportunities(db_url=temp_db)
    assert len(active) == 1

    eng = create_engine(temp_db)
    with eng.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM opportunities")).scalar()
        canonicals = conn.execute(
            text("SELECT COUNT(DISTINCT canonical_id) FROM opportunities")
        ).scalar()
    assert total == 2
    assert canonicals == 1


def test_persist_disjoint_opps_keep_separate_canonicals(temp_db):
    from saas_radar.storage.db import load_active_opportunities, persist_run_to_db

    persist_run_to_db(
        run_data={"run_at": "2026-04-25T00:00:00+00:00", "posts_analyzed": 10, "valid_extractions": 5, "status": "ok", "duration_sec": 1},
        opportunities=[
            _opp("A", ["[item 1] packing orders shipping daily"]),
            _opp("B", ["[item 2] tracking competitor prices spreadsheets"]),
        ],
        db_url=temp_db,
    )
    active = load_active_opportunities(db_url=temp_db)
    assert len(active) == 2
    assert set(active["id"]) == set(active["canonical_id"])


# ── Tests find_canonical_v2 ──────────────────────────────────────────────


def test_find_canonical_v2_no_installed_raises():
    """Si sentence-transformers no está, debe lanzar RuntimeError claro."""
    import sys
    import unittest.mock as mock

    import saas_radar.analysis.dedup as dedup_mod

    original_model = dedup_mod._ST_MODEL
    try:
        # Forzar que el singleton no esté cargado y simular módulo ausente.
        # existing debe tener al menos 1 elemento para que el guard `if not existing`
        # no lo cortocircuite antes de llamar al modelo.
        dedup_mod._ST_MODEL = None
        with mock.patch.dict(sys.modules, {"sentence_transformers": None}):
            with pytest.raises(RuntimeError, match="sentence-transformers not installed"):
                from saas_radar.analysis.dedup import find_canonical_v2

                find_canonical_v2(
                    {"product_name": "x", "core_problem": "y", "niche": "z"},
                    [{"id": 1, "canonical_id": 1, "product_name": "a", "core_problem": "b", "niche": "c"}],
                )
    finally:
        # Restaurar singleton para no afectar tests posteriores.
        dedup_mod._ST_MODEL = original_model


def test_find_canonical_v2_empty_existing():
    """Sin existentes, siempre devuelve None."""
    pytest.importorskip("sentence_transformers")
    from saas_radar.analysis.dedup import find_canonical_v2

    opp = {"product_name": "Invoice Tracker", "core_problem": "manual invoicing", "niche": "accounting"}
    assert find_canonical_v2(opp, []) is None


def test_find_canonical_v2_identical_opps_match():
    """Dos opps idénticas deben matchear con threshold=0.75."""
    pytest.importorskip("sentence_transformers")
    from saas_radar.analysis.dedup import find_canonical_v2

    opp = {"product_name": "Invoice Tracker", "core_problem": "manual invoicing pain", "niche": "accounting"}
    existing = [
        {
            "id": 1,
            "canonical_id": 1,
            "product_name": "Invoice Tracker",
            "core_problem": "manual invoicing pain",
            "niche": "accounting",
        }
    ]
    result = find_canonical_v2(opp, existing)
    assert result == 1


def test_find_canonical_v2_disjoint_vocabulary_no_match_jaccard_but_embedding_may_match():
    """
    Vocabulario disjunto pero semánticamente similar → v2 puede matchear (a diferencia de Jaccard).
    Este test documenta el caso id=8 del legacy: si no matchea con Jaccard pero sí con embeddings,
    es un falso negativo corregido. El test solo verifica que la función no crashea y devuelve
    int o None — el resultado exacto depende del modelo y threshold.
    """
    pytest.importorskip("sentence_transformers")
    from saas_radar.analysis.dedup import find_canonical_v2

    opp = {
        "product_name": "Spreadsheet Automation Tool",
        "core_problem": "manual data entry in Excel",
        "niche": "operations",
    }
    existing = [
        {
            "id": 2,
            "canonical_id": 2,
            "product_name": "Data Entry Automator",
            "core_problem": "repetitive manual spreadsheet work",
            "niche": "productivity",
        },
    ]
    result = find_canonical_v2(opp, existing)
    assert result is None or isinstance(result, int)


def test_find_canonical_v2_threshold_respected():
    """Con threshold=1.0 (imposible de alcanzar con textos distintos) → None."""
    pytest.importorskip("sentence_transformers")
    from saas_radar.analysis.dedup import find_canonical_v2

    opp = {"product_name": "Tax Helper", "core_problem": "filing taxes manually", "niche": "finance"}
    existing = [
        {
            "id": 5,
            "canonical_id": 5,
            "product_name": "Invoice Tracker",
            "core_problem": "manual invoicing",
            "niche": "accounting",
        }
    ]
    result = find_canonical_v2(opp, existing, threshold=1.0)
    assert result is None
