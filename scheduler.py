"""
Job periodico che controlla le aste scadute e le chiude,
e invia notifiche prima della scadenza ai watcher.
Include healthcheck ping e backup periodico.
"""
import io
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone

import database as db
import utils
import settings
from handlers.firma import chiedi_anni

logger = logging.getLogger(__name__)

HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")


async def check_scadenze(context):
    try:
        now = datetime.now(timezone.utc)
        minuti = settings.notifica_minuti_scadenza()
        soglia = now + timedelta(minutes=minuti)
        aste = db.get_aste_aperte()

        for asta in aste:
            scade_at = datetime.fromisoformat(asta["scade_at"])

            if now >= scade_at:
                logger.info("Chiusura asta id=%d giocatore=%s", asta["id"], asta["giocatore"])
                db.chiudi_asta(asta["id"], now.isoformat())

                if asta["offerente_team_id"]:
                    from handlers.helpers import aggiorna_canale
                    await aggiorna_canale(context, asta["id"])
                    await chiedi_anni(context, asta["id"])
                else:
                    db.concludi_asta(asta["id"])
                    from handlers.helpers import aggiorna_canale
                    await aggiorna_canale(context, asta["id"])
                    logger.info("Asta id=%d chiusa senza offerte", asta["id"])
                continue

            if now < scade_at <= soglia:
                if not db.notifica_15min_inviata(asta["id"]):
                    db.segna_notifica_15min(asta["id"])
                    watchers = db.get_watchers(asta["id"])
                    if watchers:
                        import teams as tm
                        teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
                        vincitore = teams_map.get(asta["offerente_team_id"], "—") if asta["offerente_team_id"] else "nessuno"
                        testo = (
                            f"⏰ <b>{minuti} minuti alla scadenza!</b>\n"
                            f"🏀 <b>{asta['giocatore']}</b>\n"
                            f"Offerta attuale: <b>{asta['offerta_corrente']}M — {vincitore}</b>"
                        )
                        for gm_id in watchers:
                            try:
                                await context.bot.send_message(chat_id=gm_id, text=testo, parse_mode="HTML")
                            except Exception as e:
                                logger.warning("notifica scadenza a %d fallita: %s", gm_id, e)
                        logger.info("Notifica scadenza inviata per asta id=%d a %d watcher", asta["id"], len(watchers))
    except Exception as e:
        from handlers.helpers import log_job_error
        await log_job_error(context, "check_scadenze", e)


async def ping_healthcheck(context):
    """Pinga healthchecks.io ogni 5 minuti per segnalare che il bot è vivo."""
    if not HEALTHCHECK_URL:
        return
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            await session.get(HEALTHCHECK_URL, timeout=aiohttp.ClientTimeout(total=5))
        logger.debug("Healthcheck ping OK")
    except Exception as e:
        logger.warning("Healthcheck ping fallito: %s", e)


def _crea_backup_zip() -> bytes:
    """Crea uno zip in memoria con DB + config."""
    db_path = os.environ.get("DB_PATH", "/data/aste.db")
    config_dir = os.path.dirname(os.environ.get("GLOBALS_PATH", "/config/globals.json"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(db_path):
            zf.write(db_path, "data/aste.db")
        for fname in ["globals.json", "teams.json", "settings.json", "fa_players.csv"]:
            fpath = os.path.join(config_dir, fname)
            if os.path.exists(fpath):
                zf.write(fpath, f"config/{fname}")
    return buf.getvalue()


async def invia_backup(context, chat_id: int, label: str):
    """Invia il backup zip a una chat specifica."""
    try:
        data = _crea_backup_zip()
        now = datetime.now(utils.ROME)
        filename = f"backup_aste_{now.strftime('%Y%m%d_%H%M')}.zip"
        caption = f"💾 <b>Backup {label}</b> — {now.strftime('%d/%m/%Y %H:%M')}"
        await context.bot.send_document(
            chat_id=chat_id,
            document=io.BytesIO(data),
            filename=filename,
            caption=caption,
            parse_mode="HTML",
        )
        logger.info("Backup inviato a chat_id=%d (%s)", chat_id, label)
    except Exception as e:
        logger.warning("Invio backup fallito a %d: %s", chat_id, e)


async def backup_giornaliero(context):
    """Invia backup 2 volte al giorno al canale log."""
    try:
        log_channel_id = utils.get_log_channel_id()
        if log_channel_id:
            await invia_backup(context, log_channel_id, "giornaliero")
    except Exception as e:
        from handlers.helpers import log_job_error
        await log_job_error(context, "backup_giornaliero", e)


async def backup_settimanale(context):
    """Invia backup settimanale al gruppo admin."""
    try:
        admin_group_id = utils.get_admin_group_id()
        if admin_group_id:
            await invia_backup(context, admin_group_id, "settimanale")
    except Exception as e:
        from handlers.helpers import log_job_error
        await log_job_error(context, "backup_settimanale", e)
