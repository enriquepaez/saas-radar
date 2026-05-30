"""Agente heurístico con LLM: genera sugerencias de queries, subreddits y frases de dolor.

Toma el meta-JSON del último run y los posts de mayor señal, construye un prompt
enriquecido y llama al LLM para obtener sugerencias nuevas que persiste en
`meta_recommendations` para que el tuner determinista las eleve en siguientes runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys

import pandas as pd

from saas_radar.analysis.llm_clients import call_llm

logger = logging.getLogger(__name__)

# ── Schema esperado del LLM ───────────────────────────────────────────────────
_EMPTY_SUGGESTIONS: dict = {
    "new_queries": [],
    "new_subreddits": [],
    "new_phrases": [],
}


def _validate_schema(payload: dict) -> bool:
    """Valida que el dict tiene el schema esperado de sugerencias.

    Schema requerido:
    - new_queries: list de dicts con claves 'query' (str) y 'rationale' (str)
    - new_subreddits: list de dicts con claves 'name' (str) y 'rationale' (str)
    - new_phrases: list de dicts con claves 'phrase' (str), 'weight' (int), 'rationale' (str)
    """
    if not isinstance(payload, dict):
        return False
    for key in ("new_queries", "new_subreddits", "new_phrases"):
        if key not in payload:
            return False
        if not isinstance(payload[key], list):
            return False
    for item in payload.get("new_queries", []):
        if not isinstance(item, dict) or "query" not in item or "rationale" not in item:
            return False
        if not isinstance(item["query"], str) or not isinstance(item["rationale"], str):
            return False
    for item in payload.get("new_subreddits", []):
        if not isinstance(item, dict) or "name" not in item or "rationale" not in item:
            return False
        if not isinstance(item["name"], str) or not isinstance(item["rationale"], str):
            return False
    for item in payload.get("new_phrases", []):
        if not isinstance(item, dict):
            return False
        if "phrase" not in item or "weight" not in item or "rationale" not in item:
            return False
        if not isinstance(item["phrase"], str) or not isinstance(item["rationale"], str):
            return False
        if not isinstance(item["weight"], int):
            return False
    return True


def _build_prompt(
    recurring_niches: list[dict],
    top_posts_df: pd.DataFrame,
    discovered_subreddits: list[dict],
    empty_queries: list[str],
) -> str:
    """Construye el prompt para el LLM con los datos del run.

    El prompt incluye:
    - Nichos recurrentes (para orientar las queries nuevas)
    - Top posts (título + snippet + subreddit) para contexto concreto
    - Subreddits descubiertos (candidatos a añadir)
    - Queries vacías (a no repetir, contexto de cobertura actual)
    """
    lines: list[str] = []
    lines.append(
        "You are a micro-SaaS opportunity researcher. Based on the following data from "
        "a recent Reddit scraping run, suggest NEW queries, subreddits and pain signal "
        "phrases that would improve the pipeline's coverage of business pain points."
    )
    lines.append("")

    # Nichos recurrentes
    lines.append("## RECURRING NICHES (appear in multiple runs)")
    if recurring_niches:
        for n in recurring_niches:
            lines.append(f"- {n['niche']} (count: {n.get('count', '?')})")
    else:
        lines.append("(none)")
    lines.append("")

    # Top posts
    lines.append("## TOP POSTS BY SEMANTIC SCORE (up to 20)")
    if not top_posts_df.empty:
        for _, row in top_posts_df.head(20).iterrows():
            title = str(row.get("title", ""))
            text = str(row.get("text", ""))[:300]
            sub = str(row.get("subreddit", ""))
            score = row.get("semantic_score", "?")
            lines.append(f"- [{sub}] (score={score}) {title}")
            if text:
                lines.append(f"  > {text}")
    else:
        lines.append("(none)")
    lines.append("")

    # Subreddits descubiertos
    lines.append("## DISCOVERED SUBREDDITS (appeared in pain_search but NOT in config)")
    if discovered_subreddits:
        for d in discovered_subreddits:
            sub = d.get("subreddit") or d.get("name", "")
            posts = d.get("posts", "?")
            lines.append(f"- r/{sub} ({posts} posts)")
    else:
        lines.append("(none)")
    lines.append("")

    # Queries vacías
    lines.append("## EMPTY QUERIES (produced 0 results in this run - do NOT repeat these)")
    if empty_queries:
        for q in empty_queries[:20]:
            lines.append(f"- {q!r}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## INSTRUCTIONS")
    lines.append(
        "Suggest 3-5 NEW search queries targeting underserved pain points, "
        "1-3 subreddits worth adding, and 2-4 new pain signal phrases with weights 1-3."
    )
    lines.append("Focus on SPECIFIC, actionable business pain (not generic topics).")
    lines.append("Do NOT repeat queries from the empty list above.")
    lines.append("")
    lines.append("Respond ONLY with valid JSON matching this schema exactly:")
    lines.append("{")
    lines.append('  "new_queries": [{"query": "string", "rationale": "string"}],')
    lines.append('  "new_subreddits": [{"name": "string", "rationale": "string"}],')
    lines.append('  "new_phrases": [{"phrase": "string", "weight": 1, "rationale": "string"}]')
    lines.append("}")

    return "\n".join(lines)


def _dedup_against_config(suggestions: dict) -> dict:
    """Elimina sugerencias que ya están en la config actual.

    Importa config en el momento de la llamada (import lazy) para no depender
    del estado del módulo al importar este archivo.
    """
    from saas_radar import config

    existing_queries_lower = {q.lower() for q in config.PAIN_SEARCH_QUERIES}
    existing_subreddits_lower = {s.lower() for s in config.SUBREDDITS}
    existing_phrases_lower = {p[0].lower() for p in config.PAIN_SIGNAL_PHRASES}

    filtered_queries = [
        item for item in suggestions.get("new_queries", [])
        if item["query"].lower() not in existing_queries_lower
    ]
    filtered_subreddits = [
        item for item in suggestions.get("new_subreddits", [])
        if item["name"].lower() not in existing_subreddits_lower
    ]
    filtered_phrases = [
        item for item in suggestions.get("new_phrases", [])
        if item["phrase"].lower() not in existing_phrases_lower
    ]

    removed_q = len(suggestions.get("new_queries", [])) - len(filtered_queries)
    removed_s = len(suggestions.get("new_subreddits", [])) - len(filtered_subreddits)
    removed_p = len(suggestions.get("new_phrases", [])) - len(filtered_phrases)
    if removed_q or removed_s or removed_p:
        logger.info(
            "Dedup contra config: eliminadas %d queries, %d subreddits, %d phrases ya existentes.",
            removed_q,
            removed_s,
            removed_p,
        )

    return {
        "new_queries": filtered_queries,
        "new_subreddits": filtered_subreddits,
        "new_phrases": filtered_phrases,
    }


def generate_heuristic_suggestions(
    meta_json_path: str,
    top_posts_df: pd.DataFrame,
    provider: str = "claude",
) -> dict:
    """Genera sugerencias heurísticas de queries, subreddits y frases de dolor.

    Args:
        meta_json_path: Path al JSON de meta-análisis del run actual.
            Lee recurring_niches (recurrence >= 2), discovered_subreddits y empty_queries.
        top_posts_df: DataFrame con los posts de mayor semantic_score del run.
            Columnas esperadas: title, text, subreddit, semantic_score.
        provider: Provider LLM a usar ('claude', 'gemini', 'groq').

    Returns:
        dict con claves new_queries, new_subreddits, new_phrases. Si el LLM
        devuelve schema inválido o None, devuelve las listas vacías y loguea WARNING.
    """
    # Leer el meta-JSON
    try:
        with open(meta_json_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("No se pudo leer meta-JSON %s: %s", meta_json_path, exc)
        return dict(_EMPTY_SUGGESTIONS)

    # Filtrar nichos recurrentes (recurrence >= 2 del meta-JSON)
    recurring_niches = [n for n in meta.get("recurring_niches", []) if n.get("count", 0) >= 2]
    discovered_subreddits = meta.get("discovered_subreddits", [])
    empty_queries = meta.get("empty_queries", [])

    # Construir y enviar el prompt
    prompt = _build_prompt(recurring_niches, top_posts_df, discovered_subreddits, empty_queries)
    logger.info("Llamando al LLM (%s) para sugerencias heurísticas...", provider)
    result = call_llm(prompt, provider=provider, phase="synthesis")

    if result is None:
        logger.warning("LLM devolvió None para sugerencias heurísticas — skipping.")
        return dict(_EMPTY_SUGGESTIONS)

    if not _validate_schema(result):
        logger.warning(
            "Schema inválido en respuesta heurística del LLM: %s",
            str(result)[:300],
        )
        return dict(_EMPTY_SUGGESTIONS)

    logger.info(
        "LLM sugirió: %d queries, %d subreddits, %d phrases.",
        len(result.get("new_queries", [])),
        len(result.get("new_subreddits", [])),
        len(result.get("new_phrases", [])),
    )

    # Dedup contra la config actual
    return _dedup_against_config(result)


def persist_heuristic_suggestions(suggestions: dict, db_path: str) -> int:
    """Persiste las sugerencias heurísticas en meta_recommendations.

    Para cada sugerencia:
    - Si ya existe (type, target) → UPDATE recurrence = recurrence + 1
    - Si no existe → INSERT con recurrence=1, acted=0

    Args:
        suggestions: dict con new_queries, new_subreddits, new_phrases.
        db_path: Path a la BD SQLite.

    Returns:
        Número total de filas insertadas o actualizadas.
    """
    if not os.path.exists(db_path):
        logger.warning("BD no encontrada en %s — no se persisten sugerencias.", db_path)
        return 0

    rows: list[tuple[str, str]] = []
    for item in suggestions.get("new_queries", []):
        rows.append(("query_suggestion", item["query"]))
    for item in suggestions.get("new_subreddits", []):
        rows.append(("subreddit_suggestion", item["name"]))
    for item in suggestions.get("new_phrases", []):
        rows.append(("phrase_suggestion", item["phrase"]))

    if not rows:
        return 0

    conn = sqlite3.connect(db_path)
    count = 0
    try:
        for rec_type, target in rows:
            existing = conn.execute(
                "SELECT id, recurrence FROM meta_recommendations WHERE type = ? AND target = ?",
                (rec_type, target),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE meta_recommendations SET recurrence = recurrence + 1 WHERE id = ?",
                    (existing[0],),
                )
                logger.debug("Incrementado recurrence para (%s, %s).", rec_type, target)
            else:
                conn.execute(
                    "INSERT INTO meta_recommendations (type, target, recurrence, acted) VALUES (?, ?, 1, 0)",
                    (rec_type, target),
                )
                logger.debug("Insertado nuevo (%s, %s) con recurrence=1.", rec_type, target)
            count += 1
        conn.commit()
    finally:
        conn.close()

    logger.info("Persistidas %d sugerencias heurísticas en meta_recommendations.", count)
    return count


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="saas_radar.agents.heuristic_tuner",
        description="Genera sugerencias de queries/subreddits/frases via LLM y las persiste en BD.",
    )
    parser.add_argument("--meta-json", required=True, help="Path al JSON de meta-análisis del run.")
    parser.add_argument("--db-path", default="data/saas.db", help="Path a la BD SQLite.")
    parser.add_argument("--provider", default="claude", help="Provider LLM: claude | gemini | groq.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Imprime resumen de sugerencias sin escribir en BD.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from saas_radar.logging_setup import setup_logging

    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), fmt=os.getenv("LOG_FORMAT", "text"))

    # Necesitamos un DataFrame vacío cuando no tenemos posts (CLI standalone)
    top_posts_df = pd.DataFrame(columns=["title", "text", "subreddit", "semantic_score"])

    suggestions = generate_heuristic_suggestions(
        meta_json_path=args.meta_json,
        top_posts_df=top_posts_df,
        provider=args.provider,
    )

    # Imprimir resumen
    print(f"\n── SUGERENCIAS HEURÍSTICAS LLM ({args.provider}) ──")
    new_q = suggestions.get("new_queries", [])
    new_s = suggestions.get("new_subreddits", [])
    new_p = suggestions.get("new_phrases", [])
    print(f"  Queries nuevas:      {len(new_q)}")
    print(f"  Subreddits nuevos:   {len(new_s)}")
    print(f"  Frases de dolor:     {len(new_p)}")

    if new_q:
        print("\n  QUERIES:")
        for item in new_q:
            print(f"    [{item['query']}] — {item['rationale']}")
    if new_s:
        print("\n  SUBREDDITS:")
        for item in new_s:
            print(f"    [r/{item['name']}] — {item['rationale']}")
    if new_p:
        print("\n  FRASES (peso):")
        for item in new_p:
            print(f"    [{item['phrase']} w={item['weight']}] — {item['rationale']}")

    if args.dry_run:
        print("\n  (dry-run: sin escritura en BD)")
        return 0

    persisted = persist_heuristic_suggestions(suggestions, args.db_path)
    print(f"\n  {persisted} sugerencias persistidas en meta_recommendations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
