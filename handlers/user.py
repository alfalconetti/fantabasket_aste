"""
Comandi utente non legati al flusso offerte:
  /me, /watched, /lista_fa, watch_callback
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

import database as db
import teams as tm
import utils
import settings
from handlers.helpers import teams_map
from handlers.admin import autocap as _autocap_from_user

logger = logging.getLogger(__name__)


# ── /me ──────────────────────────────────────────────────────────────────────

async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return

    cap_tot        = team["cap_disponibile"]
    slot_tot       = team["slot_disponibili"]
    cap_virtuale   = db.get_cap_virtuale(team["id"])
    slot_impegnati = db.get_slot_virtuali(team["id"])
    cap_libero     = cap_tot - cap_virtuale
    slot_liberi    = slot_tot - slot_impegnati
    offerte_vince  = db.get_offerte_vincenti_team(team["id"])

    fase = utils.load_globals().get("fase", "offseason")
    cap_pen = team.get("cap_penalizzato", 0)
    s = settings.get()

    righe = [
        f"🏀 <b>{team['nome']}</b>",
        "",
        f"💰 Cap disponibile (offseason): <b>{cap_tot}M</b>",
        f"⏳ Cap virtualmente impegnato: <b>{cap_virtuale}M</b>",
        f"✅ Cap effettivamente libero: <b>{cap_libero}M</b>",
    ]

    if fase == "offseason":
        rfa_attive = db.get_rfa_proprietario(team["id"])
        if rfa_attive:
            cap_rfa = sum(r["vecchio_compenso"] or 0 for r in rfa_attive)
            nomi_rfa = ", ".join(r["giocatore"] for r in rfa_attive)
            righe.append(f"⚠️ Cap occupato da RFA: <b>{cap_rfa}M</b> ({nomi_rfa})")
        delta = s["cap_offseason"] - s["cap_regular"] + cap_pen
        cap_rs = cap_libero - delta
        nota_pen = f", penalità {cap_pen}M" if cap_pen else ""
        righe.append(f"📉 Cap libero in Regular Season: <b>{cap_rs}M</b> (-{delta}M{nota_pen})")

    righe += [
        "",
        f"🪑 Slot totali: <b>{slot_tot}</b>",
        f"⏳ Slot virtualmente impegnati: <b>{slot_impegnati}</b>",
        f"✅ Slot effettivamente liberi: <b>{slot_liberi}</b>",
    ]

    if offerte_vince:
        righe.append("")
        righe.append("<i>Offerte vincenti in corso:</i>")
        for o in offerte_vince:
            righe.append(
                f"  • {o['giocatore']} — {o['offerta_corrente']}M "
                f"(scade {utils.format_dt(o['scade_at'])})"
            )

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /watched ─────────────────────────────────────────────────────────────────

async def cmd_watched(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return

    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.* FROM aste a
               JOIN aste_watch w ON w.asta_id = a.id
               WHERE w.gm_id=? AND a.stato IN ('APERTA','CHIUSA','PAREGGIO')
               ORDER BY a.scade_at""",
            (user.id,),
        ).fetchall()

    if not rows:
        await update.message.reply_text("Non stai seguendo nessuna asta al momento.")
        return

    tm_map = teams_map()
    righe = ["<b>Aste che segui:</b>\n"]
    for a in rows:
        tipo = "🔴 RFA" if a["tipo"] == "RFA" else "🟢 FA"
        vincitore = tm_map.get(a["offerente_team_id"], "—") if a["offerente_team_id"] else "nessuno"
        stato_label = {"APERTA": "⏳", "CHIUSA": "🔒", "PAREGGIO": "⚖️"}.get(a["stato"], "")
        righe.append(
            f"{stato_label} {tipo} <b>{a['giocatore']}</b>\n"
            f"  Offerta: {a['offerta_corrente']}M — {vincitore}\n"
            f"  Scade: {utils.format_dt(a['scade_at'])}\n"
            f"  ID: <code>{a['id']}</code>"
        )
    await update.message.reply_text("\n\n".join(righe), parse_mode="HTML")


# ── watch callback ────────────────────────────────────────────────────────────

async def watch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    gm_id = query.from_user.id

    team = tm.get_team_by_gm(gm_id)
    if team is None:
        await query.answer("⛔ Non sei registrato come GM.", show_alert=True)
        return

    asta = db.get_asta(asta_id)
    if db.is_watching(asta_id, gm_id):
        db.remove_watch(asta_id, gm_id)
        await query.answer("🔕 Non segui più questa asta.", show_alert=True)
    else:
        db.add_watch(asta_id, gm_id)
        if asta:
            tm_map = teams_map()
            vincitore = tm_map.get(asta["offerente_team_id"], "—") if asta["offerente_team_id"] else "nessuno"
            testo_feedback = (
                f"🔔 Ora segui <b>{asta['giocatore']}</b>\n"
                f"Offerta attuale: <b>{asta['offerta_corrente']}M — {vincitore}</b>\n"
                f"Scade: {utils.format_dt(asta['scade_at'])}"
            )
            try:
                await context.bot.send_message(chat_id=gm_id, text=testo_feedback, parse_mode="HTML")
            except Exception as e:
                logger.warning("feedback watch privato fallito: %s", e)
        await query.answer("🔔 Ora segui questa asta.", show_alert=True)


# ── /lista_fa ─────────────────────────────────────────────────────────────────

def _lista_fa_keyboard(rows: list[dict], aste_aperte_giocatori: set, page: int) -> InlineKeyboardMarkup:
    page_size = settings.paginazione_fa()
    start = page * page_size
    pagina = rows[start: start + page_size]
    totale_pagine = max(1, -(-len(rows) // page_size))

    righe_kb = []
    for r in pagina:
        pallino = "🟡 " if r["nome"] in aste_aperte_giocatori else ""
        fm = f" — FM: {r['fantamedia']}" if r["fantamedia"] else ""
        label = f"{pallino}{r['nome']}{fm}"
        righe_kb.append([InlineKeyboardButton(label, callback_data=f"fa_avvia:{r['nome']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prec", callback_data=f"fa_page:{page-1}"))
    if page < totale_pagine - 1:
        nav.append(InlineKeyboardButton("Succ ▶", callback_data=f"fa_page:{page+1}"))
    if nav:
        righe_kb.append(nav)

    return InlineKeyboardMarkup(righe_kb)


async def cmd_lista_fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [r for r in utils.get_fa_rows() if r["firmato"] == "0"]
    if not rows:
        await update.message.reply_text("Nessun giocatore FA disponibile.")
        return

    aste_aperte_giocatori = {a["giocatore"] for a in db.get_aste_aperte()}
    aste_aperte_giocatori |= {a["giocatore"] for a in db.get_aste_chiuse()}

    kb = _lista_fa_keyboard(rows, aste_aperte_giocatori, 0)
    await update.message.reply_text(
        "🟢 <b>Giocatori FA disponibili</b>\n🟡 = asta in corso\nClicca per avviare asta:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def lista_fa_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    rows = [r for r in utils.get_fa_rows() if r["firmato"] == "0"]
    aste_aperte_giocatori = {a["giocatore"] for a in db.get_aste_aperte()}
    aste_aperte_giocatori |= {a["giocatore"] for a in db.get_aste_chiuse()}
    kb = _lista_fa_keyboard(rows, aste_aperte_giocatori, page)
    await query.edit_message_reply_markup(reply_markup=kb)


async def lista_fa_avvia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    nome = query.data.split(":", 1)[1]
    user = query.from_user
    team = tm.get_team_by_gm(user.id)

    if team is None:
        await query.answer("⛔ Non sei registrato come GM.", show_alert=True)
        return

    if not utils.is_mercato_aperto():
        await query.answer("🔒 Il mercato FA è attualmente chiuso.", show_alert=True)
        return

    if db.giocatore_gia_in_asta(nome):
        await query.answer(f"❌ Esiste già un'asta aperta per {nome}.", show_alert=True)
        return

    await query.message.reply_text(
        f"Per aprire l'asta per <b>{nome}</b>, scrivi:",
        parse_mode="HTML",
    )
    await query.message.reply_text(f"/nuova_fa {nome}")


def get_handlers():
    return [
        CommandHandler("me",       cmd_me),
        CommandHandler("autocap",  _autocap_from_user),
        CommandHandler("watched",  cmd_watched),
        CommandHandler("lista_fa", cmd_lista_fa),
        CallbackQueryHandler(watch_callback,         pattern=r"^watch:\d+$"),
        CallbackQueryHandler(lista_fa_page_callback, pattern=r"^fa_page:\d+$"),
        CallbackQueryHandler(lista_fa_avvia_callback,pattern=r"^fa_avvia:.+$"),
    ]
