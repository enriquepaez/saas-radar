"""Orquestador IA: encadena carga, extracción, síntesis y persistencia."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from saas_radar.analysis.data_loader import load_pain_posts
from saas_radar.analysis.extraction import (
    DEEP_EXTRACTION_THRESHOLD,
    _clean_extractions,
    extract_problem_deep,
    run_batch_extraction,
)
from saas_radar.analysis.llm_clients import call_llm
from saas_radar.analysis.synthesis import _validate_synthesis, build_synthesis_prompt
from saas_radar.storage.db import init_db, persist_run_to_db

logger = logging.getLogger(__name__)


# ── Cache defensivo ───────────────────────────────────────────────────────────


def _save_extractions_cache(new_data: list[dict], cache_path: str) -> None:
    """Guarda cache solo si new_data no está vacío O si no existe cache previo.

    Lógica defensiva (architecture.md §7 — "Cache defensivo en pipelines costosos"):
    - Si new_data tiene elementos → sobrescribe el fichero sin condiciones.
    - Si new_data está vacío Y existe un fichero previo con datos válidos
      (valid_old > 0) → NO sobrescribe; escribe el contenido fallido en
      <cache_path>.failed.json para inspección manual.
    - Si new_data está vacío Y no hay fichero previo → escribe igualmente
      (no hay nada que proteger).
    """
    p = Path(cache_path)

    if new_data:
        # Hay datos nuevos: guardar siempre
        p.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Cache de extracciones guardado: %d items en %s", len(new_data), cache_path)
        return

    # new_data está vacío — comprobar si el cache previo tiene valor
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
            valid_old = len([e for e in existing if e.get("has_problem") and not e.get("_error")])
        except Exception:
            valid_old = 0

        if valid_old > 0:
            # El cache previo es bueno: no lo destruimos
            failed_path = cache_path + ".failed.json"
            Path(failed_path).write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.warning(
                "Cache defensivo: valid_new=0 pero valid_old=%d → NO sobrescribiendo %s. Estado fallido escrito en %s",
                valid_old,
                cache_path,
                failed_path,
            )
            return

    # No existe cache previo o está vacío: escribir aunque new_data esté vacío
    p.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Cache inicial de extracciones escrito (vacío): %s", cache_path)


# ── Funciones de presentación y salida ───────────────────────────────────────


def _print_results(results: dict[str, Any]) -> None:
    """Imprime el resumen de oportunidades en consola (output visible al usuario).

    Los prints aquí son intencionales: esta función es el user-facing output
    del pipeline, análogo a las cabeceras de fase del CLI (ver architecture.md §9).
    """
    opps = results.get("opportunities") or []
    top3 = results.get("top_3_recommended") or []
    disq = results.get("disqualified_ideas") or []

    print(f"\n{'=' * 60}")
    print("  RESULTADOS DE ANÁLISIS IA")
    print(f"{'=' * 60}")
    print(f"  Oportunidades válidas:  {len(opps)}")
    print(f"  Ideas descartadas:      {len(disq)}")
    print(f"  Top 3 recomendadas:     {top3}")

    if opps:
        print(f"\n{'─' * 60}")
        print("  OPORTUNIDADES DETECTADAS")
        print(f"{'─' * 60}")
        for opp in sorted(opps, key=lambda o: -(o.get("priority_score") or 0)):
            score = opp.get("priority_score", "?")
            name = opp.get("product_name", "(sin nombre)")
            niche = opp.get("niche", "")
            pay = opp.get("payment_signal", "?")
            print(f"\n  [{score}/10] {name}")
            print(f"    Nicho:  {niche}")
            print(f"    Señal de pago: {pay}")
            ev = opp.get("evidence_items") or []
            print(f"    Evidencias: {ev}")

    if disq:
        print(f"\n{'─' * 60}")
        print("  IDEAS DESCARTADAS")
        print(f"{'─' * 60}")
        for d in disq:
            print(f"  × {d.get('idea', '?')} → {d.get('rule_violated', '?')}")

    print(f"\n{'=' * 60}\n")


def _save_results(results: dict[str, Any], output_path: str, timestamp: str) -> str:
    """Guarda los resultados de síntesis en un JSON con timestamp.

    Crea el directorio output_path si no existe. Devuelve la ruta del archivo
    generado en formato data/runs/<timestamp>_results.json.
    """
    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{timestamp}_results.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Resultados guardados en %s", out_file)
    return str(out_file)


# ── Orquestador principal ─────────────────────────────────────────────────────


def run_ai_analysis(
    min_score: float = 5.0,
    top_n: int = 50,
    post_age_days: int = 365,
    provider: str = "claude",
    use_cached_extractions: bool = False,
    extractions_cache_path: str = "extractions_cache.json",
    output_path: str = "data/runs/",
    db_path: str = "data/saas.db",
) -> dict:
    """Orquesta el pipeline completo de análisis IA.

    Flujo:
    1. init_db para asegurar que el schema está actualizado.
    2. load_pain_posts para obtener los posts candidatos.
    3. Extracción (deep si len<=30, batch si len>30) con cache defensivo.
    4. Abortar si < 2 extracciones válidas (RULE 1 imposible).
    5. build_synthesis_prompt → call_llm → _validate_synthesis.
    6. _print_results + _save_results.
    7. persist_run_to_db con status 'ok'/'partial'/'failed'.

    Args:
        min_score: Score mínimo de Reddit para filtrar posts.
        top_n: Número máximo de posts a analizar.
        post_age_days: Antigüedad máxima de posts en días.
        provider: Proveedor LLM ("claude", "gemini", "groq").
        use_cached_extractions: Si True, salta la extracción si existe el cache.
        extractions_cache_path: Ruta del archivo JSON de cache.
        output_path: Directorio donde guardar los resultados JSON.
        db_path: Ruta a la BD SQLite.

    Returns:
        dict con keys: status, opportunities, disqualified, top_3, run_id, json_path.
    """
    db_url = f"sqlite:///{db_path}"
    start_time = time.time()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ── Paso 1: init_db ───────────────────────────────────────────────────────
    init_db(db_url)

    # ── Paso 2: cargar posts ──────────────────────────────────────────────────
    logger.info("Cargando posts de la BD (min_score=%s, top_n=%d, age=%dd)", min_score, top_n, post_age_days)
    posts_df = load_pain_posts(
        min_score=min_score,
        top_n=top_n,
        include_comments=True,
        post_age_days=post_age_days,
    )

    if posts_df.empty:
        logger.warning("Sin posts tras el filtro. Abortando análisis.")
        run_id = persist_run_to_db(
            run_data={
                "run_at": timestamp,
                "posts_analyzed": 0,
                "valid_extractions": 0,
                "opportunities_count": 0,
                "status": "failed",
                "duration_sec": int(time.time() - start_time),
                "error_message": "Sin posts tras el filtro",
                "ai_provider": provider,
            },
            opportunities=[],
            db_url=db_url,
        )
        return {
            "status": "failed",
            "opportunities": [],
            "disqualified": [],
            "top_3": [],
            "run_id": run_id,
            "json_path": None,
        }

    posts_list: list[pd.Series] = [posts_df.iloc[i] for i in range(len(posts_df))]
    logger.info("Posts candidatos a extracción: %d", len(posts_list))

    # ── Paso 3: extracción (con cache) ────────────────────────────────────────
    cache_p = Path(extractions_cache_path)

    if use_cached_extractions and cache_p.exists():
        logger.info("Usando extracciones cacheadas: %s", extractions_cache_path)
        try:
            all_extractions: list[dict] = json.loads(cache_p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Cache corrupto (%s) — re-extrayendo", exc)
            all_extractions = _extract_and_cache(posts_list, extractions_cache_path)
    else:
        all_extractions = _extract_and_cache(posts_list, extractions_cache_path)

    valid_extractions = _clean_extractions(all_extractions)
    logger.info("Extracciones válidas: %d / %d", len(valid_extractions), len(all_extractions))

    # ── Paso 4: abortar si < 2 válidas ───────────────────────────────────────
    if len(valid_extractions) < 2:
        logger.warning(
            "Solo %d extraccion(es) válida(s) — RULE 1 imposible (mínimo 2). Abortando síntesis.",
            len(valid_extractions),
        )
        run_id = persist_run_to_db(
            run_data={
                "run_at": timestamp,
                "posts_analyzed": len(posts_list),
                "valid_extractions": len(valid_extractions),
                "opportunities_count": 0,
                "status": "failed",
                "duration_sec": int(time.time() - start_time),
                "error_message": f"Solo {len(valid_extractions)} extraccion(es) válida(s) (mínimo 2 para RULE 1)",
                "ai_provider": provider,
            },
            opportunities=[],
            db_url=db_url,
        )
        return {
            "status": "failed",
            "opportunities": [],
            "disqualified": [],
            "top_3": [],
            "run_id": run_id,
            "json_path": None,
        }

    # ── Paso 5: síntesis ──────────────────────────────────────────────────────
    logger.info("Construyendo prompt de síntesis para %d extracciones...", len(valid_extractions))
    prompt, ordered_extractions = build_synthesis_prompt(valid_extractions)

    logger.info("Llamando al LLM (%s) para síntesis...", provider)
    raw = call_llm(prompt, max_tokens=4096, phase="synthesis", provider=provider)

    if raw is None:
        logger.error("LLM devolvió None en síntesis. Abortando.")
        run_id = persist_run_to_db(
            run_data={
                "run_at": timestamp,
                "posts_analyzed": len(posts_list),
                "valid_extractions": len(valid_extractions),
                "opportunities_count": 0,
                "status": "failed",
                "duration_sec": int(time.time() - start_time),
                "error_message": "LLM devolvió None en síntesis",
                "ai_provider": provider,
            },
            opportunities=[],
            db_url=db_url,
        )
        return {
            "status": "failed",
            "opportunities": [],
            "disqualified": [],
            "top_3": [],
            "run_id": run_id,
            "json_path": None,
        }

    # ── Paso 6: validación ────────────────────────────────────────────────────
    results = _validate_synthesis(raw, ordered_extractions)
    opps = results.get("opportunities") or []
    disq = results.get("disqualified_ideas") or []
    top3 = results.get("top_3_recommended") or []

    logger.info(
        "Síntesis: %d oportunidades válidas, %d descartadas, top3=%s",
        len(opps),
        len(disq),
        top3,
    )

    # ── Paso 7: presentación y guardado ──────────────────────────────────────
    _print_results(results)
    json_path = _save_results(results, output_path, timestamp)

    # ── Paso 8: persistencia ──────────────────────────────────────────────────
    status = "ok" if len(opps) >= 1 else "partial"

    # Serializar campos que SQLite no acepta como Python nativo
    serialized_opps = _serialize_opportunities(opps)

    run_id = persist_run_to_db(
        run_data={
            "run_at": timestamp,
            "posts_analyzed": len(posts_list),
            "valid_extractions": len(valid_extractions),
            "opportunities_count": len(opps),
            "json_path": json_path,
            "status": status,
            "duration_sec": int(time.time() - start_time),
            "ai_provider": provider,
        },
        opportunities=serialized_opps,
        db_url=db_url,
    )

    logger.info("Run persistido: run_id=%d status=%s", run_id, status)

    return {
        "status": status,
        "opportunities": opps,
        "disqualified": disq,
        "top_3": top3,
        "run_id": run_id,
        "json_path": json_path,
    }


# ── Helpers internos ──────────────────────────────────────────────────────────


def _extract_and_cache(posts_list: list[pd.Series], cache_path: str) -> list[dict]:
    """Ejecuta la extracción y guarda el resultado en cache.

    Selecciona el modo de extracción según DEEP_EXTRACTION_THRESHOLD:
    - len(posts) <= threshold → extract_problem_deep por cada post.
    - len(posts) > threshold → run_batch_extraction en batches de 5.

    Siempre llama a _save_extractions_cache tras la extracción, incluso
    si el resultado está vacío (el cache defensivo decide si sobrescribe).
    """
    if len(posts_list) <= DEEP_EXTRACTION_THRESHOLD:
        logger.info("Modo deep (N=%d <= %d): extracción post a post", len(posts_list), DEEP_EXTRACTION_THRESHOLD)
        extractions = [extract_problem_deep(row) for row in posts_list]
    else:
        logger.info("Modo batch (N=%d > %d): extracción en batches de 5", len(posts_list), DEEP_EXTRACTION_THRESHOLD)
        extractions = run_batch_extraction(posts_list)

    _save_extractions_cache(extractions, cache_path)
    return extractions


def _serialize_opportunities(opps: list[dict]) -> list[dict]:
    """Serializa campos de lista/dict a JSON string para almacenamiento en SQLite.

    SQLite espera TEXT para los campos evidence_items, evidence_quotes y
    mentioned_competitors, que el LLM devuelve como listas de Python.
    json.dumps convierte list → str; json.dumps de un str ya serializado
    es incorrecto, así que solo se aplica si el valor es list o dict.
    """
    serialized = []
    for opp in opps:
        row = dict(opp)
        for field in ("evidence_items", "evidence_quotes", "mentioned_competitors", "mvp_scope"):
            val = row.get(field)
            if isinstance(val, (list, dict)):
                row[field] = json.dumps(val, ensure_ascii=False)
        serialized.append(row)
    return serialized
