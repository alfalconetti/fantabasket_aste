import logging
import os
import traceback

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes

import database as db
import utils
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Eccezione non gestita:", exc_info=context.error)
    dev_id = utils.load_globals().get("dev_id")
    if not dev_id:
        return
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))
    user_info = ""
    if update and hasattr(update, "effective_user") and update.effective_user:
        u = update.effective_user
        user_info = f"👤 {u.first_name} (@{u.username or '?'}) · ID: {u.id}\n"
    testo = (
        f"❌ <b>Errore non gestito</b>\n"
        f"{user_info}\n"
        f"<code>{str(context.error)}</code>\n\n"
        f"<pre>{tb[-2000:]}</pre>"
    )
    try:
        await context.bot.send_message(chat_id=dev_id, text=testo, parse_mode="HTML")
    except Exception as e:
        logger.warning("Impossibile inviare errore al dev: %s", e)


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

    logger.info("Bot avviato.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
