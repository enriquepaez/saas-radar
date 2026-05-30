"""Pipeline principal: orquesta scraping (fases 1-3), análisis IA (fase 4) y GTM stub (fase 5)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from saas_radar.analysis.ai_analyzer import run_ai_analysis
from saas_radar.analysis.pain_filter import _semantic_score
from saas_radar.analysis.post_classifier import classify_post
from saas_radar.analysis.text_cleaning import clean_text
from saas_radar.config import (
    COMMENT_FETCH_WORKERS,
    COMMENT_TARGET_POSTS,
    HIGH_ENGAGEMENT_THRESHOLD,
    INCREMENTAL_POST_AGE_DAYS,
    MAX_POST_AGE_DAYS,
    MAX_POSTS,
    PAIN_SEARCH_QUERIES,
    SUBREDDITS,
)
from saas_radar.scrapers.reddit_scraper import fetch_posts, fetch_top_comments, search_pain_posts
from saas_radar.storage.db import db_stats, has_successful_run, init_db, save_to_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _fmt(seconds: float) -> str:
    """Formatea segundos como mm:ss o hh:mm:ss si supera una hora."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def enrich_posts(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    combined = df["title"].fillna("") + " " + df["text"].fillna("")
    df["clean_text"] = combined.apply(clean_text)
    df["category"] = df.apply(lambda row: classify_post(row["title"], row["text"]), axis=1)
    df["semantic_score"] = df.apply(
        lambda row: _semantic_score(str(row.get("title", "")), str(row.get("text", ""))),
        axis=1,
    )
    return df


def enrich_comments(comments: list[dict]) -> list[dict]:
    for c in comments:
        c["clean_text"] = clean_text(c["text"])
        c["category"] = classify_post("", c["text"])
    return comments


def phase_subreddits(incremental: bool = False) -> pd.DataFrame:
    print("\n-- FASE 1: Scraping de subreddits")
    t0 = time.time()
    all_posts = []
    for sub in SUBREDDITS:
        print(f"  /r/{sub}...")
        try:
            df = fetch_posts(sub, incremental=incremental)
            df = enrich_posts(df)
            all_posts.append(df)
            time.sleep(1)
        except Exception as e:
            print(f"  [WARN] Error en /r/{sub}: {e}")

    if not all_posts:
        return pd.DataFrame()

    combined = pd.concat(all_posts, ignore_index=True).drop_duplicates(subset="id")
    print(f"  Total: {len(combined)} posts ({_fmt(time.time() - t0)})")
    save_to_db(combined, table_name="reddit_posts")
    return combined


def phase_pain_search(incremental: bool = False) -> pd.DataFrame:
    print("\n-- FASE 2: Busqueda por keywords de dolor")
    t0 = time.time()
    all_posts = []
    for query in PAIN_SEARCH_QUERIES:
        print(f"  '{query}'...")
        try:
            df = search_pain_posts(query, incremental=incremental)
            df = enrich_posts(df)
            all_posts.append(df)
            time.sleep(2)
        except Exception as e:
            print(f"  [WARN] Error en query '{query}': {e}")

    if not all_posts:
        return pd.DataFrame()

    combined = pd.concat(all_posts, ignore_index=True).drop_duplicates(subset="id")
    print(f"  Total: {len(combined)} posts unicos ({_fmt(time.time() - t0)})")
    save_to_db(combined, table_name="reddit_posts")
    return combined


def phase_comments(posts_df: pd.DataFrame) -> None:
    print(f"\n-- FASE 3: Comentarios (posts con >={HIGH_ENGAGEMENT_THRESHOLD} comentarios)")
    t0 = time.time()
    if posts_df.empty:
        print("  Sin posts -- saltando.")
        return

    high = posts_df[posts_df["num_comments"] >= HIGH_ENGAGEMENT_THRESHOLD].copy()
    print(f"  Posts con >={HIGH_ENGAGEMENT_THRESHOLD} comentarios: {len(high)}")

    if len(high) > COMMENT_TARGET_POSTS:
        if "semantic_score" not in high.columns:
            high["semantic_score"] = high.apply(
                lambda r: _semantic_score(str(r.get("title", "")), str(r.get("text", ""))),
                axis=1,
            )
        high = high.sort_values(["semantic_score", "num_comments"], ascending=False).head(COMMENT_TARGET_POSTS)
        print(f"  Priorizados por señal semántica: {len(high)} posts")

    all_comments: list[dict] = []

    def fetch_safe(post_id: str) -> list[dict]:
        try:
            return enrich_comments(fetch_top_comments(post_id))
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS) as executor:
        futures = {executor.submit(fetch_safe, row["id"]): row["id"] for _, row in high.iterrows()}
        done = 0
        for future in as_completed(futures):
            all_comments.extend(future.result())
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(high)} posts procesados ({len(all_comments)} comentarios)")

    if not all_comments:
        print("  Sin comentarios recogidos.")
        return

    comments_df = pd.DataFrame(all_comments).drop_duplicates(subset="comment_id")
    print(f"  Total: {len(comments_df)} comentarios ({_fmt(time.time() - t0)})")
    save_to_db(comments_df, table_name="reddit_comments")


def phase_gtm() -> None:
    print("\n-- FASE 5: GTM agent (opps canonicas pendientes)")
    print("  GTM agent: fase pendiente (feature #17)")


def run_pipeline(
    skip_scrape: bool = False,
    skip_ai: bool = False,
    skip_gtm: bool = False,
    min_score: int = 5,
    top_posts: int = MAX_POSTS,
    output: str = "data/ai_analysis.json",
    use_cached_extractions: bool = False,
    full_scan: bool = False,
) -> None:
    print("Reddit SaaS Radar v3 -- pipeline completo")
    init_db()

    incremental = False
    if not full_scan and has_successful_run():
        incremental = True
    post_age_days = INCREMENTAL_POST_AGE_DAYS if incremental else MAX_POST_AGE_DAYS

    if incremental:
        print("  Modo: INCREMENTAL (24h) -- run previo exitoso detectado")
    else:
        reason = "forzado con --full-scan" if full_scan else "primer run o sin runs exitosos"
        print(f"  Modo: CARGA COMPLETA ({MAX_POST_AGE_DAYS}d) -- {reason}")

    t_total = time.time()
    all_posts = pd.DataFrame()

    if not skip_scrape:
        t0 = time.time()
        subreddit_posts = phase_subreddits(incremental=incremental)
        pain_posts = phase_pain_search(incremental=incremental)

        frames = [df for df in [subreddit_posts, pain_posts] if not df.empty]
        if frames:
            all_posts = pd.concat(frames, ignore_index=True).drop_duplicates(subset="id")

        phase_comments(all_posts)
        t_scrape = time.time() - t0

        stats = db_stats()
        print(f"\n  Scraping completado ({_fmt(t_scrape)})")
        print(f"   reddit_posts:    {stats.get('reddit_posts', 0):,} posts")
        print(f"   reddit_comments: {stats.get('reddit_comments', 0):,} comentarios")
    else:
        print("\n  Scraping omitido (--skip-scrape). Usando datos existentes en la BD.")

    if not skip_ai:
        t0 = time.time()
        run_ai_analysis(
            min_score=min_score,
            top_n=top_posts,
            output_path=output,
            use_cached_extractions=use_cached_extractions,
            post_age_days=post_age_days,
            provider=os.getenv("AI_PROVIDER", "claude"),
        )
        print(f"   Análisis IA:     {_fmt(time.time() - t0)}")
    else:
        print("\n  Analisis IA omitido (--skip-ai).")

    if not skip_gtm:
        phase_gtm()
    else:
        print("\n  GTM agent omitido (--skip-gtm).")

    print(f"\n  Pipeline finalizado -- total {_fmt(time.time() - t_total)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reddit SaaS Radar — pipeline completo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python -m saas_radar.main                        # pipeline completo
  python -m saas_radar.main --skip-scrape          # solo análisis IA (datos ya en BD)
  python -m saas_radar.main --skip-ai              # solo scraping
  python -m saas_radar.main --top-posts 20         # analizar los 20 posts más relevantes
  python -m saas_radar.main --min-score 5          # incluir posts con score >= 5
        """,
    )
    parser.add_argument(
        "--skip-scrape", action="store_true", help="Omite las fases 1-3 y usa los datos existentes en la BD"
    )
    parser.add_argument("--skip-ai", action="store_true", help="Omite la fase 4 (análisis con IA)")
    parser.add_argument(
        "--skip-gtm",
        action="store_true",
        help="Omite la fase 5 (GTM agent sobre opps canonicas pendientes).",
    )
    parser.add_argument(
        "--min-score", type=int, default=5, help="Score mínimo de Reddit para incluir un post (default: 5)"
    )
    parser.add_argument(
        "--top-posts",
        type=int,
        default=MAX_POSTS,
        help=f"Número máximo de posts a analizar con IA (default: {MAX_POSTS})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/ai_analysis.json",
        help="Ruta del JSON de resultados (default: data/ai_analysis.json)",
    )
    parser.add_argument(
        "--use-cached-extractions",
        action="store_true",
        help="Salta la fase de extracción y usa data/extractions_cache.json.",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Fuerza scan completo (365 días) incluso si ya hay runs previos exitosos.",
    )

    args = parser.parse_args()
    run_pipeline(
        skip_scrape=args.skip_scrape,
        skip_ai=args.skip_ai,
        skip_gtm=args.skip_gtm,
        min_score=args.min_score,
        top_posts=args.top_posts,
        output=args.output,
        use_cached_extractions=args.use_cached_extractions,
        full_scan=args.full_scan,
    )
