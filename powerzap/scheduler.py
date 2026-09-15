"""Serviço de envio em background do PowerZap."""
import argparse
import logging
import os
import sys
import time

from powerzap import db
from powerzap.evolution import EvolutionAPI, EvolutionError, detect_media_type

log = logging.getLogger("powerzap.scheduler")


def _heartbeat():
    try:
        db.set_setting("scheduler_last_run", db._now())
    except Exception:
        pass


def _send_one(api: EvolutionAPI, msg: dict):
    media_path = msg.get("media_path")
    if media_path:
        if not os.path.exists(media_path):
            raise EvolutionError(f"Arquivo anexado não encontrado: {media_path}")
        size = os.path.getsize(media_path)
        if size > db.MAX_MEDIA_BYTES:
            raise EvolutionError(
                f"Anexo com {size // (1024*1024)}MB excede o limite de 16MB do WhatsApp."
            )
        caption = msg.get("caption") or msg.get("text") or ""
        api.send_media(
            msg["number"], media_path,
            mediatype=detect_media_type(media_path, msg.get("media_type")),
            caption=caption,
            mimetype=msg.get("mimetype"),
        )
    else:
        if not (msg.get("text") or "").strip():
            raise EvolutionError("Mensagem sem texto e sem anexo.")
        api.send_text(msg["number"], msg["text"])


def process_due_messages(api: EvolutionAPI) -> int:
    now = db._now()
    due = db.list_pending(now)
    if not due:
        return 0
    if not api.is_connected():
        log.warning(
            "%d mensagem(ns) aguardando, mas a instância '%s' não está conectada. "
            "Tentativas serão mantidas como pendentes.",
            len(due), api.instance,
        )
        return 0
    sent = 0
    for msg in due:
        try:
            _send_one(api, msg)
            db.mark_sent(msg["id"])
            try:
                db.schedule_next_occurrence(msg)
            except Exception as ex:
                log.warning("Falha ao agendar recorrência de %s: %s", msg["id"], ex)
            log.info("Mensagem %s enviada para %s", msg["id"], msg["number"])
            sent += 1
        except EvolutionError as ex:
            db.mark_failed(msg["id"], str(ex))
            log.error("Falha ao enviar mensagem %s: %s", msg["id"], ex)
    return sent


def run(interval: float):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("PowerZap scheduler iniciado (intervalo %.0fs)", interval)
    while True:
        try:
            settings = db.get_settings()
            if not settings["api_key"]:
                log.warning("API Key não configurada. Abra o PowerZap e configure em Ajustes.")
            else:
                api = EvolutionAPI(
                    settings["evolution_url"], settings["api_key"], settings["instance"]
                )
                n = process_due_messages(api)
                if n:
                    log.info("%d mensagem(ns) processada(s)", n)
            _heartbeat()
        except Exception as e:
            log.exception("Erro no ciclo do scheduler: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PowerZap - serviço de envio")
    parser.add_argument("--interval", type=float, default=20.0,
                        help="intervalo entre ciclos em segundos")
    args = parser.parse_args()
    try:
        run(args.interval)
    except KeyboardInterrupt:
        sys.exit(0)
