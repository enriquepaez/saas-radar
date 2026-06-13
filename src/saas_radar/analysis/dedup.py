"""Dedup semántico de oportunidades entre runs: Jaccard v1 y embeddings v2."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

# Stopwords compartidas para name_similarity. NO se usan en evidence_overlap
# (las quotes ya tienen señal de dominio fuerte: nombres de tools, números,
# acciones concretas). Mantener este set MUY pequeño: filtrar de más mata
# precisión cuando los productos son cortos (2-3 palabras tras stopwords).
_NAME_STOP = {
    "for", "with", "the", "to", "of", "a", "an", "and", "or",
    "in", "on", "at", "by", "from", "tool", "tools", "app",
}

# Stopwords mínimas para evidence tokens. El objetivo es quitar relleno
# absoluto sin tocar verbos/sustantivos del workflow. Si dos quotes solo
# comparten tokens de esta lista, NO deberían matchear.
_EVIDENCE_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "they", "them", "their", "there", "would", "could", "should", "have",
    "been", "into", "than", "then", "what", "when", "where", "which",
    "while", "your", "just", "like", "also", "very", "much", "some",
    "every", "thing", "things", "stuff", "kind", "really", "even",
}

_ITEM_PREFIX = re.compile(r"^\s*\[item\s+\d+\]\s*", re.IGNORECASE)

logger = logging.getLogger(__name__)

# Lazy singleton para el modelo sentence-transformers.
# Se carga la primera vez que find_canonical_v2 es invocada, no al importar
# el módulo. Así el import de dedup.py no descarga/carga 80 MB en cada test.
_ST_MODEL = None


def _name_tokens(name: str) -> frozenset[str]:
    """Tokeniza un product_name para comparación: lowercase, ≥3 chars, sin stopwords."""
    if not name:
        return frozenset()
    words = re.findall(r"[a-z0-9]+", str(name).lower())
    return frozenset(w for w in words if len(w) >= 3 and w not in _NAME_STOP)


def _evidence_tokens(quotes: Any) -> frozenset[str]:
    """
    Tokeniza el set de evidence_quotes para Jaccard.

    Acepta: list[str] (formato persistido), str con JSON serializado, o None.
    Descarta el prefijo `[item N]` antes de tokenizar (no aporta señal y
    ensucia el Jaccard porque "item" aparecería en TODAS las quotes).

    Tokens: lowercase, ≥4 chars, alfanuméricos, sin stopwords genéricas.
    El umbral 4 es deliberado: filtra "for/the/to" sin meterse con verbos
    cortos del workflow (≥4 chars cubre "pack", "ship", "pick", "track").
    """
    items = _coerce_quote_list(quotes)
    tokens: set[str] = set()
    for q in items:
        body = _ITEM_PREFIX.sub("", str(q))
        for w in re.findall(r"[a-z0-9]+", body.lower()):
            if len(w) >= 4 and w not in _EVIDENCE_STOP:
                tokens.add(w)
    return frozenset(tokens)


def _coerce_quote_list(quotes: Any) -> list[str]:
    """
    Normaliza `evidence_quotes` que puede venir como:
      - list[str] cuando se construye en memoria (ai_analyzer.py)
      - str (JSON dump) cuando se lee directo de SQLite
      - None / vacío
    """
    if quotes is None:
        return []
    if isinstance(quotes, list):
        return [str(q) for q in quotes]
    if isinstance(quotes, str):
        s = quotes.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(q) for q in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        return [s]
    return []


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard estándar |A∩B|/|A∪B|. Devuelve 0.0 si ambos sets están vacíos."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def evidence_overlap(opp_a: dict[str, Any], opp_b: dict[str, Any]) -> float:
    """Jaccard de tokens de evidence_quotes entre dos opps."""
    return _jaccard(_evidence_tokens(opp_a.get("evidence_quotes")), _evidence_tokens(opp_b.get("evidence_quotes")))


def name_similarity(opp_a: dict[str, Any], opp_b: dict[str, Any]) -> float:
    """Jaccard de tokens del product_name. Tie-breaker, no condición de match."""
    return _jaccard(_name_tokens(opp_a.get("product_name", "")), _name_tokens(opp_b.get("product_name", "")))


def find_canonical(
    opp: dict[str, Any],
    existing: list[dict[str, Any]],
    threshold: float = 0.3,
) -> int | None:
    """
    Devuelve el `canonical_id` de la opp existente que mejor matchea, o None.

    Reglas:
      1. Filtrar `existing` a candidatos con evidence_overlap >= threshold.
      2. Si hay 0 candidatos: devuelve None (la opp es nueva canónica).
      3. Si hay >=1: ordenar por (evidence_overlap desc, name_similarity desc,
         id asc) y devolver `canonical_id` del primero. Si esa fila no tiene
         `canonical_id` (caller legacy), devolver su `id`.

    `existing` debe ser una lista de dicts con al menos: id, canonical_id (o
    None), product_name, evidence_quotes.
    """
    if not existing:
        return None

    candidates = []
    for row in existing:
        score = evidence_overlap(opp, row)
        if score >= threshold:
            candidates.append((score, name_similarity(opp, row), row))

    if not candidates:
        return None

    candidates.sort(key=lambda t: (-t[0], -t[1], t[2].get("id", 0)))
    best = candidates[0][2]
    return best.get("canonical_id") or best.get("id")


def _get_st_model():
    """Carga el modelo sentence-transformers como lazy singleton.

    La carga ocurre una sola vez por proceso. Si sentence_transformers no está
    instalado, lanza RuntimeError con instrucciones claras.
    """
    global _ST_MODEL
    if _ST_MODEL is not None:
        return _ST_MODEL

    try:
        from sentence_transformers import SentenceTransformer
    except (ImportError, TypeError):
        raise RuntimeError(
            "sentence-transformers not installed; install with pip install 'saas-radar[dedup-v2]'"
        )

    logger.info("Cargando modelo sentence-transformers all-MiniLM-L6-v2 (primera vez)")
    _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _ST_MODEL


def _cosine(a: "Any", b: "Any") -> float:
    """Similitud coseno entre dos vectores numpy (arrays 1-D)."""
    import math

    dot = float(sum(float(x) * float(y) for x, y in zip(a, b)))
    norm_a = math.sqrt(sum(float(x) ** 2 for x in a))
    norm_b = math.sqrt(sum(float(x) ** 2 for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_canonical_v2(
    opp: dict[str, Any],
    existing: list[dict[str, Any]],
    threshold: float = 0.75,
) -> int | None:
    """Devuelve el `canonical_id` de la opp existente más similar por embeddings, o None.

    Texto de comparación: product_name + " " + core_problem + " " + niche.
    Similitud coseno >= threshold → candidato. Si hay varios candidatos devuelve
    el de mayor similitud; en caso de empate, el de menor id (id asc).

    Falla limpia si sentence-transformers no está instalado: lanza RuntimeError
    con instrucciones de instalación.
    """
    if not existing:
        return None

    model = _get_st_model()

    def _text(d: dict[str, Any]) -> str:
        return " ".join(
            str(d.get(k) or "")
            for k in ("product_name", "core_problem", "niche")
        ).strip()

    opp_text = _text(opp)
    existing_texts = [_text(row) for row in existing]

    all_texts = [opp_text] + existing_texts
    embeddings = model.encode(all_texts, convert_to_numpy=True)
    opp_vec = embeddings[0]

    candidates: list[tuple[float, int, dict[str, Any]]] = []
    for idx, row in enumerate(existing):
        sim = _cosine(opp_vec, embeddings[idx + 1])
        if sim >= threshold:
            candidates.append((sim, row.get("id", 0), row))

    if not candidates:
        return None

    # Mayor similitud primero; empate se resuelve por id asc (menor id = más antiguo = canónica)
    candidates.sort(key=lambda t: (-t[0], t[1]))
    best = candidates[0][2]
    return best.get("canonical_id") or best.get("id")
