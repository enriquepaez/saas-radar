"""Reglas deterministas del agente de tuning.

Cada funcion aplica una regla del README "Tuning del pipeline" a un historico de meta-analisis
y devuelve las acciones propuestas. Sin LLM: decisiones reproducibles,
baratas y testables.

Entradas esperadas:
- `runs`: lista de dicts con estructura de `data/runs/<ts>_meta.json`, ordenados
  del mas antiguo al mas reciente.
- `meta_recommendations`: filas de la tabla `meta_recommendations` (dicts con
  `type`, `target`, `recurrence`, `acted`).
- Los subreddits se comparan siempre en minusculas.

Reglas A5/A6/A7 (nuevas, basadas en meta_recommendations heurísticos):
- A5 add_query: query_suggestion con recurrence >= 2 y acted = 0.
- A6 add_subreddit: subreddit_suggestion con recurrence >= 2 y acted = 0.
- A7 add_phrase: phrase_suggestion con recurrence >= 2 y acted = 0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Proposal:
    action: str  # "add_high_signal" | "remove_subreddit" | "demote_high_signal" | "remove_query"
    #            # "add_query" | "add_subreddit" | "add_phrase" (A5/A6/A7)
    target: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "target": self.target, "reason": self.reason}


# ── Helpers ───────────────────────────────────────────────────────────────


def _aggregate_subreddit_stats(runs: list[dict]) -> dict[str, dict[str, int]]:
    """Suma posts/with_problem/payment por subreddit a traves de los runs dados."""
    agg: dict[str, dict[str, int]] = {}
    for run in runs:
        for entry in run.get("subreddit_signal", []):
            sub = entry["subreddit"].lower()
            stats = agg.setdefault(sub, {"posts": 0, "with_problem": 0, "payment": 0, "runs_seen": 0})
            stats["posts"] += entry.get("posts_analyzed", 0)
            stats["with_problem"] += entry.get("with_problem", 0)
            stats["payment"] += entry.get("with_payment_signal", 0)
            stats["runs_seen"] += 1
    return agg


def _count_consecutive_silent(runs: list[dict], subreddit: str) -> int:
    """Runs consecutivos (desde el mas reciente) donde `subreddit` es silent."""
    sub_lower = subreddit.lower()
    count = 0
    for run in reversed(runs):
        silent_lower = {s.lower() for s in run.get("silent_subreddits", [])}
        if sub_lower in silent_lower:
            count += 1
        else:
            break
    return count


def _count_consecutive_empty_query(runs: list[dict], query: str) -> int:
    """Runs consecutivos (desde el mas reciente) donde `query` aparece en empty_queries."""
    count = 0
    for run in reversed(runs):
        if query in run.get("empty_queries", []):
            count += 1
        else:
            break
    return count


# ── Regla 1: promover a HIGH_SIGNAL ───────────────────────────────────────


def propose_promote_to_high_signal(
    runs: list[dict],
    current_high_signal: set[str],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla 1.

    Propone añadir subreddit a HIGH_SIGNAL si acumulado en los runs dados:
    - posts >= 3 AND hit_rate >= 75% AND payment_signals >= 1, O
    - payment_signals >= 2 (hit rate libre).
    """
    proposals: list[Proposal] = []
    current_lower = {s.lower() for s in current_high_signal}
    agg = _aggregate_subreddit_stats(runs)

    for sub, stats in agg.items():
        if sub in current_lower:
            continue
        posts = stats["posts"]
        if posts < 1:
            continue
        hit_rate = stats["with_problem"] / posts
        payment = stats["payment"]

        matches_main = posts >= 3 and hit_rate >= 0.75 and payment >= 1
        matches_payment = payment >= 2

        if matches_main or matches_payment:
            proposals.append(
                Proposal(
                    action="add_high_signal",
                    target=sub,
                    reason=(
                        f"{posts} posts acumulados, {stats['with_problem']} con problema "
                        f"({hit_rate:.0%} hit), {payment} payment signals en {stats['runs_seen']} runs."
                    ),
                )
            )
    return proposals


# ── Regla 2: quitar de SUBREDDITS ─────────────────────────────────────────


def propose_remove_from_subreddits(
    runs: list[dict],
    meta_recommendations: list[dict],
    current_subreddits: list[str],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla 2.

    Propone quitar subreddit si:
    - existe `meta_recommendation` con type='remove_subreddit' y recurrence >= 3, O
    - el subreddit aparece (no silent) con 0 hit en los ultimos 2 runs
      consecutivos Y la muestra total acumulada es >= 5 posts.
    """
    proposals: list[Proposal] = []
    current_lower = {s.lower() for s in current_subreddits}
    seen_targets: set[str] = set()

    # 2a — recurrence en meta_recommendations
    for rec in meta_recommendations:
        if rec.get("type") != "remove_subreddit":
            continue
        if rec.get("recurrence", 0) < 3:
            continue
        target = (rec.get("target") or "").lower()
        if not target or target in seen_targets or target not in current_lower:
            continue
        seen_targets.add(target)
        proposals.append(
            Proposal(
                action="remove_subreddit",
                target=target,
                reason=f"Recomendacion del meta con recurrence={rec['recurrence']}.",
            )
        )

    # 2b — 0% hit en 2 runs consecutivos con muestra acumulada >= 5
    if len(runs) >= 2:
        last_two = runs[-2:]
        for sub in current_subreddits:
            sub_lower = sub.lower()
            if sub_lower in seen_targets:
                continue
            runs_present = 0
            total_posts = 0
            total_problem = 0
            for run in last_two:
                for entry in run.get("subreddit_signal", []):
                    if entry["subreddit"].lower() == sub_lower:
                        runs_present += 1
                        total_posts += entry.get("posts_analyzed", 0)
                        total_problem += entry.get("with_problem", 0)
                        break
            if runs_present < 2 or total_posts < 5 or total_problem > 0:
                continue
            seen_targets.add(sub_lower)
            proposals.append(
                Proposal(
                    action="remove_subreddit",
                    target=sub_lower,
                    reason=f"0% hit en 2 runs consecutivos ({total_posts} posts acumulados).",
                )
            )

    return proposals


# ── Regla 3: degradar de HIGH_SIGNAL ──────────────────────────────────────


def propose_demote_from_high_signal(
    runs: list[dict],
    current_high_signal: set[str],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla 3.

    Propone degradar subreddit de HIGH_SIGNAL si:
    - >= 5 runs consecutivos silent, O
    - hit rate < 25% en los ultimos 3 runs con muestra acumulada >= 10 posts.
    """
    proposals: list[Proposal] = []
    current_lower = {s.lower() for s in current_high_signal}
    seen: set[str] = set()

    # 3a — silent consecutivos
    for sub in current_lower:
        if _count_consecutive_silent(runs, sub) >= 5:
            seen.add(sub)
            proposals.append(
                Proposal(
                    action="demote_high_signal",
                    target=sub,
                    reason="5+ runs silent consecutivos.",
                )
            )

    # 3b — hit rate bajo sostenido
    if len(runs) >= 3:
        last_three = runs[-3:]
        for sub in current_lower:
            if sub in seen:
                continue
            runs_present = 0
            total_posts = 0
            total_problem = 0
            for run in last_three:
                for entry in run.get("subreddit_signal", []):
                    if entry["subreddit"].lower() == sub:
                        runs_present += 1
                        total_posts += entry.get("posts_analyzed", 0)
                        total_problem += entry.get("with_problem", 0)
                        break
            if runs_present < 3 or total_posts < 10:
                continue
            hit_rate = total_problem / total_posts
            if hit_rate < 0.25:
                seen.add(sub)
                proposals.append(
                    Proposal(
                        action="demote_high_signal",
                        target=sub,
                        reason=(
                            f"Hit rate {hit_rate:.0%} en 3 runs "
                            f"({total_posts} posts acumulados, {total_problem} con problema)."
                        ),
                    )
                )

    return proposals


# ── Regla 4: quitar query ─────────────────────────────────────────────────


def propose_remove_queries(
    runs: list[dict],
    current_queries: list[str],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla 4.

    Propone quitar query si aparece en `empty_queries` durante >= 3 runs
    consecutivos (mas recientes).
    """
    if len(runs) < 3:
        return []
    proposals: list[Proposal] = []
    for query in current_queries:
        if _count_consecutive_empty_query(runs, query) >= 3:
            proposals.append(
                Proposal(
                    action="remove_query",
                    target=query,
                    reason="0 resultados en 3 runs consecutivos.",
                )
            )
    return proposals


# ── Regla 5: añadir query sugerida por LLM ────────────────────────────────


def propose_add_queries_from_llm(
    meta_recommendations: list[dict],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla A5.

    Propone add_query para cada meta_recommendations con:
    - type == 'query_suggestion'
    - recurrence >= 2
    - acted == 0
    """
    proposals: list[Proposal] = []
    seen: set[str] = set()
    for rec in meta_recommendations:
        if rec.get("type") != "query_suggestion":
            continue
        if int(rec.get("recurrence", 0)) < 2:
            continue
        if int(rec.get("acted", 0)) != 0:
            continue
        target = rec.get("target", "")
        if not target or target in seen:
            continue
        seen.add(target)
        proposals.append(
            Proposal(
                action="add_query",
                target=target,
                reason=f"LLM sugirió esta query en {rec['recurrence']} runs consecutivos.",
            )
        )
    return proposals


# ── Regla 6: añadir subreddit sugerido por LLM ────────────────────────────


def propose_add_subreddits_from_llm(
    meta_recommendations: list[dict],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla A6.

    Propone add_subreddit para cada meta_recommendations con:
    - type == 'subreddit_suggestion'
    - recurrence >= 2
    - acted == 0
    """
    proposals: list[Proposal] = []
    seen: set[str] = set()
    for rec in meta_recommendations:
        if rec.get("type") != "subreddit_suggestion":
            continue
        if int(rec.get("recurrence", 0)) < 2:
            continue
        if int(rec.get("acted", 0)) != 0:
            continue
        target = rec.get("target", "")
        if not target or target in seen:
            continue
        seen.add(target)
        proposals.append(
            Proposal(
                action="add_subreddit",
                target=target,
                reason=f"LLM sugirió este subreddit en {rec['recurrence']} runs consecutivos.",
            )
        )
    return proposals


# ── Regla 7: añadir frase de dolor sugerida por LLM ──────────────────────


def propose_add_phrases_from_llm(
    meta_recommendations: list[dict],
) -> list[Proposal]:
    """README "Tuning del pipeline" — regla A7.

    Propone add_phrase para cada meta_recommendations con:
    - type == 'phrase_suggestion'
    - recurrence >= 2
    - acted == 0
    """
    proposals: list[Proposal] = []
    seen: set[str] = set()
    for rec in meta_recommendations:
        if rec.get("type") != "phrase_suggestion":
            continue
        if int(rec.get("recurrence", 0)) < 2:
            continue
        if int(rec.get("acted", 0)) != 0:
            continue
        target = rec.get("target", "")
        if not target or target in seen:
            continue
        seen.add(target)
        proposals.append(
            Proposal(
                action="add_phrase",
                target=target,
                reason=f"LLM sugirió esta frase en {rec['recurrence']} runs consecutivos.",
            )
        )
    return proposals


# ── Orquestador ───────────────────────────────────────────────────────────


def propose_all_changes(
    runs: list[dict],
    meta_recommendations: list[dict],
    current_high_signal: set[str],
    current_subreddits: list[str],
    current_queries: list[str],
) -> list[Proposal]:
    """Aplica las 7 reglas y devuelve la lista consolidada.

    Orden: conservador primero (A1-A4: remove/demote), luego expansión
    (A1 add_high_signal), luego sugerencias LLM (A5-A7: add_query/subreddit/phrase).
    Permite al consumidor cortar el stream si aplica un cap de cambios por iteracion.
    """
    proposals: list[Proposal] = []
    proposals.extend(propose_remove_queries(runs, current_queries))
    proposals.extend(propose_demote_from_high_signal(runs, current_high_signal))
    proposals.extend(propose_remove_from_subreddits(runs, meta_recommendations, current_subreddits))
    proposals.extend(propose_promote_to_high_signal(runs, current_high_signal))
    proposals.extend(propose_add_queries_from_llm(meta_recommendations))
    proposals.extend(propose_add_subreddits_from_llm(meta_recommendations))
    proposals.extend(propose_add_phrases_from_llm(meta_recommendations))
    return proposals
