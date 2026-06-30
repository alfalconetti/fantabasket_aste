"""
Flusso firma post-asta.

FA:
  - vincitore ha 48h per scegliere anni (dalla chiusura asta)
  - default automatico: 3 anni come penale

RFA:
  - vincitore ha 12h per scegliere anni (dalla chiusura asta)
  - default automatico: minimo per fascia (1 se <20, 2 se 20-34, 3 se >=35)
  - dopo che il vincitore sceglie gli anni → stato PAREGGIO
  - proprietario ha 24h per pareggiare (sì/no)
  - se no o timeout: giocatore passa al vincitore
  - se nessuna offerta: proprietario può firmare o lasciare andare

Principio di responsabilità singola:
  - _registra_firma_finale: gestisce SEMPRE la liberazione cap RFA + scala cap/slot + contratto
  - _concludi_rfa_senza_contratto: gestisce chiusura RFA senza firma (lascia andare, annullata senza offerte)
  - I chiamanti non devono mai chiamare libera_cap direttamente
"""
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

import database as db
import teams as tm
import utils
import settings
from handlers.helpers import aggiorna_canale as _aggiorna_canale, notifica_admin_group as _notifica_admin_group, notifica_watchers as _notifica_watchers, log_warn as _log_warn

logger = logging.getLogger(__name__)


# ── helpers soglie RFA ────────────────────────────────────────────────────────

def anni_minimi(importo: int) -> int:
    s = settings.get()
    if importo >= s["soglia_anni_3"]:
        return 3
    if importo >= s["soglia_anni_2"]:
        return 2
    return 1


def soglia_pareggio(vecchio_compenso: int) -> int:
    """
    Importo minimo che il proprietario deve offrire.
    ≤fascia_bassa_max: 1M
    fascia_bassa_max+1..fascia_media_max: ⌊vecchio/3⌋
    >fascia_media_max: ⌊vecchio/2⌋
    """
    s = settings.get()
    if vecchio_compenso <= s["fascia_bassa_max"]:
        return 1
    if vecchio_compenso <= s["fascia_media_max"]:
        return vecchio_compenso // 3
    return vecchio_compenso // 2


def importo_minimo_pareggio(offerta: int, vecchio_compenso: int) -> int:
    soglia = soglia_pareggio(vecchio_compenso)
    return max(offerta, soglia)


# ── chiusura RFA senza contratto (lascia andare / annullata senza offerte) ────

async def _concludi_rfa_senza_contratto(context, asta_id: int):
    """
    Chiude una RFA senza assegnare nessun contratto.
    Libera il vecchio compenso dal cap del proprietario.
    """
    asta = db.get_asta(asta_id)
    if not asta:
        return
    if asta["vecchio_compenso"]:
        tm.libera_cap(asta["squadra_proprietaria"], asta["vecchio_compenso"])
        logger.info("Liberato vecchio compenso %dM per RFA asta_id=%d", asta["vecchio_compenso"], asta_id)


# ── notifica admin group ──────────────────────────────────────────────────────

# ── chiedi anni al vincitore ──────────────────────────────────────────────────

async def chiedi_anni(context: ContextTypes.DEFAULT_TYPE, asta_id: int):
    asta = db.get_asta(asta_id)
    if not asta or not asta["offerente_team_id"]:
        if asta and asta["tipo"] == "RFA":
            await _chiedi_firma_proprietario_senza_offerte(context, asta_id)
        return

    team = tm.get_team_by_id(asta["offerente_team_id"])
    if not team:
        return

    importo = asta["offerta_corrente"]
    is_rfa  = asta["tipo"] == "RFA"
    ore     = settings.timeout_firma_rfa_ore() if is_rfa else settings.timeout_firma_fa_ore()

    bottoni = []
    for a in [1, 2, 3]:
        if a >= anni_minimi(importo):
            bottoni.append(InlineKeyboardButton(
                f"{a} ann{'o' if a==1 else 'i'}",
                callback_data=f"firma:{asta_id}:{a}"
            ))
    kb = InlineKeyboardMarkup([bottoni])

    s = settings.get()
    nota_anni = f"<i>({s['soglia_anni_2']}–{s['fascia_media_max']}M → min 2 anni · {s['soglia_anni_3']}M+ → 3 anni obbligatori)</i>"
    testo = (
        f"🏆 <b>Hai vinto l'asta per {asta['giocatore']}!</b>\n\n"
        f"💰 Offerta: <b>{importo}M</b>\n\n"
        f"Scegli per quanti anni firmare il contratto.\n{nota_anni}\n\n"
        f"⏰ Hai <b>{ore} ore</b> per rispondere.\n"
        f"Se non rispondi, {'gli anni minimi verranno assegnati automaticamente e il proprietario verrà avvisato' if is_rfa else 'il contratto sarà firmato automaticamente per 3 anni (penale)'}"
        f"."
    )

    inviato = False
    for gm_id in team["gm_ids"]:
        try:
            await context.bot.send_message(
                chat_id=gm_id, text=testo, parse_mode="HTML", reply_markup=kb
            )
            inviato = True
        except Exception as e:
            await _log_warn(context, f"Impossibile contattare GM {gm_id}: {e}")

    if not inviato:
        await _notifica_admin_group(
            context,
            f"⚠️ Impossibile contattare il GM vincitore di <b>{asta['giocatore']}</b> "
            f"(team: {team['nome']}) per la firma. Intervenire manualmente."
        )

    timeout_h = settings.timeout_firma_rfa_ore() if is_rfa else settings.timeout_firma_fa_ore()
    context.job_queue.run_once(
        firma_automatica,
        when=timeout_h * 3600,
        data={"asta_id": asta_id},
        name=f"firma_auto_{asta_id}",
    )
    logger.info("chiedi_anni: asta_id=%d team=%s timeout=%dh", asta_id, team["id"], timeout_h)


# ── callback scelta anni vincitore ────────────────────────────────────────────

async def firma_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, asta_id_s, anni_s = query.data.split(":")
    asta_id = int(asta_id_s)
    anni    = int(anni_s)

    asta = db.get_asta(asta_id)
    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    if asta["stato"] in ("PAREGGIO", "CONCLUSA", "ANNULLATA"):
        await query.edit_message_text("✅ Firma già registrata o asta annullata.")
        return

    if asta["stato"] != "CHIUSA":
        await query.edit_message_text("❌ L'asta non è in fase di firma.")
        return

    team = tm.get_team_by_gm(query.from_user.id)
    if not team or team["id"] != asta["offerente_team_id"]:
        await query.edit_message_text("⛔ Non sei il GM vincitore di questa asta.")
        return

    errore = _valida_anni(asta["offerta_corrente"], anni)
    if errore:
        await query.edit_message_text(f"❌ {errore}")
        return

    for j in context.job_queue.get_jobs_by_name(f"firma_auto_{asta_id}"):
        j.schedule_removal()

    if asta["tipo"] == "FA":
        await _registra_firma_finale(context, asta_id, anni, team["id"], query=query)
    else:
        # Salva prima nel DB, poi contatta proprietario, poi aggiorna canale
        db.set_anni_offerti(asta_id, anni)
        await _chiedi_pareggio(context, asta_id, anni)
        await _aggiorna_canale(context, asta_id)
        await query.edit_message_text(
            f"✅ Anni registrati: <b>{anni}</b> per <b>{asta['giocatore']}</b> a <b>{asta['offerta_corrente']}M</b>.\n\n"
            f"Il proprietario dei diritti ha 24 ore per decidere se pareggiare l'offerta.",
            parse_mode="HTML",
        )


# ── firma automatica (timeout vincitore) ─────────────────────────────────────

async def firma_automatica(context: ContextTypes.DEFAULT_TYPE):
    try:
        asta_id = context.job.data["asta_id"]
        asta = db.get_asta(asta_id)
        if not asta or asta["stato"] not in ("CHIUSA",):
            return

        if asta["tipo"] == "FA":
            anni = 3
            logger.info("Firma automatica FA (penale 3 anni): asta_id=%d", asta_id)
            await _registra_firma_finale(context, asta_id, anni, asta["offerente_team_id"])
        else:
            anni = anni_minimi(asta["offerta_corrente"])
            logger.info("Firma automatica RFA vincitore (anni minimi %d): asta_id=%d", anni, asta_id)
            db.set_anni_offerti(asta_id, anni)
            await _aggiorna_canale(context, asta_id)

            team = tm.get_team_by_id(asta["offerente_team_id"])
            if team:
                for gm_id in team["gm_ids"]:
                    try:
                        await context.bot.send_message(
                            chat_id=gm_id,
                            text=(
                                f"⚠️ Non hai risposto in tempo. Anni assegnati automaticamente: <b>{anni}</b> "
                                f"per <b>{asta['giocatore']}</b> a <b>{asta['offerta_corrente']}M</b>.\n\n"
                                f"Il proprietario dei diritti ha ora 24 ore per decidere se pareggiare."
                            ),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        await _log_warn(context, f"Notifica auto vincitore RFA fallita GM {gm_id}: {e}")

        await _chiedi_pareggio(context, asta_id, anni)
    except Exception as e:
        from handlers.helpers import log_job_error
        await log_job_error(context, "firma_automatica", e)


# ── fase pareggio RFA ─────────────────────────────────────────────────────────

async def _chiedi_pareggio(context, asta_id: int, anni_vincitore: int):
    asta = db.get_asta(asta_id)
    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop:
        return

    importo     = asta["offerta_corrente"]
    vec_comp    = asta["vecchio_compenso"] or 0
    min_importo = importo_minimo_pareggio(importo, vec_comp)
    min_anni    = anni_vincitore

    team_vince = tm.get_team_by_id(asta["offerente_team_id"])
    team_vince_nome = team_vince["nome"] if team_vince else "altra franchigia"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Pareggio", callback_data=f"pareggio:{asta_id}:si"),
        InlineKeyboardButton("❌ Rinuncio", callback_data=f"pareggio:{asta_id}:no"),
    ]])

    nota_fascia = ""
    sotto_soglia = importo < soglia_pareggio(vec_comp)
    if vec_comp > 20 and sotto_soglia:
        fraz = "1/3" if vec_comp <= 34 else "1/2"
        nota_fascia = (
            f"\n\n⚠️ <i>L'offerta ricevuta ({importo}M) è inferiore a {fraz} del vecchio compenso "
            f"({vec_comp}M). Devi offrire almeno {min_importo}M, ma puoi scegliere gli anni liberamente.</i>"
        )

    testo = (
        f"⚖️ <b>Decisione RFA — {asta['giocatore']}</b>\n\n"
        f"<b>{team_vince_nome}</b> ha offerto: <b>{importo}M × {anni_vincitore} ann{'o' if anni_vincitore==1 else 'i'}</b>\n\n"
        f"Per pareggiare devi offrire:\n"
        f"  💰 Almeno <b>{min_importo}M</b>\n"
        f"  📅 Almeno <b>{min_anni} ann{'o' if min_anni==1 else 'i'}</b>"
        f"{nota_fascia}\n\n"
        f"Sei il proprietario dei diritti di <b>{asta['giocatore']}</b>.\n"
        f"Hai <b>24 ore</b> per decidere."
    )

    inviato = False
    for gm_id in team_prop["gm_ids"]:
        try:
            await context.bot.send_message(
                chat_id=gm_id, text=testo, parse_mode="HTML", reply_markup=kb
            )
            inviato = True
        except Exception as e:
            await _log_warn(context, f"Impossibile contattare GM pareggio {gm_id}: {e}")

    if not inviato:
        await _notifica_admin_group(
            context,
            f"⚠️ Impossibile contattare il proprietario RFA <b>{asta['giocatore']}</b> "
            f"(team: {team_prop['nome']}) per il pareggio. Intervenire manualmente."
        )

    context.job_queue.run_once(
        pareggio_automatico,
        when=settings.timeout_pareggio_ore() * 3600,
        data={"asta_id": asta_id},
        name=f"pareggio_auto_{asta_id}",
    )
    logger.info("Fase pareggio avviata: asta_id=%d", asta_id)


async def pareggio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, asta_id_s, scelta = query.data.split(":")
    asta_id = int(asta_id_s)
    asta    = db.get_asta(asta_id)

    if not asta or asta["stato"] != "PAREGGIO":
        await query.edit_message_text("✅ Fase pareggio già conclusa.")
        return

    team = tm.get_team_by_gm(query.from_user.id)
    if not team or team["id"] != asta["squadra_proprietaria"]:
        await query.edit_message_text("⛔ Non sei il proprietario di questo RFA.")
        return

    for j in context.job_queue.get_jobs_by_name(f"pareggio_auto_{asta_id}"):
        j.schedule_removal()

    if scelta == "indietro":
        await _chiedi_pareggio(context, asta_id, asta["anni_offerti"])
        await query.delete_message()
        return

    if scelta == "no":
        team_vince = tm.get_team_by_id(asta["offerente_team_id"])
        team_vince_nome = team_vince["nome"] if team_vince else "altra franchigia"
        await query.edit_message_text(
            f"❌ Hai rinunciato a pareggiare.\n"
            f"<b>{asta['giocatore']}</b> passa a <b>{team_vince_nome}</b> "
            f"a {asta['offerta_corrente']}M × {asta['anni_offerti']} ann{'o' if asta['anni_offerti']==1 else 'i'}.",
            parse_mode="HTML",
        )
        await _registra_firma_finale(
            context, asta_id, asta["anni_offerti"], asta["offerente_team_id"]
        )
    else:
        # pareggio: mostra anni disponibili
        importo     = asta["offerta_corrente"]
        vec_comp    = asta["vecchio_compenso"] or 0
        min_importo = importo_minimo_pareggio(importo, vec_comp)
        min_anni    = asta["anni_offerti"]
        sotto_soglia = importo < soglia_pareggio(vec_comp)

        if sotto_soglia:
            min_anni_btn = anni_minimi(min_importo)
            nota_anni = f"minimo {min_anni_btn} (offerta sotto soglia — anni liberi)"
        else:
            min_anni_btn = max(min_anni, anni_minimi(min_importo))
            nota_anni = f"minimo {min_anni_btn}"

        anni_btn = []
        for a in [1, 2, 3]:
            if a >= min_anni_btn:
                anni_btn.append(InlineKeyboardButton(
                    f"{a} ann{'o' if a==1 else 'i'}",
                    callback_data=f"pareggio_anni:{asta_id}:{a}"
                ))
        kb = InlineKeyboardMarkup([
            anni_btn,
            [InlineKeyboardButton("← Indietro", callback_data=f"pareggio:{asta_id}:indietro")],
        ])

        await query.edit_message_text(
            f"Stai pareggiando per <b>{asta['giocatore']}</b>.\n"
            f"Importo pareggio: <b>{min_importo}M</b>\n\n"
            f"Scegli gli anni ({nota_anni}):",
            parse_mode="HTML",
            reply_markup=kb,
        )


async def pareggio_anni_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, asta_id_s, anni_s = query.data.split(":")
    asta_id = int(asta_id_s)
    anni    = int(anni_s)
    asta    = db.get_asta(asta_id)

    if not asta or asta["stato"] != "PAREGGIO":
        await query.edit_message_text("✅ Già conclusa.")
        return

    team = tm.get_team_by_gm(query.from_user.id)
    if not team or team["id"] != asta["squadra_proprietaria"]:
        await query.edit_message_text("⛔ Non sei il proprietario di questo RFA.")
        return

    vec_comp    = asta["vecchio_compenso"] or 0
    importo     = importo_minimo_pareggio(asta["offerta_corrente"], vec_comp)

    cap_impegnato  = db.get_cap_virtuale(team["id"])
    slot_impegnati = db.get_slot_virtuali(team["id"])
    team_data      = tm.get_team_by_id(team["id"])
    cap_libero     = team_data["cap_disponibile"] - cap_impegnato + vec_comp
    slot_liberi    = team_data["slot_disponibili"] - slot_impegnati

    if cap_libero < importo:
        await query.edit_message_text(
            f"❌ Cap insufficiente per pareggiare.\n"
            f"Importo pareggio: <b>{importo}M</b>\n"
            f"Cap effettivo (dopo liberazione vecchio contratto da {vec_comp}M): <b>{cap_libero}M</b>.",
            parse_mode="HTML",
        )
        return
    if slot_liberi <= 0:
        await query.edit_message_text(
            f"❌ Nessuno slot libero per pareggiare.\n"
            f"Slot virtuali già impegnati: {slot_impegnati}.",
            parse_mode="HTML",
        )
        return

    await query.edit_message_text(
        f"✅ Pareggio confermato!\n"
        f"<b>{asta['giocatore']}</b> rimane con <b>{team['nome']}</b>\n"
        f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}",
        parse_mode="HTML",
    )
    await _registra_firma_finale(context, asta_id, anni, asta["squadra_proprietaria"], importo_override=importo)


async def pareggio_automatico(context: ContextTypes.DEFAULT_TYPE):
    try:
        asta_id = context.job.data["asta_id"]
        asta    = db.get_asta(asta_id)
        if not asta or asta["stato"] != "PAREGGIO":
            return
        logger.info("Pareggio automatico scaduto: asta_id=%d — passa al vincitore", asta_id)

        team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
        team_vince = tm.get_team_by_id(asta["offerente_team_id"])
        team_vince_nome = team_vince["nome"] if team_vince else "altra franchigia"

        if team_prop:
            for gm_id in team_prop["gm_ids"]:
                try:
                    await context.bot.send_message(
                        chat_id=gm_id,
                        text=(
                            f"⏰ Tempo scaduto per il pareggio.\n"
                            f"<b>{asta['giocatore']}</b> passa a <b>{team_vince_nome}</b> "
                            f"a {asta['offerta_corrente']}M × {asta['anni_offerti']} ann{'o' if asta['anni_offerti']==1 else 'i'}."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    await _log_warn(context, f"Notifica pareggio auto fallita GM {gm_id}: {e}")

        await _registra_firma_finale(
            context, asta_id, asta["anni_offerti"], asta["offerente_team_id"]
        )
    except Exception as e:
        from handlers.helpers import log_job_error
        await log_job_error(context, "pareggio_automatico", e)


# ── RFA senza offerte: proprietario può firmare ───────────────────────────────

async def _chiedi_firma_proprietario_senza_offerte(context, asta_id: int):
    asta = db.get_asta(asta_id)
    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop:
        return

    vec_comp = asta["vecchio_compenso"] or 0
    soglia   = soglia_pareggio(vec_comp)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Firmo", callback_data=f"firma_prop:{asta_id}"),
        InlineKeyboardButton("❌ Lascio andare", callback_data=f"lascia:{asta_id}"),
    ]])

    if vec_comp <= 20:
        nota = f"Puoi firmare a qualsiasi cifra (minimo 1M)."
    elif vec_comp <= 34:
        nota = f"Offerta minima: <b>{soglia}M</b> (1/3 del vecchio compenso di {vec_comp}M, approssimato per difetto)."
    else:
        nota = f"Offerta minima: <b>{soglia}M</b> (1/2 del vecchio compenso di {vec_comp}M, approssimato per difetto)."

    testo = (
        f"🏀 <b>{asta['giocatore']}</b> non ha ricevuto offerte.\n\n"
        f"Sei il proprietario dei diritti. Puoi firmarlo o lasciarlo andare in free agency.\n\n"
        f"{nota}"
    )

    inviato = False
    for gm_id in team_prop["gm_ids"]:
        try:
            await context.bot.send_message(
                chat_id=gm_id, text=testo, parse_mode="HTML", reply_markup=kb
            )
            inviato = True
        except Exception as e:
            await _log_warn(context, f"Impossibile contattare GM firma proprietario {gm_id}: {e}")

    if not inviato:
        await _notifica_admin_group(
            context,
            f"⚠️ Impossibile contattare il proprietario RFA <b>{asta['giocatore']}</b> "
            f"(team: {team_prop['nome']}) — nessuna offerta. Intervenire manualmente."
        )


async def firma_prop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    asta    = db.get_asta(asta_id)

    if not asta or asta["stato"] != "CHIUSA":
        await query.edit_message_text("✅ Già conclusa.")
        return

    team = tm.get_team_by_gm(query.from_user.id)
    if not team or team["id"] != asta["squadra_proprietaria"]:
        await query.edit_message_text("⛔ Non sei il proprietario.")
        return

    vec_comp    = asta["vecchio_compenso"] or 0
    min_importo = soglia_pareggio(vec_comp)

    anni_btn = []
    for a in [1, 2, 3]:
        if a >= anni_minimi(min_importo):
            anni_btn.append(InlineKeyboardButton(
                f"{a} ann{'o' if a==1 else 'i'}",
                callback_data=f"firma_prop_anni:{asta_id}:{a}"
            ))
    kb = InlineKeyboardMarkup([anni_btn])

    await query.edit_message_text(
        f"Firmi <b>{asta['giocatore']}</b> a <b>{min_importo}M</b>.\n"
        f"Scegli gli anni del contratto:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def firma_prop_anni_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, asta_id_s, anni_s = query.data.split(":")
    asta_id = int(asta_id_s)
    anni    = int(anni_s)
    asta    = db.get_asta(asta_id)

    if not asta or asta["stato"] != "CHIUSA":
        await query.edit_message_text("✅ Già conclusa.")
        return

    team = tm.get_team_by_gm(query.from_user.id)
    if not team or team["id"] != asta["squadra_proprietaria"]:
        await query.edit_message_text("⛔ Non sei il proprietario.")
        return

    vec_comp = asta["vecchio_compenso"] or 0
    importo  = soglia_pareggio(vec_comp)

    cap_impegnato  = db.get_cap_virtuale(team["id"])
    slot_impegnati = db.get_slot_virtuali(team["id"])
    team_data      = tm.get_team_by_id(team["id"])
    cap_libero     = team_data["cap_disponibile"] - cap_impegnato + vec_comp
    slot_liberi    = team_data["slot_disponibili"] - slot_impegnati

    if cap_libero < importo:
        await query.edit_message_text(
            f"❌ Cap insufficiente per firmare.\n"
            f"Importo: <b>{importo}M</b>\n"
            f"Cap effettivo (dopo liberazione vecchio contratto da {vec_comp}M): <b>{cap_libero}M</b>.",
            parse_mode="HTML",
        )
        return
    if slot_liberi <= 0:
        await query.edit_message_text(
            f"❌ Nessuno slot libero. Slot virtuali impegnati: {slot_impegnati}.",
            parse_mode="HTML",
        )
        return

    await query.edit_message_text(
        f"✅ <b>{asta['giocatore']}</b> firmato con <b>{team['nome']}</b>\n"
        f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}",
        parse_mode="HTML",
    )
    await _registra_firma_finale(context, asta_id, anni, asta["squadra_proprietaria"], importo_override=importo)


async def lascia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    asta_id = int(query.data.split(":")[1])
    asta    = db.get_asta(asta_id)

    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    await _concludi_rfa_senza_contratto(context, asta_id)
    db.annulla_asta(asta_id)  # usa ANNULLATA per distinguere da CONCLUSA
    await _aggiorna_canale(context, asta_id)
    await query.edit_message_text(
        f"<b>{asta['giocatore']}</b> lasciato andare in free agency.\n"
        f"Il cap liberato ({asta['vecchio_compenso']}M) è stato restituito alla tua squadra.",
        parse_mode="HTML",
    )
    logger.info("RFA lasciato andare: asta_id=%d", asta_id)


# ── registrazione firma finale ────────────────────────────────────────────────

async def _registra_firma_finale(
    context, asta_id: int, anni: int, team_id: str,
    query=None, importo_override: int = None
):
    """
    Gestisce SEMPRE:
    1. Liberazione vecchio compenso RFA dal cap del proprietario
    2. Scala cap e slot a chi firma
    3. Registra contratto nel DB
    4. Aggiorna messaggio canale
    5. Annuncio firma nel canale
    """
    asta = db.get_asta(asta_id)
    if not asta:
        return

    importo = importo_override if importo_override is not None else asta["offerta_corrente"]
    now     = datetime.now(timezone.utc).isoformat()

    if asta["tipo"] == "FA":
        tm.scala_cap_slot(team_id, importo)
        import utils as _utils
        _utils.segna_giocatore_firmato(asta["giocatore"])
    else:
        # RFA: libera sempre il vecchio compenso dal proprietario
        if asta["vecchio_compenso"]:
            tm.libera_cap(asta["squadra_proprietaria"], asta["vecchio_compenso"])
        # scala cap e slot a chi firma (il giocatore RFA non occupava slot)
        tm.scala_cap_slot(team_id, importo)

    db.registra_contratto(
        asta_id=asta_id, giocatore=asta["giocatore"],
        team_id=team_id, importo=importo, anni=anni,
        ruolo=None, firmato_at=now,
    )
    db.concludi_asta(asta_id, anni_contratto=anni, firmato_at=now)

    await _aggiorna_canale(context, asta_id)

    team      = tm.get_team_by_id(team_id)
    team_nome = team["nome"] if team else team_id
    gm_nome   = team.get("gm_nome", "") if team else ""

    channel_id = utils.get_channel_id()
    firma_label = f"{gm_nome} firma" if gm_nome else "Firma"
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=(
                f"🖊️ <b>FIRMA</b>\n"
                f"<b>{asta['giocatore']}</b> → <b>{team_nome}</b>\n"
                f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await _log_warn(context, f"Annuncio firma canale fallito: {e}")

    # Annuncio nel canale principale lega (solo firme)
    main_channel_id = utils.load_globals().get("main_channel_id")
    if main_channel_id:
        try:
            await context.bot.send_message(
                chat_id=main_channel_id,
                text=(
                    f"🖊️ <b>{firma_label} {asta['giocatore']}</b> con <b>{team_nome}</b>\n"
                    f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await _log_warn(context, f"Annuncio firma canale principale fallito: {e}")

    # notifica watcher della firma
    team_nome_w = team["nome"] if team else team_id
    await _notifica_watchers(
        context, asta_id,
        f"✅ <b>{asta['giocatore']}</b> firmato con <b>{team_nome_w}</b>\n"
        f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}"
    )

    logger.info("Firma finale: asta_id=%d team=%s anni=%d importo=%d",
                asta_id, team_id, anni, importo)


# ── validazione anni ──────────────────────────────────────────────────────────

def _valida_anni(importo: int, anni: int) -> str | None:
    s = settings.get()
    if importo >= s["soglia_anni_3"] and anni != 3:
        return f"Con un contratto da {s['soglia_anni_3']}M+ sono obbligatori 3 anni."
    if importo >= s["soglia_anni_2"] and anni < 2:
        return f"Con un contratto da {s['soglia_anni_2']}M+ sono obbligatori almeno 2 anni."
    if anni not in (1, 2, 3):
        return "Anni non validi."
    return None


def get_handlers():
    return [
        CallbackQueryHandler(firma_callback,            pattern=r"^firma:\d+:\d+$"),
        CallbackQueryHandler(pareggio_callback,         pattern=r"^pareggio:\d+:(si|no|indietro)$"),
        CallbackQueryHandler(pareggio_anni_callback,    pattern=r"^pareggio_anni:\d+:\d+$"),
        CallbackQueryHandler(firma_prop_callback,       pattern=r"^firma_prop:\d+$"),
        CallbackQueryHandler(firma_prop_anni_callback,  pattern=r"^firma_prop_anni:\d+:\d+$"),
        CallbackQueryHandler(lascia_callback,           pattern=r"^lascia:\d+$"),
    ]
