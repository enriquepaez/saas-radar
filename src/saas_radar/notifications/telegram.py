"""Alertas de oportunidades vía Telegram: no-op silencioso sin credenciales."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def _get_config() -> tuple[str, str, int]:
    """Lee TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID y TELEGRAM_ALERT_THRESHOLD de env."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    threshold = int(os.getenv("TELEGRAM_ALERT_THRESHOLD", "8"))
    return token, chat_id, threshold


def send_opportunity_alert(opp: dict) -> bool:
    """Envía alerta de una oportunidad. Devuelve True si se envió."""
    token, chat_id, threshold = _get_config()
    if not token or not chat_id:
        return False

    score = opp.get("priority_score", 0)
    if score < threshold:
        return False

    name = opp.get("product_name", "?")
    niche = opp.get("niche", "?")
    problem = opp.get("core_problem", "?")
    price = opp.get("estimated_price", "?")
    payment = opp.get("payment_signal", "?")
    workaround = opp.get("concrete_workaround", "?")
    mvp_weeks = opp.get("mvp_weeks", "?")
    evidence = opp.get("evidence_items", [])

    text = (
        f"*Nueva oportunidad SaaS* (score {score}/10)\n\n"
        f"*{name}*\n"
        f"Nicho: {niche}\n"
        f"Problema: {problem}\n"
        f"Workaround: {workaround}\n"
        f"Precio: {price}\n"
        f"Pago: {payment}\n"
        f"MVP: {mvp_weeks} semanas\n"
        f"Evidencia: {len(evidence)} posts"
    )

    return _send_message(token, chat_id, text)


def send_run_summary(
    posts_analyzed: int,
    opportunities_count: int,
    duration_sec: int,
    mode: str,
) -> bool:
    """Envía resumen del run. No-op silencioso sin credenciales."""
    token, chat_id, _ = _get_config()
    if not token or not chat_id:
        return False

    minutes = duration_sec // 60
    seconds = duration_sec % 60
    text = (
        f"*Run completado* ({mode})\n\n"
        f"Posts analizados: {posts_analyzed}\n"
        f"Oportunidades: {opportunities_count}\n"
        f"Duración: {minutes}m {seconds}s"
    )

    return _send_message(token, chat_id, text)


def send_text(text: str) -> bool:
    """Envía un mensaje de texto plano a Telegram.

    No-op silencioso si faltan token o chat_id. Trunca a 4000 chars
    (límite Telegram = 4096) dejando margen para metadata del protocolo.
    """
    token, chat_id, _ = _get_config()
    if not token or not chat_id:
        return False

    if len(text) > 4000:
        text = text[:3980] + "\n... [truncado]"

    return _send_message(token, chat_id, text)


def send_tuner_report(report_path: str) -> bool:
    """Envía el dry-run report del tuning agent a Telegram como bloque de código.

    El report se envuelve en ``` para preservar el alineado de columnas del CLI.
    Trunca el cuerpo a 3900 chars dejando margen para el fence y el encabezado.
    """
    token, chat_id, _ = _get_config()
    if not token or not chat_id:
        return False

    try:
        with open(report_path, encoding="utf-8") as f:
            report = f.read()
    except OSError as exc:
        logger.warning("no se pudo leer %s: %s", report_path, exc)
        return False

    max_body = 3900
    if len(report) > max_body:
        report = report[: max_body - 20] + "\n... [truncado]"

    text = f"Tuner dry-run report\n```\n{report}\n```"
    return _send_message(token, chat_id, text)


def _send_message(token: str, chat_id: str, text: str) -> bool:
    """Hace POST a la API de Telegram. Devuelve True si la respuesta es 2xx."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        if not resp.is_success:
            logger.warning("Telegram error: %s - %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram error: %s", exc)
        return False


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="saas_radar.notifications.telegram")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tuner = sub.add_parser("tuner-report", help="Envía un report de tuner a Telegram.")
    p_tuner.add_argument("path", help="Ruta al fichero de report (texto plano).")

    p_send = sub.add_parser("send", help="Envía un mensaje de texto plano a Telegram.")
    p_send.add_argument("--text", required=True, help="Cuerpo del mensaje.")

    args = parser.parse_args()
    if args.cmd == "tuner-report":
        ok = send_tuner_report(args.path)
        sys.exit(0 if ok else 1)
    elif args.cmd == "send":
        ok = send_text(args.text)
        sys.exit(0 if ok else 1)
