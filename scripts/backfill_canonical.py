"""
scripts/backfill_canonical.py — Backfill one-shot de canonical_id.

Recorre `opportunities` en orden cronológico (id asc) y propone canonical_id
para cada fila usando `saas_radar.analysis.dedup.find_canonical`. La primera
fila de un cluster queda como su propia canónica. El usuario confirma cada
propuesta (o pasa --yes para aceptarlas todas).

Uso:
    python scripts/backfill_canonical.py                         # interactivo, BD por defecto
    python scripts/backfill_canonical.py --db data/saas.db       # BD explícita
    python scripts/backfill_canonical.py --yes                   # auto-acepta todo
    python scripts/backfill_canonical.py --dry-run               # solo imprime, no escribe

Idempotente: si una fila ya tiene canonical_id != NULL, no la toca salvo
con --force.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine, text

from saas_radar.analysis.dedup import find_canonical


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill canonical_id en opportunities")
    p.add_argument("--db", default="data/saas.db", help="Ruta a la BD SQLite")
    p.add_argument("--yes", action="store_true", help="Auto-acepta todas las propuestas")
    p.add_argument("--dry-run", action="store_true", help="No persiste; solo muestra el plan")
    p.add_argument("--force", action="store_true", help="Reasigna canonical_id incluso si ya estaba seteado")
    p.add_argument("--threshold", type=float, default=0.3, help="Jaccard mínimo (default 0.3)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[backfill] BD no encontrada: {db_path}")
        return 1

    engine = create_engine(f"sqlite:///{db_path}")

    # Migración inline: si la BD remota es antigua, añadir canonical_id antes
    # de leer. Idempotente: comprueba PRAGMA antes de tocar.
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(opportunities)")).fetchall()}
        if "canonical_id" not in cols:
            print("[backfill] Migrando: ALTER TABLE opportunities ADD COLUMN canonical_id INTEGER")
            conn.execute(text("ALTER TABLE opportunities ADD COLUMN canonical_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_opps_canonical ON opportunities(canonical_id)"))

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, canonical_id, product_name, evidence_quotes "
                "FROM opportunities ORDER BY id ASC"
            )
        ).fetchall()

        if not rows:
            print("[backfill] No hay filas en opportunities.")
            return 0

        existing: list[dict] = []
        plan: list[tuple[int, int | None, int]] = []  # (id, old_canonical, new_canonical)

        for r in rows:
            row = {
                "id": r[0],
                "canonical_id": r[1],
                "product_name": r[2] or "",
                "evidence_quotes": r[3],
            }
            if row["canonical_id"] is not None and not args.force:
                # Ya estaba asignada — la respetamos y la añadimos al pool de candidatos.
                existing.append(row)
                continue

            opp = {"product_name": row["product_name"], "evidence_quotes": row["evidence_quotes"]}
            match = find_canonical(opp, existing, threshold=args.threshold)
            new_canonical = match if match is not None else row["id"]
            plan.append((row["id"], row["canonical_id"], new_canonical))

            row["canonical_id"] = new_canonical
            existing.append(row)

        if not plan:
            print("[backfill] Nada que actualizar (todas las filas ya tienen canonical_id).")
            return 0

        # Resumen del plan
        print("\n=== PLAN DE BACKFILL ===")
        clusters: dict[int, list[int]] = {}
        for opp_id, _old, new in plan:
            clusters.setdefault(new, []).append(opp_id)
        for canonical, members in sorted(clusters.items()):
            mark = "*" if len(members) > 1 or members[0] != canonical else " "
            print(f"  [{mark}] canonical={canonical}  miembros={members}")

        canonicals_total = len({c for _, _, c in plan})
        print(f"\nTotal filas a actualizar: {len(plan)}  |  canónicas resultantes: {canonicals_total}")

        if args.dry_run:
            print("[backfill] --dry-run: no se persiste nada.")
            return 0

        if not args.yes:
            resp = input("\n¿Aplicar? (y/N) ").strip().lower()
            if resp not in ("y", "yes", "s", "si"):
                print("[backfill] Cancelado por el usuario.")
                return 0

        for opp_id, _old_canonical, new_canonical in plan:
            conn.execute(
                text("UPDATE opportunities SET canonical_id = :c WHERE id = :id"),
                {"c": new_canonical, "id": opp_id},
            )

        print(f"[backfill] OK: {len(plan)} filas actualizadas, {canonicals_total} canónicas.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
