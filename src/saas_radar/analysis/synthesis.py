"""Síntesis de oportunidades con pre-clustering por subreddit y validación post-LLM."""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)


def build_synthesis_prompt(extractions: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Construye el prompt de síntesis con pre-clustering por subreddit y RULES 1-7.

    Pre-clusteriza las extracciones agrupando por subreddit (ordenado por count desc)
    para que el LLM vea posts de la misma industria juntos. Numeración global [1..N].

    Returns:
        (prompt_str, ordered_extractions) donde ordered_extractions está alineado
        con los índices [1..N] usados en el prompt.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ex in extractions:
        if not ex.get("has_problem"):
            continue
        groups[ex.get("_subreddit", "?")].append(ex)

    # Subreddits con más extracciones primero — lección §1.4: el LLM ve posts
    # de la misma industria consecutivos, lo que facilita detectar clusters reales.
    ordered_subs = sorted(groups.keys(), key=lambda s: -len(groups[s]))
    ordered_extractions = [ex for s in ordered_subs for ex in groups[s]]

    items_text = ""
    current_sub = None
    for i, ex in enumerate(ordered_extractions, 1):
        sub = ex.get("_subreddit", "?")
        if sub != current_sub:
            items_text += f"\n\n### CLUSTER: r/{sub} ({len(groups[sub])} items) ###"
            current_sub = sub
        pay = "YES" if ex.get("payment_signal") else "no"
        items_text += f"""
[{i}] r/{ex["_subreddit"]} | score:{ex["_score"]} | pay_signal:{pay}
  who: {ex.get("who_has_it", "")}
  problem: {ex.get("problem_description", "")}
  workflow: {ex.get("workflow_context", "")}
  workaround: {ex.get("current_workaround", "")}
  quote: "{ex.get("key_quote", "")}"
  payment_quote: "{ex.get("payment_quote", "")}"
  competitors: {ex.get("competitor_mentions", [])}
---"""

    n_industries = len(groups)

    prompt_body = f"""You are a brutally selective SaaS opportunity analyst. \
A solo full-stack developer needs you to find their next product.

Below are SPECIFIC problems extracted one by one from Reddit posts. \
Each item is a real situation described by a real person.

════════════════════════════════════════════════
FILTERING RULES — apply before generating anything
════════════════════════════════════════════════

RULE 1 — MINIMUM EVIDENCE THRESHOLD (hardest filter):
An opportunity is ONLY valid if at least 2 different items describe the SAME \
specific workflow problem. "Same workflow" means the SAME concrete action \
(e.g. "tracking invoices in a spreadsheet"), NOT just the same topic area \
(e.g. "finance" or "tracking"). Two items about different industries doing \
completely unrelated tasks are NOT evidence of the same workflow, even if \
they use similar words. But two items from different industries doing the \
SAME action (e.g. both tracking prices in spreadsheets) DO count.
TEST: for each pair of evidence items, ask "would the same tool solve both \
problems?". If yes, they count as the same workflow.
HARD CONSTRAINT on output: `evidence_items` MUST contain at least 2 distinct \
item indices, and `evidence_quotes` MUST contain at least 2 quotes (one per \
item, prefixed with `[item N]`). Each quote must describe the SAME specific \
action or pain. If you cannot meet this, drop the opportunity entirely — \
do NOT submit an opportunity with fewer than 2 evidence items. \
3+ evidence items is strongly preferred and should boost priority_score.

RULE 2 — THE WORKAROUND TEST:
Every opportunity SHOULD have a concrete answer to "what do people use today?". \
Acceptable workarounds: Excel, Google Sheets, copy-paste, email, paper, \
a specific expensive tool, a manual process with steps.
EXCEPTION: if the post describes a QUANTIFIABLE pain (e.g. "2+ hours/day on \
documentation", "10-14 hours/week checking prices") but no explicit workaround, \
the item is still valid — the time cost IS the evidence of urgency.
REJECT only when there is NEITHER a concrete workaround NOR a quantifiable \
time/money cost. Vague complaints with no specifics = no urgency = no SaaS.

RULE 3 — SATURATED MARKET FILTER (reject GENERIC solutions only):
The following categories are BANNED when they target a broad audience. \
However, if the solution targets a SPECIFIC named profession or narrow \
workflow, it IS allowed. The test: would you name the product "[Category] \
for [Job Title]"? If [Job Title] is specific enough (e.g. "solo MSP owners", \
"veterinary clinic managers", "construction bookkeepers"), it passes.
  × CRM / client management (UNLESS for a specific profession)
  × Project management / task management (UNLESS for a specific profession)
  × Note-taking / knowledge base (UNLESS for a specific profession)
  × General analytics / dashboards
  × Email marketing / newsletters
  × Social media scheduling
  × "AI writing tool" / "AI assistant" / chatbot
  × General invoicing / billing (UNLESS for a specific profession — e.g. \
"AR tracking for solo MSP owners", "invoicing for veterinary clinics", \
"WIP reporting for construction bookkeepers")
  × General time tracking (UNLESS for a specific profession)
  × Habit trackers / productivity apps
  × Mental health / wellness apps
  × Landing page builders
IMPORTANT: Do NOT auto-reject an opportunity just because it touches \
invoicing, time tracking, or CRM. Check if the niche is specific first. \
"Invoice tracking for solo bookkeepers serving construction clients" is \
ALLOWED. "Better invoicing tool" is BANNED.

RULE 4 — SPECIFICITY TEST:
The niche must pass this test: can you name the exact job title or situation \
of the person who has this problem?
  FAIL: "freelancers", "small businesses", "agencies", "developers"
  PASS: "solo Zapier consultants who build zaps for clients and need to \
hand off credentials securely", "Notion template sellers who need to track \
which customers downloaded which version"

RULE 5 — WILLINGNESS TO PAY:
Prioritize problems where the data shows: an expensive existing tool, \
a manual process costing significant time (>2h/week), or an explicit \
"I would pay for this". Deprioritize problems where people are annoyed \
but not spending money or time.

RULE 6 — BUILDABILITY:
Core value must work without: ML training pipelines, large datasets to be \
useful, network effects, hardware, or regulatory approval. \
If it requires a large team or specialized domain knowledge, mark \
solo_buildable as false.

RULE 7 — INDUSTRY DIVERSITY (soft preference — NEVER sacrifice coherence):
The input below contains {n_industries} different subreddit clusters. \
When choosing between opportunities of SIMILAR quality, prefer the one \
from a less-represented industry. HOWEVER — this is a soft preference, \
NOT a quota. Coherence (RULE 1) always wins over diversity.
HARD ANTI-PATTERN (automatic rejection): do NOT pair a strong evidence \
item with a weak, loosely-related item just to reach 2 evidences or to \
hit a different industry. If the two items do not describe the SAME \
concrete action on the SAME kind of object, the cluster is invalid even \
if both posts come from "bookkeeping" or "e-commerce". Examples of \
INVALID clusters:
  × "generate WIP reports" + "don't know how to use QuickBooks" \
(different problems: reporting vs. training)
  × "track inventory in spreadsheets" + "duct-taping tools together" \
(different problems: inventory vs. generic workflow)
If only ONE industry yields RULE-1-compliant evidence, that's fine — \
return fewer opportunities rather than pad with fake clusters.

════════════════════════════════════════════════
EXTRACTED PROBLEMS FROM REDDIT
════════════════════════════════════════════════
{items_text}

════════════════════════════════════════════════

Before responding, verify each opportunity satisfies RULE 1: \
`len(evidence_items) >= 2` and `len(evidence_quotes) >= 2`. \
Drop any opportunity that fails this check. Opportunities with 3+ items \
should get a higher priority_score.

Respond ONLY with this exact JSON, no extra text, no markdown fences:

{{
  "opportunities": [
    {{
      "id": 1,
      "product_name": "Specific name describing the exact workflow it solves",
      "niche": "exact job title or situation — who specifically, not a category",
      "core_problem": "1 sentence using the exact words people used in the posts",
      "why_gap_exists": "why do 50+ tools NOT solve this already? Be specific.",
      "evidence_items": [2, 7],
      "evidence_quotes": [
        "[item 2] direct quote",
        "[item 7] direct quote"
      ],
      "concrete_workaround": "what they use TODAY — tool name or exact manual steps",
      "workaround_cost": "time or money wasted per week/month with current workaround",
      "mvp_scope": "3 features max. Each must be 1 concrete sentence. No vague verbs.",
      "monetization": "$X/mo — explain who pays and why they would switch",
      "estimated_price": "$X-Y/month",
      "competitor_gap": "what Zapier/Notion/Airtable/existing tools specifically fail to do",
      "mentioned_competitors": ["tool1", "tool2"],
      "payment_signal": "high | medium | low",
      "payment_evidence": "exact quote or described behavior showing willingness to pay",
      "solo_buildable": true,
      "mvp_weeks": 8,
      "priority_score": 7,
      "priority_reason": "Score X/10 because: [N items of evidence] + [payment signal] + [workaround specificity] + [niche width]"
    }}
  ],
  "top_3_recommended": [1, 2, 3],
  "disqualified_ideas": [
    {{
      "idea": "brief description of what was considered",
      "rule_violated": "RULE N — specific reason"
    }}
  ]
}}"""
    return prompt_body, ordered_extractions


# ── Coherencia léxica ────────────────────────────────────────────────────────

# Stopwords funcionales + raíces de dominio que aparecen en CUALQUIER queja
# sobre spreadsheets/manualidad. Si dos extracciones solo comparten estas
# raíces, el cluster es genérico y debe rechazarse. Las entradas son tanto
# palabras completas como raíces de 4 chars (captura familias enteras).
_COHERENCE_STOP = {
    # Funcionales / pronombres / conectores
    "about",
    "after",
    "because",
    "before",
    "being",
    "could",
    "didn",
    "doing",
    "doesn",
    "every",
    "their",
    "there",
    "these",
    "thing",
    "think",
    "those",
    "using",
    "would",
    "should",
    "really",
    "never",
    "other",
    "still",
    "where",
    "which",
    "while",
    "years",
    "people",
    "start",
    "don't",
    "can't",
    "want",
    "just",
    "like",
    "with",
    "that",
    "this",
    "from",
    "have",
    "into",
    "than",
    "them",
    "then",
    "they",
    "what",
    "when",
    "will",
    "your",
    "been",
    "were",
    "item",
    "going",
    "getting",
    "making",
    "taking",
    "looking",
    "trying",
    # Raíces de dominio — ruido para el filtro de coherencia porque aparecen
    # en cualquier queja de pain en SaaS. Listadas como raíces de 4 chars;
    # _coherence_words filtra por w[:4] in _COHERENCE_STOP para capturar
    # familias enteras (track/tracking/tracked → 'trac', etc.).
    "manu",  # manual, manually
    "trac",  # track, tracking, tracked, tracker
    "spre",  # spreadsheet, spread
    "exce",  # excel, excellent
    "shee",  # sheet, sheets
    "info",  # info, information
    "hour",  # hour, hours, hourly
    "week",  # week, weeks, weekly
    "mont",  # month, months, monthly
    "dail",  # daily
    "time",  # time, times, timely
    "much",
    "many",
    "lots",
    "tool",  # tool, tools, toolkit
    "soft",  # software
    "syst",  # system, systems
    "work",  # work, workflow, working
    "need",  # need, needs, needed
    "spen",  # spend, spending, spent
    "stuf",  # stuff
    "thin",  # thing, things
    "ever",  # every, everything
    "anyt",  # anything
    "some",  # some, something
    "peop",  # people
    "user",  # user, users
    # "cust" y "clie" eliminados: "client reporting" y "customer onboarding"
    # son señal de nicho que aporta coherencia entre extracciones.
    "busi",  # business, businesses
    "smal",  # small
    "curr",  # currently
    "real",  # really
    "actu",  # actually, active
    "even",  # even, every
    "look",  # looking
    "also",  # also
    "make",  # make, makes, making
    "take",  # take, taking
    "give",  # give, giving
    "find",  # find, finding
    "data",  # data (relleno genérico)
}

# Siglas y nombres de herramientas cortos (<4 chars) que el regex [a-z]{4,}
# descarta pero son señal fuerte de coherencia. Se añaden al set de raíces
# si aparecen como palabra completa en el texto.
_SHORT_TOOL_NAMES = {"qbo", "crm", "erp", "sap", "csv", "api", "etl", "pos", "ar", "ap"}


def _coherence_words(quote: str) -> set[str]:
    """Extrae raíces de 4 chars de una quote para comparar coherencia entre extracciones.

    Stemming a 4 chars: invoice/invoices → 'invo', price/prices/pricing → 'pric'.
    Filtra contra _COHERENCE_STOP por dos vías:
    1. palabra completa (p.ej. "manual" se filtra antes del stemming)
    2. raíz de 4 chars (p.ej. "manu" captura manuscript, manually, etc.)

    Elimina el prefijo [item N] antes de procesar para no contaminar con "item".
    Añade siglas/herramientas cortas (QBO, CRM, CSV…) encontradas como palabras completas.
    """
    q = re.sub(r"^\[item\s+\d+\]\s*", "", str(quote).lower())
    roots = {w[:4] for w in re.findall(r"[a-z]{4,}", q) if w not in _COHERENCE_STOP and w[:4] not in _COHERENCE_STOP}
    words = set(re.findall(r"[a-z]+", q))
    roots |= words & _SHORT_TOOL_NAMES
    return roots


def _quotes_are_coherent(quotes: list[str], min_shared: int = 2) -> bool:
    """True si al menos min_shared raíces (4 chars) aparecen en >50% de las quotes.

    Con min_shared=2 evitamos pares que solo comparten una palabra-relleno residual.
    Clusters reales suelen compartir 3+ raíces específicas (verbo + objeto + dominio).
    Una sola quote siempre es coherente (no hay par que comparar).
    """
    if len(quotes) < 2:
        return True
    word_sets = [_coherence_words(q) for q in quotes]
    all_words: Counter[str] = Counter()
    for ws in word_sets:
        for w in ws:
            all_words[w] += 1
    threshold = len(quotes) / 2
    majority_words = {w for w, c in all_words.items() if c > threshold}
    return len(majority_words) >= min_shared


# ── Validación post-síntesis ─────────────────────────────────────────────────


def _validate_synthesis(
    results: dict[str, Any],
    ordered_extractions: list[dict[str, Any]],
    min_evidence: int = 2,
) -> dict[str, Any]:
    """Valida en código las oportunidades devueltas por el LLM.

    Aplica dos checks:
    1. RULE 1 cantidad: evidence_items >= min_evidence Y evidence_quotes >= min_evidence.
    2. RULE 1 coherencia: los problem_description REALES de los evidence_items
       referenciados deben compartir vocabulario sustantivo. Validamos contra el
       texto original de las extracciones (lección §1.5: no contra evidence_quotes
       del LLM, que puede seleccionar mal o falsificar).

    Reescribe top_3_recommended con solo ids supervivientes.
    Acumula en disqualified_ideas sin sobrescribir las entradas que el LLM ya incluyó.
    """
    if not isinstance(results, dict):
        return results

    # Mapa idx (1-based) → texto enriquecido para comparar coherencia.
    # Juntamos problem_description + workflow_context + current_workaround porque
    # los tres aportan vocabulario específico del workflow sin depender del key_quote.
    idx_to_text: dict[int, str] = {}
    if ordered_extractions:
        for i, ex in enumerate(ordered_extractions, 1):
            idx_to_text[i] = " ".join(
                [
                    str(ex.get("problem_description") or ""),
                    str(ex.get("workflow_context") or ""),
                    str(ex.get("current_workaround") or ""),
                ]
            )

    opps = results.get("opportunities") or []
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for opp in opps:
        ev_items = opp.get("evidence_items") or []
        ev_quotes = opp.get("evidence_quotes") or []
        name = opp.get("product_name", "(sin nombre)")

        # Check 1: cantidad mínima
        if len(ev_items) < min_evidence or len(ev_quotes) < min_evidence:
            dropped.append(
                {
                    "idea": name,
                    "rule_violated": (
                        f"RULE 1 cantidad — evidence_items={len(ev_items)}, "
                        f"evidence_quotes={len(ev_quotes)} (mínimo {min_evidence})"
                    ),
                }
            )
            continue

        # Check 2: coherencia sobre problem_description REAL de los items.
        # Si no hay ordered_extractions (caller legacy), fallback a evidence_quotes del LLM.
        if idx_to_text:
            # Filtra ids no numéricos que el LLM emite a veces
            pairs = [(i, idx_to_text.get(int(i), "")) for i in ev_items if str(i).lstrip("-").isdigit()]
            texts = [t for _, t in pairs]
            coherent = _quotes_are_coherent(texts)
            if not coherent:
                logger.debug(f"  [coherencia] rechazada '{name}':")
                for i, txt in pairs:
                    roots = _coherence_words(txt)
                    logger.debug(f"     [item {i}] {txt[:100]}")
                    logger.debug(f"       raices: {sorted(roots)[:10]}")
                dropped.append(
                    {
                        "idea": name,
                        "rule_violated": (
                            "RULE 1 coherencia — los problem_description de los "
                            "evidence_items no comparten vocabulario de workflow"
                        ),
                    }
                )
                continue
        else:
            if not _quotes_are_coherent(ev_quotes):
                logger.debug(f"  [coherencia] rechazada '{name}':")
                for q in ev_quotes:
                    roots = _coherence_words(q)
                    logger.debug(f"     {str(q)[:100]}")
                    logger.debug(f"       raices: {sorted(roots)[:10]}")
                dropped.append(
                    {
                        "idea": name,
                        "rule_violated": (
                            "RULE 1 coherencia — las quotes de evidencia no comparten vocabulario de workflow"
                        ),
                    }
                )
                continue

        kept.append(opp)

    if dropped:
        for d in dropped:
            logger.info(f"  [validacion] descartada: {d['idea']} => {d['rule_violated']}")

    results["opportunities"] = kept
    # Reconstruir top_3 con solo ids supervivientes
    kept_ids = {opp.get("id") for opp in kept}
    top3 = [i for i in (results.get("top_3_recommended") or []) if i in kept_ids]
    results["top_3_recommended"] = top3
    # Acumular disqualified_ideas — no sobrescribir lo que el LLM ya descartó
    results["disqualified_ideas"] = (results.get("disqualified_ideas") or []) + dropped
    return results
