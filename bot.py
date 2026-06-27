import logging
import os
import traceback
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes

import database as db
import utils
import settings
from handlers.admin   import get_handlers as admin_handlers
from handlers.offerte import get_handlers as offerte_handlers
from handlers.firma   import get_handlers as firma_handlers
from handlers.user    import get_handlers as user_handlers
from scheduler        import check_scadenze

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]


async def _send_log(bot, testo: str):
    """Invia messaggio al canale log se configurato."""
    log_channel_id = utils.get_log_channel_id()
    if log_channel_id:
        try:
            await bot.send_message(chat_id=log_channel_id, text=testo, parse_mode="HTML")
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

    logger.error("Eccezione non gestita:", exc_info=context.error)
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))
    testo = (
        f"❌ <b>Errore non gestito</b>\n"
        f"{user_info}\n"
        f"<code>{str(context.error)}</code>\n\n"
        f"<pre>{tb[-2000:]}</pre>"
    )
    # errori gravi: canale log + dev in privato
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
    """
    from handlers.firma import chiedi_anni, _chiedi_pareggio
    from handlers.helpers import aggiorna_canale

    # Aste CHIUSE: il vincitore non ha ancora scelto gli anni
    chiuse = db.get_aste_chiuse()
    for asta in chiuse:
        logger.info("Recupero asta CHIUSA id=%d giocatore=%s", asta["id"], asta["giocatore"])
        if asta["offerente_team_id"]:
            await chiedi_anni(context, asta["id"])
        else:
            # RFA senza offerte — contatta il proprietario
            from handlers.firma import _chiedi_firma_proprietario_senza_offerte
            await _chiedi_firma_proprietario_senza_offerte(context, asta["id"])
        await aggiorna_canale(context, asta["id"])

    # Aste in PAREGGIO: il proprietario non ha ancora risposto
    in_pareggio = db.get_aste_in_pareggio()
    for asta in in_pareggio:
        if asta["anni_offerti"]:
            logger.info("Recupero asta PAREGGIO id=%d giocatore=%s", asta["id"], asta["giocatore"])
            await _chiedi_pareggio(context, asta["id"], asta["anni_offerti"])
            await aggiorna_canale(context, asta["id"])

    if chiuse or in_pareggio:
        logger.info("Recupero completato: %d CHIUSE, %d PAREGGIO", len(chiuse), len(in_pareggio))


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

    app.add_error_handler(error_handler)
    app.job_queue.run_repeating(check_scadenze, interval=60, first=10)

    # Recupera stati pendenti 5 secondi dopo l'avvio
    app.job_queue.run_once(recupera_stati_pendenti, when=5)

    logger.info("Bot avviato.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
