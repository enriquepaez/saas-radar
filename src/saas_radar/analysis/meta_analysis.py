"""Meta-análisis post-run: señal por subreddit, queries vacías, nichos, recomendaciones."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from saas_radar import config
from saas_radar.storage.db import _get_db_url, _make_engine, persist_meta_recommendations

logger = logging.getLogger(__name__)


def generate_meta_analysis(
    extractions: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    post_age_days: int,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Genera el informe de meta-análisis a partir de las extracciones y oportunidades del run.

    No accede a la BD directamente: recibe los datos como listas de dicts y
    delega las consultas SQL a las sub-funciones privadas que reciben db_url.
    """
    # ── 1. Señal por subreddit ──────────────────────────────────────────────
    sub_counts: Counter = Counter()
    sub_problems: Counter = Counter()
    sub_payment: Counter = Counter()
    for ex in extractions:
        sub = ex.get("_subreddit", "unknown")
        sub_counts[sub] += 1
        if ex.get("has_problem"):
            sub_problems[sub] += 1
        if ex.get("payment_signal"):
            sub_payment[sub] += 1

    subreddit_signal = []
    for sub in sorted(sub_counts.keys(), key=lambda s: -sub_problems.get(s, 0)):
        total = sub_counts[sub]
        problems = sub_problems.get(sub, 0)
        payments = sub_payment.get(sub, 0)
        ratio = problems / total if total > 0 else 0
        subreddit_signal.append({
            "subreddit": sub,
            "posts_analyzed": total,
            "with_problem": problems,
            "with_payment_signal": payments,
            "hit_rate": round(ratio, 2),
        })

    # Subreddits configurados que NO produjeron ninguna extracción en este run
    configured = {s.lower() for s in config.SUBREDDITS}
    active = {s["subreddit"].lower() for s in subreddit_signal}
    silent_subreddits = sorted(configured - active)

    # ── 2. Queries de dolor sin resultados ─────────────────────────────────
    empty_queries = _find_empty_queries(post_age_days, db_url)

    # ── 3. Nichos recurrentes ───────────────────────────────────────────────
    who_counter: Counter = Counter()
    for ex in extractions:
        if ex.get("has_problem"):
            who = ex.get("who_has_it", "").strip().lower()
            if who and who not in {"unknown", "n/a", "the user"}:
                who_counter[who] += 1
    recurring_niches = [
        {"niche": niche, "count": count}
        for niche, count in who_counter.most_common(15)
    ]

    # ── 4. Categorías de dolor ─────────────────────────────────────────────
    pain_keywords: Counter = Counter()
    pain_terms = [
        "spreadsheet", "excel", "manual", "invoice", "tracking",
        "scheduling", "inventory", "onboarding", "reporting",
        "reconciliation", "pricing", "billing", "payroll",
        "maintenance", "documentation", "integration", "api",
    ]
    for ex in extractions:
        if not ex.get("has_problem"):
            continue
        haystack = " ".join([
            str(ex.get("problem_description", "")),
            str(ex.get("workflow_context", "")),
            str(ex.get("current_workaround", "")),
        ]).lower()
        for term in pain_terms:
            if term in haystack:
                pain_keywords[term] += 1
    pain_categories = [
        {"category": cat, "count": count}
        for cat, count in pain_keywords.most_common(10)
    ]

    # ── 5. Subreddits descubiertos via pain_search ─────────────────────────
    discovered_subs = _find_discovered_subreddits(post_age_days, db_url)

    # ── 6. Recomendaciones accionables ─────────────────────────────────────
    recommendations = _build_recommendations(
        subreddit_signal, silent_subreddits, empty_queries,
        recurring_niches, discovered_subs,
    )

    meta = {
        "subreddit_signal": subreddit_signal,
        "silent_subreddits": silent_subreddits,
        "empty_queries": empty_queries,
        "recurring_niches": recurring_niches,
        "pain_categories": pain_categories,
        "discovered_subreddits": discovered_subs,
        "recommendations": recommendations,
        "summary": {
            "total_extractions": len(extractions),
            "with_problem": sum(1 for e in extractions if e.get("has_problem")),
            "with_payment": sum(1 for e in extractions if e.get("payment_signal")),
            "opportunities": len(opportunities),
            "subreddits_active": len(active),
            "subreddits_silent": len(silent_subreddits),
            "empty_queries_count": len(empty_queries),
        },
    }
    return meta


def save_meta_analysis(
    meta: dict[str, Any],
    run_json_path: str,
    run_id: int | None = None,
    db_url: str | None = None,
) -> str:
    """Guarda el meta-análisis en JSON y persiste recomendaciones en BD.

    Deriva la ruta a partir del NOMBRE del archivo de results (nunca del
    directorio): '<ts>_results.json' -> '<ts>_meta.json'. El meta JSON queda
    junto al results, así el glob de la fase 4.5 (main.py) lo encuentra en el
    mismo directorio de output.
    Si run_id se pasa, llama persist_meta_recommendations para acumular
    recurrence en recomendaciones repetidas entre runs.
    """
    meta_path = str(_derive_meta_path(run_json_path))
    os.makedirs(os.path.dirname(meta_path) or ".", exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Meta-análisis guardado en %s", meta_path)

    if run_id is not None and meta.get("recommendations"):
        persist_meta_recommendations(run_id, meta["recommendations"], db_url)

    return meta_path


def _derive_meta_path(run_json_path: str) -> Path:
    """Deriva la ruta del meta JSON tocando solo el nombre del archivo.

    Un str.replace('.json', '_meta.json') sobre la ruta completa corrompería
    directorios cuyo nombre contiene '.json' (caso legacy real: el default
    antiguo data/ai_analysis.json/ generaba <ts>_results.json dentro).
    """
    p = Path(run_json_path)
    name = p.name
    if name.endswith("_results.json"):
        meta_name = name[: -len("_results.json")] + "_meta.json"
    elif name.endswith(".json"):
        meta_name = name[: -len(".json")] + "_meta.json"
    else:
        meta_name = name + "_meta.json"
    return p.with_name(meta_name)


def print_meta_summary(meta: dict[str, Any], db_url: str | None = None) -> None:
    """Imprime un resumen compacto del meta-análisis en consola."""
    s = meta["summary"]
    print(f"\n{'=' * 70}")
    print("META-ANALISIS DEL RUN")
    print(f"{'=' * 70}")
    print(f"  Extracciones: {s['total_extractions']} total, {s['with_problem']} con problema, {s['with_payment']} con pago")
    print(f"  Subreddits: {s['subreddits_active']} activos, {s['subreddits_silent']} silenciosos")
    print(f"  Queries vacias: {s['empty_queries_count']}")

    if meta.get("pain_categories"):
        cats = ", ".join(f"{c['category']}({c['count']})" for c in meta["pain_categories"][:5])
        print(f"  Top dolor: {cats}")

    recs = meta.get("recommendations", [])
    if recs:
        print(f"\n  RECOMENDACIONES ({len(recs)}):")
        for r in recs:
            print(f"    [{r['type']}] {r['action']}")

    recurring = _get_recurring_recommendations(min_recurrence=2, db_url=db_url)
    if recurring:
        print(f"\n  RECURRENTES (aparecen en {2}+ runs):")
        for r in recurring:
            print(f"    [{r['type']}] {r['target']} - {r['recurrence']} runs consecutivos")

    print(f"{'=' * 70}")


def _find_empty_queries(post_age_days: int, db_url: str | None = None) -> list[str]:
    """Queries de PAIN_SEARCH_QUERIES que no tienen posts en el rango temporal."""
    cutoff = time.time() - post_age_days * 86400
    url = _get_db_url(db_url)
    eng = _make_engine(url)
    empty = []
    with eng.connect() as conn:
        for query in config.PAIN_SEARCH_QUERIES:
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM reddit_posts "
                    "WHERE search_query = :q AND created_utc >= :cutoff"
                ),
                {"q": query, "cutoff": cutoff},
            )
            if result.scalar() == 0:
                empty.append(query)
    return empty


def _find_discovered_subreddits(
    post_age_days: int, db_url: str | None = None
) -> list[dict[str, Any]]:
    """Subreddits que aparecen en pain_search con >=2 hits pero NO están en SUBREDDITS config."""
    cutoff = time.time() - post_age_days * 86400
    configured = {s.lower() for s in config.SUBREDDITS}
    url = _get_db_url(db_url)
    eng = _make_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT LOWER(subreddit) as sub, COUNT(*) as cnt "
                "FROM reddit_posts "
                "WHERE source = 'pain_search' AND created_utc >= :cutoff "
                "GROUP BY LOWER(subreddit) "
                "HAVING cnt >= 2 "
                "ORDER BY cnt DESC"
            ),
            {"cutoff": cutoff},
        ).fetchall()
    discovered = []
    for row in rows:
        if row[0] not in configured:
            discovered.append({"subreddit": row[0], "posts": row[1]})
    return discovered[:10]


def _build_recommendations(
    subreddit_signal: list[dict],
    silent_subreddits: list[str],
    empty_queries: list[str],
    recurring_niches: list[dict],
    discovered_subs: list[dict],
) -> list[dict[str, str]]:
    """Genera recomendaciones accionables basadas en los datos del meta-análisis."""
    recs = []

    # Subreddits con 0% hit rate y al menos 3 posts analizados
    for s in subreddit_signal:
        if s["posts_analyzed"] >= 3 and s["hit_rate"] == 0:
            recs.append({
                "type": "remove_subreddit",
                "target": f"r/{s['subreddit']}",
                "action": f"Considerar quitar r/{s['subreddit']} — {s['posts_analyzed']} posts analizados, 0 con problema real.",
            })

    # Subreddits con alto hit rate + al menos 1 señal de pago
    for s in subreddit_signal:
        if s["hit_rate"] >= 0.6 and s["with_payment_signal"] >= 1:
            recs.append({
                "type": "boost_subreddit",
                "target": f"r/{s['subreddit']}",
                "action": f"r/{s['subreddit']} tiene {s['hit_rate']:.0%} hit rate y {s['with_payment_signal']} señales de pago — considerar subirlo a HIGH_SIGNAL_SUBREDDITS.",
            })

    # Subreddits silenciosos
    if silent_subreddits:
        recs.append({
            "type": "check_silent",
            "target": "silent_subreddits",
            "action": f"{len(silent_subreddits)} subreddits configurados sin posts en este run: {', '.join(silent_subreddits[:5])}{'...' if len(silent_subreddits) > 5 else ''}.",
        })

    # Top 3 subreddits descubiertos
    for d in discovered_subs[:3]:
        recs.append({
            "type": "add_subreddit",
            "target": f"r/{d['subreddit']}",
            "action": f"r/{d['subreddit']} apareció {d['posts']} veces en pain_search pero no está en SUBREDDITS — considerar añadirlo.",
        })

    # Muchas queries vacías
    if len(empty_queries) > 10:
        recs.append({
            "type": "prune_queries",
            "target": "empty_queries",
            "action": f"{len(empty_queries)} queries de dolor no produjeron resultados en este rango temporal. Considerar revisar/eliminar las menos relevantes.",
        })

    # Nichos emergentes con count >= 3
    for n in recurring_niches[:3]:
        if n["count"] >= 3:
            recs.append({
                "type": "emerging_niche",
                "target": n["niche"],
                "action": f"Nicho recurrente: '{n['niche']}' apareció {n['count']} veces — considerar queries de dolor más específicas para este perfil.",
            })

    return recs


def _get_recurring_recommendations(
    min_recurrence: int = 2, db_url: str | None = None
) -> list[dict]:
    """Consulta recomendaciones no actuadas con recurrence >= min_recurrence."""
    url = _get_db_url(db_url)
    eng = _make_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT type, target, action, recurrence FROM meta_recommendations "
                "WHERE acted = 0 AND recurrence >= :min "
                "ORDER BY recurrence DESC"
            ),
            {"min": min_recurrence},
        ).fetchall()
    return [
        {"type": row[0], "target": row[1], "action": row[2], "recurrence": row[3]}
        for row in rows
    ]
