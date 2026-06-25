"""
Comandi admin:
  /nuova_rfa <giocatore> <team_id> <vecchio_compenso>
  /apri_mercato
  /chiudi_mercato
  /listteams
  /set_cap <team_id> <valore>
  /set_slot <team_id> <valore>
"""
import logging
import json
import os
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

import database as db
import teams as tm
import utils
import settings
from handlers.helpers import aggiorna_canale as _aggiorna_canale

logger = logging.getLogger(__name__)

DURATA_ASTA_H = 18
GLOBALS_PATH  = os.environ.get("GLOBALS_PATH", "/config/globals.json")


def is_admin(user_id: int) -> bool:
    return user_id in utils.get_admin_ids()


def _set_mercato(aperto: bool):
    with open(GLOBALS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["mercato_aperto"] = aperto
    with open(GLOBALS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── /nuova_rfa ────────────────────────────────────────────────────────────────

async def nuova_rfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: /nuova_rfa <giocatore> <team_id> <vecchio_compenso>\n"
            "Esempio: /nuova_rfa Luka Doncic lakers 25"
        )
        return

    try:
        vecchio_compenso = int(args[-1])
    except ValueError:
        await update.message.reply_text("❌ Il vecchio compenso deve essere un numero intero.")
        return

    team_id   = args[-2]
    giocatore_input = " ".join(args[:-2])

    if not giocatore_input:
        await update.message.reply_text("❌ Nome giocatore mancante.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato. Usa /listteams.", parse_mode="HTML")
        return

    # max 1 RFA per team
    if db.team_ha_rfa_stagione(team_id):
        await update.message.reply_text(f"❌ {team['nome']} ha già utilizzato la RFA questa stagione.")
        return

    # normalizzazione nome giocatore — per RFA non c'è lista fissa,
    # ma normalizziamo l'input per salvarlo in forma canonica
    # (l'admin inserisce il nome come vuole, lo salviamo normalizzato-decoded)
    giocatore = giocatore_input.strip()

    if db.giocatore_gia_in_asta(giocatore):
        await update.message.reply_text(f"❌ Esiste già un'asta aperta per {giocatore}.")
        return

    now   = datetime.now(timezone.utc)
    scade = now + timedelta(hours=DURATA_ASTA_H)

    asta_id = db.crea_asta(
        tipo="RFA",
        giocatore=giocatore,
        squadra_proprietaria=team_id,
        vecchio_compenso=vecchio_compenso,
        creata_at=now.isoformat(),
        scade_at=scade.isoformat(),
    )

    channel_id = utils.get_channel_id()
    teams_map  = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    asta_row   = db.get_asta(asta_id)
    testo      = utils.build_canale_message(asta_row, [], teams_map)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏀 Offri",  url=f"https://t.me/{context.bot.username}?start=offri_{asta_id}"),
        InlineKeyboardButton("🔔 Segui",  callback_data=f"watch:{asta_id}"),
    ]])

    msg = await context.bot.send_message(
        chat_id=channel_id, text=testo, parse_mode="HTML", reply_markup=keyboard,
    )
    db.set_canale_msg_id(asta_id, msg.message_id)

    await update.message.reply_text(
        f"✅ Asta RFA aperta per <b>{giocatore}</b>\n"
        f"Proprietario: {team['nome']} | Vecchio compenso: {vecchio_compenso}M\n"
        f"Scade: {utils.format_dt(scade.isoformat())}",
        parse_mode="HTML",
    )
    logger.info("Nuova RFA: asta_id=%d giocatore=%s proprietario=%s compenso=%d",
                asta_id, giocatore, team_id, vecchio_compenso)


# ── /listteams ────────────────────────────────────────────────────────────────

async def listteams(update: Update, context: ContextTypes.DEFAULT_TYPE):

    all_teams = tm.get_all_teams()
    righe = ["<b>Squadre registrate:</b>\n"]
    for t in all_teams:
        righe.append(f"• <b>{t['nome']}</b>\n  ID: <code>{t['id']}</code>  Cap: {t['cap_disponibile']}M  Slot: {t['slot_disponibili']}")

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /set_cap e /set_slot ──────────────────────────────────────────────────────

async def set_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Uso: /set_cap <team_id> <valore>")
        return

    team_id = context.args[0]
    try:
        valore = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Il valore deve essere un numero intero.")
        return

    if valore < 0:
        await update.message.reply_text("❌ Il cap non può essere negativo.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    tm.set_cap(team_id, valore)
    await update.message.reply_text(
        f"✅ Cap di <b>{team['nome']}</b> impostato a <b>{valore}M</b>.", parse_mode="HTML"
    )
    logger.info("set_cap: team=%s valore=%d admin=%d", team_id, valore, update.effective_user.id)


async def set_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Uso: /set_slot <team_id> <valore>")
        return

    team_id = context.args[0]
    try:
        valore = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Il valore deve essere un numero intero.")
        return

    if valore < 0:
        await update.message.reply_text("❌ Gli slot non possono essere negativi.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    tm.set_slot(team_id, valore)
    await update.message.reply_text(
        f"✅ Slot di <b>{team['nome']}</b> impostati a <b>{valore}</b>.", parse_mode="HTML"
    )
    logger.info("set_slot: team=%s valore=%d admin=%d", team_id, valore, update.effective_user.id)


# ── /apri_mercato e /chiudi_mercato ──────────────────────────────────────────

async def apri_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return
    _set_mercato(True)
    await update.message.reply_text("✅ Mercato FA aperto.")


async def chiudi_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return
    _set_mercato(False)
    await update.message.reply_text("🔒 Mercato FA chiuso.")


# ── /aste ────────────────────────────────────────────────────────────────────

async def aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aste_aperte = db.get_aste_aperte()
    if not aste_aperte:
        await update.message.reply_text("Nessuna asta aperta al momento.")
        return

    teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    righe = ["<b>Aste in corso:</b>\n"]
    for a in aste_aperte:
        tipo = "🔴 RFA" if a["tipo"] == "RFA" else "🟢 FA"
        team_nome = teams_map.get(a["offerente_team_id"], "—") if a["offerente_team_id"] else "—"
        prop = f" <i>(diritti: {teams_map.get(a['squadra_proprietaria'], a['squadra_proprietaria'])})</i>" if a["tipo"] == "RFA" and a["squadra_proprietaria"] else ""
        righe.append(
            f"{tipo} <b>{a['giocatore']}</b>{prop}\n"
            f"  Offerta: {a['offerta_corrente']}M — {team_nome}\n"
            f"  Scade: {utils.format_dt(a['scade_at'])}\n"
            f"  ID: <code>{a['id']}</code>"
        )
    await update.message.reply_text("\n\n".join(righe), parse_mode="HTML")


# ── /chiudi_asta e /annulla_asta ─────────────────────────────────────────────

async def chiudi_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /chiudi_asta <asta_id>")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido.")
        return

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await update.message.reply_text("❌ Asta non trovata o non aperta.")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma chiusura", callback_data=f"admin_chiudi:{asta_id}"),
        InlineKeyboardButton("❌ Annulla",           callback_data="admin_noop"),
    ]])
    await update.message.reply_text(
        f"Chiudere forzatamente l'asta per <b>{asta['giocatore']}</b>?\n"
        f"Offerta attuale: {asta['offerta_corrente']}M\n"
        f"Il vincitore verrà contattato per la firma.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def annulla_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /annulla_asta <asta_id>")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido.")
        return

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] not in ("APERTA", "CHIUSA", "PAREGGIO"):
        await update.message.reply_text("❌ Asta non trovata o già conclusa.")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma annullamento", callback_data=f"admin_annulla:{asta_id}"),
        InlineKeyboardButton("❌ Annulla",               callback_data="admin_noop"),
    ]])
    await update.message.reply_text(
        f"Annullare l'asta per <b>{asta['giocatore']}</b>? Nessun vincitore verrà assegnato.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def admin_chiudi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Non sei autorizzato.")
        return

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await query.edit_message_text("❌ Asta non più disponibile.")
        return

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    db.chiudi_asta(asta_id, now.isoformat())

    await _aggiorna_canale(context, asta_id)

    if asta["offerente_team_id"]:
        from handlers.firma import chiedi_anni
        await chiedi_anni(context, asta_id)
        await query.edit_message_text(
            f"✅ Asta <b>{asta['giocatore']}</b> chiusa. Il vincitore è stato contattato.",
            parse_mode="HTML",
        )
    else:
        # Nessun vincitore: per FA chiudi senza assegnazione, per RFA avvia flusso proprietario
        if asta["tipo"] == "RFA":
            from handlers.firma import chiedi_anni as _chiedi
            await _chiedi(context, asta_id)
            await query.edit_message_text(
                f"✅ Asta RFA <b>{asta['giocatore']}</b> chiusa senza offerte. "
                f"Il proprietario è stato contattato per decidere.",
                parse_mode="HTML",
            )
        else:
            db.annulla_asta(asta_id)
            from handlers.offerte import _aggiorna_canale as _ag2
            await _ag2(context, asta_id)
            await query.edit_message_text(
                f"✅ Asta FA <b>{asta['giocatore']}</b> chiusa senza offerte.",
                parse_mode="HTML",
            )
    logger.info("Chiusura forzata: asta_id=%d admin=%d", asta_id, query.from_user.id)


async def admin_annulla_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Non sei autorizzato.")
        return

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    # cancella eventuali job pendenti
    for nome in [f"firma_auto_{asta_id}", f"pareggio_auto_{asta_id}", f"notif15_{asta_id}"]:
        for j in context.job_queue.get_jobs_by_name(nome):
            j.schedule_removal()

    db.annulla_asta(asta_id)
    await _aggiorna_canale(context, asta_id)

    # notifica GM vincitore se esiste
    import teams as tm_ann
    if asta["offerente_team_id"]:
        team_vince = tm_ann.get_team_by_id(asta["offerente_team_id"])
        if team_vince:
            for gm_id in team_vince["gm_ids"]:
                try:
                    await context.bot.send_message(
                        chat_id=gm_id,
                        text=(
                            f"⚠️ L'asta per <b>{asta['giocatore']}</b> è stata annullata da un admin.\n"
                            f"La tua offerta di {asta['offerta_corrente']}M è stata annullata."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("notifica annullamento GM %d: %s", gm_id, e)

    # notifica proprietario RFA se diverso dal vincitore
    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"]:
        team_prop = tm_ann.get_team_by_id(asta["squadra_proprietaria"])
        if team_prop:
            for gm_id in team_prop["gm_ids"]:
                try:
                    await context.bot.send_message(
                        chat_id=gm_id,
                        text=f"⚠️ L'asta RFA per <b>{asta['giocatore']}</b> è stata annullata da un admin.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("notifica annullamento prop RFA %d: %s", gm_id, e)

    # annuncio nel canale
    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=f"❌ Asta annullata: <b>{asta['giocatore']}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("annuncio annullamento canale: %s", e)

    await query.edit_message_text(
        f"✅ Asta <b>{asta['giocatore']}</b> annullata.", parse_mode="HTML"
    )
    logger.info("Annullamento: asta_id=%d admin=%d", asta_id, query.from_user.id)


async def admin_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Operazione annullata.")


# ── /set_fase ────────────────────────────────────────────────────────────────

async def set_fase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args or context.args[0] not in ("offseason", "regular"):
        await update.message.reply_text("Uso: /set_fase <offseason|regular>")
        return

    fase = context.args[0]

    # leggi fase corrente
    with open(GLOBALS_PATH, "r", encoding="utf-8") as f:
        globals_data = json.load(f)

    fase_corrente = globals_data.get("fase", "offseason")
    if fase_corrente == fase:
        await update.message.reply_text(f"Siamo già in fase <b>{fase}</b>.", parse_mode="HTML")
        return

    all_teams = tm.get_all_teams()
    righe = [f"Cambio fase: <b>{fase_corrente} → {fase}</b>\n"]

    import teams as tm_fase
    for t in all_teams:
        penalita = t.get("cap_penalizzato", 0)
        delta = 15 + penalita
        if fase == "regular":
            nuovo_cap = t["cap_disponibile"] - delta
        else:
            nuovo_cap = t["cap_disponibile"] + delta
        tm_fase.set_cap(t["id"], max(0, nuovo_cap))
        righe.append(f"• <b>{t['nome']}</b>: {t['cap_disponibile']}M → {max(0, nuovo_cap)}M (penalità: {penalita})")

    # aggiorna fase in globals
    globals_data["fase"] = fase
    with open(GLOBALS_PATH, "w", encoding="utf-8") as f:
        json.dump(globals_data, f, ensure_ascii=False, indent=2)

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")
    logger.info("set_fase: %s → %s admin=%d", fase_corrente, fase, update.effective_user.id)


# ── /reset_rfa ───────────────────────────────────────────────────────────────

async def reset_rfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra anteprima e chiede conferma prima di resettare."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT giocatore, stato FROM aste WHERE tipo='RFA' AND stato IN ('CONCLUSA','ANNULLATA')"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Nessuna asta RFA conclusa da resettare.")
        return

    elenco = "\n".join(f"  • {r['giocatore']} ({r['stato']})" for r in rows)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma reset", callback_data="reset_rfa:conferma"),
        InlineKeyboardButton("❌ Annulla",        callback_data="admin_noop"),
    ]])
    await update.message.reply_text(
        f"Stai per eliminare <b>{len(rows)} aste RFA</b> della stagione precedente:\n{elenco}\n\nConfermi?",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def reset_rfa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Non sei autorizzato.")
        return

    with db.get_conn() as conn:
        result = conn.execute(
            "SELECT COUNT(*) as n FROM aste WHERE tipo='RFA' AND stato IN ('CONCLUSA','ANNULLATA')"
        ).fetchone()
        n = result["n"]
        conn.execute("DELETE FROM aste WHERE tipo='RFA' AND stato IN ('CONCLUSA','ANNULLATA')")

    await query.edit_message_text(
        f"✅ Reset RFA completato. Eliminate {n} aste RFA della stagione precedente.\nTutti i team possono ora aprire una nuova RFA.",
        parse_mode="HTML",
    )
    logger.info("reset_rfa confermato: eliminate %d aste admin=%d", n, query.from_user.id)


# ── /set_cap_penalizzato ─────────────────────────────────────────────────────

async def set_cap_penalizzato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Uso: /set_cap_penalizzato <team_id> <valore>")
        return

    team_id = context.args[0]
    try:
        valore = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Il valore deve essere un numero intero.")
        return

    if valore < 0:
        await update.message.reply_text("❌ Il valore non può essere negativo.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    with open(GLOBALS_PATH.replace("globals.json", "teams.json"), "r", encoding="utf-8") as f:
        import json as _json
        data = _json.load(f)
    for t in data["teams"]:
        if t["id"] == team_id:
            t["cap_penalizzato"] = valore
            break
    teams_path = os.environ.get("TEAMS_PATH", "/config/teams.json")
    with open(teams_path, "w", encoding="utf-8") as f:
        import json as _json2
        _json2.dump(data, f, ensure_ascii=False, indent=2)

    await update.message.reply_text(
        f"✅ Cap penalizzato di <b>{team['nome']}</b> impostato a <b>{valore}M</b>.", parse_mode="HTML"
    )
    logger.info("set_cap_penalizzato: team=%s valore=%d", team_id, valore)


# ── /admin ────────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    testo = (
        "<b>Comandi admin:</b>\n\n"
        "/nuova_rfa &lt;giocatore&gt; &lt;team_id&gt; &lt;vecchio_compenso&gt; — apre asta RFA\n"
        "/chiudi_asta &lt;asta_id&gt; — chiude forzatamente un'asta\n"
        "/annulla_asta &lt;asta_id&gt; — annulla un'asta\n"
        "/reset_rfa — resetta flag RFA per nuova stagione\n"
        "/set_cap &lt;team_id&gt; &lt;valore&gt; — imposta cap squadra\n"
        "/set_cap_penalizzato &lt;team_id&gt; &lt;valore&gt; — imposta penalità cap\n"
        "/set_slot &lt;team_id&gt; &lt;valore&gt; — imposta slot squadra\n"
        "/set_fase &lt;offseason|regular&gt; — cambia fase e scala cap\n"
        "/apri_mercato — apre il mercato FA\n"
        "/chiudi_mercato — chiude il mercato FA\n"
        "/listteams — lista squadre con ID e cap\n"
        "/aste — lista aste in corso\n"
        "/admin — questo messaggio"
    )
    await update.message.reply_text(testo, parse_mode="HTML")


def get_handlers():
    return [
        CommandHandler("nuova_rfa",      nuova_rfa),
        CommandHandler("listteams",      listteams),
        CommandHandler("set_cap",        set_cap),
        CommandHandler("set_slot",       set_slot),
        CommandHandler("apri_mercato",   apri_mercato),
        CommandHandler("chiudi_mercato", chiudi_mercato),
        CommandHandler("set_fase",       set_fase),
        CommandHandler("aste",           aste),
        CommandHandler("chiudi_asta",    chiudi_asta),
        CommandHandler("annulla_asta",   annulla_asta),
        CommandHandler("reset_rfa",            reset_rfa),
        CommandHandler("set_cap_penalizzato",   set_cap_penalizzato),
        CallbackQueryHandler(reset_rfa_callback, pattern=r"^reset_rfa:conferma$"),
        CommandHandler("admin",           cmd_admin),
        CallbackQueryHandler(admin_chiudi_callback,  pattern=r"^admin_chiudi:\d+$"),
        CallbackQueryHandler(admin_annulla_callback, pattern=r"^admin_annulla:\d+$"),
        CallbackQueryHandler(admin_noop_callback,    pattern=r"^admin_noop$"),
    ]
