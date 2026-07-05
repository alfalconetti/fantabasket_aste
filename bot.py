import logging
import os
import signal
import traceback
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

import database as db
import utils
import settings
from handlers.admin   import get_handlers as admin_handlers
from handlers.offerte import get_handlers as offerte_handlers
from handlers.firma   import get_handlers as firma_handlers
from handlers.user    import get_handlers as user_handlers
from handlers.dev     import get_handlers as dev_handlers
from scheduler        import check_scadenze, ping_healthcheck, backup_giornaliero, backup_settimanale, check_cap_stagionale

BOT_VERSION = "v39"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

import log_buffer as _log_buffer_mod
_log_buffer_mod.install()

logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]


def _ora() -> str:
    return utils.format_dt(datetime.now(timezone.utc).isoformat())


async def _send_log(bot, testo: str):
    """Invia messaggio al canale log se configurato."""
    log_channel_id = utils.get_log_channel_id()
    if log_channel_id:
        try:
            await bot.send_message(
                chat_id=log_channel_id,
                text=f"🕐 {_ora()}\n{testo}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Impossibile inviare al canale log: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    from telegram.error import TimedOut, NetworkError

    user_info = ""
    if update and hasattr(update, "effective_user") and update.effective_user:
        u = update.effective_user
        user_info = f"👤 {u.first_name} (@{u.username or '?'}) · ID: {u.id}\n"

    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning("Errore di rete: %s", context.error)
        testo_log = f"⚠️ <b>Errore di rete</b>\n{user_info}<code>{str(context.error)}</code>"
        await _send_log(context.bot, testo_log)
        return

    # "Message is not modified" è innocuo ma vale la pena saperlo
    if "Message is not modified" in str(context.error):
        logger.debug("Message is not modified: %s", context.error)
        await _send_log(context.bot, f"ℹ️ Messaggio canale già aggiornato (nessun danno)\n{user_info}")
        return

    logger.error("Eccezione non gestita:", exc_info=context.error)
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))
    testo = (
        f"❌ <b>Errore non gestito</b>\n"
        f"{user_info}\n"
        f"<code>{str(context.error)}</code>\n\n"
        f"<pre>{tb[-2000:]}</pre>"
    )
    await _send_log(context.bot, testo)
    dev_id = utils.load_globals().get("dev_id")
    if dev_id:
        try:
            await context.bot.send_message(chat_id=dev_id, text=testo, parse_mode="HTML")
        except Exception as e:
            logger.warning("Impossibile inviare errore al dev: %s", e)


async def recupera_stati_pendenti(context: ContextTypes.DEFAULT_TYPE):
    """
    Eseguito all'avvio: recupera aste in stato CHIUSA o PAREGGIO
    e ripristina i flussi interrotti da un eventuale restart.
    Rischedulia firma_automatica e pareggio_automatico con il tempo residuo
    corretto — non dall'inizio, ma da quanto manca alla scadenza originale.
    """
    from handlers.firma import (chiedi_anni, _chiedi_pareggio,
                                _chiedi_firma_proprietario_senza_offerte,
                                firma_automatica, pareggio_automatico)
    from handlers.helpers import aggiorna_canale, log_job_error

    try:
        ora = utils.format_dt(datetime.now(timezone.utc).isoformat())
        await _send_log(context.bot, f"🔄 Bot riavviato ({BOT_VERSION}) — {ora}")

        now = datetime.now(timezone.utc)

        chiuse = db.get_aste_chiuse()
        for asta in chiuse:
            asta_id = asta["id"]
            logger.info("Recupero asta CHIUSA id=%d giocatore=%s", asta_id, asta["giocatore"])

            if asta["offerente_team_id"]:
                await chiedi_anni(context, asta_id, schedula_job=False)

                # Rischedulia firma_automatica con tempo residuo
                conclusa_at = datetime.fromisoformat(asta["conclusa_at"])
                is_rfa = asta["tipo"] == "RFA"
                timeout_h = settings.timeout_firma_rfa_ore() if is_rfa else settings.timeout_firma_fa_ore()
                scadenza_firma = conclusa_at + timedelta(hours=timeout_h)
                residuo = (scadenza_firma - now).total_seconds()

                if residuo > 0:
                    context.job_queue.run_once(
                        firma_automatica,
                        when=residuo,
                        data={"asta_id": asta_id},
                        name=f"firma_auto_{asta_id}",
                    )
                    logger.info("Rischedulato firma_automatica: asta_id=%d residuo=%.0fs", asta_id, residuo)
                else:
                    # Timeout già scaduto durante il downtime — esegui subito
                    logger.warning("firma_automatica scaduta durante downtime: asta_id=%d — eseguo subito", asta_id)
                    context.job_queue.run_once(
                        firma_automatica,
                        when=5,
                        data={"asta_id": asta_id},
                        name=f"firma_auto_{asta_id}",
                    )
            else:
                await _chiedi_firma_proprietario_senza_offerte(context, asta_id)

            await aggiorna_canale(context, asta_id)

        in_pareggio = db.get_aste_in_pareggio()
        for asta in in_pareggio:
            asta_id = asta["id"]
            if not asta["anni_offerti"]:
                continue
            logger.info("Recupero asta PAREGGIO id=%d giocatore=%s", asta_id, asta["giocatore"])
            await _chiedi_pareggio(context, asta_id, asta["anni_offerti"], schedula_job=False)
            await aggiorna_canale(context, asta_id)

            # Rischedulia pareggio_automatico con tempo residuo
            conclusa_at = datetime.fromisoformat(asta["conclusa_at"])
            scadenza_pareggio = conclusa_at + timedelta(hours=settings.timeout_pareggio_ore())
            residuo = (scadenza_pareggio - now).total_seconds()

            if residuo > 0:
                context.job_queue.run_once(
                    pareggio_automatico,
                    when=residuo,
                    data={"asta_id": asta_id},
                    name=f"pareggio_auto_{asta_id}",
                )
                logger.info("Rischedulato pareggio_automatico: asta_id=%d residuo=%.0fs", asta_id, residuo)
            else:
                logger.warning("pareggio_automatico scaduto durante downtime: asta_id=%d — eseguo subito", asta_id)
                context.job_queue.run_once(
                    pareggio_automatico,
                    when=5,
                    data={"asta_id": asta_id},
                    name=f"pareggio_auto_{asta_id}",
                )

        if chiuse or in_pareggio:
            msg = f"🔄 Recuperate {len(chiuse)} aste CHIUSE e {len(in_pareggio)} in PAREGGIO."
            logger.info(msg)
            await _send_log(context.bot, msg)
    except Exception as e:
        await log_job_error(context, "recupera_stati_pendenti", e)


async def cmd_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Solo dev_id può riavviare il bot."""
    dev_id = utils.load_globals().get("dev_id")
    if not dev_id or update.effective_user.id != dev_id:
        return
    await update.message.reply_text("🔄 Riavvio in corso...")
    logger.info("Reboot richiesto da dev %d", update.effective_user.id)
    os.kill(os.getpid(), signal.SIGTERM)


def main():
    db.init_db()
    logger.info("DB inizializzato.")

    app = ApplicationBuilder().token(TOKEN).build()

    for h in admin_handlers():
        app.add_handler(h)
    for h in offerte_handlers():
        app.add_handler(h)
    for h in firma_handlers():
        app.add_handler(h)
    for h in user_handlers():
        app.add_handler(h)
    for h in dev_handlers():
        app.add_handler(h)

    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_error_handler(error_handler)
    app.job_queue.run_repeating(check_scadenze, interval=60, first=10)
    app.job_queue.run_once(recupera_stati_pendenti, when=5)

    # Healthcheck ogni 5 minuti
    if os.environ.get("HEALTHCHECK_URL"):
        app.job_queue.run_repeating(ping_healthcheck, interval=300, first=30)

    # Backup giornaliero: mezzogiorno e mezzanotte (ora di Roma)
    from datetime import time as dtime
    from zoneinfo import ZoneInfo
    rome = ZoneInfo("Europe/Rome")
    app.job_queue.run_daily(backup_giornaliero, time=dtime(12, 0, tzinfo=rome))
    app.job_queue.run_daily(backup_giornaliero, time=dtime(0, 0, tzinfo=rome))

    # Backup settimanale: domenica mezzanotte
    app.job_queue.run_daily(
        backup_settimanale,
        time=dtime(0, 30, tzinfo=rome),
        days=(6,),  # domenica
    )

    # Check cap stagionale: ogni giorno alle 13:00
    app.job_queue.run_daily(check_cap_stagionale, time=dtime(13, 0, tzinfo=rome))

    # Validazione numero_teams vs teams.json all'avvio
    import teams as _tm_check
    n_teams_settings = settings.numero_teams()
    n_teams_reali = len(_tm_check.get_all_teams())
    if n_teams_settings != n_teams_reali:
        logger.warning(
            "⚠️ numero_teams in settings.json (%d) non corrisponde a teams.json (%d)",
            n_teams_settings, n_teams_reali
        )

    logger.info("Bot avviato.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
