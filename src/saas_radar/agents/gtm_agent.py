"""Agente GTM: genera planes Go-to-Market para oportunidades canónicas."""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

from saas_radar.analysis.llm_clients import _parse_json_payload, call_llm
from saas_radar.analysis.prompts.gtm import build_gtm_prompt
from saas_radar.storage.db import _get_db_url, _make_engine, has_gtm, load_active_opportunities, persist_gtm

logger = logging.getLogger(__name__)

GTM_DEFAULT_MIN_PRIORITY = 7

# Campos que deben estar presentes en el payload del LLM para considerarlo válido.
_REQUIRED_KEYS = {"viability_desperation", "viability_build_ease", "viability_scalability"}


def _generate_gtm(opp: dict) -> dict | None:
    """Genera el payload GTM para una oportunidad llamando al LLM.

    Llama a build_gtm_prompt para construir el prompt, luego a call_llm con
    max_tokens=2000. Parsea la respuesta con _parse_json_payload.

    Calcula viability_total = viability_desperation + viability_build_ease +
    viability_scalability. Este cálculo lo hace Python (no el LLM) para
    garantizar consistencia aritmética.

    Devuelve None si:
    - call_llm devuelve None (error de red, rate limit sin retry, etc.).
    - _parse_json_payload no puede parsear la respuesta como JSON.
    - El JSON no contiene los campos requeridos de viabilidad.
    """
    prompt = build_gtm_prompt(opp)

    raw = call_llm(prompt, max_tokens=2000)
    if raw is None:
        logger.error("_generate_gtm: call_llm devolvió None para opp id=%s", opp.get("id"))
        return None

    payload = _parse_json_payload(raw)
    if payload is None:
        logger.error("_generate_gtm: _parse_json_payload falló para opp id=%s", opp.get("id"))
        return None

    # Validar que el LLM devolvió los campos de viabilidad.
    missing = _REQUIRED_KEYS - set(payload.keys())
    if missing:
        logger.error(
            "_generate_gtm: campos requeridos faltantes %s para opp id=%s",
            missing,
            opp.get("id"),
        )
        return None

    # Calcular viability_total en Python para garantizar consistencia.
    # Forzar conversión a int con fallback a 0 si el LLM devuelve None o string.
    try:
        d = int(payload.get("viability_desperation") or 0)
        b = int(payload.get("viability_build_ease") or 0)
        s = int(payload.get("viability_scalability") or 0)
    except (TypeError, ValueError):
        logger.error("_generate_gtm: scores de viabilidad no son enteros para opp id=%s", opp.get("id"))
        return None

    payload["viability_total"] = d + b + s

    return payload


def _process_opportunity(
    opp_id: int,
    opp: dict,
    force: bool = False,
    db_url: str | None = None,
) -> str:
    """Procesa una sola oportunidad y persiste el resultado GTM.

    Flujo:
    1. Si ya existe GTM y no force → devuelve "skipped_existing".
    2. Si force → borra la fila existente antes de insertar.
    3. Llama a _generate_gtm → si None → persiste gtm_status="failed".
    4. Si viability_total < 20 → persiste solo scores con gtm_status="skipped_low_viability".
    5. Si todo OK → persiste payload completo con gtm_status="generated".

    Devuelve: "generated" | "skipped_low_viability" | "failed" | "skipped_existing"
    """
    if has_gtm(opp_id, db_url=db_url) and not force:
        logger.info("GTM ya existe para opp_id=%d — usando --force para regenerar", opp_id)
        return "skipped_existing"

    if force:
        # Borrar la fila existente para permitir el INSERT limpio.
        url = _get_db_url(db_url)
        engine = _make_engine(url)
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text("DELETE FROM opportunity_gtm WHERE opportunity_id = :oid"),
                    {"oid": opp_id},
                )
        logger.info("GTM fila eliminada para opp_id=%d (--force)", opp_id)

    payload = _generate_gtm(opp)

    if payload is None:
        # LLM falló: persistir fila mínima con status="failed" y scores NULL.
        persist_gtm(opp_id, {"gtm_status": "failed"}, db_url=db_url)
        logger.error("GTM generación fallida para opp_id=%d — persistido como failed", opp_id)
        return "failed"

    viability_total = payload.get("viability_total", 0)

    if viability_total < 20:
        # Baja viabilidad: persistir solo scores + status, sin campos B y C.
        slim_payload = {
            "viability_desperation": payload.get("viability_desperation"),
            "viability_build_ease": payload.get("viability_build_ease"),
            "viability_scalability": payload.get("viability_scalability"),
            "viability_total": viability_total,
            "gtm_status": "skipped_low_viability",
        }
        persist_gtm(opp_id, slim_payload, db_url=db_url)
        logger.info(
            "GTM baja viabilidad (%d<20) para opp_id=%d — persistido como skipped_low_viability",
            viability_total,
            opp_id,
        )
        return "skipped_low_viability"

    # Viabilidad OK: persistir payload completo.
    payload["gtm_status"] = "generated"
    persist_gtm(opp_id, payload, db_url=db_url)
    logger.info("GTM generado para opp_id=%d (viability_total=%d)", opp_id, viability_total)
    return "generated"


def run_all_pending(
    min_priority: int = GTM_DEFAULT_MIN_PRIORITY,
    force: bool = False,
    db_url: str | None = None,
) -> dict:
    """Procesa todas las oportunidades canónicas activas con priority_score >= min_priority.

    Carga las opps con load_active_opportunities (filtra discarded=1 y no-canónicas).
    Para cada opp que supera el umbral de prioridad, llama a _process_opportunity.

    Devuelve dict con conteos: {generated, skipped_low_viability, failed, skipped_existing}.
    """
    df = load_active_opportunities(db_url=db_url)

    counts: dict[str, int] = {
        "generated": 0,
        "skipped_low_viability": 0,
        "failed": 0,
        "skipped_existing": 0,
    }

    if df.empty:
        logger.info("run_all_pending: no hay oportunidades activas")
        return counts

    # Filtrar por priority_score. La columna puede ser int o float (SQLite).
    filtered = df[df["priority_score"].fillna(0) >= min_priority]
    logger.info(
        "run_all_pending: %d opps activas, %d con priority>=%d",
        len(df),
        len(filtered),
        min_priority,
    )

    for _, row in filtered.iterrows():
        opp_id = int(row["id"])
        opp = row.to_dict()
        try:
            status = _process_opportunity(opp_id, opp, force=force, db_url=db_url)
            counts[status] = counts.get(status, 0) + 1
        except Exception as e:
            logger.error("run_all_pending: error inesperado procesando opp_id=%d: %s", opp_id, e)
            counts["failed"] = counts.get("failed", 0) + 1

    return counts


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="GTM agent — genera planes Go-to-Market para oportunidades de micro-SaaS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python -m saas_radar.agents.gtm_agent --opp-id 3
  python -m saas_radar.agents.gtm_agent --all-pending
  python -m saas_radar.agents.gtm_agent --all-pending --min-priority 5
  python -m saas_radar.agents.gtm_agent --opp-id 3 --force
        """,
    )
    parser.add_argument("--opp-id", type=int, help="ID de la oportunidad a procesar")
    parser.add_argument("--all-pending", action="store_true", help="Procesar todas las opps canónicas pendientes")
    parser.add_argument("--force", action="store_true", help="Regenerar GTM aunque ya exista (DELETE+INSERT)")
    parser.add_argument(
        "--min-priority",
        type=int,
        default=GTM_DEFAULT_MIN_PRIORITY,
        help=f"Priority score mínimo para --all-pending (default: {GTM_DEFAULT_MIN_PRIORITY})",
    )
    parser.add_argument("--db-url", type=str, default=None, help="URL de la BD SQLite (default: data/saas.db)")

    args = parser.parse_args()

    if args.opp_id:
        # Modo single: cargar la opp por id directamente desde la BD.
        from saas_radar.storage.db import _get_db_url, _make_engine

        url = _get_db_url(args.db_url)
        eng = _make_engine(url)
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM opportunities WHERE id = :id"),
                {"id": args.opp_id},
            ).fetchone()

        if row is None:
            print(f"Error: opp_id={args.opp_id} no encontrado en la BD.")
        else:
            opp = dict(row._mapping)
            status = _process_opportunity(
                args.opp_id, opp, force=args.force, db_url=args.db_url
            )
            print(f"GTM opp_id={args.opp_id}: {status}")

    elif args.all_pending:
        result = run_all_pending(
            min_priority=args.min_priority,
            force=args.force,
            db_url=args.db_url,
        )
        print(
            f"GTM completado: {result.get('generated', 0)} generadas, "
            f"{result.get('skipped_low_viability', 0)} baja viabilidad, "
            f"{result.get('failed', 0)} fallidas, "
            f"{result.get('skipped_existing', 0)} ya existentes"
        )
    else:
        parser.print_help()
