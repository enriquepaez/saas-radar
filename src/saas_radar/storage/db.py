"""Capa de persistencia SQLite: schema, migraciones idempotentes y operaciones CRUD."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

from saas_radar.analysis.dedup import find_canonical, find_canonical_v2

logger = logging.getLogger(__name__)

_DEFAULT_DB_URL = "sqlite:///data/saas.db"

# Engine global para uso desde módulos de análisis (data_loader, etc.).
# Se construye al importar el módulo usando DB_URL del entorno (o el default).
# Los tests hacen monkey-patch de saas_radar.analysis.data_loader.engine, no de este.
engine = create_engine(
    os.environ.get("DB_URL", _DEFAULT_DB_URL),
    connect_args={"check_same_thread": False},
)

# ---------------------------------------------------------------------------
# DDL — tablas e índices
# ---------------------------------------------------------------------------

_CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS reddit_posts (
        id             TEXT PRIMARY KEY,
        source         TEXT,
        subreddit      TEXT,
        title          TEXT,
        text           TEXT,
        score          INTEGER,
        upvote_ratio   REAL,
        num_comments   INTEGER,
        created_utc    REAL,
        url            TEXT,
        flair          TEXT,
        search_query   TEXT,
        clean_text     TEXT,
        category       TEXT,
        semantic_score REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reddit_comments (
        comment_id   TEXT PRIMARY KEY,
        post_id      TEXT,
        subreddit    TEXT,
        text         TEXT,
        score        INTEGER,
        created_utc  REAL,
        clean_text   TEXT,
        category     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at              TEXT NOT NULL,
        posts_analyzed      INTEGER DEFAULT 0,
        valid_extractions   INTEGER DEFAULT 0,
        opportunities_count INTEGER DEFAULT 0,
        json_path           TEXT,
        status              TEXT,
        duration_sec        INTEGER,
        error_message       TEXT,
        ai_provider         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id                INTEGER REFERENCES analysis_runs(id),
        product_name          TEXT,
        niche                 TEXT,
        core_problem          TEXT,
        why_gap_exists        TEXT,
        concrete_workaround   TEXT,
        workaround_cost       TEXT,
        mvp_scope             TEXT,
        estimated_price       TEXT,
        monetization          TEXT,
        competitor_gap        TEXT,
        mentioned_competitors TEXT,
        payment_signal        TEXT,
        payment_evidence      TEXT,
        solo_buildable        INTEGER,
        mvp_weeks             INTEGER,
        priority_score        INTEGER,
        priority_reason       TEXT,
        evidence_items        TEXT,
        evidence_quotes       TEXT,
        reviewed              INTEGER DEFAULT 0,
        starred               INTEGER DEFAULT 0,
        discarded             INTEGER DEFAULT 0,
        user_notes            TEXT,
        created_at            TEXT,
        canonical_id          INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta_recommendations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER REFERENCES analysis_runs(id),
        type        TEXT,
        action      TEXT,
        target      TEXT,
        recurrence  INTEGER DEFAULT 1,
        acted       INTEGER DEFAULT 0,
        created_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS opportunity_gtm (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id         INTEGER NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE CASCADE,
        viability_desperation  INTEGER,
        viability_build_ease   INTEGER,
        viability_scalability  INTEGER,
        viability_total        INTEGER,
        elevator_pitch         TEXT,
        pricing_tiers          TEXT,
        acquisition_channels   TEXT,
        cold_outreach_script   TEXT,
        organic_post_template  TEXT,
        validation_plan_7d     TEXT,
        pivot_signals          TEXT,
        kpis                   TEXT,
        gtm_status             TEXT,
        user_notes             TEXT,
        created_at             TEXT
    )
    """,
]

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON reddit_posts(subreddit)",
    "CREATE INDEX IF NOT EXISTS idx_posts_category ON reddit_posts(category)",
    "CREATE INDEX IF NOT EXISTS idx_posts_score ON reddit_posts(score)",
    "CREATE INDEX IF NOT EXISTS idx_posts_created ON reddit_posts(created_utc)",
    "CREATE INDEX IF NOT EXISTS idx_posts_semantic ON reddit_posts(semantic_score)",
    "CREATE INDEX IF NOT EXISTS idx_comments_post_id ON reddit_comments(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_comments_subreddit ON reddit_comments(subreddit)",
    "CREATE INDEX IF NOT EXISTS idx_opps_priority ON opportunities(priority_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_opps_starred ON opportunities(starred)",
    "CREATE INDEX IF NOT EXISTS idx_opps_reviewed ON opportunities(reviewed)",
    "CREATE INDEX IF NOT EXISTS idx_opps_canonical ON opportunities(canonical_id)",
    "CREATE INDEX IF NOT EXISTS idx_meta_type ON meta_recommendations(type)",
    "CREATE INDEX IF NOT EXISTS idx_meta_target ON meta_recommendations(target)",
]


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _get_db_url(db_url: str | None) -> str:
    return db_url or os.environ.get("DB_URL", _DEFAULT_DB_URL)


def _make_engine(db_url: str):
    return create_engine(db_url, connect_args={"check_same_thread": False})


def _column_exists(conn, table: str, column: str) -> bool:
    """Comprueba si una columna existe en una tabla usando PRAGMA table_info."""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def _extract_target(rec_type: str, action: str) -> str:
    """Extrae el target canónico de una recomendación para dedup por (type, target)."""
    if "subreddit" in rec_type:
        tokens = action.split()
        for token in tokens:
            if token.startswith("r/"):
                return token
        return action[:50]
    return action[:50]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def init_db(db_url: str | None = None) -> None:
    """Crea tablas, índices y ejecuta migraciones idempotentes.

    Seguro de llamar múltiples veces: usa CREATE TABLE IF NOT EXISTS y
    detecta columnas existentes con PRAGMA antes de ALTER TABLE.
    """
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    with engine.connect() as conn:
        with conn.begin():
            for ddl in _CREATE_TABLES:
                conn.execute(text(ddl))

            # Migraciones idempotentes ANTES de crear índices que dependen de esas columnas
            if not _column_exists(conn, "reddit_posts", "semantic_score"):
                conn.execute(text("ALTER TABLE reddit_posts ADD COLUMN semantic_score REAL"))
                logger.info("Migración: añadida columna semantic_score a reddit_posts")

            if not _column_exists(conn, "opportunities", "canonical_id"):
                conn.execute(text("ALTER TABLE opportunities ADD COLUMN canonical_id INTEGER"))
                logger.info("Migración: añadida columna canonical_id a opportunities")

            # Backfill de flags insertados como NULL explícito por versiones
            # anteriores de persist_run_to_db (el DEFAULT 0 del schema no
            # aplica cuando la columna aparece en el INSERT con valor NULL).
            for flag in ("reviewed", "starred", "discarded"):
                result = conn.execute(text(f"UPDATE opportunities SET {flag} = 0 WHERE {flag} IS NULL"))
                if result.rowcount:
                    logger.info("Migración: backfill %s=0 en %d opportunities", flag, result.rowcount)

            for idx in _CREATE_INDEXES:
                conn.execute(text(idx))


def save_to_db(df: pd.DataFrame, table_name: str, db_url: str | None = None) -> int:
    """Inserta filas nuevas en table_name usando INSERT OR IGNORE vía tabla staging.

    Normaliza la columna subreddit a minúsculas antes de insertar.
    Devuelve el número de filas efectivamente insertadas (no ignoradas).
    """
    if df.empty:
        return 0

    url = _get_db_url(db_url)
    engine = _make_engine(url)
    staging = f"_staging_{table_name}"

    df = df.copy()
    if "subreddit" in df.columns:
        df["subreddit"] = df["subreddit"].str.lower()

    with engine.connect() as conn:
        with conn.begin():
            count_before = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()

            df.to_sql(staging, conn, if_exists="replace", index=False)

            cols = ", ".join(df.columns.tolist())
            conn.execute(text(f"INSERT OR IGNORE INTO {table_name} ({cols}) SELECT {cols} FROM {staging}"))

            conn.execute(text(f"DROP TABLE IF EXISTS {staging}"))

            count_after = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()

    return count_after - count_before


def load_from_db(table_name: str, db_url: str | None = None) -> pd.DataFrame:
    """Carga toda la tabla como DataFrame. Devuelve DataFrame vacío si no hay filas."""
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    with engine.connect() as conn:
        return pd.read_sql(f"SELECT * FROM {table_name}", conn)  # noqa: S608


def db_stats(db_url: str | None = None) -> dict:
    """Devuelve conteos de reddit_posts y reddit_comments. Devuelve 0 si la tabla no existe."""
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    result = {}
    with engine.connect() as conn:
        for table in ("reddit_posts", "reddit_comments"):
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                result[table] = count
            except Exception:
                result[table] = 0

    return result


def persist_run_to_db(
    run_data: dict,
    opportunities: list[dict],
    db_url: str | None = None,
) -> int:
    """Inserta un analysis_run y sus oportunidades asociadas.

    Para cada oportunidad, llama a find_canonical contra las opps existentes
    para detectar duplicados entre runs. Si hay match, usa ese canonical_id.
    Si no hay match, inserta la opp y hace UPDATE autorreferencial
    (canonical_id = id).

    Las opps existentes se cargan UNA vez antes del loop (no por cada opp)
    para minimizar queries a la BD.

    Devuelve el run_id asignado por AUTOINCREMENT.
    """
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    run_fields = [
        "run_at", "posts_analyzed", "valid_extractions", "opportunities_count",
        "json_path", "status", "duration_sec", "error_message", "ai_provider",
    ]

    with engine.connect() as conn:
        with conn.begin():
            run_cols = [f for f in run_fields if f in run_data]
            placeholders = ", ".join(f":{c}" for c in run_cols)
            col_list = ", ".join(run_cols)
            conn.execute(
                text(f"INSERT INTO analysis_runs ({col_list}) VALUES ({placeholders})"),
                {c: run_data.get(c) for c in run_cols},
            )
            run_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

            # Cargar existing UNA vez antes del loop: campos necesarios para
            # find_canonical (v1: evidence_quotes) y find_canonical_v2
            # (v2: product_name, core_problem, niche). Cargarlas dentro del
            # loop haría N queries.
            existing_rows = [
                {
                    "id": r[0],
                    "canonical_id": r[1],
                    "product_name": r[2] or "",
                    "evidence_quotes": r[3],
                    "core_problem": r[4] or "",
                    "niche": r[5] or "",
                }
                for r in conn.execute(
                    text(
                        "SELECT id, canonical_id, product_name, evidence_quotes, "
                        "core_problem, niche FROM opportunities"
                    )
                ).fetchall()
            ]

            opp_fields = [
                "product_name", "niche", "core_problem", "why_gap_exists",
                "concrete_workaround", "workaround_cost", "mvp_scope",
                "estimated_price", "monetization", "competitor_gap",
                "mentioned_competitors", "payment_signal", "payment_evidence",
                "solo_buildable", "mvp_weeks", "priority_score", "priority_reason",
                "evidence_items", "evidence_quotes", "reviewed", "starred",
                "discarded", "user_notes", "created_at", "canonical_id",
            ]

            # Flags humanos con DEFAULT 0 en el schema. Como el INSERT
            # parametrizado incluye estas columnas, un None se convertiría en
            # NULL explícito y el DEFAULT no aplicaría (SQLite solo lo usa si
            # la columna no aparece en el INSERT). Con NULL, el filtro
            # `discarded = 0` de load_active_opportunities no matchea.
            flag_defaults = {"reviewed": 0, "starred": 0, "discarded": 0}

            from saas_radar.config import ENABLE_DEDUP_V2

            for opp in opportunities:
                if ENABLE_DEDUP_V2 == "1":
                    canonical = find_canonical_v2(opp, existing_rows)
                else:
                    canonical = find_canonical(opp, existing_rows, threshold=0.3)

                opp_row = {f: opp.get(f) for f in opp_fields}
                for flag, default in flag_defaults.items():
                    if opp_row[flag] is None:
                        opp_row[flag] = default
                opp_row["run_id"] = run_id
                if canonical is not None:
                    opp_row["canonical_id"] = canonical

                all_cols = ["run_id"] + opp_fields
                col_str = ", ".join(all_cols)
                ph_str = ", ".join(f":{c}" for c in all_cols)
                params = {c: opp_row.get(c) if c != "run_id" else run_id for c in all_cols}

                conn.execute(text(f"INSERT INTO opportunities ({col_str}) VALUES ({ph_str})"), params)
                opp_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

                if canonical is None:
                    # Nueva canónica: autoreferencia tras INSERT para que
                    # load_active_opportunities (WHERE id = canonical_id) la encuentre.
                    conn.execute(
                        text("UPDATE opportunities SET canonical_id = :oid WHERE id = :oid"),
                        {"oid": opp_id},
                    )
                    # Añadir al pool de existing para que las opps restantes
                    # del mismo run también puedan matchear contra ésta.
                    existing_rows.append({
                        "id": opp_id,
                        "canonical_id": opp_id,
                        "product_name": opp.get("product_name") or "",
                        "evidence_quotes": opp.get("evidence_quotes"),
                        "core_problem": opp.get("core_problem") or "",
                        "niche": opp.get("niche") or "",
                    })

    return run_id


def load_active_opportunities(db_url: str | None = None) -> pd.DataFrame:
    """Carga oportunidades activas: id == canonical_id AND discarded = 0."""
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    with engine.connect() as conn:
        return pd.read_sql(
            "SELECT * FROM opportunities WHERE id = canonical_id AND discarded = 0",
            conn,
        )


def persist_meta_recommendations(
    run_id: int,
    recs: list[dict],
    db_url: str | None = None,
) -> None:
    """Persiste recomendaciones de meta-análisis con dedup por (type, target).

    Si ya existe una fila con el mismo (type, target) y acted = 0,
    incrementa recurrence en lugar de insertar una nueva.
    """
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    with engine.connect() as conn:
        with conn.begin():
            for rec in recs:
                rec_type = rec.get("type", "")
                action = rec.get("action", "")
                target = _extract_target(rec_type, action)

                existing = conn.execute(
                    text(
                        "SELECT id FROM meta_recommendations "
                        "WHERE type = :t AND target = :tg AND acted = 0"
                    ),
                    {"t": rec_type, "tg": target},
                ).fetchone()

                if existing:
                    conn.execute(
                        text("UPDATE meta_recommendations SET recurrence = recurrence + 1 WHERE id = :id"),
                        {"id": existing[0]},
                    )
                else:
                    conn.execute(
                        text(
                            "INSERT INTO meta_recommendations "
                            "(run_id, type, action, target, recurrence, acted, created_at) "
                            "VALUES (:run_id, :type, :action, :target, 1, 0, :created_at)"
                        ),
                        {
                            "run_id": run_id,
                            "type": rec_type,
                            "action": action,
                            "target": target,
                            "created_at": datetime.utcnow().isoformat(),
                        },
                    )


def has_successful_run(db_url: str | None = None) -> bool:
    """Devuelve True si existe al menos un analysis_run con status = 'ok'."""
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    try:
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM analysis_runs WHERE status = 'ok'")
            ).scalar()
            return count > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# GTM — persist_gtm, load_gtm, has_gtm
# ---------------------------------------------------------------------------

# Campos que se serializan como JSON TEXT en opportunity_gtm.
# El resto de columnas son INTEGER o TEXT planos.
_GTM_JSON_FIELDS = {"pricing_tiers", "acquisition_channels", "validation_plan_7d", "pivot_signals", "kpis"}

# Todas las columnas de opportunity_gtm que el caller puede pasar en payload.
# opportunity_id, id y created_at se manejan aparte; no van en este set.
_GTM_PAYLOAD_FIELDS = [
    "viability_desperation",
    "viability_build_ease",
    "viability_scalability",
    "viability_total",
    "elevator_pitch",
    "pricing_tiers",
    "acquisition_channels",
    "cold_outreach_script",
    "organic_post_template",
    "validation_plan_7d",
    "pivot_signals",
    "kpis",
    "gtm_status",
    "user_notes",
]


def persist_gtm(opportunity_id: int, payload: dict, db_url: str | None = None) -> int:
    """Inserta una fila en opportunity_gtm para la oportunidad dada.

    Serializa automáticamente los 5 campos JSON: pricing_tiers,
    acquisition_channels, validation_plan_7d, pivot_signals, kpis.
    El resto de campos (TEXT o INTEGER) se insertan tal cual.

    Devuelve el id asignado por AUTOINCREMENT.

    Nota de idempotencia: esta función hace INSERT directo. Si la fila ya
    existe (UNIQUE constraint en opportunity_id), la BD lanzará un error.
    El caller debe gestionar esto con has_gtm() antes de llamar, o hacer
    DELETE antes si usa --force.
    """
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    # Serializar campos JSON: si el valor ya es string (p.ej. LLM devolvió
    # string), json.dumps lo convierte a string JSON válido. Si es lista/dict,
    # lo serializa. Si es None, lo deja como None (NULL en BD).
    row: dict = {"opportunity_id": opportunity_id, "created_at": datetime.utcnow().isoformat()}
    for field in _GTM_PAYLOAD_FIELDS:
        value = payload.get(field)
        if field in _GTM_JSON_FIELDS and value is not None:
            # json.dumps convierte list/dict → string JSON; si ya es string,
            # lo envuelve entre comillas (doble serialización). Para evitarlo,
            # solo serializar si no es ya un string.
            row[field] = value if isinstance(value, str) else json.dumps(value)
        else:
            row[field] = value

    all_cols = list(row.keys())
    col_str = ", ".join(all_cols)
    ph_str = ", ".join(f":{c}" for c in all_cols)

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"INSERT INTO opportunity_gtm ({col_str}) VALUES ({ph_str})"), row)
            gtm_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    return int(gtm_id)


def load_gtm(opportunity_id: int, db_url: str | None = None) -> dict | None:
    """Carga la fila opportunity_gtm para la opp dada.

    Parsea los 5 campos JSON con tolerancia a corrupción: si json.loads falla,
    deja el valor como string en lugar de propagar el error.

    Devuelve None si no existe fila para opportunity_id.
    """
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM opportunity_gtm WHERE opportunity_id = :oid"),
            {"oid": opportunity_id},
        ).fetchone()

    if row is None:
        return None

    # fetchone() devuelve una Row de SQLAlchemy; _mapping convierte a dict-like.
    result = dict(row._mapping)

    # Parsear los campos JSON con tolerancia a corrupción.
    for field in _GTM_JSON_FIELDS:
        raw = result.get(field)
        if raw is not None and isinstance(raw, str):
            try:
                result[field] = json.loads(raw)
            except json.JSONDecodeError:
                # Corrupción de datos: dejar el valor como string en lugar de
                # propagar el error. El caller puede detectar que no es lista/dict
                # si lo necesita.
                logger.warning("load_gtm: json.loads falló en campo %s para opp %d", field, opportunity_id)

    return result


def has_gtm(opportunity_id: int, db_url: str | None = None) -> bool:
    """True si existe fila en opportunity_gtm para opportunity_id."""
    url = _get_db_url(db_url)
    engine = _make_engine(url)

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM opportunity_gtm WHERE opportunity_id = :oid"),
            {"oid": opportunity_id},
        ).scalar()

    return count > 0
