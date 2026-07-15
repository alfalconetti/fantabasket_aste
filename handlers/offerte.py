"""
Flusso offerte:
  - Deep link /start offri_<asta_id>  → offerta diretta (da canale o da /offri)
  - /offri                             → lista paginata aste aperte con bottoni deep link
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
from handlers.helpers import aggiorna_canale, notifica_watchers, teams_map, log_warn

logger = logging.getLogger(__name__)

# stati ConversationHandler (SCEGLI_ASTA non più usato, mantenuto per compatibilità)
SCEGLI_ASTA, INSERISCI_IMPORTO, NUOVA_FA_IMPORTO, NUOVA_FA_CONFERMA, NUOVA_FA_OMONIMIA = range(5)


# ── helpers interni ───────────────────────────────────────────────────────────

def _aste_keyboard(aste: list, page: int, bot_username: str) -> InlineKeyboardMarkup:
    """
    Lista aste paginata con bottoni deep link per ogni asta.
    I bottoni di navigazione usano callback_data — sono globali e non scadono.
    I bottoni delle aste usano URL deep link — funzionano sempre, anche ore dopo.
    """
    page_size = settings.paginazione_aste()
    start = page * page_size
    pagina = aste[start: start + page_size]
    totale_pagine = max(1, -(-len(aste) // page_size))

    righe = []
    for asta in pagina:
        label = f"{'🔴' if asta['tipo']=='RFA' else '🟢'} {asta['giocatore']} — {asta['offerta_corrente']}M"
        url = f"https://t.me/{bot_username}?start=offri_{asta['id']}"
        righe.append([InlineKeyboardButton(label, url=url)])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prec", callback_data=f"offri_page:{page-1}"))
    if page < totale_pagine - 1:
        nav.append(InlineKeyboardButton("Succ ▶", callback_data=f"offri_page:{page+1}"))
    if nav:
        righe.append(nav)

    return InlineKeyboardMarkup(righe)


async def _canale_keyboard(context, asta_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🏀 Offri",
            url=f"https://t.me/{context.bot.username}?start=offri_{asta_id}"
        ),
        InlineKeyboardButton("🔔 Segui", callback_data=f"watch:{asta_id}"),
    ]])


def _cap_libero(team_id: str) -> int:
    """Restituisce il cap effettivamente libero (disponibile - virtuale impegnato)."""
    team = tm.get_team_by_id(team_id)
    if not team:
        return 0
    return team["cap_disponibile"] - db.get_cap_virtuale(team_id)


def _cap_info_lines(team: dict, cap_libero: int) -> str:
    """Righe cap da mostrare all'apertura di una nuova asta FA."""
    righe = [f"💰 Cap libero: <b>{cap_libero}M</b>"]
    fase = utils.load_globals().get("fase", "offseason")
    if fase == "offseason":
        s = settings.get()
        cap_pen = team.get("cap_penalizzato", 0)
        delta = s["cap_offseason"] - s["cap_regular"] + cap_pen
        cap_rs = cap_libero - delta
        nota_pen = f", penalità {cap_pen}M" if cap_pen else ""
        righe.append(f"📉 Cap in Regular Season: <b>{cap_rs}M</b> (-{delta}M{nota_pen})")
    return "\n".join(righe)


def _check_partecipazione(team: dict, asta: dict) -> str | None:
    """
    Controlla se il team può partecipare all'asta.
    Restituisce un messaggio di errore HTML se non può, None se può.
    Usato sia da /offri che dal deep link per uniformità.
    """
    if asta["offerente_team_id"] == team["id"]:
        return "❌ Sei già il miglior offerente su questa asta."

    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"] == team["id"]:
        return "❌ Detieni i diritti RFA: non puoi offrire."

    min_offerta = asta["offerta_corrente"] + settings.rilancio_minimo()
    cap = _cap_libero(team["id"])
    if cap < min_offerta:
        return (
            f"❌ Non hai cap sufficiente per fare un'offerta su <b>{asta['giocatore']}</b>.\n"
            f"Cap libero: <b>{cap}M</b> — minimo necessario: <b>{min_offerta}M</b>."
        )

    slot_imp = db.get_slot_virtuali(team["id"])
    if not tm.check_slot_virtuale(team["id"], slot_imp):
        return (
            f"❌ Non hai slot disponibili per fare un'offerta.\n"
            f"Hai già <b>{slot_imp}</b> slot virtualmente impegnati su <b>{team['slot_disponibili']}</b>."
        )

    return None


def _check_cap(team_id: str, importo: int) -> tuple[bool, int]:
    cap_impegnato = db.get_cap_virtuale(team_id)
    team = tm.get_team_by_id(team_id)
    if not team:
        return False, 0
    cap_libero = team["cap_disponibile"] - cap_impegnato
    return cap_libero >= importo, cap_libero


# ── /offri — lista aste (no ConversationHandler) ──────────────────────────────

async def cmd_offri(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra la lista delle aste aperte con bottoni deep link.
    Non è più un ConversationHandler — i bottoni deep link non scadono mai.
    """
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.effective_message.reply_text("⛔ Non sei registrato come GM.")
        return

    aste = db.get_aste_aperte()
    if not aste:
        await update.effective_message.reply_text("Nessuna asta aperta al momento.")
        return

    bot_username = context.bot.username
    await update.effective_message.reply_text(
        "Scegli l'asta su cui vuoi offrire:",
        reply_markup=_aste_keyboard(aste, 0, bot_username),
    )


async def offri_pagina_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler globale per la paginazione di /offri — non scade mai."""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    aste = db.get_aste_aperte()
    if not aste:
        await query.edit_message_text("Nessuna asta aperta al momento.")
        return
    bot_username = context.bot.username
    await query.edit_message_reply_markup(reply_markup=_aste_keyboard(aste, page, bot_username))


# ── /nuova_fa ─────────────────────────────────────────────────────────────────

async def nuova_fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    msg  = update.effective_message
    team = tm.get_team_by_gm(user.id)

    if team is None:
        await msg.reply_text("⛔ Non sei registrato come GM.")
        return ConversationHandler.END

    if not utils.is_mercato_aperto():
        await msg.reply_text("🔒 Il mercato FA è attualmente chiuso.")
        return ConversationHandler.END

    cap = _cap_libero(team["id"])
    if cap < settings.rilancio_minimo():
        await msg.reply_text(
            f"❌ Non hai cap sufficiente per aprire un'asta.\n"
            f"Cap libero: <b>{cap}M</b> — minimo richiesto: <b>{settings.rilancio_minimo()}M</b>.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    testo_grezzo = " ".join(context.args) if context.args else ""
    if not testo_grezzo:
        await msg.reply_text(
            "Uso: /nuova_fa <nome giocatore>\nEsempio: /nuova_fa LeBron James\n\n"
            "Puoi anche usare /lista_fa per scegliere direttamente.",
        )
        return ConversationHandler.END

    nome_esatto, suggerimento, omonimi = utils.trova_giocatore_fa(testo_grezzo)

    if nome_esatto:
        if db.giocatore_gia_in_asta(nome_esatto):
            await msg.reply_text(f"❌ Esiste già un'asta aperta per <b>{nome_esatto}</b>.", parse_mode="HTML")
            return ConversationHandler.END
        context.user_data["nuova_fa_giocatore"] = nome_esatto
        context.user_data["nuova_fa_team"] = team
        await msg.reply_text(
            f"🏀 Nuova asta FA per <b>{nome_esatto}</b>.\n\n"
            f"{_cap_info_lines(team, cap)}\n\n"
            f"Quanto offri? (minimo {settings.rilancio_minimo()}M)\n"
            f"<i>Per annullare: /annulla</i>",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    if omonimi and suggerimento and suggerimento.startswith("__cognome__"):
        # Fuzzy cognome con omonimi: prima chiede conferma del cognome, poi mostra lista
        cognome_originale = suggerimento.replace("__cognome__", "")
        context.user_data["nuova_fa_team"] = team
        context.user_data["nuova_fa_omonimi"] = omonimi
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Sì, {cognome_originale}", callback_data="fa_conferma:cognome"),
            InlineKeyboardButton("❌ No", callback_data="fa_conferma:no"),
        ]])
        await msg.reply_text(
            f"Intendevi il cognome <b>{cognome_originale}</b>?", parse_mode="HTML", reply_markup=kb
        )
        return NUOVA_FA_CONFERMA

    if omonimi:
        context.user_data["nuova_fa_team"] = team
        context.user_data["nuova_fa_omonimi"] = omonimi
        bottoni = [[InlineKeyboardButton(n, callback_data=f"fa_omonimo:{i}")]
                   for i, n in enumerate(omonimi)]
        bottoni.append([InlineKeyboardButton("❌ Annulla", callback_data="annulla")])
        await msg.reply_text(
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
        await msg.reply_text(
            f"Intendevi <b>{suggerimento}</b>?", parse_mode="HTML", reply_markup=kb
        )
        return NUOVA_FA_CONFERMA

    await msg.reply_text(
        f"❌ <b>{testo_grezzo}</b> non trovato nella lista FA.", parse_mode="HTML"
    )
    return ConversationHandler.END


async def nuova_fa_omonimo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    omonimi = context.user_data.get("nuova_fa_omonimi", [])
    idx = int(query.data.split(":")[1])
    if idx >= len(omonimi):
        await query.edit_message_text("❌ Sessione scaduta. Riprova.")
        return ConversationHandler.END
    nome = omonimi[idx]
    if db.giocatore_gia_in_asta(nome):
        await query.edit_message_text(f"❌ Esiste già un'asta aperta per <b>{nome}</b>.", parse_mode="HTML")
        return ConversationHandler.END
    team = context.user_data.get("nuova_fa_team") or tm.get_team_by_gm(query.from_user.id)
    cap = _cap_libero(team["id"]) if team else 0
    if cap < settings.rilancio_minimo():
        await query.edit_message_text(
            f"❌ Non hai cap sufficiente per aprire un'asta.\n"
            f"Cap libero: <b>{cap}M</b> — minimo richiesto: <b>{settings.rilancio_minimo()}M</b>.",
            parse_mode="HTML",
        )
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

    if scelta == "cognome":
        # Cognome confermato: mostra lista omonimi
        omonimi = context.user_data.get("nuova_fa_omonimi", [])
        if not omonimi:
            await query.edit_message_text("❌ Sessione scaduta. Riprova con /nuova_fa.")
            return ConversationHandler.END
        bottoni = [[InlineKeyboardButton(n, callback_data=f"fa_omonimo:{i}")]
                   for i, n in enumerate(omonimi)]
        bottoni.append([InlineKeyboardButton("❌ Annulla", callback_data="annulla")])
        await query.edit_message_text(
            "Trovati più giocatori con questo cognome. Scegli:",
            reply_markup=InlineKeyboardMarkup(bottoni),
        )
        return NUOVA_FA_OMONIMIA

    # scelta == "si" — suggerimento fuzzy confermato
    suggerimento = context.user_data.get("nuova_fa_suggerimento")
    team = context.user_data.get("nuova_fa_team")

    if not suggerimento or not team:
        await query.edit_message_text("❌ Sessione scaduta. Riprova con /nuova_fa.")
        return ConversationHandler.END

    if db.giocatore_gia_in_asta(suggerimento):
        await query.edit_message_text(f"❌ Esiste già un'asta aperta per <b>{suggerimento}</b>.", parse_mode="HTML")
        return ConversationHandler.END

    cap = _cap_libero(team["id"])
    if cap < settings.rilancio_minimo():
        await query.edit_message_text(
            f"❌ Non hai cap sufficiente per aprire un'asta.\n"
            f"Cap libero: <b>{cap}M</b> — minimo richiesto: <b>{settings.rilancio_minimo()}M</b>.",
            parse_mode="HTML",
        )
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
        await update.effective_message.reply_text("❌ Sessione scaduta. Riprova con /nuova_fa.")
        return ConversationHandler.END

    _ANNULLA_HINT = "\n<i>Per annullare: /annulla</i>"
    min_offerta = settings.rilancio_minimo()
    max_offerta = settings.offerta_massima()

    if not update.message or not update.message.text:
        await update.effective_message.reply_text(
            f"❌ Manda solo il numero dell'importo in milioni (es. 15).\n\nQuanto offri?{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    testo = update.message.text.strip()
    if not testo.isdigit():
        await update.effective_message.reply_text(
            f"❌ Inserisci un numero intero (es. 15).\n\nQuanto offri?{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    importo = int(testo)

    if importo < min_offerta:
        await update.effective_message.reply_text(
            f"❌ Offerta minima: {min_offerta}M.\n\nQuanto offri?{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    if importo > max_offerta:
        await update.effective_message.reply_text(
            f"❌ Offerta massima: {max_offerta}M.\n\nQuanto offri?{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    ok, cap_libero = _check_cap(team["id"], importo)
    if not ok:
        cap = _cap_libero(team["id"])
        if cap < min_offerta:
            await update.effective_message.reply_text(
                f"❌ Cap insufficiente. Cap libero: {cap_libero}M.\n\nNon hai cap sufficiente. Operazione annullata.",
            )
            return ConversationHandler.END
        await update.effective_message.reply_text(
            f"❌ Cap insufficiente. Cap libero: {cap_libero}M.\n\nInserisci un importo valido:{_ANNULLA_HINT}",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    slot_impegnati = db.get_slot_virtuali(team["id"])
    if not tm.check_slot_virtuale(team["id"], slot_impegnati):
        await update.effective_message.reply_text(
            f"❌ Nessuno slot libero: ne hai già {slot_impegnati} virtualmente impegnati.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    if db.giocatore_gia_in_asta(giocatore):
        await update.effective_message.reply_text(
            f"❌ Nel frattempo è stata aperta un'asta per <b>{giocatore}</b>.", parse_mode="HTML"
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
        stagione=utils.load_globals().get("stagione_corrente"),
    )
    db.registra_offerta(asta_id, team["id"], importo, now.isoformat())
    db.aggiorna_offerta(asta_id, team["id"], importo, scade.isoformat())
    db.add_watch(asta_id, user.id)

    # Pubblica il messaggio nel canale e salva il msg_id
    channel_id = utils.get_channel_id()
    asta_row = db.get_asta(asta_id)
    offerte = db.get_offerte(asta_id)
    testo_canale = utils.build_canale_message(asta_row, offerte, teams_map())
    keyboard = await _canale_keyboard(context, asta_id)
    msg = await context.bot.send_message(
        chat_id=channel_id, text=testo_canale, parse_mode="HTML", reply_markup=keyboard,
    )
    db.set_canale_msg_id(asta_id, msg.message_id)

    await update.effective_message.reply_text(
        f"✅ Asta FA aperta per <b>{giocatore}</b> con offerta iniziale <b>{importo}M</b>.\n"
        f"Scade il {utils.format_dt(scade.isoformat())}.",
        parse_mode="HTML",
    )
    logger.info("Nuova FA: asta_id=%d giocatore=%s team=%s importo=%d", asta_id, giocatore, team["id"], importo)
    return ConversationHandler.END


# ── deep link /start ──────────────────────────────────────────────────────────

async def start_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)

    if not context.args or (not context.args[0].startswith("offri_") and not context.args[0].startswith("nuova_fa_")):
        if team:
            guida = (
                f"Ciao <b>{user.first_name}</b>! Benvenuto nel bot aste Fantabasket 🏀\n\n"
                f"<b>Azioni aste</b>\n"
                f"/offri — fai un'offerta su un'asta aperta\n"
                f"/nuova_fa &lt;giocatore&gt; — apri un'asta FA\n"
                f"/lista_fa — lista giocatori FA disponibili (cliccabile)\n"
                f"/offerta asta &lt;asta_id&gt; &lt;importo&gt; — offerta diretta senza menu\n\n"
                f"<b>La tua situazione</b>\n"
                f"/me — cap, slot e offerte vincenti\n"
                f"/watched — aste che stai seguendo\n\n"
                f"<b>Situazione lega</b>\n"
                f"/aste — aste in corso\n"
                f"/listteams — tutte le squadre\n"
                f"/team &lt;team_id&gt; — situazione di una squadra\n\n"
                f"<b>Altro</b>\n"
                f"/silenzia &lt;asta_id&gt; — smetti di seguire un'asta\n"
                f"/annulla — esci da qualsiasi operazione\n"
                f"/guida — guida completa in privata\n"
                f"/admin — comandi admin (solo admin)\n\n"
                f"<b>Come funziona:</b>\n"
                f"Ogni offerta resetta il timer a 18 ore. Non puoi rilanciare su te stesso. "
                f"Il bot controlla cap e slot in tempo reale, tenendo conto di tutte le aste che stai vincendo.\n\n"
                f"Dal bottone 🔔 nel canale puoi seguire un'asta e ricevere notifiche ad ogni rilancio, "
                f"15 minuti prima della scadenza e alla firma.\n\n"
                f"<b>Quando vinci:</b>\n"
                f"Ricevi un messaggio privato per scegliere gli anni del contratto. "
                f"Hai 48 ore (24h per le RFA), poi vengono assegnati automaticamente."
            )
            await update.effective_message.reply_text(guida, parse_mode="HTML")
        else:
            await update.effective_message.reply_text("⛔ Non sei registrato come GM di nessuna squadra.")
        return ConversationHandler.END

    if team is None:
        await update.effective_message.reply_text("⛔ Non sei registrato come GM di nessuna squadra.")
        return ConversationHandler.END

    # ── deep link nuova_fa_<nome> ──────────────────────────────────────────────
    if context.args[0].startswith("nuova_fa_"):
        nome = context.args[0][len("nuova_fa_"):].replace("_", " ")

        if not utils.is_mercato_aperto():
            await update.effective_message.reply_text("🔒 Il mercato FA è attualmente chiuso.")
            return ConversationHandler.END

        nome_esatto, suggerimento, omonimi = utils.trova_giocatore_fa(nome)
        if not nome_esatto:
            # Fallback: suggerimento non ambiguo (es. cognome con apostrofo come O'Neale → oneale)
            if suggerimento and not suggerimento.startswith("__cognome__") and not omonimi:
                nome_esatto = suggerimento
            else:
                await update.effective_message.reply_text(
                    f"❌ Giocatore '<b>{nome}</b>' non trovato nella lista FA.",
                    parse_mode="HTML",
                )
                return ConversationHandler.END

        if db.giocatore_gia_in_asta(nome_esatto):
            await update.effective_message.reply_text(
                f"❌ Esiste già un'asta aperta per <b>{nome_esatto}</b>.", parse_mode="HTML"
            )
            return ConversationHandler.END

        cap = _cap_libero(team["id"])
        if cap < settings.rilancio_minimo():
            await update.effective_message.reply_text(
                f"❌ Non hai cap sufficiente per aprire un'asta.\n"
                f"Cap libero: <b>{cap}M</b> — minimo richiesto: <b>{settings.rilancio_minimo()}M</b>.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        slot_imp = db.get_slot_virtuali(team["id"])
        if not tm.check_slot_virtuale(team["id"], slot_imp):
            await update.effective_message.reply_text(
                f"❌ Non hai slot disponibili per aprire un'asta.\n"
                f"Hai già <b>{slot_imp}</b> slot virtualmente impegnati su <b>{team['slot_disponibili']}</b>.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        context.user_data["nuova_fa_giocatore"] = nome_esatto
        context.user_data["nuova_fa_team"] = team
        await update.effective_message.reply_text(
            f"🏀 Nuova asta FA per <b>{nome_esatto}</b>.\n\n"
            f"{_cap_info_lines(team, cap)}\n\n"
            f"Quanto offri? (minimo {settings.rilancio_minimo()}M)\n"
            f"<i>Per annullare: /annulla</i>",
            parse_mode="HTML",
        )
        return NUOVA_FA_IMPORTO

    # ── deep link offri_<asta_id> ──────────────────────────────────────────────
    try:
        asta_id = int(context.args[0].split("_")[1])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("❌ Link non valido.")
        return ConversationHandler.END

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await update.effective_message.reply_text("❌ Asta non disponibile o già chiusa.")
        return ConversationHandler.END

    errore = _check_partecipazione(team, dict(asta))
    if errore:
        await update.effective_message.reply_text(errore, parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["asta_id"] = asta_id
    context.user_data["offri_team"] = team

    min_offerta = asta["offerta_corrente"] + settings.rilancio_minimo()
    cap_lib = _cap_libero(team["id"])
    await update.effective_message.reply_text(
        f"🏀 <b>{asta['giocatore']}</b>\n"
        f"Offerta attuale: <b>{asta['offerta_corrente']}M</b>\n"
        f"{_cap_info_lines(team, cap_lib)}\n\n"
        f"Scrivi il tuo importo (minimo <b>{min_offerta}M</b>):\n"
        f"<i>Per annullare: /annulla</i>",
        parse_mode="HTML",
    )
    return INSERISCI_IMPORTO


# ── inserisci importo (rilancio) ──────────────────────────────────────────────

async def inserisci_importo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.effective_message.reply_text("⛔ Non sei registrato come GM.")
        return ConversationHandler.END

    if not update.message or not update.message.text:
        await update.effective_message.reply_text(
            "❌ Manda solo il numero dell'importo in milioni (es. 15).\n"
            "<i>Per annullare: /annulla</i>",
            parse_mode="HTML",
        )
        return INSERISCI_IMPORTO

    testo = update.message.text.strip()
    if not testo.isdigit():
        await update.effective_message.reply_text("❌ Inserisci un numero intero (es. 15).")
        return INSERISCI_IMPORTO

    importo = int(testo)
    asta_id = context.user_data.get("asta_id")
    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return ConversationHandler.END

    errore = await _esegui_offerta(context, team, asta_id, importo, user.id)
    if errore:
        if "insufficiente" in errore:
            asta_check = db.get_asta(asta_id)
            min_necessario = (asta_check["offerta_corrente"] + settings.rilancio_minimo()) if asta_check else 1
            cap = _cap_libero(team["id"])
            if cap < min_necessario:
                await update.effective_message.reply_text(
                    f"❌ {errore}\n\nNon hai cap sufficiente per fare alcuna offerta su quest'asta. Operazione annullata.",
                    parse_mode="HTML",
                )
                return ConversationHandler.END
        rimani = "bassa" in errore or "insufficiente" in errore
        suffix = f"\n\nInserisci un nuovo importo:\n<i>Per annullare: /annulla</i>" if rimani else ""
        await update.effective_message.reply_text(f"❌ {errore}{suffix}", parse_mode="HTML")
        return INSERISCI_IMPORTO if rimani else ConversationHandler.END

    asta_aggiornata = db.get_asta(asta_id)
    await update.effective_message.reply_text(
        f"✅ Offerta di <b>{importo}M</b> per <b>{asta['giocatore']}</b> registrata!\n"
        f"Scade: {utils.format_dt(asta_aggiornata['scade_at'])}.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ── logica offerta ────────────────────────────────────────────────────────────

async def _esegui_offerta(context, team, asta_id: int, importo: int, gm_id: int) -> str | None:
    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        return "L'asta non è più disponibile."

    if asta["offerente_team_id"] == team["id"]:
        return "Sei già il miglior offerente."

    min_offerta = asta["offerta_corrente"] + settings.rilancio_minimo()
    if importo < min_offerta:
        return f"Offerta troppo bassa. Minimo: {min_offerta}M."
    if importo > settings.offerta_massima():
        return f"Offerta troppo alta. Massimo consentito: {settings.offerta_massima()}M."

    cap_impegnato = db.get_cap_virtuale(team["id"])
    team_data = tm.get_team_by_id(team["id"])
    cap_libero = team_data["cap_disponibile"] - cap_impegnato
    if cap_libero < importo:
        return f"Cap insufficiente. Cap libero: {cap_libero}M."

    slot_impegnati = db.get_slot_virtuali(team["id"])
    if not tm.check_slot_virtuale(team["id"], slot_impegnati):
        return f"Nessuno slot libero: ne hai già {slot_impegnati} virtualmente impegnati."

    now = datetime.now(timezone.utc)

    if importo == settings.offerta_massima():
        nuova_scadenza = now.isoformat()
        db.registra_offerta(asta_id, team["id"], importo, now.isoformat())
        db.aggiorna_offerta(asta_id, team["id"], importo, nuova_scadenza)
        db.add_watch(asta_id, gm_id)
        db.chiudi_asta(asta_id, now.isoformat())
        await aggiorna_canale(context, asta_id)
        team_nome = team["nome"]
        await notifica_watchers(
            context, asta_id,
            f"🔔 <b>{asta['giocatore']}</b>\nOfferta massima raggiunta: <b>{importo}M — {team_nome}</b>\n"
            f"⏰ Asta chiusa immediatamente.",
            escludi_gm=gm_id,
        )
        from handlers.firma import chiedi_anni
        await chiedi_anni(context, asta_id)
        logger.info("Offerta massima: asta_id=%d team=%s importo=%d — chiusura immediata",
                    asta_id, team["id"], importo)
        return None

    nuova_scadenza = (now + timedelta(hours=settings.durata_asta_ore())).isoformat()
    db.registra_offerta(asta_id, team["id"], importo, now.isoformat())
    db.aggiorna_offerta(asta_id, team["id"], importo, nuova_scadenza)
    db.add_watch(asta_id, gm_id)
    await aggiorna_canale(context, asta_id)
    await notifica_watchers(
        context, asta_id,
        f"🔔 <b>{asta['giocatore']}</b>\n"
        f"Nuova offerta: <b>{importo}M — {team['nome']}</b>\n"
        f"Scade: {utils.format_dt(nuova_scadenza)}\n"
        f"Per silenziare: /silenzia {asta_id}",
        escludi_gm=gm_id,
    )

    # RFA: notifica il proprietario ad ogni nuova offerta (skip se già watcher)
    if asta["tipo"] == "RFA" and team["id"] != asta["squadra_proprietaria"]:
        team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
        if team_prop:
            watchers = set(db.get_watchers(asta_id))
            testo_prop = (
                f"👀 <b>{asta['giocatore']}</b> (tuo RFA)\n"
                f"Nuova offerta: <b>{importo}M — {team['nome']}</b>\n"
                f"Scade: {utils.format_dt(nuova_scadenza)}"
            )
            for prop_gm in team_prop["gm_ids"]:
                if prop_gm in watchers or prop_gm == gm_id:
                    continue
                try:
                    await context.bot.send_message(
                        chat_id=prop_gm, text=testo_prop, parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning("Notifica proprietario RFA %d fallita: %s", prop_gm, e)

    logger.info("Offerta: asta_id=%d team=%s importo=%d scade=%s", asta_id, team["id"], importo, nuova_scadenza)
    return None


# ── handler registrations ─────────────────────────────────────────────────────

async def cmd_annulla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_message.reply_text("Operazione annullata.")
    return ConversationHandler.END


async def annulla_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Operazione annullata.")
    return ConversationHandler.END


def get_handlers():
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",    start_deep_link),
            CommandHandler("nuova_fa", nuova_fa),
        ],
        states={
            INSERISCI_IMPORTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, inserisci_importo),
            ],
            NUOVA_FA_IMPORTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, nuova_fa_importo),
            ],
            NUOVA_FA_CONFERMA: [
                CallbackQueryHandler(nuova_fa_conferma_callback, pattern=r"^fa_conferma:(si|no|cognome)$"),
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
    return [
        conv,
        # /offri e paginazione come handler globali — non scadono mai
        CommandHandler("offri", cmd_offri),
        CallbackQueryHandler(offri_pagina_callback, pattern=r"^offri_page:\d+$"),
    ]
