"""Tuning agent — fase A2 (dry-run).

Lee los ultimos N meta-JSONs de `data/runs/` y las filas de
`meta_recommendations` en la BD, aplica las reglas deterministas de
`tuning_rules.py` y imprime un report en consola. En A2 no modifica
`config.py` ni abre PR.

Uso:
    python -m saas_radar.agents.tuner --lookback 7 --max-changes 5 --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from saas_radar.agents.tuning_rules import Proposal, propose_all_changes

logger = logging.getLogger(__name__)

# ── Loaders ───────────────────────────────────────────────────────────────


def load_recent_runs(runs_dir: str, lookback: int) -> list[dict]:
    """Carga los `lookback` meta-JSONs mas recientes, ascendente por timestamp.

    Tolera ficheros corruptos: los salta con un warning en stderr.
    """
    pattern = os.path.join(runs_dir, "*_meta.json")
    paths = sorted(glob.glob(pattern))
    if not paths:
        return []
    # Los nombres empiezan por ISO timestamp, asi que `sorted` asc ya vale.
    selected = paths[-lookback:]
    runs: list[dict] = []
    for p in selected:
        try:
            with open(p, encoding="utf-8") as f:
                runs.append(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[WARN] meta-JSON corrupto o ilegible, se salta: {p} ({exc})", file=sys.stderr)
    return runs


def load_meta_recommendations(db_path: str) -> list[dict]:
    """Devuelve los rows de `meta_recommendations` como lista de dicts.

    Devuelve lista vacia si la BD no existe o la tabla no esta creada aun.
    """
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, run_id, type, target, recurrence, acted, created_at FROM meta_recommendations"
        ).fetchall()
    except sqlite3.OperationalError:
        # Tabla ausente: la primera vez que init_db no ha corrido aun.
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Priorizacion y cap ────────────────────────────────────────────────────


_ACTION_ORDER = {
    "remove_query": 0,
    "demote_high_signal": 1,
    "remove_subreddit": 2,
    "add_high_signal": 3,
}


def _recurrence_of(proposal: Proposal, rec_by_target: dict[tuple[str, str], int]) -> int:
    """Devuelve la recurrence conocida para (action, target) o 0 si no hay."""
    return rec_by_target.get((proposal.action, proposal.target.lower()), 0)


def prioritize_and_cap(
    proposals: list[Proposal],
    meta_recommendations: list[dict],
    max_changes: int,
) -> list[Proposal]:
    """Ordena las propuestas (conservador primero, luego por recurrence desc,
    empate alfabetico por target) y recorta a `max_changes`."""
    rec_by_target: dict[tuple[str, str], int] = {}
    for rec in meta_recommendations:
        action = _META_TYPE_TO_ACTION.get(rec.get("type") or "")
        target = (rec.get("target") or "").lower()
        if not action or not target:
            continue
        # Nos quedamos con el max recurrence visto para ese par.
        key = (action, target)
        rec_by_target[key] = max(rec_by_target.get(key, 0), int(rec.get("recurrence") or 0))

    def sort_key(p: Proposal):
        return (
            _ACTION_ORDER.get(p.action, 99),
            -_recurrence_of(p, rec_by_target),
            p.target.lower(),
        )

    ordered = sorted(proposals, key=sort_key)
    if max_changes is None or max_changes < 0:
        return ordered
    return ordered[:max_changes]


# Mapeo de tipos de meta_recommendations → acciones del tuner.
# Solo los que tienen correspondencia directa; el resto no influye en el orden.
_META_TYPE_TO_ACTION = {
    "remove_subreddit": "remove_subreddit",
    "boost_subreddit": "add_high_signal",
}


# ── Report ────────────────────────────────────────────────────────────────


@dataclass
class ReportData:
    run_count: int
    first_ts: str
    last_ts: str
    total_proposals: int
    applied_proposals: list[Proposal]
    max_changes: int


def _run_timestamp(run: dict) -> str:
    """Intenta extraer un timestamp legible del meta-JSON. Fallback: vacio."""
    # Los metas reales no guardan ts, asi que caemos a los nombres de fichero.
    return run.get("run_at") or ""


def render_report(data: ReportData, runs_ts: list[str]) -> str:
    """Genera el texto del dry-run report."""
    counts = {"add_high_signal": 0, "demote_high_signal": 0, "remove_subreddit": 0, "remove_query": 0}
    for p in data.applied_proposals:
        counts[p.action] = counts.get(p.action, 0) + 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    lines.append(f"TUNER DRY-RUN -- {now}")
    rango = f"{runs_ts[0]} .. {runs_ts[-1]}" if runs_ts else "(sin runs)"
    lines.append(f"runs analizados: {data.run_count} ({rango})")
    lines.append(
        f"proposals totales: {data.total_proposals}  |  aplicadas (cap {data.max_changes}): "
        f"{len(data.applied_proposals)}"
    )
    lines.append("")
    if not data.applied_proposals:
        lines.append("(sin propuestas — configuracion actual estable con la ventana analizada)")
    else:
        width = max(len(p.action) for p in data.applied_proposals)
        for p in data.applied_proposals:
            target = p.target if len(p.target) <= 40 else p.target[:37] + "..."
            lines.append(f"[{p.action:<{width}}] {target:<40} reason={p.reason}")
    lines.append("")
    lines.append(
        f"RESUMEN: add={counts['add_high_signal']} "
        f"demote={counts['demote_high_signal']} "
        f"remove_sub={counts['remove_subreddit']} "
        f"remove_query={counts['remove_query']}"
    )
    return "\n".join(lines)


# ── Diff simulado de config.py ────────────────────────────────────────────


def render_config_diff(proposals: Iterable[Proposal]) -> str:
    """Previsualiza las ediciones en config.py como pseudo-Python legible.

    No edita el fichero — en A2 es solo para informar. En A4 un helper real
    aplicara estos cambios via libcst.
    """
    lines: list[str] = []
    for p in proposals:
        t = p.target
        if p.action == "add_high_signal":
            lines.append(f'HIGH_SIGNAL_SUBREDDITS.add("{t}")')
        elif p.action == "demote_high_signal":
            lines.append(f'HIGH_SIGNAL_SUBREDDITS.discard("{t}")')
        elif p.action == "remove_subreddit":
            lines.append(f'SUBREDDITS.remove("{t}")')
        elif p.action == "remove_query":
            escaped = t.replace('"', '\\"')
            lines.append(f'PAIN_SEARCH_QUERIES.remove("{escaped}")')
        else:
            lines.append(f"# accion desconocida: {p.action} {t}")
    if not lines:
        return "(sin cambios a aplicar sobre config.py)"
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="saas_radar.agents.tuner",
        description="Agente de tuning (dry-run): propone cambios a config.py.",
    )
    parser.add_argument("--runs-dir", default="data/runs", help="Carpeta con *_meta.json")
    parser.add_argument("--db-path", default="data/saas.db", help="Path a la BD SQLite")
    parser.add_argument("--lookback", type=int, default=10, help="Ultimos N runs a analizar")
    parser.add_argument("--max-changes", type=int, default=5, help="Cap de propuestas aplicadas")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Solo imprime report (unico modo en A2).",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Ademas del report imprime el diff simulado de config.py.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Import tardio de config: solo cuando se invoca el CLI. Permite que los
    # tests monten un entorno sin tocar el modulo real.
    from saas_radar import config

    runs = load_recent_runs(args.runs_dir, args.lookback)
    recs = load_meta_recommendations(args.db_path)

    proposals = propose_all_changes(
        runs=runs,
        meta_recommendations=recs,
        current_high_signal=config.HIGH_SIGNAL_SUBREDDITS,
        current_subreddits=config.SUBREDDITS,
        current_queries=config.PAIN_SEARCH_QUERIES,
    )
    applied = prioritize_and_cap(proposals, recs, args.max_changes)

    runs_ts = [
        os.path.basename(p).replace("_meta.json", "")
        for p in sorted(glob.glob(os.path.join(args.runs_dir, "*_meta.json")))[-len(runs) :]
    ]
    data = ReportData(
        run_count=len(runs),
        first_ts=runs_ts[0] if runs_ts else "",
        last_ts=runs_ts[-1] if runs_ts else "",
        total_proposals=len(proposals),
        applied_proposals=applied,
        max_changes=args.max_changes,
    )
    print(render_report(data, runs_ts))

    if args.show_diff:
        print()
        print("-- config.py diff simulado --")
        print(render_config_diff(applied))

    return 0


if __name__ == "__main__":
    sys.exit(main())
