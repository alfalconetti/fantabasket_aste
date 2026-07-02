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


def _admin_label(user) -> str:
    """Costruisce 'Nome (@username)' o 'Nome' se l'username manca."""
    nome = user.first_name or "Admin"
    if user.username:
        return f"{nome} (@{user.username})"
    return nome


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
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    args = context.args
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Uso: /nuova_rfa <giocatore> <team_id> <vecchio_compenso>\n"
            "Esempio: /nuova_rfa Luka Doncic lakers 25"
        )
        return

    try:
        vecchio_compenso = int(args[-1])
    except ValueError:
        await update.effective_message.reply_text("❌ Il vecchio compenso deve essere un numero intero.")
        return

    team_id   = args[-2]
    giocatore_input = " ".join(args[:-2])

    if not giocatore_input:
        await update.effective_message.reply_text("❌ Nome giocatore mancante.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato. Usa /listteams.", parse_mode="HTML")
        return

    # max 1 RFA per team per stagione
    stagione = utils.get_stagione_corrente()
    if db.team_ha_rfa_stagione(team_id, stagione):
        await update.effective_message.reply_text(f"❌ {team['nome']} ha già utilizzato la RFA questa stagione.")
        return

    # normalizzazione nome giocatore — per RFA non c'è lista fissa,
    # ma normalizziamo l'input per salvarlo in forma canonica
    # (l'admin inserisce il nome come vuole, lo salviamo normalizzato-decoded)
    giocatore = giocatore_input.strip()

    if db.giocatore_gia_in_asta(giocatore):
        await update.effective_message.reply_text(f"❌ Esiste già un'asta aperta per {giocatore}.")
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

    admin_label = _admin_label(update.effective_user)
    channel_id = utils.get_channel_id()
    teams_map  = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    asta_row   = db.get_asta(asta_id)
    testo      = utils.build_canale_message(asta_row, [], teams_map, aperta_da=admin_label)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏀 Offri",  url=f"https://t.me/{context.bot.username}?start=offri_{asta_id}"),
        InlineKeyboardButton("🔔 Segui",  callback_data=f"watch:{asta_id}"),
    ]])

    msg = await context.bot.send_message(
        chat_id=channel_id, text=testo, parse_mode="HTML", reply_markup=keyboard,
    )
    db.set_canale_msg_id(asta_id, msg.message_id)

    await update.effective_message.reply_text(
        f"✅ Asta RFA aperta per <b>{giocatore}</b>\n"
        f"Proprietario: {team['nome']} | Vecchio compenso: {vecchio_compenso}M\n"
        f"Scade: {utils.format_dt(scade.isoformat())}",
        parse_mode="HTML",
    )
    logger.info("Nuova RFA: asta_id=%d giocatore=%s proprietario=%s compenso=%d admin=%d",
                asta_id, giocatore, team_id, vecchio_compenso, update.effective_user.id)


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

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /set_cap e /set_slot ──────────────────────────────────────────────────────

async def set_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /set_cap <team_id> <valore>")
        return

    team_id = context.args[0]
    try:
        valore = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("❌ Il valore deve essere un numero intero.")
        return

    if valore < 0:
        await update.effective_message.reply_text("❌ Il cap non può essere negativo.")
        return
    if valore > settings.cap_massimo():
        await update.effective_message.reply_text(f"❌ Il cap non può superare {settings.cap_massimo()}M.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    vecchio = team["cap_disponibile"]
    tm.set_cap(team_id, valore)
    await update.effective_message.reply_text(
        f"✅ Cap di <b>{team['nome']}</b> impostato a <b>{valore}M</b>.", parse_mode="HTML"
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ Il tuo cap è stato modificato.\nCap: {vecchio}M → <b>{valore}M</b>",
        admin_name=update.effective_user.first_name)
    await _check_cap_virtuale_negativo(context, team_id, team["nome"])
    logger.info("set_cap: team=%s valore=%d admin=%d", team_id, valore, update.effective_user.id)


async def set_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /set_slot <team_id> <valore>")
        return

    team_id = context.args[0]
    try:
        valore = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("❌ Il valore deve essere un numero intero.")
        return

    if valore < 0:
        await update.effective_message.reply_text("❌ Gli slot non possono essere negativi.")
        return
    if valore > settings.slot_massimo():
        await update.effective_message.reply_text(f"❌ Gli slot non possono superare {settings.slot_massimo()}.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    vecchio = team["slot_disponibili"]
    tm.set_slot(team_id, valore)
    await update.effective_message.reply_text(
        f"✅ Slot di <b>{team['nome']}</b> impostati a <b>{valore}</b>.", parse_mode="HTML"
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ I tuoi slot sono stati modificati.\nSlot: {vecchio} → <b>{valore}</b>",
        admin_name=update.effective_user.first_name)
    logger.info("set_slot: team=%s valore=%d admin=%d", team_id, valore, update.effective_user.id)


# ── /apri_mercato e /chiudi_mercato ──────────────────────────────────────────

async def apri_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return
    admin_label = _admin_label(update.effective_user)
    _set_mercato(True)
    await update.effective_message.reply_text("✅ Mercato FA aperto.")
    from handlers.helpers import log_warn as _lw
    await _lw(context, f"🟢 Mercato FA aperto da <b>{admin_label}</b>.")


async def chiudi_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return
    admin_label = _admin_label(update.effective_user)
    _set_mercato(False)
    await update.effective_message.reply_text("🔒 Mercato FA chiuso.")
    from handlers.helpers import log_warn as _lw
    await _lw(context, f"🔒 Mercato FA chiuso da <b>{admin_label}</b>.")


# ── /aste ────────────────────────────────────────────────────────────────────

async def aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aste_aperte = db.get_aste_aperte()
    if not aste_aperte:
        await update.effective_message.reply_text("Nessuna asta aperta al momento.")
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
    await update.effective_message.reply_text("\n\n".join(righe), parse_mode="HTML")


# ── /chiudi_asta e /annulla_asta ─────────────────────────────────────────────

async def chiudi_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /chiudi_asta <asta_id>")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await update.effective_message.reply_text("❌ Asta non trovata o non aperta.")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma chiusura", callback_data=f"admin_chiudi:{asta_id}"),
        InlineKeyboardButton("❌ Annulla",           callback_data="admin_noop"),
    ]])
    await update.effective_message.reply_text(
        f"Chiudere forzatamente l'asta per <b>{asta['giocatore']}</b>?\n"
        f"Offerta attuale: {asta['offerta_corrente']}M\n"
        f"Il vincitore verrà contattato per la firma.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def annulla_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /annulla_asta <asta_id>")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] not in ("APERTA", "CHIUSA", "PAREGGIO"):
        await update.effective_message.reply_text("❌ Asta non trovata o già conclusa.")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma annullamento", callback_data=f"admin_annulla:{asta_id}"),
        InlineKeyboardButton("❌ Annulla",               callback_data="admin_noop"),
    ]])
    await update.effective_message.reply_text(
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

    admin_label = _admin_label(query.from_user)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    db.chiudi_asta(asta_id, now.isoformat())

    await _aggiorna_canale(context, asta_id)

    # annuncio nel canale
    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=f"⏰ Asta <b>{asta['giocatore']}</b> chiusa anticipatamente da <b>{admin_label}</b>.",
            parse_mode="HTML",
        )
    except Exception as e:
        await _log_warn(context, f"Annuncio chiusura anticipata canale fallito: {e}")

    # notifica watcher
    watchers = db.get_watchers(asta_id)
    gm_ids_vincitore = set()
    if asta["offerente_team_id"]:
        team_v = tm.get_team_by_id(asta["offerente_team_id"])
        if team_v:
            gm_ids_vincitore.update(team_v["gm_ids"])
    for gm_id in watchers:
        if gm_id not in gm_ids_vincitore:
            try:
                await context.bot.send_message(
                    chat_id=gm_id,
                    text=f"⏰ L'asta per <b>{asta['giocatore']}</b> è stata chiusa anticipatamente da <b>{admin_label}</b>.",
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica chiusura anticipata watcher {gm_id}: {e}")

    # notifica proprietario RFA (solo se è un'asta RFA e c'è un vincitore)
    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"] and asta["offerente_team_id"]:
        team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
        if team_prop:
            for gm_id in team_prop["gm_ids"]:
                try:
                    await context.bot.send_message(
                        chat_id=gm_id,
                        text=(
                            f"⏰ L'asta RFA per <b>{asta['giocatore']}</b> è stata chiusa anticipatamente "
                            f"da <b>{admin_label}</b>. Riceverai a breve la richiesta di pareggio."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    await _log_warn(context, f"Notifica chiusura anticipata proprietario RFA {gm_id}: {e}")

    if asta["offerente_team_id"]:
        from handlers.firma import chiedi_anni
        # notifica preventiva al GM vincitore che la chiusura è stata anticipata da un admin
        team_v = tm.get_team_by_id(asta["offerente_team_id"])
        if team_v:
            for gm_id in team_v["gm_ids"]:
                try:
                    await context.bot.send_message(
                        chat_id=gm_id,
                        text=(
                            f"⏰ L'asta per <b>{asta['giocatore']}</b> è stata chiusa anticipatamente "
                            f"da <b>{admin_label}</b>. Sei il vincitore con {asta['offerta_corrente']}M — "
                            f"riceverai a breve la richiesta di firma."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    await _log_warn(context, f"Notifica chiusura anticipata GM vincitore {gm_id}: {e}")
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

    admin_label = _admin_label(query.from_user)

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
                            f"⚠️ L'asta per <b>{asta['giocatore']}</b> è stata annullata da <b>{admin_label}</b>.\n"
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
                        text=f"⚠️ L'asta RFA per <b>{asta['giocatore']}</b> è stata annullata da <b>{admin_label}</b>.",
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
                    text=f"❌ L'asta per <b>{asta['giocatore']}</b> è stata annullata da <b>{admin_label}</b>.",
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica annullamento watcher {gm_id}: {e}")

    # annuncio nel canale
    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=f"❌ Asta annullata: <b>{asta['giocatore']}</b> (da {admin_label})",
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
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args or context.args[0] not in ("offseason", "regular"):
        await update.effective_message.reply_text("Uso: /set_fase <offseason|regular>")
        return

    fase = context.args[0]

    # leggi fase corrente
    with open(GLOBALS_PATH, "r", encoding="utf-8") as f:
        globals_data = json.load(f)

    fase_corrente = globals_data.get("fase", "offseason")
    if fase_corrente == fase:
        await update.effective_message.reply_text(f"Siamo già in fase <b>{fase}</b>.", parse_mode="HTML")
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

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")
    logger.info("set_fase: %s → %s admin=%d", fase_corrente, fase, update.effective_user.id)


# ── /reset_rfa ───────────────────────────────────────────────────────────────

async def reset_rfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chiede la nuova stagione e conferma prima di resettare."""
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        stagione_corrente = utils.get_stagione_corrente()
        await update.effective_message.reply_text(
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
    await update.effective_message.reply_text(
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
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /set_cap_penalizzato <team_id> <valore>")
        return

    team_id = context.args[0]
    try:
        valore = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("❌ Il valore deve essere un numero intero.")
        return

    if valore < 0:
        await update.effective_message.reply_text("❌ Il valore non può essere negativo.")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
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

    await update.effective_message.reply_text(
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
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /add_cap <team_id> <importo>\nEsempio: /add_cap bulls 15 oppure /add_cap bulls -10")
        return

    team_id = context.args[0]
    try:
        delta = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("❌ L'importo deve essere un numero intero (anche negativo).")
        return

    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return

    nuovo_cap = team["cap_disponibile"] + delta
    if nuovo_cap < 0:
        await update.effective_message.reply_text(f"❌ Il cap non può diventare negativo ({team['cap_disponibile']}M + {delta}M = {nuovo_cap}M).")
        return
    if nuovo_cap > settings.cap_massimo():
        await update.effective_message.reply_text(f"❌ Il cap non può superare {settings.cap_massimo()}M ({team['cap_disponibile']}M + {delta}M = {nuovo_cap}M).")
        return

    tm.set_cap(team_id, nuovo_cap)
    segno = "+" if delta >= 0 else ""
    await update.effective_message.reply_text(
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
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Uso: /add_slot <team_id> <importo>\nEsempio: /add_slot bulls 1 oppure /add_slot bulls -1")
        return
    team_id = context.args[0]
    try:
        delta = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("❌ L'importo deve essere un numero intero.")
        return
    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(f"❌ Team ID '<code>{team_id}</code>' non trovato.", parse_mode="HTML")
        return
    nuovo = team["slot_disponibili"] + delta
    if nuovo < 0:
        await update.effective_message.reply_text(f"❌ Gli slot non possono diventare negativi ({team['slot_disponibili']} + {delta} = {nuovo}).")
        return
    if nuovo > settings.slot_massimo():
        await update.effective_message.reply_text(f"❌ Gli slot non possono superare {settings.slot_massimo()} ({team['slot_disponibili']} + {delta} = {nuovo}).")
        return
    tm.set_slot(team_id, nuovo)
    segno = "+" if delta >= 0 else ""
    await update.effective_message.reply_text(
        f"✅ Slot di <b>{team['nome']}</b>: {team['slot_disponibili']} {segno}{delta} → <b>{nuovo}</b>", parse_mode="HTML"
    )
    await _notifica_gm_cap_slot(context, team,
        f"ℹ️ I tuoi slot sono stati modificati.\nSlot: {team['slot_disponibili']} {segno}{delta} → <b>{nuovo}</b>",
        admin_name=update.effective_user.first_name)
    logger.info("add_slot: team=%s delta=%d nuovo=%d admin=%d", team_id, delta, nuovo, update.effective_user.id)


# ── /annulla_offerta ──────────────────────────────────────────────────────────

async def annulla_offerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /annulla_offerta <asta_id>")
        return
    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return
    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await update.effective_message.reply_text("❌ Asta non trovata o non aperta.")
        return

    # preview senza eseguire — controlla se il nuovo vincitore avrebbe cap negativo
    preview = db.preview_annulla_ultima_offerta(asta_id)
    if preview is None:
        await update.effective_message.reply_text("❌ Nessuna offerta da annullare.")
        return

    admin_label = _admin_label(update.effective_user)

    if preview["nuovo_team"]:
        team_data = tm.get_team_by_id(preview["nuovo_team"])
        if team_data:
            cap_disp = team_data["cap_disponibile"]
            # cap virtuale attuale esclude già questa asta (il team non è vincitore ora)
            # dopo l'annullamento tornerebbe a essere vincitore aggiungendo nuovo_importo
            cap_virt = db.get_cap_virtuale(preview["nuovo_team"])
            cap_post = cap_virt + preview["nuovo_importo"]
            if cap_post > cap_disp:
                admin_group_id = utils.get_admin_group_id()
                if not admin_group_id:
                    await update.effective_message.reply_text(
                        "❌ Annullando questa offerta il cap virtuale di una squadra andrebbe in negativo, "
                        "ma <code>admin_group_id</code> non è configurato. Impossibile richiedere conferma.",
                        parse_mode="HTML",
                    )
                    return
                await _richiedi_conferma_cap_negativo(
                    context, dict(asta), 0, admin_group_id, admin_label,
                    cap_disp, cap_virt, cap_post, "annulla_off_cap"
                )
                await update.effective_message.reply_text(
                    f"⚠️ Richiesta di conferma inviata al gruppo admin.\n"
                    f"Annullando l'offerta su <b>{asta['giocatore']}</b> il cap virtuale "
                    f"di una squadra andrebbe in negativo.",
                    parse_mode="HTML",
                )
                return

    result = db.annulla_ultima_offerta(asta_id)
    if result is None:
        await update.effective_message.reply_text("❌ Nessuna offerta da annullare.")
        return

    admin_label = _admin_label(update.effective_user)
    teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    team_el = teams_map.get(result["team_eliminato"], result["team_eliminato"])
    nuovo_team = teams_map.get(result["nuovo_team"], "nessuno") if result["nuovo_team"] else "nessuno"

    await _aggiorna_canale(context, asta_id)

    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"⚠️ <b>Offerta annullata da {admin_label}</b>\n"
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
                        f"per <b>{asta['giocatore']}</b> è stata annullata da <b>{admin_label}</b>.\n"
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
                        f"⚠️ Offerta annullata da <b>{admin_label}</b> su <b>{asta['giocatore']}</b>\n"
                        f"Offerta eliminata: {result['offerta_eliminata']}M — {team_el}\n"
                        f"Offerta attuale: <b>{result['nuovo_importo']}M — {nuovo_team}</b>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica annulla_offerta watcher {gm_id}: {e}")

    await update.effective_message.reply_text(
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
        await update.effective_message.reply_text("⛔ Non sei registrato come GM.")
        return

    if not context.args or len(context.args) != 1:
        await update.effective_message.reply_text(
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
        await update.effective_message.reply_text("❌ L'importo deve essere un numero intero.")
        return

    if importo <= 0:
        await update.effective_message.reply_text("❌ L'importo deve essere positivo.")
        return

    vecchio_cap = team["cap_disponibile"]
    nuovo_cap = vecchio_cap + importo

    if nuovo_cap > settings.cap_massimo():
        await update.effective_message.reply_text(
            f"❌ Con questa aggiunta il tuo cap arriverebbe a <b>{nuovo_cap}M</b>, "
            f"superiore al limite massimo di <b>{settings.cap_massimo()}M</b>. "
            f"Contatta un admin se ritieni ci sia un errore.",
            parse_mode="HTML",
        )
        return

    tm.set_cap(team["id"], nuovo_cap)

    import utils as _utils
    from datetime import datetime, timezone
    ora = _utils.format_dt(datetime.now(timezone.utc).isoformat())

    await update.effective_message.reply_text(
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
        await update.effective_message.reply_text("⛔ Non sei registrato come GM.")
        return
    if not context.args or len(context.args) != 1:
        await update.effective_message.reply_text(
            "Uso: /autoslot &lt;importo&gt;\nEsempio: /autoslot 1\n\n"
            "<i>⚠️ Usare solo in caso di necessità. La richiesta viene segnalata agli admin per verifica.</i>",
            parse_mode="HTML",
        )
        return
    try:
        importo = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ L'importo deve essere un numero intero.")
        return
    if importo <= 0:
        await update.effective_message.reply_text("❌ L'importo deve essere positivo.")
        return

    vecchio = team["slot_disponibili"]
    nuovo = vecchio + importo

    if nuovo > settings.slot_massimo():
        await update.effective_message.reply_text(
            f"❌ Con questa aggiunta i tuoi slot arriverebbero a <b>{nuovo}</b>, "
            f"superiori al limite massimo di <b>{settings.slot_massimo()}</b>. "
            f"Contatta un admin se ritieni ci sia un errore.",
            parse_mode="HTML",
        )
        return

    tm.set_slot(team["id"], nuovo)

    import utils as _utils
    from datetime import datetime, timezone
    ora = _utils.format_dt(datetime.now(timezone.utc).isoformat())

    await update.effective_message.reply_text(
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


# ── /team ─────────────────────────────────────────────────────────────────────

async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra la situazione completa di una squadra, identica a /me ma per qualsiasi team.
    Solo admin. Uso: /team <team_id>
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Uso: /team &lt;team_id&gt;\n"
            "Esempio: /team bulls\n\n"
            "Usa /listteams per vedere tutti gli ID squadra.",
            parse_mode="HTML",
        )
        return

    team_id = context.args[0]
    team = tm.get_team_by_id(team_id)
    if team is None:
        await update.effective_message.reply_text(
            f"❌ Team ID '<code>{team_id}</code>' non trovato. Usa /listteams per la lista completa.",
            parse_mode="HTML",
        )
        return

    cap_tot        = team["cap_disponibile"]
    slot_tot       = team["slot_disponibili"]
    cap_virtuale   = db.get_cap_virtuale(team_id)
    slot_impegnati = db.get_slot_virtuali(team_id)
    cap_libero     = cap_tot - cap_virtuale
    slot_liberi    = slot_tot - slot_impegnati
    offerte_vince  = db.get_offerte_vincenti_team(team_id)

    fase = utils.load_globals().get("fase", "offseason")
    cap_pen = team.get("cap_penalizzato", 0)
    s = settings.get()

    righe = [
        f"🏀 <b>{team['nome']}</b> — vista admin",
        f"<i>ID: <code>{team_id}</code></i>",
        "",
        f"💰 Cap disponibile (offseason): <b>{cap_tot}M</b>",
        f"⏳ Cap virtualmente impegnato: <b>{cap_virtuale}M</b>",
        f"✅ Cap effettivamente libero: <b>{cap_libero}M</b>",
    ]

    if fase == "offseason":
        rfa_attive = db.get_rfa_proprietario(team_id)
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

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /estendi_asta ─────────────────────────────────────────────────────────────

async def estendi_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sposta la scadenza di un'asta in avanti di N ore.
    Solo admin. Uso: /estendi_asta <asta_id> <ore>
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Uso: /estendi_asta &lt;asta_id&gt; &lt;ore&gt;\nEsempio: /estendi_asta 42 6",
            parse_mode="HTML",
        )
        return

    try:
        asta_id = int(context.args[0])
        ore     = int(context.args[1])
        if ore == 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Parametri non validi. Le ore devono essere un intero diverso da zero "
            "(negativo per accorciare, es. -3)."
        )
        return

    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return
    if asta["stato"] not in ("APERTA", "CHIUSA", "PAREGGIO"):
        await update.effective_message.reply_text(
            f"❌ L'asta <b>{asta['giocatore']}</b> è in stato <b>{asta['stato']}</b>: "
            f"non è possibile estenderla.",
            parse_mode="HTML",
        )
        return

    vecchio = datetime.fromisoformat(asta["scade_at"])
    nuovo   = vecchio + timedelta(hours=ore)

    if nuovo <= datetime.now(timezone.utc):
        await update.effective_message.reply_text(
            f"❌ La nuova scadenza ({utils.format_dt(nuovo.isoformat())}) sarebbe nel passato. "
            f"Usa un valore negativo più piccolo o /sposta_asta per impostare una scadenza precisa."
        )
        return
    db.set_scade_at(asta_id, nuovo.isoformat())
    admin_label = _admin_label(update.effective_user)
    azione = f"estesa di {ore}h" if ore > 0 else f"accorciata di {abs(ore)}h"

    from handlers.helpers import aggiorna_canale, log_warn as _lw
    await aggiorna_canale(context, asta_id)
    await _lw(context, f"⏰ Asta <b>{asta['giocatore']}</b> {azione} da <b>{admin_label}</b>. Nuova scadenza: {utils.format_dt(nuovo.isoformat())}")

    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"⏰ Scadenza asta <b>{asta['giocatore']}</b> {azione} da <b>{admin_label}</b>.\n"
                f"Nuova scadenza: <b>{utils.format_dt(nuovo.isoformat())}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await _lw(context, f"Annuncio estensione canale fallito: {e}")

    await update.effective_message.reply_text(
        f"✅ Asta <b>{asta['giocatore']}</b> {azione}.\n"
        f"Nuova scadenza: <b>{utils.format_dt(nuovo.isoformat())}</b>",
        parse_mode="HTML",
    )
    logger.info("estendi_asta: asta_id=%d ore=%d nuova_scadenza=%s admin=%d",
                asta_id, ore, nuovo.isoformat(), update.effective_user.id)


# ── /sposta_asta ──────────────────────────────────────────────────────────────

async def sposta_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Imposta una scadenza precisa per un'asta.
    Solo admin. Uso: /sposta_asta <asta_id> <YYYY-MM-DDTHH:MM>
    La data/ora si intende in ora di Roma (Europe/Rome).
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Uso: /sposta_asta &lt;asta_id&gt; &lt;YYYY-MM-DDTHH:MM&gt;\n"
            "Esempio: /sposta_asta 42 2026-07-15T20:00\n"
            "L'orario si intende in ora di Roma.",
            parse_mode="HTML",
        )
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return

    try:
        from zoneinfo import ZoneInfo
        rome = ZoneInfo("Europe/Rome")
        nuovo_naive = datetime.fromisoformat(context.args[1])
        nuovo = nuovo_naive.replace(tzinfo=rome).astimezone(timezone.utc)
    except Exception:
        await update.effective_message.reply_text(
            "❌ Formato data non valido. Usa: <code>YYYY-MM-DDTHH:MM</code>",
            parse_mode="HTML",
        )
        return

    if nuovo <= datetime.now(timezone.utc):
        await update.effective_message.reply_text("❌ La nuova scadenza deve essere nel futuro.")
        return

    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return
    if asta["stato"] not in ("APERTA", "CHIUSA", "PAREGGIO"):
        await update.effective_message.reply_text(
            f"❌ L'asta <b>{asta['giocatore']}</b> è in stato <b>{asta['stato']}</b>: "
            f"non è possibile spostarla.",
            parse_mode="HTML",
        )
        return

    db.set_scade_at(asta_id, nuovo.isoformat())
    admin_label = _admin_label(update.effective_user)

    from handlers.helpers import aggiorna_canale, log_warn as _lw
    await aggiorna_canale(context, asta_id)
    await _lw(context, f"⏰ Asta <b>{asta['giocatore']}</b> spostata a {utils.format_dt(nuovo.isoformat())} da <b>{admin_label}</b>.")

    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"⏰ Scadenza asta <b>{asta['giocatore']}</b> spostata da <b>{admin_label}</b>.\n"
                f"Nuova scadenza: <b>{utils.format_dt(nuovo.isoformat())}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await _lw(context, f"Annuncio spostamento canale fallito: {e}")

    await update.effective_message.reply_text(
        f"✅ Scadenza asta <b>{asta['giocatore']}</b> spostata a "
        f"<b>{utils.format_dt(nuovo.isoformat())}</b>",
        parse_mode="HTML",
    )
    logger.info("sposta_asta: asta_id=%d nuova_scadenza=%s admin=%d",
                asta_id, nuovo.isoformat(), update.effective_user.id)


# ── /stato_asta ───────────────────────────────────────────────────────────────

async def stato_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dump completo di tutti i campi DB di un'asta. Utile per debug rapido
    senza dover aprire sqlite3.
    Solo admin. Uso: /stato_asta <asta_id>
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /stato_asta &lt;asta_id&gt;", parse_mode="HTML")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return

    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return

    righe = [f"🔍 <b>Dump asta #{asta_id}</b>", ""]
    for k, v in dict(asta).items():
        righe.append(f"<code>{k}</code>: {v}")

    offerte = db.get_offerte(asta_id)
    righe.append("")
    righe.append(f"<b>Offerte ({len(offerte)}):</b>")
    for o in offerte:
        righe.append(f"  {o['team_id']} — {o['importo']}M ({o['timestamp']})")

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /all_aste ─────────────────────────────────────────────────────────────────

async def all_aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lista tutte le aste con filtro opzionale per stato.
    Solo admin. Uso: /all_aste [stato]
    Stati validi: aperta, chiusa, pareggio, conclusa, annullata
    Senza argomenti mostra le ultime 30 aste di qualsiasi stato.
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    stati_validi = ("APERTA", "CHIUSA", "PAREGGIO", "CONCLUSA", "ANNULLATA")

    if context.args:
        filtro = context.args[0].upper()
        if filtro not in stati_validi:
            await update.effective_message.reply_text(
                f"❌ Stato non valido. Stati possibili: {', '.join(s.lower() for s in stati_validi)}",
            )
            return
        aste = db.get_aste_per_stato(filtro)
        titolo = f"<b>Aste {filtro} ({len(aste)})</b>"
    else:
        aste = db.get_all_aste()[:30]
        titolo = f"<b>Ultime {len(aste)} aste</b>"

    if not aste:
        await update.effective_message.reply_text("Nessuna asta trovata.")
        return

    righe = [titolo, ""]
    for a in aste:
        righe.append(
            f"#{a['id']} [{a['stato']}] {a['tipo']} — <b>{a['giocatore']}</b> "
            f"({a['offerta_corrente']}M, scade {utils.format_dt(a['scade_at'])})"
        )

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /riapri_asta ──────────────────────────────────────────────────────────────

async def riapri_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Riporta un'asta in stato CHIUSA o PAREGGIO allo stato APERTA,
    con una nuova scadenza di N ore da adesso (default 18h).
    Cancella i job di firma/pareggio pendenti e notifica nel canale.
    Solo admin. Uso: /riapri_asta <asta_id> [ore]
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Uso: /riapri_asta &lt;asta_id&gt; [ore]\n"
            "Esempio: /riapri_asta 42 6 — riapre con scadenza tra 6 ore\n"
            "Default: 18 ore se non specificato.",
            parse_mode="HTML",
        )
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Serve l'ID numerico dell'asta. Usa /all_aste chiusa per vedere gli ID."
        )
        return

    ore = 18
    if len(context.args) > 1:
        try:
            ore = int(context.args[1])
            if ore <= 0:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("❌ Le ore devono essere un intero positivo.")
            return

    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return
    if asta["stato"] not in ("CHIUSA", "PAREGGIO", "ANNULLATA"):
        await update.effective_message.reply_text(
            f"❌ L'asta <b>{asta['giocatore']}</b> è in stato <b>{asta['stato']}</b>: "
            f"/riapri_asta funziona solo su aste CHIUSA, PAREGGIO o ANNULLATA.",
            parse_mode="HTML",
        )
        return

    # Se ANNULLATA chiede conferma prima di procedere
    if asta["stato"] == "ANNULLATA":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Sì, riapri",
                callback_data=f"riapri_annullata:{asta_id}:{ore}"
            ),
            InlineKeyboardButton("❌ Annulla", callback_data="admin_noop"),
        ]])
        await update.effective_message.reply_text(
            f"⚠️ L'asta per <b>{asta['giocatore']}</b> è stata <b>annullata</b>.\n"
            f"Sei sicuro di volerla riaprire con scadenza tra <b>{ore}h</b>?",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # cancella job pendenti
    for nome in [f"firma_auto_{asta_id}", f"pareggio_auto_{asta_id}", f"notif15_{asta_id}"]:
        for j in context.job_queue.get_jobs_by_name(nome):
            j.schedule_removal()

    admin_label = _admin_label(update.effective_user)

    # check cap virtuale negativo — se l'offerta vincente sforerebbe il cap, chiede conferma al gruppo admin
    if asta["offerente_team_id"]:
        cap_disp, cap_virt, cap_post = _cap_virtuale_post_riapertura(dict(asta))
        if cap_post > cap_disp:
            admin_group_id = utils.get_admin_group_id()
            if not admin_group_id:
                await update.effective_message.reply_text(
                    "❌ Riaprendo questa asta il cap virtuale di una squadra andrebbe in negativo, "
                    "ma <code>admin_group_id</code> non è configurato in globals.json. "
                    "Impossibile richiedere conferma.",
                    parse_mode="HTML",
                )
                return
            await _richiedi_conferma_cap_negativo(
                context, dict(asta), ore, admin_group_id, admin_label,
                cap_disp, cap_virt, cap_post,
                "riapri_cap"
            )
            await update.effective_message.reply_text(
                f"⚠️ Richiesta di conferma inviata al gruppo admin.\n"
                f"Riaprendo l'asta di <b>{asta['giocatore']}</b> il cap virtuale di una squadra "
                f"andrebbe in negativo. Un admin dal gruppo deve confermare per procedere.",
                parse_mode="HTML",
            )
            return

    nuova_scadenza = datetime.now(timezone.utc) + timedelta(hours=ore)
    db.riapri_asta(asta_id, nuova_scadenza.isoformat())
    await _notifica_riapertura(context, dict(asta), nuova_scadenza, admin_label)

    await update.effective_message.reply_text(
        f"✅ Asta <b>{asta['giocatore']}</b> riaperta con scadenza tra {ore}h.\n"
        f"Nuova scadenza: <b>{utils.format_dt(nuova_scadenza.isoformat())}</b>",
        parse_mode="HTML",
    )
    logger.info("riapri_asta: asta_id=%d ore=%d nuova_scadenza=%s admin=%d",
                asta_id, ore, nuova_scadenza.isoformat(), update.effective_user.id)


async def _notifica_riapertura(context, asta: dict, nuova_scadenza, admin_label: str):
    """Invia annuncio canale e notifica watcher per riapertura asta."""
    from handlers.helpers import aggiorna_canale
    await aggiorna_canale(context, asta["id"])

    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"🔄 Asta <b>{asta['giocatore']}</b> riaperta da <b>{admin_label}</b>.\n"
                f"Nuova scadenza: <b>{utils.format_dt(nuova_scadenza.isoformat())}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await _log_warn(context, f"Annuncio riapertura canale fallito: {e}")

    watchers = db.get_watchers(asta["id"])
    for gm_id in watchers:
        try:
            await context.bot.send_message(
                chat_id=gm_id,
                text=(
                    f"🔄 L'asta per <b>{asta['giocatore']}</b> è stata riaperta da <b>{admin_label}</b>.\n"
                    f"Nuova scadenza: <b>{utils.format_dt(nuova_scadenza.isoformat())}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await _log_warn(context, f"Notifica riapertura watcher {gm_id}: {e}")



def _cap_virtuale_post_riapertura(asta: dict) -> tuple[int, int, int]:
    """
    Calcola cap virtuale attuale + offerta vincente per il team vincitore.
    Restituisce (cap_disponibile, cap_virtuale_attuale, cap_post_riapertura).
    Utile per verificare se riaprire un'asta porterebbe il team in negativo.
    """
    team_id = asta.get("offerente_team_id") if isinstance(asta, dict) else asta["offerente_team_id"]
    if not team_id:
        return 0, 0, 0
    team_data = tm.get_team_by_id(team_id)
    if not team_data:
        return 0, 0, 0
    cap_disp  = team_data["cap_disponibile"]
    cap_virt  = db.get_cap_virtuale(team_id)
    offerta   = asta["offerta_corrente"] or 0
    return cap_disp, cap_virt, cap_virt + offerta


async def _richiedi_conferma_cap_negativo(context, asta: dict, ore: int,
                                           admin_group_id: int, admin_label: str,
                                           cap_disp: int, cap_virt: int, cap_post: int,
                                           callback_prefix: str):
    """
    Invia al gruppo admin un warning con bottone di conferma per operazione
    che porterebbe il cap virtuale in negativo.
    callback_prefix: 'riapri_cap' o 'annulla_off_cap'
    """
    team_id = asta["offerente_team_id"]
    team_data = tm.get_team_by_id(team_id)
    team_nome = team_data["nome"] if team_data else team_id
    deficit = cap_post - cap_disp

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Confermo, procedi",
            callback_data=f"{callback_prefix}:{asta['id']}:{ore}"
        ),
        InlineKeyboardButton("❌ Annulla", callback_data="admin_noop"),
    ]])
    await context.bot.send_message(
        chat_id=admin_group_id,
        text=(
            f"⚠️ <b>Attenzione — cap virtuale negativo</b>\n\n"
            f"L'operazione richiesta da <b>{admin_label}</b> su <b>{asta['giocatore']}</b> "
            f"porterebbe <b>{team_nome}</b> a cap virtuale negativo:\n"
            f"Cap disponibile: {cap_disp}M\n"
            f"Cap virtuale attuale: {cap_virt}M\n"
            f"Cap virtuale dopo: <b>{cap_post}M (+{deficit}M oltre il limite)</b>\n\n"
            f"Confermare per procedere comunque."
        ),
        parse_mode="HTML",
        reply_markup=kb,
    )


async def riapri_annullata_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback di conferma per riapertura asta ANNULLATA o con cap negativo."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    prefix    = parts[0]
    asta_id   = int(parts[1])
    ore       = int(parts[2])

    asta = db.get_asta(asta_id)
    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    if prefix == "riapri_annullata" and asta["stato"] != "ANNULLATA":
        await query.edit_message_text("❌ Asta non più in stato ANNULLATA.")
        return
    if prefix == "riapri_cap" and asta["stato"] not in ("CHIUSA", "PAREGGIO", "ANNULLATA"):
        await query.edit_message_text("❌ Stato asta cambiato, operazione annullata.")
        return

    nuova_scadenza = datetime.now(timezone.utc) + timedelta(hours=ore)

    # Se arriva da riapri_annullata ma c'è anche cap negativo, controlla ancora
    if prefix == "riapri_annullata":
        cap_disp, cap_virt, cap_post = _cap_virtuale_post_riapertura(dict(asta))
        if cap_post > cap_disp:
            admin_group_id = utils.get_admin_group_id()
            if not admin_group_id:
                await query.edit_message_text(
                    "❌ `admin_group_id` non configurato in globals.json. "
                    "Impossibile richiedere conferma per cap negativo.",
                    parse_mode="HTML",
                )
                return
            admin_label = _admin_label(query.from_user)
            await _richiedi_conferma_cap_negativo(
                context, dict(asta), ore, admin_group_id, admin_label,
                cap_disp, cap_virt, cap_post, "riapri_cap"
            )
            await query.edit_message_text(
                f"⚠️ Richiesta di conferma inviata al gruppo admin "
                f"(cap virtuale negativo per {asta['giocatore']})."
            )
            return

    # Esegui riapertura
    for nome in [f"firma_auto_{asta_id}", f"pareggio_auto_{asta_id}", f"notif15_{asta_id}"]:
        for j in context.job_queue.get_jobs_by_name(nome):
            j.schedule_removal()

    db.riapri_asta(asta_id, nuova_scadenza.isoformat())
    admin_label = _admin_label(query.from_user)
    await _notifica_riapertura(context, dict(asta), nuova_scadenza, admin_label)

    await query.edit_message_text(
        f"✅ Asta <b>{asta['giocatore']}</b> riaperta con scadenza tra {ore}h.\n"
        f"Nuova scadenza: <b>{utils.format_dt(nuova_scadenza.isoformat())}</b>",
        parse_mode="HTML",
    )
    logger.info("riapri_callback: asta_id=%d ore=%d prefix=%s admin=%d",
                asta_id, ore, prefix, query.from_user.id)


# ── /ripubblica_asta ───────────────────────────────────────────────────────────

async def ripubblica_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Pubblica un nuovo messaggio nel canale per un'asta esistente e aggiorna
    canale_msg_id nel DB. Utile se il messaggio originale è stato cancellato
    accidentalmente e aggiorna_canale continua a fallire.
    Solo admin.
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /ripubblica_asta &lt;asta_id&gt;", parse_mode="HTML")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return

    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return
    if asta["stato"] in ("CONCLUSA", "ANNULLATA"):
        await update.effective_message.reply_text(
            f"❌ L'asta per <b>{asta['giocatore']}</b> è già {asta['stato'].lower()}, "
            f"non ha senso ripubblicarla.",
            parse_mode="HTML",
        )
        return

    teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    offerte = db.get_offerte(asta_id)
    testo = utils.build_canale_message(asta, offerte, teams_map)
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
        msg = await context.bot.send_message(
            chat_id=channel_id,
            text=testo,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Invio nel canale fallito: {e}")
        return

    db.set_canale_msg_id(asta_id, msg.message_id)

    await update.effective_message.reply_text(
        f"✅ Asta <b>{asta['giocatore']}</b> ripubblicata nel canale.\n"
        f"Nuovo canale_msg_id: <code>{msg.message_id}</code>",
        parse_mode="HTML",
    )
    logger.info("ripubblica_asta: asta_id=%d nuovo_msg_id=%d admin=%d",
                asta_id, msg.message_id, update.effective_user.id)


# ── /force_esito ──────────────────────────────────────────────────────────────

async def force_esito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Rimanda manualmente il messaggio di firma/pareggio al GM o proprietario,
    in base allo stato attuale dell'asta. Utile quando il job automatico è
    saltato o il GM non ha ricevuto il messaggio (es. aveva bloccato il bot).
    Non cambia lo stato dell'asta nel DB, si limita a reinviare i messaggi.
    Solo admin.
    """
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /force_esito &lt;asta_id&gt;", parse_mode="HTML")
        return

    try:
        asta_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso.")
        return

    asta = db.get_asta(asta_id)
    if not asta:
        await update.effective_message.reply_text("❌ Asta non trovata.")
        return

    stato = asta["stato"]

    if stato == "CHIUSA":
        from handlers.firma import chiedi_anni, _chiedi_firma_proprietario_senza_offerte
        if asta["offerente_team_id"]:
            await chiedi_anni(context, asta_id)
            await update.effective_message.reply_text(
                f"✅ Rimandato il messaggio di scelta anni al GM vincitore di <b>{asta['giocatore']}</b>.",
                parse_mode="HTML",
            )
        else:
            if asta["tipo"] == "RFA":
                await _chiedi_firma_proprietario_senza_offerte(context, asta_id)
                await update.effective_message.reply_text(
                    f"✅ Rimandato il messaggio al proprietario RFA di <b>{asta['giocatore']}</b> "
                    f"(nessuna offerta — firma o lascia andare).",
                    parse_mode="HTML",
                )
            else:
                await update.effective_message.reply_text(
                    f"❌ Asta FA <b>{asta['giocatore']}</b> in stato CHIUSA senza offerte: "
                    f"nessun messaggio da mandare, andrebbe annullata con /annulla_asta.",
                    parse_mode="HTML",
                )
            return

    elif stato == "PAREGGIO":
        if not asta["anni_offerti"]:
            await update.effective_message.reply_text(
                f"❌ Asta <b>{asta['giocatore']}</b> in PAREGGIO ma senza anni_offerti nel DB. "
                f"Dato inconsistente, intervenire manualmente.",
                parse_mode="HTML",
            )
            return
        from handlers.firma import _chiedi_pareggio
        await _chiedi_pareggio(context, asta_id, asta["anni_offerti"])
        await update.effective_message.reply_text(
            f"✅ Rimandato il messaggio di pareggio al proprietario RFA di <b>{asta['giocatore']}</b>.",
            parse_mode="HTML",
        )

    else:
        await update.effective_message.reply_text(
            f"❌ L'asta <b>{asta['giocatore']}</b> è in stato <b>{stato}</b>: "
            f"/force_esito funziona solo su aste in stato CHIUSA o PAREGGIO.",
            parse_mode="HTML",
        )
        return

    logger.info("force_esito: asta_id=%d stato=%s admin=%d", asta_id, stato, update.effective_user.id)


async def annulla_off_cap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback di conferma per annullamento offerta con cap virtuale negativo."""
    query = update.callback_query
    await query.answer()

    _, asta_id_s, _ = query.data.split(":")
    asta_id = int(asta_id_s)

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] != "APERTA":
        await query.edit_message_text("❌ Asta non più aperta.")
        return

    result = db.annulla_ultima_offerta(asta_id)
    if result is None:
        await query.edit_message_text("❌ Nessuna offerta da annullare.")
        return

    admin_label = _admin_label(query.from_user)
    teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
    team_el   = teams_map.get(result["team_eliminato"], result["team_eliminato"])
    nuovo_team = teams_map.get(result["nuovo_team"], "nessuno") if result["nuovo_team"] else "nessuno"

    await _aggiorna_canale(context, asta_id)

    channel_id = utils.get_channel_id()
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"⚠️ <b>Offerta annullata da {admin_label}</b> (confermato dal gruppo admin)\n"
                f"🏀 {asta['giocatore']}\n"
                f"Offerta eliminata: {result['offerta_eliminata']}M — {team_el}\n"
                f"Offerta attuale: {result['nuovo_importo']}M — {nuovo_team}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await _log_warn(context, f"Annuncio annulla_offerta canale fallito: {e}")

    await query.edit_message_text(
        f"✅ Offerta annullata su <b>{asta['giocatore']}</b>.\n"
        f"⚠️ Il cap virtuale di <b>{nuovo_team}</b> potrebbe essere in negativo — verificare.",
        parse_mode="HTML",
    )
    logger.info("annulla_off_cap_callback: asta_id=%d admin=%d", asta_id, query.from_user.id)


# ── /admin ────────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Non sei autorizzato.")
        return

    testo = (
        "<b>Comandi admin</b>\n"
        "\n"
        "<b>🏀 Aste</b>\n"
        "/nuova_rfa &lt;giocatore&gt; &lt;team_id&gt; &lt;vecchio_compenso&gt; — apre asta RFA\n"
        "/aste — lista aste aperte\n"
        "/all_aste [stato] — lista tutte le aste con filtro opzionale per stato\n"
        "/chiudi_asta &lt;asta_id&gt; — chiude forzatamente un'asta\n"
        "/annulla_asta &lt;asta_id&gt; — annulla un'asta\n"
        "/annulla_offerta &lt;asta_id&gt; — annulla ultima offerta\n"
        "/estendi_asta &lt;asta_id&gt; &lt;ore&gt; — sposta la scadenza in avanti di N ore\n"
        "/sposta_asta &lt;asta_id&gt; &lt;YYYY-MM-DDTHH:MM&gt; — imposta scadenza precisa (ora di Roma)\n"
        "/riapri_asta &lt;asta_id&gt; [ore] — riporta asta CHIUSA/PAREGGIO/ANNULLATA ad APERTA (conferma se ANNULLATA)\n"
        "/reset_rfa — resetta flag RFA per nuova stagione\n"
        "\n"
        "<b>💰 Cap e slot</b>\n"
        "/set_cap &lt;team_id&gt; &lt;valore&gt; — imposta cap squadra\n"
        "/add_cap &lt;team_id&gt; &lt;importo&gt; — aggiunge/sottrae cap (accetta negativi)\n"
        "/set_slot &lt;team_id&gt; &lt;valore&gt; — imposta slot squadra\n"
        "/add_slot &lt;team_id&gt; &lt;importo&gt; — aggiunge/sottrae slot\n"
        "/set_cap_penalizzato &lt;team_id&gt; &lt;valore&gt; — imposta penalità cap\n"
        "\n"
        "<b>🏟️ Lega</b>\n"
        "/apri_mercato — apre il mercato FA\n"
        "/chiudi_mercato — chiude il mercato FA\n"
        "/set_fase &lt;offseason|regular&gt; — cambia fase e scala cap\n"
        "/listteams — lista squadre con ID e cap\n"
        "/team &lt;team_id&gt; — situazione cap e slot di una squadra\n"
        "\n"
        "<b>🔧 Recovery ed emergenza</b>\n"
        "/ripubblica_asta &lt;asta_id&gt; — ripubblica il messaggio canale se cancellato per errore\n"
        "/force_esito &lt;asta_id&gt; — rimanda il messaggio di firma/pareggio al GM senza reboottare\n"
        "\n"
        "<b>🔍 Diagnostica</b>\n"
        "/stato_asta &lt;asta_id&gt; — dump completo del record DB di un'asta\n"
        "\n"
        "/admin — questo messaggio"
    )
    await update.effective_message.reply_text(testo, parse_mode="HTML")


def get_handlers():
    return [
        CommandHandler("nuova_rfa",        nuova_rfa),
        CommandHandler("team",             cmd_team),
        CommandHandler("listteams",        listteams),
        CommandHandler("set_cap",          set_cap),
        CommandHandler("set_slot",         set_slot),
        CommandHandler("apri_mercato",     apri_mercato),
        CommandHandler("chiudi_mercato",   chiudi_mercato),
        CommandHandler("set_fase",         set_fase),
        CommandHandler("aste",             aste),
        CommandHandler("chiudi_asta",      chiudi_asta),
        CommandHandler("annulla_asta",     annulla_asta),
        CommandHandler("reset_rfa",            reset_rfa),
        CommandHandler("set_cap_penalizzato",   set_cap_penalizzato),
        CommandHandler("all_aste",              all_aste),
        CommandHandler("riapri_asta",           riapri_asta),
        CallbackQueryHandler(riapri_annullata_callback, pattern=r"^riapri_annullata:\d+:\d+$"),
        CallbackQueryHandler(riapri_annullata_callback, pattern=r"^riapri_cap:\d+:\d+$"),
        CallbackQueryHandler(annulla_off_cap_callback,  pattern=r"^annulla_off_cap:\d+:\d+$"),
        CommandHandler("ripubblica_asta",       ripubblica_asta),
        CommandHandler("force_esito",           force_esito),
        CommandHandler("estendi_asta",          estendi_asta),
        CommandHandler("sposta_asta",           sposta_asta),
        CommandHandler("stato_asta",            stato_asta),
        CallbackQueryHandler(reset_rfa_callback, pattern=r"^reset_rfa:conferma$"),
        CommandHandler("admin",            cmd_admin),
        CommandHandler("add_cap",          add_cap),
        CommandHandler("add_slot",         add_slot),
        CommandHandler("annulla_offerta",  annulla_offerta),
        CallbackQueryHandler(admin_chiudi_callback,  pattern=r"^admin_chiudi:\d+$"),
        CallbackQueryHandler(admin_annulla_callback, pattern=r"^admin_annulla:\d+$"),
        CallbackQueryHandler(admin_noop_callback,    pattern=r"^admin_noop$"),
    ]
