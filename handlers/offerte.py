"""
Flusso offerte:
  - Deep link /start offri_<asta_id>  → offerta diretta
  - /offri                             → lista paginata aste aperte
  - /nuova_fa <giocatore>             → apre asta FA + chiede prima offerta
"""
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters,
)

import database as db
import teams as tm
import utils
import settings
from handlers.helpers import aggiorna_canale, notifica_watchers, teams_map

logger = logging.getLogger(__name__)

# stati ConversationHandler
SCEGLI_ASTA, INSERISCI_IMPORTO, NUOVA_FA_IMPORTO, NUOVA_FA_CONFERMA, NUOVA_FA_OMONIMIA = range(5)


# ── helpers interni ───────────────────────────────────────────────────────────

def _aste_keyboard(aste: list, page: int) -> InlineKeyboardMarkup:
    page_size = settings.paginazione_aste()
    start = page * page_size
    pagina = aste[start: start + page_size]
    totale_pagine = max(1, -(-len(aste) // page_size))

    righe = []
    for asta in pagina:
        label = f"{'🔴' if asta['tipo']=='RFA' else '🟢'} {asta['giocatore']} — {asta['offerta_corrente']}M"
        righe.append([InlineKeyboardButton(label, callback_data=f"asta:{asta['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prec", callback_data=f"page:{page-1}"))
    if page < totale_pagine - 1:
        nav.append(InlineKeyboardButton("Succ ▶", callback_data=f"page:{page+1}"))
    if nav:
        righe.append(nav)

    righe.append([InlineKeyboardButton("❌ Annulla", callback_data="annulla")])
    return InlineKeyboardMarkup(righe)


async def _canale_keyboard(context, asta_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🏀 Offri",
            url=f"https://t.me/{context.bot.username}?start=offri_{asta_id}"
        ),
        InlineKeyboardButton("🔔 Segui", callback_data=f"watch:{asta_id}"),
    ]])


def _check_cap(team_id: str, importo: int) -> tuple[bool, int]:
    cap_impegnato = db.get_cap_virtuale(team_id)
    team = tm.get_team_by_id(team_id)
    if not team:
        return False, 0
    cap_libero = team["cap_disponibile"] - cap_impegnato
    return cap_libero >= importo, cap_libero


# ── /nuova_fa ─────────────────────────────────────────────────────────────────

async def nuova_fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text(
            "Uso: /nuova_fa <nome giocatore>\nEsempio: /nuova_fa Stephen Curry"
        )
        return ConversationHandler.END

    if not utils.is_mercato_aperto():
        await update.message.reply_text("🔒 Il mercato FA è attualmente chiuso.")
        return ConversationHandler.END

    giocatore_input = " ".join(context.args)
    nome_esatto, suggerimento, omonimi = utils.trova_giocatore_fa(giocatore_input)

    if nome_esatto:
        if db.giocatore_gia_in_asta(nome_esatto):
            await update.message.reply_text(f"❌ Esiste già un'asta aperta per <b>{nome_esatto}</b>.", parse_mode="HTML")
            return ConversationHandler.END
        context.user_data["nuova_fa_giocatore"] = nome_esatto
        context.user_data["nuova_fa_team"] = team
        await update.message.reply_text(
            f"🏀 Nuova asta FA per <b>{nome_esatto}</b>.\n\nQuanto offri? (minimo {settings.rilancio_minimo()}M)\n"
            f"<i>Per annullare: /annulla</i>",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    if omonimi:
        context.user_data["nuova_fa_team"] = team
        bottoni = [[InlineKeyboardButton(n, callback_data=f"fa_omonimo:{i}")]
                   for i, n in enumerate(omonimi)]
        bottoni.append([InlineKeyboardButton("❌ Annulla", callback_data="annulla")])
        context.user_data["nuova_fa_omonimi"] = omonimi
        await update.message.reply_text(
            "Trovati più giocatori con questo cognome. Scegli:",
            reply_markup=InlineKeyboardMarkup(bottoni),
        )
        return NUOVA_FA_OMONIMIA

    if suggerimento:
        context.user_data["nuova_fa_suggerimento"] = suggerimento
        context.user_data["nuova_fa_team"] = team
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Sì, {suggerimento}", callback_data="fa_conferma:si"),
            InlineKeyboardButton("❌ No", callback_data="fa_conferma:no"),
        ]])
        await update.message.reply_text(
            f"Intendevi <b>{suggerimento}</b>?", parse_mode="HTML", reply_markup=kb
        )
        return NUOVA_FA_CONFERMA

    await update.message.reply_text(
        f"❌ <b>{giocatore_input}</b> non trovato nella lista FA.", parse_mode="HTML"
    )
    return ConversationHandler.END


async def nuova_fa_omonimo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    omonimi = context.user_data.get("nuova_fa_omonimi", [])
    if idx >= len(omonimi):
        await query.edit_message_text("❌ Selezione non valida.")
        return ConversationHandler.END
    nome = omonimi[idx]
    if db.giocatore_gia_in_asta(nome):
        await query.edit_message_text(f"❌ Esiste già un'asta aperta per <b>{nome}</b>.", parse_mode="HTML")
        return ConversationHandler.END
    context.user_data["nuova_fa_giocatore"] = nome
    await query.edit_message_text(
        f"🏀 Nuova asta FA per <b>{nome}</b>.\n\nQuanto offri? (minimo {settings.rilancio_minimo()}M)\n"
        f"<i>Per annullare: /annulla</i>",
        parse_mode="HTML",
    )
    return NUOVA_FA_IMPORTO


async def nuova_fa_conferma_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    scelta = query.data.split(":")[1]

    if scelta == "no":
        await query.edit_message_text("Operazione annullata.")
        return ConversationHandler.END

    suggerimento = context.user_data.get("nuova_fa_suggerimento")
    team = context.user_data.get("nuova_fa_team")

    if not suggerimento or not team:
        await query.edit_message_text("❌ Sessione scaduta. Riprova con /nuova_fa.")
        return ConversationHandler.END

    if db.giocatore_gia_in_asta(suggerimento):
        await query.edit_message_text(f"❌ Esiste già un'asta aperta per <b>{suggerimento}</b>.", parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["nuova_fa_giocatore"] = suggerimento
    await query.edit_message_text(
        f"🏀 Nuova asta FA per <b>{suggerimento}</b>.\n\nQuanto offri? (minimo {settings.rilancio_minimo()}M)\n"
        f"<i>Per annullare: /annulla</i>",
        parse_mode="HTML",
    )
    return NUOVA_FA_IMPORTO


async def nuova_fa_importo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = context.user_data.get("nuova_fa_team") or tm.get_team_by_gm(user.id)
    giocatore = context.user_data.get("nuova_fa_giocatore")

    if not team or not giocatore:
        await update.message.reply_text("❌ Sessione scaduta. Riprova con /nuova_fa.")
        return ConversationHandler.END

    _ANNULLA_HINT = "\n<i>Per annullare: /annulla</i>"
    min_offerta = settings.rilancio_minimo()
    testo = update.message.text.strip()
    if not testo.isdigit() or int(testo) < min_offerta:
        await update.message.reply_text(
            f"❌ Inserisci un numero intero >= {min_offerta}.\n\nQuanto offri?{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    importo = int(testo)

    ok, cap_libero = _check_cap(team["id"], importo)
    if not ok:
        await update.message.reply_text(
            f"❌ Cap insufficiente. Cap libero: <b>{cap_libero}M</b>.\n\nInserisci un importo valido:{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    slot_impegnati = db.get_slot_virtuali(team["id"])
    if not tm.check_slot_virtuale(team["id"], slot_impegnati):
        await update.message.reply_text(
            f"❌ Nessuno slot libero: ne hai già {slot_impegnati} virtualmente impegnati.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    now = datetime.now(timezone.utc)
    scade = now + timedelta(hours=settings.durata_asta_ore())

    asta_id = db.crea_asta(
        tipo="FA",
        giocatore=giocatore,
        squadra_proprietaria=None,
        creata_at=now.isoformat(),
        scade_at=scade.isoformat(),
    )
    db.registra_offerta(asta_id, team["id"], importo, now.isoformat())
    db.aggiorna_offerta(asta_id, team["id"], importo, scade.isoformat())
    db.add_watch(asta_id, user.id)

    channel_id = utils.get_channel_id()
    asta_row = db.get_asta(asta_id)
    offerte = db.get_offerte(asta_id)
    testo_canale = utils.build_canale_message(asta_row, offerte, teams_map())
    keyboard = await _canale_keyboard(context, asta_id)

    msg = await context.bot.send_message(
        chat_id=channel_id, text=testo_canale, parse_mode="HTML", reply_markup=keyboard,
    )
    db.set_canale_msg_id(asta_id, msg.message_id)

    await update.message.reply_text(
        f"✅ Asta FA aperta per <b>{giocatore}</b> con offerta iniziale <b>{importo}M</b>.\n"
        f"Scade il {utils.format_dt(scade.isoformat())}.",
        parse_mode="HTML",
    )
    logger.info("Nuova FA: asta_id=%d giocatore=%s team=%s importo=%d", asta_id, giocatore, team["id"], importo)
    return ConversationHandler.END


# ── /offri ────────────────────────────────────────────────────────────────────

async def cmd_offri(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return ConversationHandler.END

    aste = db.get_aste_aperte()
    if not aste:
        await update.message.reply_text("Nessuna asta aperta al momento.")
        return ConversationHandler.END

    context.user_data["offri_page"] = 0
    context.user_data["offri_team"] = team
    await update.message.reply_text("Scegli l'asta:", reply_markup=_aste_keyboard(aste, 0))
    return SCEGLI_ASTA


async def pagina_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    context.user_data["offri_page"] = page
    aste = db.get_aste_aperte()
    await query.edit_message_reply_markup(reply_markup=_aste_keyboard(aste, page))
    return SCEGLI_ASTA


async def asta_scelta_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)

    if not asta or asta["stato"] != "APERTA":
        await query.edit_message_text("❌ Asta non più disponibile.")
        return ConversationHandler.END

    team = context.user_data.get("offri_team") or tm.get_team_by_gm(query.from_user.id)
    if team is None:
        await query.edit_message_text("⛔ Non sei registrato come GM.")
        return ConversationHandler.END

    context.user_data["asta_id"] = asta_id

    if asta["offerente_team_id"] == team["id"]:
        await query.edit_message_text("❌ Sei già il miglior offerente, non puoi auto-rilanciare.")
        return ConversationHandler.END

    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"] == team["id"]:
        await query.edit_message_text("❌ Detieni i diritti RFA: non puoi offrire.")
        return ConversationHandler.END

    min_offerta = asta["offerta_corrente"] + settings.rilancio_minimo()
    await query.edit_message_text(
        f"🏀 <b>{asta['giocatore']}</b>\n"
        f"Offerta attuale: <b>{asta['offerta_corrente']}M</b>\n\n"
        f"Scrivi il tuo importo (minimo <b>{min_offerta}M</b>):\n"
        f"<i>Per annullare: /annulla</i>",
        parse_mode="HTML",
    )
    return INSERISCI_IMPORTO


async def annulla_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Operazione annullata.")
    return ConversationHandler.END


async def _esegui_offerta(context, team, asta_id: int, importo: int, gm_id: int) -> str | None:
    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        return "L'asta non è più disponibile."

    if asta["offerente_team_id"] == team["id"]:
        return "Sei già il miglior offerente."

    min_offerta = asta["offerta_corrente"] + settings.rilancio_minimo()
    if importo < min_offerta:
        return f"Offerta troppo bassa. Minimo: {min_offerta}M."

    cap_impegnato = db.get_cap_virtuale(team["id"])
    team_data = tm.get_team_by_id(team["id"])
    cap_libero = team_data["cap_disponibile"] - cap_impegnato
    if cap_libero < importo:
        return f"Cap insufficiente. Cap libero: {cap_libero}M."

    slot_impegnati = db.get_slot_virtuali(team["id"])
    if not tm.check_slot_virtuale(team["id"], slot_impegnati):
        return f"Nessuno slot libero: ne hai già {slot_impegnati} virtualmente impegnati."

    now = datetime.now(timezone.utc)
    nuova_scadenza = (now + timedelta(hours=settings.durata_asta_ore())).isoformat()

    db.registra_offerta(asta_id, team["id"], importo, now.isoformat())
    db.aggiorna_offerta(asta_id, team["id"], importo, nuova_scadenza)
    db.add_watch(asta_id, gm_id)

    await aggiorna_canale(context, asta_id)

    team_nome = team["nome"]
    await notifica_watchers(
        context, asta_id,
        f"🔔 <b>{asta['giocatore']}</b>\nNuova offerta: <b>{importo}M — {team_nome}</b>\n"
        f"⏰ Scade: {utils.format_dt(nuova_scadenza)}",
        escludi_gm=gm_id,
    )

    # notifica proprietario RFA
    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"]:
        team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
        if team_prop:
            for gm_id_prop in team_prop["gm_ids"]:
                if gm_id_prop == gm_id:
                    continue
                try:
                    await context.bot.send_message(
                        chat_id=gm_id_prop,
                        text=(
                            f"⚠️ <b>Nuova offerta sul tuo RFA {asta['giocatore']}</b>\n"
                            f"💰 {importo}M — {team_nome}\n"
                            f"⏰ Scade: {utils.format_dt(nuova_scadenza)}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("notifica prop RFA %d: %s", gm_id_prop, e)

    logger.info("Offerta: asta_id=%d team=%s importo=%d", asta_id, team["id"], importo)
    return None


async def inserisci_importo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return ConversationHandler.END

    testo = update.message.text.strip()
    if not testo.isdigit():
        await update.message.reply_text("❌ Inserisci un numero intero (es. 15).")
        return INSERISCI_IMPORTO

    importo = int(testo)
    asta_id = context.user_data.get("asta_id")
    asta = db.get_asta(asta_id)
    if not asta:
        await update.message.reply_text("❌ Asta non trovata.")
        return ConversationHandler.END

    errore = await _esegui_offerta(context, team, asta_id, importo, user.id)
    if errore:
        rimani = "bassa" in errore or "insufficiente" in errore or "impegnati" in errore
        suffix = f"\n\nInserisci un nuovo importo:\n<i>Per annullare: /annulla</i>" if rimani else ""
        await update.message.reply_text(f"❌ {errore}{suffix}", parse_mode="HTML")
        return INSERISCI_IMPORTO if rimani else ConversationHandler.END

    asta_aggiornata = db.get_asta(asta_id)
    await update.message.reply_text(
        f"✅ Offerta di <b>{importo}M</b> per <b>{asta['giocatore']}</b> registrata!\n"
        f"Scade: {utils.format_dt(asta_aggiornata['scade_at'])}.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ── deep link /start offri_<asta_id> ─────────────────────────────────────────

async def start_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)

    if not context.args or not context.args[0].startswith("offri_"):
        if team:
            guida = (
                f"Ciao <b>{user.first_name}</b>! Benvenuto nel bot aste Fantabasket 🏀\n\n"
                f"<b>Comandi:</b>\n"
                f"/offri — fai un'offerta su un'asta aperta\n"
                f"/nuova_fa &lt;giocatore&gt; — apri un'asta FA\n"
                f"/lista_fa — lista giocatori FA disponibili (cliccabile)\n"
                f"/aste — aste in corso\n"
                f"/listteams — squadre e cap\n"
                f"/watched — aste che stai seguendo\n"
                f"/me — tua situazione cap e slot\n"
                f"/annulla — esci da qualsiasi operazione in corso\n\n"
                f"<b>Come funziona:</b>\n"
                f"Ogni offerta resetta il timer a 18 ore. Non puoi rilanciare su te stesso. "
                f"Il bot controlla cap e slot in tempo reale, tenendo conto di tutte le aste che stai vincendo.\n\n"
                f"Dal bottone 🔔 nel canale puoi seguire un'asta e ricevere notifiche ad ogni rilancio, "
                f"15 minuti prima della scadenza e alla firma.\n\n"
                f"<b>Quando vinci:</b>\n"
                f"Ricevi un messaggio privato per scegliere gli anni del contratto. "
                f"Hai 48 ore (24h per le RFA), poi vengono assegnati automaticamente."
            )
            await update.message.reply_text(guida, parse_mode="HTML")
        else:
            await update.message.reply_text("⛔ Non sei registrato come GM di nessuna squadra.")
        return ConversationHandler.END

    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM di nessuna squadra.")
        return ConversationHandler.END

    try:
        asta_id = int(context.args[0].split("_")[1])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Link non valido.")
        return ConversationHandler.END

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await update.message.reply_text("❌ Asta non disponibile o già chiusa.")
        return ConversationHandler.END

    if asta["offerente_team_id"] == team["id"]:
        await update.message.reply_text("❌ Sei già il miglior offerente su questa asta.")
        return ConversationHandler.END

    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"] == team["id"]:
        await update.message.reply_text("❌ Detieni i diritti RFA: non puoi offrire.")
        return ConversationHandler.END

    context.user_data["asta_id"] = asta_id
    context.user_data["offri_team"] = team

    min_offerta = asta["offerta_corrente"] + settings.rilancio_minimo()
    await update.message.reply_text(
        f"🏀 <b>{asta['giocatore']}</b>\n"
        f"Offerta attuale: <b>{asta['offerta_corrente']}M</b>\n\n"
        f"Scrivi il tuo importo (minimo <b>{min_offerta}M</b>):\n"
        f"<i>Per annullare: /annulla</i>",
        parse_mode="HTML",
    )
    return INSERISCI_IMPORTO


# ── handler registrations ─────────────────────────────────────────────────────

async def cmd_annulla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Operazione annullata.")
    return ConversationHandler.END


def get_handlers():
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("offri",    cmd_offri),
            CommandHandler("start",    start_deep_link),
            CommandHandler("nuova_fa", nuova_fa),
        ],
        states={
            SCEGLI_ASTA: [
                CallbackQueryHandler(pagina_callback,           pattern=r"^page:\d+$"),
                CallbackQueryHandler(asta_scelta_callback,      pattern=r"^asta:\d+$"),
                CallbackQueryHandler(annulla_callback,          pattern=r"^annulla$"),
            ],
            INSERISCI_IMPORTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, inserisci_importo),
            ],
            NUOVA_FA_IMPORTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, nuova_fa_importo),
            ],
            NUOVA_FA_CONFERMA: [
                CallbackQueryHandler(nuova_fa_conferma_callback, pattern=r"^fa_conferma:(si|no)$"),
            ],
            NUOVA_FA_OMONIMIA: [
                CallbackQueryHandler(nuova_fa_omonimo_callback,  pattern=r"^fa_omonimo:\d+$"),
                CallbackQueryHandler(annulla_callback,            pattern=r"^annulla$"),
            ],
        },
        fallbacks=[
            CommandHandler("annulla", cmd_annulla),
            CallbackQueryHandler(annulla_callback, pattern=r"^annulla$"),
        ],
        conversation_timeout=300,
        per_user=True,
        per_chat=True,
    )
    return [conv]
