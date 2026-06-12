"""Extracción de problemas de posts de Reddit usando LLM."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text as sql_text

from saas_radar import config
from saas_radar.analysis.llm_clients import call_llm
from saas_radar.config import TEXT_SNIPPET_LEN
from saas_radar.storage.db import engine

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

EXTRACTION_BATCH_SIZE = 5
DEEP_EXTRACTION_THRESHOLD = 30
CIRCUIT_BREAKER_THRESHOLD = 3

# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACTION_BATCH_PROMPT = """\
You are a SaaS opportunity analyst. A solo full-stack developer needs you to \
extract the specific problem described in EACH of the Reddit posts below.

POSTS:
{posts_block}

For EACH post, extract the EXACT problem this person has. Do not generalize. \
Do not invent. Only describe what is explicitly stated or strongly implied.

Rules (apply to every post independently):
- Use the same specific words and context the person used.
- If a post is vague or does not describe a real workflow problem, set \
"has_problem" to false for that post.
- "who_has_it" must name the specific type of person (not a generic category).
- "current_workaround" must come from the text, not be invented.
- "payment_signal" is true only if the post mentions spending money, an \
expensive tool, or explicit willingness to pay.
- If a post is analyzing/summarizing OTHER people's problems, set \
"has_problem" to false.

Respond ONLY with this exact JSON, no extra text, no markdown fences. The \
"results" array MUST have exactly {n} items in the same order as the posts:

{{
  "results": [
    {{
      "post_index": 1,
      "has_problem": true,
      "who_has_it": "",
      "problem_description": "",
      "workflow_context": "",
      "current_workaround": "",
      "payment_signal": false,
      "payment_quote": "",
      "competitor_mentions": [],
      "key_quote": ""
    }}
  ]
}}"""

EXTRACTION_PROMPT = """\
You are a SaaS opportunity analyst. A solo full-stack developer needs you to \
extract the specific problem described in ONE Reddit post.

POST:
subreddit: r/{subreddit}
title: {title}
text: {text}
{comments_section}

Your task: extract the EXACT problem this person has. Do not generalize. \
Do not invent. Only describe what is explicitly stated or strongly implied.

Rules:
- Use the same specific words and context the person used (their industry, \
their tool, their workflow).
- If the post is too vague or does not describe a real workflow problem, \
set "has_problem" to false.
- "who_has_it" must name the specific type of person (e.g. "solo bookkeeper \
who invoices via email", not "small businesses").
- "current_workaround" must come from the text, not be invented.
- "payment_signal" is true only if the post mentions spending money, \
an expensive tool, or explicit willingness to pay.
- CRITICAL: If this post is analyzing, summarizing, or giving advice ABOUT \
other people's problems (e.g. "here are the best niches", "I analyzed X posts", \
"the biggest pain points are..."), set "has_problem" to false. The problem \
must belong to the person writing the post, not to people they are describing.

Respond ONLY with this JSON, no extra text:

{{
  "has_problem": true,
  "who_has_it": "exact description of the person who has this problem",
  "problem_description": "1-2 sentences using the post own words and context",
  "workflow_context": "what specific workflow or situation triggers the pain",
  "current_workaround": "what they use today, from the post text",
  "payment_signal": false,
  "payment_quote": "direct quote showing payment intent, or empty string",
  "competitor_mentions": ["tool names mentioned"],
  "key_quote": "the single most useful quote from the post for a builder"
}}"""

DEEP_EXTRACTION_PROMPT = """\
You are a SaaS opportunity analyst doing a DEEP analysis of ONE Reddit post. \
A solo full-stack developer needs you to extract every actionable signal.

POST:
subreddit: r/{subreddit}
title: {title}
text: {text}
{comments_section}

Your task: do a thorough analysis of this post AND its comments. Extract:
1. The EXACT problem described (using the poster's own words).
2. Signals from comments: do others confirm the same pain? Do they mention \
tools, prices, or workarounds the original poster didn't?
3. Market signals: how many people seem affected? Is this a niche or widespread?

Rules:
- Use the same specific words and context the person used.
- If the post is too vague or does not describe a real workflow problem, \
set "has_problem" to false.
- "who_has_it" must name the specific type of person with their context.
- "current_workaround" must come from the post or comments, not invented.
- "payment_signal" is true if the post OR comments mention spending money, \
an expensive tool, or willingness to pay.
- "comment_signals" should capture additional pain confirmations, tool \
mentions, or price references found in the comments.
- "estimated_frequency" should estimate how often this pain occurs based \
on context clues (daily, weekly, monthly, per-project, etc.).
- "tam_clues" should note any hints about market size (number of people \
affected, industry size, etc.).
- CRITICAL: If this post is analyzing or giving advice ABOUT other people's \
problems, set "has_problem" to false.

Respond ONLY with this JSON, no extra text:

{{
  "has_problem": true,
  "who_has_it": "exact description with industry and role context",
  "problem_description": "2-3 sentences using the post's own words and context",
  "workflow_context": "the specific workflow, trigger, and steps involved",
  "current_workaround": "what they use today — from post or comments",
  "payment_signal": false,
  "payment_quote": "direct quote showing payment intent, or empty string",
  "competitor_mentions": ["tool names from post AND comments"],
  "key_quote": "the single most useful quote for a builder",
  "comment_signals": "summary of what comments add: confirmations, tools mentioned, prices, alternative workarounds",
  "estimated_frequency": "how often does this pain occur (daily/weekly/monthly/per-project)",
  "tam_clues": "any hints about how many people have this problem"
}}"""

# ── Listas de limpieza ────────────────────────────────────────────────────────

_NO_WORKAROUND_PHRASES = {
    "none",
    "none mentioned",
    "not mentioned",
    "no workaround",
    "no current workaround",
    "n/a",
    "na",
    "unknown",
    "not specified",
    "not clear",
    "unclear",
    "no specific workaround",
    "nothing",
    "they don't",
    "they just don't",
    "no tool",
    "no solution",
}

_WORKAROUND_KEYWORDS = [
    ("spreadsheet", "spreadsheets"),
    ("excel", "Excel"),
    ("google sheets", "Google Sheets"),
    ("google doc", "Google Docs"),
    ("copy-paste", "manual copy-paste"),
    ("copy paste", "manual copy-paste"),
    ("pen and paper", "pen and paper"),
    ("paper", "paper-based process"),
    ("manual", "manual process"),
    ("by hand", "manual process"),
    ("whiteboard", "whiteboard"),
    ("email chain", "email threads"),
    ("email thread", "email threads"),
    ("quickbooks", "QuickBooks"),
    ("xero", "Xero"),
    ("airtable", "Airtable"),
    ("notion", "Notion"),
    ("trello", "Trello"),
    ("asana", "Asana"),
    ("monday", "Monday.com"),
    ("clickup", "ClickUp"),
    ("zapier", "Zapier"),
    ("make.com", "Make.com"),
    ("slack", "Slack"),
    ("hubspot", "HubSpot"),
    ("salesforce", "Salesforce"),
    ("zoho", "Zoho"),
    ("shopify", "Shopify"),
    ("stripe", "Stripe"),
    ("calendly", "Calendly"),
    ("zoom", "Zoom"),
]

_NON_SAAS_PAIN_SIGNALS = [
    "physical pain",
    "back pain",
    "chronic pain",
    "lonely",
    "loneliness",
    "isolation",
    "depress",
    "anxiety",
    "mental health",
    "burnout",
    "burn out",
    "stressed out",
    "overwhelm",
    "life balance",
    "work-life",
    "work life",
    "motivation",
    "procrastina",
    "habit",
    "sleep",
    "insomnia",
    "exercise",
    "diet ",
    "weight loss",
    "relationship",
    "dating",
    "marriage",
    "parenting",
]

# ── Funciones de extracción ───────────────────────────────────────────────────


def extract_problem_from_post(row: pd.Series, comments: list[str], provider: str = "claude") -> dict[str, Any]:
    """Extrae el problema de un post individual usando el LLM."""
    title = str(row.get("title", "")).strip()
    text = str(row.get("text", "")).strip()[:TEXT_SNIPPET_LEN]
    sub = row.get("subreddit", "")

    comments_section = ""
    if comments:
        joined = "\n".join(f"  - {c[:200]}" for c in comments)
        comments_section = f"\ntop comments:\n{joined}"

    prompt = EXTRACTION_PROMPT.format(
        subreddit=sub,
        title=title,
        text=text,
        comments_section=comments_section,
    )

    result = call_llm(prompt, max_tokens=600, phase="extraction", provider=provider)
    if result is None:
        return {"has_problem": False, "_title": title, "_subreddit": sub}

    result["_post_id"] = row.get("id", "")
    result["_subreddit"] = sub
    result["_score"] = int(row.get("score", 0))
    result["_num_comments"] = int(row.get("num_comments", 0))
    result["_url"] = row.get("url", "")
    result["_title"] = title
    return result


def _fetch_comments_for_post(post_id: str, limit: int = 15) -> list[str]:
    """Carga los top comentarios de un post desde la BD."""
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                "SELECT text FROM reddit_comments "
                "WHERE post_id = :pid AND length(text) > 50 "
                "ORDER BY score DESC LIMIT :lim"
            ),
            {"pid": post_id, "lim": limit},
        ).fetchall()
    return [row[0] for row in rows]


def extract_problem_deep(row: pd.Series, provider: str = "claude") -> dict[str, Any]:
    """Extracción profunda: texto completo + comentarios desde BD + prompt enriquecido."""
    title = str(row.get("title", "")).strip()
    text = str(row.get("text", "")).strip()
    sub = row.get("subreddit", "")
    post_id = row.get("id", "")

    comments = _fetch_comments_for_post(post_id)
    comments_section = ""
    if comments:
        joined = "\n".join(f"  - {c[:400]}" for c in comments)
        comments_section = f"\ntop comments ({len(comments)}):\n{joined}"

    prompt = DEEP_EXTRACTION_PROMPT.format(
        subreddit=sub,
        title=title,
        text=text,
        comments_section=comments_section,
    )

    result = call_llm(prompt, max_tokens=800, phase="extraction", provider=provider)
    if result is None:
        return {"has_problem": False, "_title": title, "_subreddit": sub, "_error": True}

    result["_post_id"] = post_id
    result["_subreddit"] = sub
    result["_score"] = int(row.get("score", 0))
    result["_num_comments"] = int(row.get("num_comments", 0))
    result["_url"] = row.get("url", "")
    result["_title"] = title
    result["_deep"] = True
    return result


def extract_problems_batch(rows: list[pd.Series], provider: str = "claude") -> list[dict[str, Any]]:
    """Extrae problemas de N posts en una sola llamada al LLM."""
    posts_block_parts = []
    for i, row in enumerate(rows, 1):
        title = str(row.get("title", "")).strip()
        text = str(row.get("text", "")).strip()[:TEXT_SNIPPET_LEN]
        sub = row.get("subreddit", "")
        src = row.get("source", "")
        if src == "comment" and not title:
            title = "[reddit comment — not a top-level post]"
        posts_block_parts.append(f"[POST {i}]\n  subreddit: r/{sub}\n  title: {title}\n  text: {text}")

    posts_block = "\n\n".join(posts_block_parts)
    prompt = EXTRACTION_BATCH_PROMPT.format(posts_block=posts_block, n=len(rows))

    result = call_llm(prompt, max_tokens=220 * len(rows), phase="extraction", provider=provider)
    if not result or "results" not in result:
        # Logging defensivo (feature #23): truncar a 500 chars la repr del resultado
        # para que el debug post-mortem de un schema malformado del LLM no
        # quede ciego (ver progress/audit_gemini_fail.md). Diferenciar entre
        # None (API fallo) y dict sin clave 'results' (schema malformado).
        if result is None:
            logger.warning(
                "Batch fallo con provider=%s -- call_llm devolvió None (API fallo o schema malformado)",
                provider,
            )
        else:
            logger.warning(
                "Batch fallo con provider=%s -- result sin clave 'results'. repr[:500]=%s",
                provider,
                repr(result)[:500],
            )
        return [
            {
                "has_problem": False,
                "_error": True,
                "_title": str(r.get("title", "")),
                "_subreddit": r.get("subreddit", ""),
            }
            for r in rows
        ]

    items = result["results"]
    if len(items) != len(rows):
        logger.warning(
            "LLM devolvio %d resultados para %d posts — los faltantes se marcan has_problem=false",
            len(items),
            len(rows),
        )

    extractions = []
    for i, row in enumerate(rows):
        ex = items[i] if i < len(items) else {"has_problem": False}
        ex["_post_id"] = row.get("id", "")
        ex["_subreddit"] = row.get("subreddit", "")
        ex["_score"] = int(row.get("score", 0))
        ex["_num_comments"] = int(row.get("num_comments", 0))
        ex["_url"] = row.get("url", "")
        ex["_title"] = str(row.get("title", ""))
        extractions.append(ex)
    return extractions


# ── Circuit breaker ───────────────────────────────────────────────────────────


def _run_batches_with_circuit_breaker(
    posts: list[pd.Series],
    batch_size: int,
    provider: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Loop interno: ejecuta los batches con circuit breaker.

    Devuelve (resultados_acumulados, circuit_breaker_triggered).
    `circuit_breaker_triggered` es True si se abortó por superar
    CIRCUIT_BREAKER_THRESHOLD batches consecutivos con todos los items en
    `_error=True`. Esta separación permite al caller decidir si reintentar
    con un provider de respaldo (feature #23, EXTRACTION_PROVIDER_FALLBACK).
    """
    results: list[dict[str, Any]] = []
    consecutive_errors = 0
    triggered = False

    for start in range(0, len(posts), batch_size):
        batch = posts[start : start + batch_size]
        batch_results = extract_problems_batch(batch, provider=provider)
        results.extend(batch_results)

        if all(item.get("_error") for item in batch_results):
            consecutive_errors += 1
        else:
            consecutive_errors = 0

        if consecutive_errors >= CIRCUIT_BREAKER_THRESHOLD:
            logger.error(
                "Circuit breaker disparado tras %d batches consecutivos con error (provider=%s) — abortando loop",
                consecutive_errors,
                provider,
            )
            triggered = True
            break

    return results, triggered


def run_batch_extraction(
    posts: list[pd.Series],
    batch_size: int = EXTRACTION_BATCH_SIZE,
    provider: str = "claude",
) -> list[dict[str, Any]]:
    """Procesa posts en batches con circuit breaker tras errores consecutivos.

    Fallback automático (feature #23): si el circuit breaker dispara con un
    provider != EXTRACTION_PROVIDER_FALLBACK (y el fallback no está vacío),
    reintenta TODOS los batches desde 0 con el provider de respaldo UNA sola
    vez. El primer pase queda como log de auditoría; los resultados del
    segundo pase reemplazan a los del primero porque la unidad de retry es
    "todos los batches desde 0" (más simple y robusto que mantener un mapa
    parcial de batches procesados → recuperar solo huérfanos).
    """
    results, triggered = _run_batches_with_circuit_breaker(posts, batch_size, provider)

    if not triggered:
        return results

    fallback = (config.EXTRACTION_PROVIDER_FALLBACK or "").strip().lower()
    if not fallback or fallback == provider:
        # Sin fallback configurado o ya estamos en el provider de respaldo.
        return results

    logger.warning(
        "Fallback activado: provider=%s disparó circuit breaker. Reintentando los %d posts con provider=%s (UNA sola vez).",
        provider,
        len(posts),
        fallback,
    )
    fallback_results, fallback_triggered = _run_batches_with_circuit_breaker(
        posts, batch_size, fallback
    )

    if fallback_triggered:
        logger.error(
            "Fallback con provider=%s también disparó circuit breaker — abortando extracción",
            fallback,
        )
        # Aun así devolvemos lo que dio el fallback: puede haber resultados
        # parciales útiles antes del corte (ej. 2 batches buenos + 3 _error).
    else:
        logger.info(
            "Fallback con provider=%s completó la extracción tras circuit breaker del provider original",
            fallback,
        )

    return fallback_results


def extract_problems(posts: list[pd.Series], provider: str = "claude") -> list[dict[str, Any]]:
    """Entrada pública: bifurca entre extracción deep y batch según DEEP_EXTRACTION_THRESHOLD."""
    if len(posts) <= DEEP_EXTRACTION_THRESHOLD:
        return [extract_problem_deep(row, provider=provider) for row in posts]
    return run_batch_extraction(posts, provider=provider)


# ── Funciones puras de limpieza ───────────────────────────────────────────────


def _extraction_haystack(ex: dict) -> str:
    """Construye texto de busqueda concatenando los campos descriptivos de la extraccion."""
    return " ".join([
        str(ex.get("problem_description") or ""),
        str(ex.get("workflow_context") or ""),
        str(ex.get("key_quote") or ""),
    ]).lower()


def _drop_who_vago(extractions: list[dict]) -> tuple[list[dict], int]:
    """Descarta extracciones con who_has_it vacio o demasiado generico."""
    _VAGUE_WHO = {"unknown", "n/a", "na", "not specified", "the user", "someone", "people", "anyone"}
    kept = []
    dropped = 0
    for ex in extractions:
        who = (ex.get("who_has_it") or "").strip().lower()
        if not who or who in _VAGUE_WHO:
            dropped += 1
        else:
            kept.append(ex)
    return kept, dropped


def _drop_non_saas(extractions: list[dict]) -> tuple[list[dict], int]:
    """Descarta extracciones cuyo dolor es no-SaaS (fisico, mental, personal)."""
    _RESCUE_TOOLS = ("spreadsheet", "excel", "quickbooks", "airtable", "notion", "crm", "invoice")
    kept = []
    dropped = 0
    for ex in extractions:
        text = _extraction_haystack(ex)
        has_non_saas_signal = any(phrase in text for phrase in _NON_SAAS_PAIN_SIGNALS)
        has_rescue_tool = any(tool in text for tool in _RESCUE_TOOLS)
        if has_non_saas_signal and not has_rescue_tool:
            dropped += 1
        else:
            kept.append(ex)
    return kept, dropped


def _fix_workaround(extractions: list[dict]) -> tuple[list[dict], int, int]:
    """Infiere workaround desde el texto cuando el LLM lo deja vacio.

    Ninguna extraccion se descarta: si no se puede inferir, se marca _weak_workaround=True.
    Devuelve (lista, nº_recuperados, nº_mantenidos_sin_workaround).
    """
    recovered = 0
    kept_no_wk = 0
    for ex in extractions:
        wk = (ex.get("current_workaround") or "").strip()
        wk_empty = not wk or wk.lower().rstrip(".") in _NO_WORKAROUND_PHRASES
        if wk_empty:
            haystack = _extraction_haystack(ex)
            inferred = ""
            for needle, label in _WORKAROUND_KEYWORDS:
                if needle in haystack:
                    inferred = f"{label} (inferred)"
                    break
            if inferred:
                ex["current_workaround"] = inferred
                recovered += 1
            else:
                ex["current_workaround"] = "no explicit workaround mentioned"
                ex["_weak_workaround"] = True
                kept_no_wk += 1
    return extractions, recovered, kept_no_wk


def _fix_payment_signal(extractions: list[dict]) -> list[dict]:
    """Corrige payment_signal=True sin payment_quote — incoherente, se pone a False."""
    for ex in extractions:
        pq = (ex.get("payment_quote") or "").strip()
        if ex.get("payment_signal") and not pq:
            ex["payment_signal"] = False
    return extractions


def _clean_extractions(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Limpieza de calidad sobre extracciones antes de pasarlas a sintesis.

    Encadena: filtro has_problem → drop_who_vago → drop_non_saas →
    fix_workaround → fix_payment_signal.
    """
    valid = [ex for ex in extractions if ex.get("has_problem") and not ex.get("_error")]

    valid, dropped_who = _drop_who_vago(valid)
    valid, dropped_non_saas = _drop_non_saas(valid)
    valid, recovered_wk, kept_no_wk = _fix_workaround(valid)
    valid = _fix_payment_signal(valid)

    if dropped_who or dropped_non_saas or recovered_wk or kept_no_wk:
        logger.info(
            "Limpieza: -%d who_vago, -%d no-SaaS, +%d workaround_inferido, +%d sin_workaround(mantenidos)",
            dropped_who,
            dropped_non_saas,
            recovered_wk,
            kept_no_wk,
        )

    return valid
