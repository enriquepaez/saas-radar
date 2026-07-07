"""Tuning agent — fase A2 (dry-run) + A4 (apply PR).

Lee los ultimos N meta-JSONs de `data/runs/` y las filas de
`meta_recommendations` en la BD, aplica las reglas deterministas de
`tuning_rules.py` y imprime un report en consola. Con --apply edita
`config.py`, crea una rama y abre un PR en GitHub.

Uso:
    python -m saas_radar.agents.tuner --lookback 7 --max-changes 5
    python -m saas_radar.agents.tuner --lookback 7 --max-changes 5 --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
    "add_query": 4,
    "add_subreddit": 5,
    "add_phrase": 6,
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
    "query_suggestion": "add_query",
    "subreddit_suggestion": "add_subreddit",
    "phrase_suggestion": "add_phrase",
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
    counts = {
        "add_high_signal": 0,
        "demote_high_signal": 0,
        "remove_subreddit": 0,
        "remove_query": 0,
        "add_query": 0,
        "add_subreddit": 0,
        "add_phrase": 0,
    }
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
        f"remove_query={counts['remove_query']} "
        f"add_query={counts['add_query']} "
        f"add_sub={counts['add_subreddit']} "
        f"add_phrase={counts['add_phrase']}"
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
        elif p.action == "add_query":
            escaped = t.replace('"', '\\"')
            lines.append(f'PAIN_SEARCH_QUERIES.append("{escaped}")')
        elif p.action == "add_subreddit":
            lines.append(f'SUBREDDITS.append("{_normalize_subreddit(t)}")')
        elif p.action == "add_phrase":
            escaped = t.replace('"', '\\"')
            lines.append(f'PAIN_SIGNAL_PHRASES.append(("{escaped}", {_DEFAULT_PHRASE_WEIGHT}))')
        else:
            lines.append(f"# accion desconocida: {p.action} {t}")
    if not lines:
        return "(sin cambios a aplicar sobre config.py)"
    return "\n".join(lines)


# ── Edición de config.py ─────────────────────────────────────────────

# Peso por defecto para frases sugeridas por el LLM (A7). Conservador: las
# frases existentes van de 1 a 3; una sugerencia aun no validada con datos
# reales no debe puntuar al maximo.
_DEFAULT_PHRASE_WEIGHT = 2


def _normalize_subreddit(target: str) -> str:
    """Quita el prefijo 'r/' (case-insensitive) de un nombre de subreddit.

    Las sugerencias del LLM pueden venir como 'r/notion', pero en config.py
    SUBREDDITS guarda solo el nombre ('notion').
    """
    return re.sub(r"^r/", "", target.strip(), flags=re.IGNORECASE)


def _find_block_range(lines: list[str], var_name: str) -> tuple[int, int]:
    """Devuelve (start_idx, end_idx) del bloque de la variable (inclusive).

    Cuenta llaves/corchetes para encontrar el cierre.
    Devuelve (-1, -1) si no encuentra la variable.
    """
    start = None
    depth = 0
    for i, line in enumerate(lines):
        if start is None:
            if re.match(rf"^{re.escape(var_name)}\s*[=]", line):
                start = i
                depth = line.count("{") + line.count("[") - line.count("}") - line.count("]")
                if depth == 0:
                    return (i, i)
        else:
            depth += line.count("{") + line.count("[") - line.count("}") - line.count("]")
            if depth <= 0:
                return (start, i)
    return (-1, -1) if start is None else (start, len(lines) - 1)


def _insert_into_set(text: str, var_name: str, entry: str) -> str:
    """Inserta '    "<entry>",' antes del cierre } de la variable set.

    Detecta la indentacion del ultimo elemento y la replica.
    Si ya existe la entrada (case-insensitive), no la duplica.
    """
    lines = text.split("\n")
    start, end = _find_block_range(lines, var_name)
    if start == -1:
        return text
    # Escapar comillas dobles: el entry puede ser texto libre del LLM
    # (add_query) y sin escapar generaria un config.py con SyntaxError.
    escaped = entry.replace('"', '\\"')
    # Si ya existe, no duplicar. El patron se construye sobre la forma
    # ESCAPADA porque es la que queda escrita en el fichero.
    pattern = re.compile(rf'^\s*"{re.escape(escaped)}"\s*,?\s*$', re.IGNORECASE)
    for i in range(start, end + 1):
        if pattern.match(lines[i]):
            return text  # ya existe
    # Encontrar linea de cierre (la primera que empiece con } en el bloque)
    for i in range(end, start - 1, -1):
        if lines[i].strip() in ("}", "},", "]", "],"):
            # Detectar indentacion del ultimo elemento real (no comentario, no vacio)
            indent = "    "
            for j in range(i - 1, start, -1):
                s = lines[j].strip()
                if s and not s.startswith("#"):
                    indent = " " * (len(lines[j]) - len(lines[j].lstrip()))
                    break
            lines.insert(i, f'{indent}"{escaped}",')
            return "\n".join(lines)
    return text


def _insert_tuple_into_list(text: str, var_name: str, entry: str, weight: int) -> str:
    """Inserta '    ("<entry>", <weight>),' antes del cierre ] de la variable lista.

    Pensado para PAIN_SIGNAL_PHRASES, cuyos elementos son tuplas (frase, puntos)
    — el formato de `_insert_into_set` ('"entry",') no sirve aqui.
    Si la frase ya existe con cualquier peso (case-insensitive), no la duplica.
    Detecta la indentacion del ultimo elemento y la replica.
    """
    lines = text.split("\n")
    start, end = _find_block_range(lines, var_name)
    if start == -1:
        return text
    # Escapar comillas dobles ANTES de construir el patron de duplicados:
    # la linea escrita en el fichero contiene la forma escapada (\"), asi
    # que el dedupe debe matchear contra esa misma forma o nunca acierta.
    escaped = entry.replace('"', '\\"')
    # Si ya existe la frase (con cualquier peso), no duplicar
    pattern = re.compile(
        rf'^\s*\(\s*"{re.escape(escaped)}"\s*,\s*\d+\s*\)\s*,?\s*$', re.IGNORECASE
    )
    for i in range(start, end + 1):
        if pattern.match(lines[i]):
            return text  # ya existe
    # Encontrar linea de cierre (la primera que empiece con ] en el bloque)
    for i in range(end, start - 1, -1):
        if lines[i].strip() in ("]", "],"):
            # Detectar indentacion del ultimo elemento real (no comentario, no vacio)
            indent = "    "
            for j in range(i - 1, start, -1):
                s = lines[j].strip()
                if s and not s.startswith("#"):
                    indent = " " * (len(lines[j]) - len(lines[j].lstrip()))
                    break
            lines.insert(i, f'{indent}("{escaped}", {weight}),')
            return "\n".join(lines)
    return text


def _remove_from_collection(text: str, var_name: str, entry: str) -> str:
    """Elimina la linea con '    "<entry>",' dentro del bloque de var_name.

    Matching case-insensitive para subreddits (que pueden tener mayusculas en config.py).
    Solo elimina la primera ocurrencia encontrada dentro del bloque.
    """
    lines = text.split("\n")
    start, end = _find_block_range(lines, var_name)
    if start == -1:
        return text
    pattern = re.compile(rf'^\s*"{re.escape(entry)}"\s*,?\s*$', re.IGNORECASE)
    for i in range(start, end + 1):
        if pattern.match(lines[i]):
            lines.pop(i)
            return "\n".join(lines)
    return text


def apply_proposals(proposals: list[Proposal], config_path: Path) -> None:
    """Aplica la lista de propuestas editando config.py en disco.

    Usa edicion linea a linea (no regex global) para preservar formato y comentarios.
    """
    text = config_path.read_text(encoding="utf-8")
    for p in proposals:
        if p.action == "add_high_signal":
            text = _insert_into_set(text, "HIGH_SIGNAL_SUBREDDITS", p.target)
        elif p.action == "demote_high_signal":
            text = _remove_from_collection(text, "HIGH_SIGNAL_SUBREDDITS", p.target)
        elif p.action == "remove_subreddit":
            text = _remove_from_collection(text, "SUBREDDITS", p.target)
        elif p.action == "remove_query":
            text = _remove_from_collection(text, "PAIN_SEARCH_QUERIES", p.target)
        elif p.action == "add_query":
            text = _insert_into_set(text, "PAIN_SEARCH_QUERIES", p.target)
        elif p.action == "add_subreddit":
            text = _insert_into_set(text, "SUBREDDITS", _normalize_subreddit(p.target))
        elif p.action == "add_phrase":
            text = _insert_tuple_into_list(
                text, "PAIN_SIGNAL_PHRASES", p.target, _DEFAULT_PHRASE_WEIGHT
            )
        else:
            logger.warning("Accion desconocida ignorada: %s", p.action)
    config_path.write_text(text, encoding="utf-8")


# ── Helpers git/gh y BD ──────────────────────────────────────────────


def check_open_pr(branch_prefix: str) -> str | None:
    """Devuelve la URL del PR abierto con ese prefijo de rama, o None si no hay.

    Usa `gh pr list` con JSON output. Si gh no esta disponible, devuelve None con warning.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "headRefName,url"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("gh pr list fallo: %s", result.stderr[:200])
            return None
        prs = json.loads(result.stdout or "[]")
        for pr in prs:
            if pr.get("headRefName", "").startswith(branch_prefix):
                return pr.get("url", "")
        return None
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        logger.warning("check_open_pr error: %s", exc)
        return None


def mark_acted(db_path: str, proposals: list[Proposal], acted: int = 1) -> None:
    """Actualiza meta_recommendations.acted para los targets de las propuestas."""
    if not proposals or not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        for p in proposals:
            conn.execute(
                "UPDATE meta_recommendations SET acted = ? WHERE target = ?",
                (acted, p.target),
            )
        conn.commit()
    finally:
        conn.close()


def sync_acted_status(db_path: str, state_file: Path) -> None:
    """Si el PR anterior fue cerrado sin merge, revierte acted=0 en las filas afectadas.

    Lee data/tuner_state.json para conocer la URL del ultimo PR. Si el PR esta
    CLOSED (no MERGED), resetea acted=0 en todas las filas con acted=1.
    """
    if not state_file.exists():
        return
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    pr_url = state.get("pr_url", "")
    if not pr_url:
        return
    try:
        result = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return
        data = json.loads(result.stdout)
        pr_state = data.get("state", "")
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return
    # state == "CLOSED" significa cerrado SIN merge; "MERGED" es merge exitoso
    if pr_state == "CLOSED":
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("UPDATE meta_recommendations SET acted = 0 WHERE acted = 1")
                conn.commit()
            finally:
                conn.close()
        logger.info("PR cerrado sin merge — acted revertido a 0.")


def _append_readme_registry(readme_path: Path, date_str: str, proposals: list[Proposal], pr_url: str) -> None:
    """Añade una entrada al registro de tuning en README.md.

    Si la seccion '## Registro de tuning automatico' no existe, la crea al final.
    """
    text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    entry_lines = [
        f"### {date_str}",
        f"PR: {pr_url}",
        "",
    ]
    for p in proposals:
        entry_lines.append(f"- `{p.action}` → `{p.target}` — {p.reason}")
    entry_lines.append("")
    entry = "\n".join(entry_lines)

    section_header = "## Registro de tuning automatico\n"
    if section_header not in text:
        text = text.rstrip("\n") + f"\n\n{section_header}\n{entry}"
    else:
        text = text + entry
    readme_path.write_text(text, encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="saas_radar.agents.tuner",
        description="Agente de tuning: propone y aplica cambios a config.py.",
    )
    parser.add_argument("--runs-dir", default="data/runs", help="Carpeta con *_meta.json")
    parser.add_argument("--db-path", default="data/saas.db", help="Path a la BD SQLite")
    parser.add_argument("--lookback", type=int, default=10, help="Ultimos N runs a analizar")
    parser.add_argument("--max-changes", type=int, default=5, help="Cap de propuestas aplicadas")
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Ademas del report imprime el diff simulado de config.py.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Aplica los cambios en config.py, crea rama y PR en GitHub.",
    )
    parser.add_argument(
        "--config-path",
        default="src/saas_radar/config.py",
        help="Path a config.py (para poder sobreescribirlo en tests).",
    )
    parser.add_argument(
        "--readme-path",
        default="README.md",
        help="Path a README.md para el registro de tuning.",
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

    if not args.apply:
        return 0

    # ── Modo apply ────────────────────────────────────────────────────────

    if not applied:
        print("(sin propuestas — no hay nada que aplicar)")
        return 0

    # 1. Sync estado del PR anterior (revertir acted si fue cerrado sin merge)
    state_file = Path(args.runs_dir).parent / "tuner_state.json"
    sync_acted_status(args.db_path, state_file)

    # 2. Guard de PR abierto
    existing_pr = check_open_pr("chore/auto-tuning-")
    if existing_pr:
        print(f"[SKIP] PR ya abierto: {existing_pr} — sin cambios.")
        return 0

    # 3. Aplicar cambios en config.py
    config_path = Path(args.config_path)
    text_before = config_path.read_text(encoding="utf-8")
    apply_proposals(applied, config_path)
    text_after = config_path.read_text(encoding="utf-8")

    # Guard: si ninguna propuesta produjo un cambio real (p.ej. todas eran
    # duplicados ya presentes en config.py), no hay nada que commitear —
    # crear rama + commit fallaria con "nothing to commit".
    if text_after == text_before:
        print("(las propuestas no produjeron cambios en config.py — no se crea PR)")
        return 0
    logger.info("config.py actualizado con %d propuestas.", len(applied))

    # 4. Crear rama, commit y push
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    branch = f"chore/auto-tuning-{date_str}"
    subprocess.run(["git", "checkout", "-b", branch], check=True)
    subprocess.run(["git", "add", str(config_path)], check=True)
    subprocess.run(["git", "commit", "-m", f"chore: auto-tuning {date_str}"], check=True)
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)

    # 5. Guardar report en fichero y construir body del PR
    report_text = render_report(data, runs_ts)
    report_path = Path("tuner_report.txt")
    report_path.write_text(report_text, encoding="utf-8")

    meta_files = sorted(glob.glob(os.path.join(args.runs_dir, "*_meta.json")))
    meta_link = meta_files[-1] if meta_files else "(no meta-JSON disponible)"
    pr_body = f"{report_text}\n\nMeta-JSON: `{meta_link}`"

    # 6. Crear PR con gh
    result = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            f"chore: auto-tuning {date_str}",
            "--body",
            pr_body,
            "--base",
            "main",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Sin esto, un check=True lanzaria CalledProcessError y el motivo
        # real del fallo de gh (stderr) quedaria invisible en el log de CI.
        print(f"[ERROR] gh pr create fallo (exit {result.returncode}):", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1
    pr_url = result.stdout.strip()
    print(f"PR creado: {pr_url}")

    # 7. Marcar acted=1 en BD
    mark_acted(args.db_path, applied)

    # 8. Guardar estado para el proximo run
    state_file.write_text(json.dumps({"pr_url": pr_url, "date": date_str}), encoding="utf-8")

    # 9. Append al README
    _append_readme_registry(Path(args.readme_path), date_str, applied, pr_url)

    return 0


if __name__ == "__main__":
    sys.exit(main())
