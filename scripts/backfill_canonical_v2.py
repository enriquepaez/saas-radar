"""
scripts/backfill_canonical_v2.py — Backfill one-shot de canonical_id con embeddings.

Recorre `opportunities` en orden cronológico (id asc) y re-asigna canonical_id
usando `saas_radar.analysis.dedup.find_canonical_v2` (sentence-transformers).
La primera fila de un cluster queda como su propia canónica.

Requiere: pip install 'saas-radar[dedup-v2]'

Uso:
    python scripts/backfill_canonical_v2.py --dry-run          # solo imprime, no escribe
    python scripts/backfill_canonical_v2.py --yes              # aplica sin confirmación
    python scripts/backfill_canonical_v2.py --yes --force      # re-procesa incluso las ya asignadas
    python scripts/backfill_canonical_v2.py --db-path data/saas.db --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from saas_radar.analysis.dedup import find_canonical_v2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill canonical_id en opportunities usando dedup v2 (embeddings)"
    )
    p.add_argument("--db-path", default="data/saas.db", help="Ruta a la BD SQLite (default: data/saas.db)")
    p.add_argument("--dry-run", action="store_true", help="Solo imprime el plan; no persiste cambios")
    p.add_argument("--yes", action="store_true", help="Aplica los cambios sin pedir confirmación")
    p.add_argument("--force", action="store_true", help="Re-procesa filas que ya tienen canonical_id asignado")
    p.add_argument("--threshold", type=float, default=0.75, help="Umbral de similitud coseno (default 0.75)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.dry_run and not args.yes:
        print("Usa --dry-run para previsualizar o --yes para aplicar.")
        sys.exit(1)

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[backfill_v2] BD no encontrada: {db_path}")
        return 1

    engine = create_engine(f"sqlite:///{db_path}")

    # Migración inline idempotente: asegura que canonical_id existe antes de leer.
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(opportunities)")).fetchall()}
        if "canonical_id" not in cols:
            print("[backfill_v2] Migrando: ALTER TABLE opportunities ADD COLUMN canonical_id INTEGER")
            conn.execute(text("ALTER TABLE opportunities ADD COLUMN canonical_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_opps_canonical ON opportunities(canonical_id)"))

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, canonical_id, product_name, core_problem, niche, evidence_quotes "
                "FROM opportunities ORDER BY id ASC"
            )
        ).fetchall()

        if not rows:
            print("[backfill_v2] No hay filas en opportunities.")
            return 0

        existing: list[dict] = []
        plan: list[tuple[int, int | None, int]] = []  # (id, old_canonical, new_canonical)

        for r in rows:
            row = {
                "id": r[0],
                "canonical_id": r[1],
                "product_name": r[2] or "",
                "core_problem": r[3] or "",
                "niche": r[4] or "",
                "evidence_quotes": r[5],
            }
            if row["canonical_id"] is not None and not args.force:
                # Ya asignada — la respetamos y la añadimos al pool de candidatos.
                existing.append(row)
                continue

            opp = {
                "product_name": row["product_name"],
                "core_problem": row["core_problem"],
                "niche": row["niche"],
            }
            match = find_canonical_v2(opp, existing, threshold=args.threshold)
            new_canonical = match if match is not None else row["id"]
            plan.append((row["id"], row["canonical_id"], new_canonical))

            row["canonical_id"] = new_canonical
            existing.append(row)

        if not plan:
            print("[backfill_v2] Nada que actualizar (todas las filas ya tienen canonical_id).")
            return 0

        # Resumen del plan
        print("\n=== PLAN DE BACKFILL v2 (embeddings) ===")
        clusters: dict[int, list[int]] = {}
        for opp_id, _old, new in plan:
            clusters.setdefault(new, []).append(opp_id)
        for canonical, members in sorted(clusters.items()):
            mark = "*" if len(members) > 1 or members[0] != canonical else " "
            print(f"  [{mark}] canonical={canonical}  miembros={members}")

        canonicals_total = len({c for _, _, c in plan})
        print(f"\nTotal filas a actualizar: {len(plan)}  |  canónicas resultantes: {canonicals_total}")

        if args.dry_run:
            print("[backfill_v2] --dry-run: no se persiste nada.")
            return 0

        for opp_id, _old_canonical, new_canonical in plan:
            conn.execute(
                text("UPDATE opportunities SET canonical_id = :c WHERE id = :id"),
                {"c": new_canonical, "id": opp_id},
            )

        print(f"[backfill_v2] OK: {len(plan)} filas actualizadas, {canonicals_total} canónicas.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
