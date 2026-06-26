"""
Funzioni condivise tra handlers.
Evita import circolari tenendo qui la logica comune.
"""
import logging
import teams as tm
import database as db
import utils

logger = logging.getLogger(__name__)


def teams_map() -> dict:
    return {t["id"]: t["nome"] for t in tm.get_all_teams()}


async def aggiorna_canale(context, asta_id: int):
    """Riedita il messaggio nel canale con i dati aggiornati."""
    asta = db.get_asta(asta_id)
    if not asta or not asta["canale_msg_id"]:
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    offerte = db.get_offerte(asta_id)
    testo = utils.build_canale_message(asta, offerte, teams_map())
    channel_id = utils.get_channel_id()

    keyboard = None
    if asta["stato"] == "APERTA":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🏀 Offri",
                url=f"https://t.me/{context.bot.username}?start=offri_{asta_id}"
            ),
            InlineKeyboardButton("🔔 Segui", callback_data=f"watch:{asta_id}"),
        ]])

    try:
        await context.bot.edit_message_text(
            chat_id=channel_id,
            message_id=asta["canale_msg_id"],
            text=testo,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.warning("edit_message_text fallita asta_id=%d: %s", asta_id, e)


async def notifica_watchers(context, asta_id: int, testo: str, escludi_gm: int = None):
    """Manda notifica push a tutti i watcher dell'asta."""
    watchers = db.get_watchers(asta_id)
    for gm_id in watchers:
        if gm_id == escludi_gm:
            continue
        try:
            await context.bot.send_message(chat_id=gm_id, text=testo, parse_mode="HTML")
        except Exception as e:
            logger.warning("notifica watcher %d fallita: %s", gm_id, e)


async def notifica_admin_group(context, testo: str):
    admin_group_id = utils.get_admin_group_id()
    if not admin_group_id:
        return
    try:
        await context.bot.send_message(
            chat_id=admin_group_id, text=testo, parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("notifica admin group fallita: %s", e)
