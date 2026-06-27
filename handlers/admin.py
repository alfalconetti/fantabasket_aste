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
from handlers.helpers import aggiorna_canale as _aggiorna_canale, log_warn as _log_warn

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

    # max 1 RFA per team per stagione
    stagione = utils.get_stagione_corrente()
    if db.team_ha_rfa_stagione(team_id, stagione):
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
        stagione=stagione,
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
        cap_virt = db.get_cap_virtuale(t['id'])
        slot_virt = db.get_slot_virtuali(t['id'])
        righe.append(
            f"• <b>{t['nome']}</b>\n"
            f"  ID: <code>{t['id']}</code>  "
            f"Cap: {t['cap_disponibile']}M (libero: {t['cap_disponibile']-cap_virt}M)  "
            f"Slot: {t['slot_disponibili']} (liberi: {t['slot_disponibili']-slot_virt})"
        )

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

    vecchio = team["cap_disponibile"]
    tm.set_cap(team_id, valore)
    await update.message.reply_text(
        f"✅ Cap di <b>{team['nome']}</b> impostato a <b>{valore}M</b>.", parse_mode="HTML"
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ Il tuo cap è stato modificato.\nCap: {vecchio}M → <b>{valore}M</b>",
        admin_name=update.effective_user.first_name)
    await _check_cap_virtuale_negativo(context, team_id, team["nome"])
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

    vecchio = team["slot_disponibili"]
    tm.set_slot(team_id, valore)
    await update.message.reply_text(
        f"✅ Slot di <b>{team['nome']}</b> impostati a <b>{valore}</b>.", parse_mode="HTML"
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ I tuoi slot sono stati modificati.\nSlot: {vecchio} → <b>{valore}</b>",
        admin_name=update.effective_user.first_name)
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
            await _aggiorna_canale(context, asta_id)
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

    # notifica watcher
    from handlers.helpers import notifica_watchers as _nw
    gm_ids_gia_notificati = set()
    if asta["offerente_team_id"]:
        team_v = tm_ann.get_team_by_id(asta["offerente_team_id"])
        if team_v:
            gm_ids_gia_notificati.update(team_v["gm_ids"])
    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"]:
        team_p = tm_ann.get_team_by_id(asta["squadra_proprietaria"])
        if team_p:
            gm_ids_gia_notificati.update(team_p["gm_ids"])
    watchers = db.get_watchers(asta_id)
    for gm_id in watchers:
        if gm_id not in gm_ids_gia_notificati:
            try:
                await context.bot.send_message(
                    chat_id=gm_id,
                    text=f"❌ L'asta per <b>{asta['giocatore']}</b> è stata annullata da un admin.",
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica annullamento watcher {gm_id}: {e}")

    # annuncio nel canale
    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=f"❌ Asta annullata: <b>{asta['giocatore']}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await _log_warn(context, f"Annuncio annullamento canale fallito: {e}")

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
    """Chiede la nuova stagione e conferma prima di resettare."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        stagione_corrente = utils.get_stagione_corrente()
        await update.message.reply_text(
            f"Uso: /reset_rfa <nuova_stagione>\n"
            f"Esempio: /reset_rfa 2025-26\n\n"
            f"Stagione corrente: <b>{stagione_corrente}</b>",
            parse_mode="HTML",
        )
        return

    nuova_stagione = context.args[0]
    stagione_corrente = utils.get_stagione_corrente()
    context.user_data["nuova_stagione"] = nuova_stagione

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma", callback_data="reset_rfa:conferma"),
        InlineKeyboardButton("❌ Annulla",  callback_data="admin_noop"),
    ]])
    await update.message.reply_text(
        f"Cambio stagione: <b>{stagione_corrente} → {nuova_stagione}</b>\n"
        f"Tutti i team potranno aprire una nuova RFA.\n\nConfermi?",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def reset_rfa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Non sei autorizzato.")
        return

    # leggi nuova stagione dagli args salvati in user_data
    nuova_stagione = context.user_data.pop("nuova_stagione", None)
    if not nuova_stagione:
        await query.edit_message_text("❌ Stagione non specificata.")
        return

    # aggiorna stagione_corrente in globals
    with open(GLOBALS_PATH, "r", encoding="utf-8") as f:
        import json as _json
        globals_data = _json.load(f)
    vecchia_stagione = globals_data.get("stagione_corrente", "?")
    globals_data["stagione_corrente"] = nuova_stagione
    with open(GLOBALS_PATH, "w", encoding="utf-8") as f:
        _json.dump(globals_data, f, ensure_ascii=False, indent=2)

    await query.edit_message_text(
        f"✅ Stagione aggiornata: <b>{vecchia_stagione} → {nuova_stagione}</b>\n"
        f"Tutti i team possono ora aprire una nuova RFA.",
        parse_mode="HTML",
    )
    logger.info("reset_rfa: stagione %s → %s admin=%d", vecchia_stagione, nuova_stagione, query.from_user.id)


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


async def _check_cap_virtuale_negativo(context, team_id: str, team_nome: str):
    """Manda warning al gruppo admin se il cap virtuale è negativo dopo una modifica."""
    cap_virt = db.get_cap_virtuale(team_id)
    team_data = tm.get_team_by_id(team_id)
    if team_data and (team_data["cap_disponibile"] - cap_virt) < 0:
        await _log_warn(
            context,
            f"⚠️ <b>{team_nome}</b> ha cap virtuale negativo dopo modifica admin!\n"
            f"Cap disponibile: {team_data['cap_disponibile']}M · Cap impegnato: {cap_virt}M"
        )
        admin_group_id = utils.get_admin_group_id()
        if admin_group_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_group_id,
                    text=(
                        f"🚨 <b>CAP VIRTUALE NEGATIVO</b>\n"
                        f"<b>{team_nome}</b>\n"
                        f"Cap disponibile: {team_data['cap_disponibile']}M\n"
                        f"Cap virtualmente impegnato: {cap_virt}M\n"
                        f"Differenza: {team_data['cap_disponibile'] - cap_virt}M"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("notifica cap negativo gruppo admin: %s", e)


async def _notifica_gm_cap_slot(context, team: dict, testo: str, admin_name: str = "Admin"):
    """Notifica tutti i GM di una squadra quando admin modifica cap o slot."""
    from datetime import datetime, timezone
    ora = utils.format_dt(datetime.now(timezone.utc).isoformat())
    testo_completo = f"{testo}\n<i>Modifica effettuata da {admin_name} · {ora}</i>"
    for gm_id in team["gm_ids"]:
        try:
            await context.bot.send_message(chat_id=gm_id, text=testo_completo, parse_mode="HTML")
        except Exception as e:
            await _log_warn(context, f"Notifica cap/slot GM {gm_id} ({team['nome']}) fallita: {e}")


# ── /add_cap ─────────────────────────────────────────────────────────────────

async def add_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aggiunge (o sottrae se negativo) cap a una squadra. Più comodo di set_cap per aggiustamenti."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Uso: /add_cap <team_id> <importo>\nEsempio: /add_cap bulls 15 oppure /add_cap bulls -10")
        return

    team_id = context.args[0]
    try:
        delta = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ L'importo deve essere un numero intero (anche negativo).")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    nuovo_cap = team["cap_disponibile"] + delta
    if nuovo_cap < 0:
        await update.message.reply_text(f"❌ Il cap non può diventare negativo ({team['cap_disponibile']}M + {delta}M = {nuovo_cap}M).")
        return

    tm.set_cap(team_id, nuovo_cap)
    segno = "+" if delta >= 0 else ""
    await update.message.reply_text(
        f"✅ Cap di <b>{team['nome']}</b>: {team['cap_disponibile']}M {segno}{delta}M → <b>{nuovo_cap}M</b>",
        parse_mode="HTML",
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ Il tuo cap è stato modificato.\nCap: {team['cap_disponibile']}M {segno}{delta}M → <b>{nuovo_cap}M</b>",
        admin_name=update.effective_user.first_name)
    await _check_cap_virtuale_negativo(context, team_id, team["nome"])
    logger.info("add_cap: team=%s delta=%d nuovo=%d admin=%d", team_id, delta, nuovo_cap, update.effective_user.id)


# ── /add_slot ────────────────────────────────────────────────────────────────

async def add_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Uso: /add_slot <team_id> <importo>\nEsempio: /add_slot bulls 1 oppure /add_slot bulls -1")
        return
    team_id = context.args[0]
    try:
        delta = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ L'importo deve essere un numero intero.")
        return
    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return
    nuovo = team["slot_disponibili"] + delta
    if nuovo < 0:
        await update.message.reply_text(f"❌ Gli slot non possono diventare negativi ({team['slot_disponibili']} + {delta} = {nuovo}).")
        return
    tm.set_slot(team_id, nuovo)
    segno = "+" if delta >= 0 else ""
    await update.message.reply_text(
        f"✅ Slot di <b>{team['nome']}</b>: {team['slot_disponibili']} {segno}{delta} → <b>{nuovo}</b>", parse_mode="HTML"
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ I tuoi slot sono stati modificati.\nSlot: {team['slot_disponibili']} {segno}{delta} → <b>{nuovo}</b>",
        admin_name=update.effective_user.first_name)
    logger.info("add_slot: team=%s delta=%d nuovo=%d admin=%d", team_id, delta, nuovo, update.effective_user.id)


# ── /annulla_offerta ──────────────────────────────────────────────────────────

async def annulla_offerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Non sei autorizzato.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /annulla_offerta <asta_id>")
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

    result = db.annulla_ultima_offerta(asta_id)
    if result is None:
        await update.message.reply_text("❌ Nessuna offerta da annullare.")
        return

    teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    team_el = teams_map.get(result["team_eliminato"], result["team_eliminato"])
    nuovo_team = teams_map.get(result["nuovo_team"], "nessuno") if result["nuovo_team"] else "nessuno"

    await _aggiorna_canale(context, asta_id)

    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"⚠️ <b>Offerta annullata da admin</b>\n"
                f"🏀 {asta['giocatore']}\n"
                f"Offerta eliminata: {result['offerta_eliminata']}M — {team_el}\n"
                f"Offerta attuale: {result['nuovo_importo']}M — {nuovo_team}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await _log_warn(context, f"Annuncio annulla_offerta canale fallito: {e}")

    # check cap virtuale del team ripristinato
    warning_cap = ""
    if result["nuovo_team"]:
        import database as _db2
        import teams as _tm2
        cap_virt = _db2.get_cap_virtuale(result["nuovo_team"])
        team_data = _tm2.get_team_by_id(result["nuovo_team"])
        if team_data and (team_data["cap_disponibile"] - cap_virt) < 0:
            warning_cap = (
                f"\n\n⚠️ <b>Attenzione:</b> {teams_map.get(result['nuovo_team'], result['nuovo_team'])} "
                f"ha cap virtuale negativo ({team_data['cap_disponibile']}M disponibile, "
                f"{cap_virt}M impegnato). Potrebbe essere necessario un intervento manuale."
            )

    # notifica GM a cui è stata annullata l'offerta
    team_el_obj = tm.get_team_by_id(result["team_eliminato"]) if result["team_eliminato"] else None
    if team_el_obj:
        for gm_id in team_el_obj["gm_ids"]:
            try:
                await context.bot.send_message(
                    chat_id=gm_id,
                    text=(
                        f"⚠️ La tua offerta di <b>{result['offerta_eliminata']}M</b> "
                        f"per <b>{asta['giocatore']}</b> è stata annullata da un admin.\n"
                        f"Offerta attuale: <b>{result['nuovo_importo']}M — {nuovo_team}</b>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica annulla_offerta GM {gm_id}: {e}")

    # notifica watcher (escluso il GM già notificato)
    gm_ids_notificati = set(team_el_obj["gm_ids"]) if team_el_obj else set()
    watchers = db.get_watchers(asta_id)
    for gm_id in watchers:
        if gm_id not in gm_ids_notificati:
            try:
                await context.bot.send_message(
                    chat_id=gm_id,
                    text=(
                        f"⚠️ Offerta annullata da admin su <b>{asta['giocatore']}</b>\n"
                        f"Offerta eliminata: {result['offerta_eliminata']}M — {team_el}\n"
                        f"Offerta attuale: <b>{result['nuovo_importo']}M — {nuovo_team}</b>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica annulla_offerta watcher {gm_id}: {e}")

    await update.message.reply_text(
        f"✅ Ultima offerta di <b>{team_el}</b> ({result['offerta_eliminata']}M) annullata.\n"
        f"Offerta attuale: <b>{result['nuovo_importo']}M — {nuovo_team}</b>{warning_cap}",
        parse_mode="HTML",
    )
    logger.info("annulla_offerta: asta_id=%d offerta=%d admin=%d", asta_id, result["offerta_eliminata"], update.effective_user.id)


# ── /autocap ─────────────────────────────────────────────────────────────────

async def autocap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Permette a un GM di aggiungere cap autonomamente (es. dopo una trade notturna).
    Il cap viene aggiunto immediatamente. Una notifica viene inviata al gruppo admin e al dev.
    Se il GM ha mentito, l'admin interviene manualmente con penalità.
    """
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "Uso: /autocap &lt;importo&gt;\nEsempio: /autocap 15\n\n"
            "<i>⚠️ Usare solo in caso di necessità (es. trade appena avvenuta). "
            "La richiesta viene segnalata agli admin per verifica. "
            "In caso di dichiarazione falsa saranno applicate penalità.</i>",
            parse_mode="HTML",
        )
        return

    try:
        importo = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ L'importo deve essere un numero intero.")
        return

    if importo <= 0:
        await update.message.reply_text("❌ L'importo deve essere positivo.")
        return

    vecchio_cap = team["cap_disponibile"]
    nuovo_cap = vecchio_cap + importo
    tm.set_cap(team["id"], nuovo_cap)

    import utils as _utils
    from datetime import datetime, timezone
    ora = _utils.format_dt(datetime.now(timezone.utc).isoformat())

    await update.message.reply_text(
        f"✅ Cap aggiunto: <b>+{importo}M</b>\nIl tuo cap ora è <b>{nuovo_cap}M</b>.\n\n"
        f"<i>La richiesta è stata segnalata agli admin.</i>",
        parse_mode="HTML",
    )

    notifica = (
        f"⚠️ <b>Autocap</b>\n"
        f"👤 {user.first_name} (@{user.username or '?'}) — <b>{team['nome']}</b>\n"
        f"💰 +{importo}M (da {vecchio_cap}M a {nuovo_cap}M)\n"
        f"🕐 {ora}\n\n"
        f"Verifica che la dichiarazione sia corretta."
    )

    # notifica gruppo admin
    admin_group_id = _utils.get_admin_group_id()
    if admin_group_id:
        try:
            await context.bot.send_message(chat_id=admin_group_id, text=notifica, parse_mode="HTML")
        except Exception as e:
            logger.warning("notifica autocap gruppo admin: %s", e)

    # notifica dev
    globals_data = _utils.load_globals()
    dev_id = globals_data.get("dev_id")
    if dev_id and dev_id != admin_group_id:
        try:
            await context.bot.send_message(chat_id=dev_id, text=notifica, parse_mode="HTML")
        except Exception as e:
            logger.warning("notifica autocap dev: %s", e)

    logger.info("autocap: team=%s importo=%d gm=%d", team["id"], importo, user.id)


# ── /auto_slot ───────────────────────────────────────────────────────────────

async def auto_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    team = tm.get_team_by_gm(user.id)
    if team is None:
        await update.message.reply_text("⛔ Non sei registrato come GM.")
        return
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "Uso: /autoslot &lt;importo&gt;\nEsempio: /autoslot 1\n\n"
            "<i>⚠️ Usare solo in caso di necessità. La richiesta viene segnalata agli admin per verifica.</i>",
            parse_mode="HTML",
        )
        return
    try:
        importo = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ L'importo deve essere un numero intero.")
        return
    if importo <= 0:
        await update.message.reply_text("❌ L'importo deve essere positivo.")
        return

    vecchio = team["slot_disponibili"]
    nuovo = vecchio + importo
    tm.set_slot(team["id"], nuovo)

    import utils as _utils
    from datetime import datetime, timezone
    ora = _utils.format_dt(datetime.now(timezone.utc).isoformat())

    await update.message.reply_text(
        f"✅ Slot aggiunti: <b>+{importo}</b>\nI tuoi slot ora sono <b>{nuovo}</b>.\n\n"
        f"<i>La richiesta è stata segnalata agli admin.</i>",
        parse_mode="HTML",
    )

    notifica = (
        f"⚠️ <b>Autoslot</b>\n"
        f"👤 {user.first_name} (@{user.username or '?'}) — <b>{team['nome']}</b>\n"
        f"🪑 +{importo} slot (da {vecchio} a {nuovo})\n"
        f"🕐 {ora}\n\n"
        f"Verifica che la dichiarazione sia corretta."
    )
    admin_group_id = _utils.get_admin_group_id()
    if admin_group_id:
        try:
            await context.bot.send_message(chat_id=admin_group_id, text=notifica, parse_mode="HTML")
        except Exception as e:
            logger.warning("notifica auto_slot gruppo admin: %s", e)
    globals_data = _utils.load_globals()
    dev_id = globals_data.get("dev_id")
    if dev_id and dev_id != admin_group_id:
        try:
            await context.bot.send_message(chat_id=dev_id, text=notifica, parse_mode="HTML")
        except Exception as e:
            logger.warning("notifica auto_slot dev: %s", e)
    logger.info("auto_slot: team=%s importo=%d gm=%d", team["id"], importo, user.id)


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
        "/add_cap &lt;team_id&gt; &lt;importo&gt; — aggiunge/sottrae cap (accetta negativi)\n"
        "/set_slot &lt;team_id&gt; &lt;valore&gt; — imposta slot squadra\n"
        "/add_slot &lt;team_id&gt; &lt;importo&gt; — aggiunge/sottrae slot\n"
        "/annulla_offerta &lt;asta_id&gt; — annulla ultima offerta\n"
        "/set_cap_penalizzato &lt;team_id&gt; &lt;valore&gt; — imposta penalità cap\n"
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
        CommandHandler("add_cap",          add_cap),
        CommandHandler("add_slot",          add_slot),
        CommandHandler("annulla_offerta",   annulla_offerta),
        CallbackQueryHandler(admin_chiudi_callback,  pattern=r"^admin_chiudi:\d+$"),
        CallbackQueryHandler(admin_annulla_callback, pattern=r"^admin_annulla:\d+$"),
        CallbackQueryHandler(admin_noop_callback,    pattern=r"^admin_noop$"),
    ]
