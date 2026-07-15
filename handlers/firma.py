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
from datetime import datetime, timezone, timedelta

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

async def chiedi_anni(context: ContextTypes.DEFAULT_TYPE, asta_id: int, schedula_job: bool = True):
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

    # RFA con offerta ≥35M: anni obbligatori 3, niente scelta — pareggio parte subito
    if is_rfa and anni_minimi(importo) == 3:
        db.set_anni_offerti(asta_id, 3)
        # Notifica vincitore
        testo_vince = (
            f"🏆 <b>Hai vinto l'asta per {asta['giocatore']}!</b>\n\n"
            f"💰 Offerta: <b>{importo}M × 3 anni</b> (automatici — contratti ≥{settings.get()['soglia_anni_3']}M richiedono 3 anni obbligatori)\n\n"
            f"Il proprietario ha 24 ore dalla chiusura dell'asta per decidere se pareggiare."
        )
        for gm_id in team["gm_ids"]:
            try:
                await context.bot.send_message(chat_id=gm_id, text=testo_vince, parse_mode="HTML")
            except Exception as e:
                await _log_warn(context, f"Impossibile contattare GM vincitore {gm_id}: {e}")

        logger.info("chiedi_anni RFA ≥35M: anni automatici=3 asta_id=%d", asta_id)

        # Annuncio canale
        channel_id = utils.get_channel_id()
        team_prop_nome = tm.get_team_by_id(asta["squadra_proprietaria"])
        team_prop_nome = team_prop_nome["nome"] if team_prop_nome else "il proprietario"
        timeout_pareggio_h = settings.timeout_pareggio_ore()
        conclusa_at = datetime.fromisoformat(asta["conclusa_at"])
        scadenza_p = conclusa_at + timedelta(hours=timeout_pareggio_h)
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=(
                    f"⚖️ <b>RFA {asta['giocatore']} — Asta conclusa</b>\n\n"
                    f"{team['nome']} ha offerto <b>{importo}M × 3 anni</b> (obbligatori).\n"
                    f"{team_prop_nome} ha tempo fino a <b>{utils.format_dt(scadenza_p.isoformat())}</b> per pareggiare."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await _log_warn(context, f"Annuncio canale RFA ≥35M fallito: {e}")

        # Notifica proprietario e avvia pareggio — schedula_job gestito da _chiedi_pareggio
        await _notifica_proprietario_chiusura_rfa(context, asta_id)
        eseguito = await _esegui_pre_pareggio_se_compatibile(context, asta_id, 3)
        if not eseguito:
            await _chiedi_pareggio(context, asta_id, 3, schedula_job=schedula_job)
        return

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

    # Annuncio canale per RFA <35M
    if is_rfa and asta["squadra_proprietaria"]:
        channel_id = utils.get_channel_id()
        team_prop_c = tm.get_team_by_id(asta["squadra_proprietaria"])
        team_prop_nome_c = team_prop_c["nome"] if team_prop_c else "il proprietario"
        conclusa_at_c = datetime.fromisoformat(asta["conclusa_at"])
        scadenza_par = conclusa_at_c + timedelta(hours=settings.timeout_pareggio_ore())
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=(
                    f"⚖️ <b>RFA {asta['giocatore']} — Asta conclusa</b>\n\n"
                    f"{team['nome']} ha offerto <b>{importo}M</b>.\n"
                    f"Il vincitore ha <b>{ore}h</b> per scegliere gli anni.\n"
                    f"{team_prop_nome_c} dovrà pareggiare entro <b>{utils.format_dt(scadenza_par.isoformat())}</b>."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await _log_warn(context, f"Annuncio canale RFA <35M fallito: {e}")

    timeout_h = settings.timeout_firma_rfa_ore() if is_rfa else settings.timeout_firma_fa_ore()
    if schedula_job:
        context.job_queue.run_once(
            firma_automatica,
            when=timeout_h * 3600,
            data={"asta_id": asta_id},
            name=f"firma_auto_{asta_id}",
        )
    logger.info("chiedi_anni: asta_id=%d team=%s timeout=%dh schedula=%s", asta_id, team["id"], timeout_h, schedula_job)

    # Per le RFA: notifica il proprietario alla chiusura con importo e scadenza
    if is_rfa and asta["squadra_proprietaria"]:
        await _notifica_proprietario_chiusura_rfa(context, asta_id)


async def _notifica_proprietario_chiusura_rfa(context, asta_id: int):
    """
    Notifica il proprietario RFA alla chiusura dell'asta.
    Mostra importo vincitore, scadenza pareggio (conclusa_at + 24h),
    avviso fascia se sotto soglia, e bottone per pre-impostare il pareggio.
    """
    from datetime import timedelta
    asta = db.get_asta(asta_id)
    if not asta:
        return
    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop:
        return
    team_vince = tm.get_team_by_id(asta["offerente_team_id"])
    team_vince_nome = team_vince["nome"] if team_vince else "altra franchigia"

    importo   = asta["offerta_corrente"]
    vec_comp  = asta["vecchio_compenso"] or 0
    conclusa_at = datetime.fromisoformat(asta["conclusa_at"])
    scadenza_pareggio = conclusa_at + timedelta(hours=settings.timeout_pareggio_ore())
    min_importo = importo_minimo_pareggio(importo, vec_comp)
    sotto_soglia = importo < soglia_pareggio(vec_comp)
    anni_obbligatori = anni_minimi(importo) == 3  # ≥35M → anni fissi

    nota_fascia = ""
    if vec_comp > 20 and sotto_soglia:
        fraz = "1/3" if vec_comp <= 34 else "1/2"
        nota_fascia = (
            f"\n\n⚠️ <b>Attenzione fascia contrattuale</b>\n"
            f"L'offerta ({importo}M) è inferiore a {fraz} del vecchio compenso ({vec_comp}M).\n"
            f"Per pareggiare dovrai offrire almeno <b>{min_importo}M</b>.\n"
            f"In questo caso gli anni sono <b>liberi</b> e non vincolati a quelli del vincitore."
        )

    if anni_obbligatori:
        # ≥35M: anni già noti (3), pareggio parte subito — niente pre-pareggio
        testo = (
            f"⚖️ <b>L'asta RFA per {asta['giocatore']} è terminata</b>\n\n"
            f"<b>{team_vince_nome}</b> ha offerto: <b>{importo}M × 3 anni</b> (obbligatori per contratti ≥{settings.get()['soglia_anni_3']}M)\n\n"
            f"⏰ Hai tempo fino a <b>{utils.format_dt(scadenza_pareggio.isoformat())}</b> per decidere se pareggiare."
            f"{nota_fascia}"
        )
        kb = None
    else:
        ore_vincitore = settings.timeout_firma_rfa_ore()
        testo = (
            f"⚖️ <b>L'asta RFA per {asta['giocatore']} è terminata</b>\n\n"
            f"<b>{team_vince_nome}</b> ha offerto: <b>{importo}M</b>\n"
            f"Gli anni verranno comunicati entro {ore_vincitore}h.\n\n"
            f"⏰ Scadenza pareggio: <b>{utils.format_dt(scadenza_pareggio.isoformat())}</b>"
            f"{nota_fascia}\n\n"
            f"<b>Pre-impostazioni opzionali</b> (ti evitano di aspettare le {ore_vincitore}h del vincitore):\n"
            f"• <b>Pre-pareggio ≤N anni</b>: pareggia automaticamente se il vincitore offre ≤N anni\n"
            f"• <b>Rifiuto assoluto</b>: il giocatore passa al vincitore qualunque siano gli anni\n"
            f"• <b>Rifiuto condizionale >N anni</b>: passa al vincitore solo se offre più di N anni\n"
            f"• Pre-pareggio e rifiuto condizionale sono compatibili se i valori non si sovrappongono\n\n"
            f"<i>Se non pre-imposti nulla, riceverai il messaggio completo appena il vincitore sceglie gli anni.</i>"
        )
        kb = _kb_preimpostazioni(asta_id, None, 0)

    for gm_id in team_prop["gm_ids"]:
        try:
            await context.bot.send_message(
                chat_id=gm_id, text=testo, parse_mode="HTML", reply_markup=kb
            )
        except Exception as e:
            await _log_warn(context, f"Notifica chiusura RFA proprietario {gm_id}: {e}")


def _testo_stato_preimpostazioni(asta: dict, pre: dict | None, rifiuto: int) -> tuple[str]:
    """Costruisce il testo di riepilogo delle pre-impostazioni correnti."""
    giocatore = asta["giocatore"]
    importo   = asta["offerta_corrente"]
    vec_comp  = asta["vecchio_compenso"] or 0
    sotto_soglia = importo < soglia_pareggio(vec_comp)
    min_importo  = importo_minimo_pareggio(importo, vec_comp)
    importo_eff  = min_importo if sotto_soglia else importo

    righe = [f"📋 <b>Pre-impostazioni per {giocatore}</b>\n"]

    if pre:
        anni_p = pre["anni"]
        righe.append(f"⚡ Pareggio: ≤{anni_p} ann{'o' if anni_p==1 else 'i'} a {importo_eff}M")
    else:
        righe.append("⚡ Pareggio: non impostato")

    if rifiuto == -1:
        righe.append("❌ Rifiuto: assoluto")
    elif rifiuto > 0:
        righe.append(f"❌ Rifiuto: condizionale se vincitore offre >{rifiuto} ann{'o' if rifiuto==1 else 'i'}")
    else:
        righe.append("❌ Rifiuto: non impostato")

    righe.append("\n<i>Puoi modificare tutto finché il vincitore non sceglie gli anni.</i>")
    return "\n".join(righe),


def _kb_preimpostazioni(asta_id: int, pre: dict | None, rifiuto: int) -> InlineKeyboardMarkup:
    """Keyboard principale con bottoni contestuali in base allo stato attuale."""
    bottoni = []
    if pre:
        bottoni.append([
            InlineKeyboardButton("✏️ Modifica pareggio", callback_data=f"pre_par_anni:{asta_id}"),
            InlineKeyboardButton("🗑 Rimuovi pareggio", callback_data=f"pre_par_annulla:{asta_id}"),
        ])
    else:
        bottoni.append([InlineKeyboardButton("⚡ Pre-imposta pareggio", callback_data=f"pre_par_anni:{asta_id}")])

    if rifiuto != 0:
        bottoni.append([
            InlineKeyboardButton("✏️ Modifica rifiuto", callback_data=f"pre_rifiuto:{asta_id}"),
            InlineKeyboardButton("🗑 Rimuovi rifiuto", callback_data=f"pre_rifiuto_annulla:{asta_id}"),
        ])
    else:
        bottoni.append([InlineKeyboardButton("❌ Pre-imposta rifiuto", callback_data=f"pre_rifiuto:{asta_id}")])

    return InlineKeyboardMarkup(bottoni)


async def pre_pareggio_anni_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Il proprietario RFA clicca 'Pre-imposta pareggio'.
    Chiede quanti anni vuole offrire (l'importo è già noto dalla DB).
    """
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] not in ("CHIUSA",):
        await query.edit_message_text("❌ Asta non più disponibile per il pre-pareggio.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario di questo giocatore.", show_alert=True)
        return

    importo   = asta["offerta_corrente"]
    vec_comp  = asta["vecchio_compenso"] or 0
    min_importo = importo_minimo_pareggio(importo, vec_comp)
    sotto_soglia = importo < soglia_pareggio(vec_comp)

    # Anni validi per il pareggio:
    # - Sotto soglia: anni liberi, mostriamo tutti (1, 2, 3)
    # - Sopra soglia: almeno anni_minimi(importo_pareggio) — es. 25M → min 2 anni
    min_anni_validi = 1 if sotto_soglia else anni_minimi(min_importo)

    bottoni_anni = [
        [InlineKeyboardButton(f"{a} ann{'o' if a==1 else 'i'}", callback_data=f"pre_par_set:{asta_id}:{a}")]
        for a in [1, 2, 3] if a >= min_anni_validi
    ]
    bottoni_anni.append([InlineKeyboardButton("← Indietro", callback_data=f"pre_rifiuto_indietro:{asta_id}")])

    importo_label = f"{min_importo}M" if sotto_soglia else f"{importo}M"
    nota = f"⚠️ Pareggerai a <b>{importo_label}</b>" + (" (soglia minima di fascia)" if sotto_soglia else "")

    if sotto_soglia:
        istruzioni = (
            f"Pareggio automatico a prescindere dagli anni offerti dal vincitore — "
            f"gli anni che scegli sono quelli del tuo contratto (minimo {min_anni_validi})."
        )
    else:
        istruzioni = (
            f"Pareggio automatico se il vincitore offre ≤ agli anni che scegli. "
            f"Il minimo consentito per questa fascia è {min_anni_validi} ann{'o' if min_anni_validi==1 else 'i'}."
        )

    await query.edit_message_text(
        f"⚡ <b>Pre-pareggio per {asta['giocatore']}</b>\n\n"
        f"{nota}\n\n"
        f"Scegli per quanti anni vuoi pre-impostare il pareggio.\n"
        f"<i>{istruzioni}</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(bottoni_anni),
    )


async def pre_pareggio_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Salva il pre-pareggio e aggiorna il messaggio con opzioni Modifica/Annulla."""
    query = update.callback_query
    await query.answer()

    _, asta_id_s, anni_s = query.data.split(":")
    asta_id = int(asta_id_s)
    anni    = int(anni_s)

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] not in ("CHIUSA",):
        await query.edit_message_text("❌ Asta non più disponibile.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario.", show_alert=True)
        return

    importo   = asta["offerta_corrente"]
    vec_comp  = asta["vecchio_compenso"] or 0
    min_importo = importo_minimo_pareggio(importo, vec_comp)
    sotto_soglia = importo < soglia_pareggio(vec_comp)
    importo_eff = min_importo if sotto_soglia else importo

    db.set_pareggio_preimpostato(asta_id, importo_eff, anni)

    # Check: se c'è rifiuto condizionale che si sovrappone, avviso
    rifiuto = db.get_rifiuto_preimpostato(asta_id)
    if rifiuto > 0 and anni >= rifiuto:
        await query.answer(
            f"⚠️ Attenzione: il rifiuto condizionale (>{rifiuto} anni) si sovrappone al pre-pareggio (≤{anni} anni). "
            f"Modifica il rifiuto per evitare contraddizioni.",
            show_alert=True
        )

    pre_aggiornato = db.get_pareggio_preimpostato(asta_id)
    testo, = _testo_stato_preimpostazioni(dict(asta), pre_aggiornato, rifiuto)
    await query.edit_message_text(
        testo,
        parse_mode="HTML",
        reply_markup=_kb_preimpostazioni(asta_id, pre_aggiornato, rifiuto),
    )


async def pre_pareggio_annulla_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Annulla il pre-pareggio."""
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario.", show_alert=True)
        return

    db.clear_pareggio_preimpostato(asta_id)
    rifiuto = db.get_rifiuto_preimpostato(asta_id)
    testo, = _testo_stato_preimpostazioni(dict(asta), None, rifiuto)
    await query.edit_message_text(
        testo,
        parse_mode="HTML",
        reply_markup=_kb_preimpostazioni(asta_id, None, rifiuto),
    )


async def pre_rifiuto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Il proprietario RFA pre-imposta il rifiuto.
    Mostra due opzioni: rifiuto assoluto o rifiuto condizionale >N anni.
    """
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] not in ("CHIUSA",):
        await query.edit_message_text("❌ Asta non più disponibile.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario di questo giocatore.", show_alert=True)
        return

    pre = db.get_pareggio_preimpostato(asta_id)
    anni_pre = pre["anni"] if pre else None
    importo  = asta["offerta_corrente"]
    vec_comp = asta["vecchio_compenso"] or 0
    sotto_soglia = importo < soglia_pareggio(vec_comp)

    bottoni = []

    if sotto_soglia:
        # Sotto soglia: anni liberi → rifiuto condizionale non ha senso, solo assoluto
        bottoni.append([InlineKeyboardButton(
            "❌ Rifiuto assoluto — il giocatore passa al vincitore a prescindere dagli anni",
            callback_data=f"pre_rifiuto_set:{asta_id}:-1"
        )])
        nota_soglia = (
            "\n\n<i>Sei in fascia sotto soglia — gli anni del vincitore non contano per il pareggio. "
            "L'unica pre-impostazione sensata è il rifiuto assoluto.</i>"
        )
    else:
        nota_soglia = ""
        # Rifiuto assoluto — solo se non c'è pre-pareggio
        if not pre:
            bottoni.append([InlineKeyboardButton(
                "❌ Rifiuto assoluto — il giocatore passa al vincitore qualunque siano gli anni",
                callback_data=f"pre_rifiuto_set:{asta_id}:-1"
            )])

        # Rifiuto condizionale >N anni (solo 1 e 2, mai >3)
        for n in [1, 2]:
            if anni_pre is None or n > anni_pre:
                if anni_pre is None:
                    label = f"❌ Rifiuto se offre >{n} ann{'o' if n==1 else 'i'} — pareggio se ≤{n}"
                else:
                    label = f"❌ Rifiuto se offre >{n} ann{'o' if n==1 else 'i'}"
                bottoni.append([InlineKeyboardButton(label, callback_data=f"pre_rifiuto_set:{asta_id}:{n}")])

    bottoni.append([InlineKeyboardButton("← Indietro", callback_data=f"pre_rifiuto_indietro:{asta_id}")])

    nota_pre = (
        f"\n\n<i>Hai già impostato pre-pareggio a ≤{anni_pre} anni — "
        f"il rifiuto assoluto non è disponibile.</i>"
    ) if pre and not sotto_soglia else ""

    await query.edit_message_text(
        f"❌ <b>Pre-rifiuto per {asta['giocatore']}</b>\n\n"
        f"Scegli il tipo di rifiuto:\n\n"
        + ("• <b>Assoluto</b>: passa al vincitore qualunque siano gli anni\n"
           "• <b>Condizionale >N anni</b>: passa al vincitore solo se offre più di N anni "
           "(compatibile con pre-pareggio ≤N anni)\n"
           if not sotto_soglia else "")
        + f"{nota_pre}{nota_soglia}\n\n"
        f"<i>Puoi modificarla o annullarla finché il vincitore non sceglie gli anni.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(bottoni),
    )


async def pre_rifiuto_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Salva il rifiuto preimpostato."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    asta_id = int(parts[1])
    valore  = int(parts[2])  # -1=assoluto, 1-2=condizionale

    asta = db.get_asta(asta_id)
    if not asta or asta["stato"] not in ("CHIUSA",):
        await query.edit_message_text("❌ Asta non più disponibile.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario.", show_alert=True)
        return

    # Check compatibilità: rifiuto assoluto incompatibile con pre-pareggio
    pre = db.get_pareggio_preimpostato(asta_id)
    if valore == -1 and pre:
        await query.answer(
            "❌ Rifiuto assoluto non compatibile con un pre-pareggio già impostato. "
            "Annulla prima il pre-pareggio.",
            show_alert=True
        )
        return

    # Check: rifiuto condizionale deve essere > anni del pre-pareggio
    if valore > 0 and pre and valore <= pre["anni"]:
        await query.answer(
            f"❌ Il rifiuto condizionale (>{valore} anni) si sovrappone al pre-pareggio (≤{pre['anni']} anni). "
            f"Scegli un valore maggiore.",
            show_alert=True
        )
        return

    db.set_rifiuto_preimpostato(asta_id, valore)

    if valore == -1:
        desc_rifiuto = "Rifiuto <b>assoluto</b> — il giocatore passerà al vincitore qualunque siano gli anni."
    else:
        desc_rifiuto = f"Rifiuto <b>condizionale</b> — il giocatore passerà al vincitore se offre >{valore} ann{'o' if valore==1 else 'i'}."

    stato_pre = ""
    if pre:
        stato_pre = f"\n⚡ Pre-pareggio attivo: ≤{pre['anni']} ann{'o' if pre['anni']==1 else 'i'}"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Modifica rifiuto", callback_data=f"pre_rifiuto:{asta_id}"),
        InlineKeyboardButton("↩️ Annulla rifiuto", callback_data=f"pre_rifiuto_annulla:{asta_id}"),
    ]])

    await query.edit_message_text(
        f"❌ <b>Rifiuto pre-impostato per {asta['giocatore']}</b>\n\n"
        f"{desc_rifiuto}"
        f"{stato_pre}\n\n"
        f"<i>Puoi modificarlo o annullarlo finché il vincitore non sceglie gli anni.</i>",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def pre_rifiuto_annulla_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Annulla il rifiuto preimpostato."""
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario.", show_alert=True)
        return

    db.set_rifiuto_preimpostato(asta_id, 0)

    pre = db.get_pareggio_preimpostato(asta_id)
    stato_pre = f"\n\n⚡ Pre-pareggio attivo: ≤{pre['anni']} ann{'o' if pre['anni']==1 else 'i'}" if pre else ""

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚡ Pre-imposta pareggio", callback_data=f"pre_par_anni:{asta_id}"),
        InlineKeyboardButton("❌ Pre-imposta rifiuto", callback_data=f"pre_rifiuto:{asta_id}"),
    ]])

    await query.edit_message_text(
        f"Rifiuto annullato per <b>{asta['giocatore']}</b>."
        f"{stato_pre}\n\n"
        f"<i>Puoi modificare le pre-impostazioni finché il vincitore non sceglie gli anni.</i>",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def pre_rifiuto_indietro_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Torna al messaggio principale senza modificare nulla."""
    query = update.callback_query
    await query.answer()

    asta_id = int(query.data.split(":")[1])
    asta = db.get_asta(asta_id)
    if not asta:
        await query.edit_message_text("❌ Asta non trovata.")
        return

    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
    if not team_prop or query.from_user.id not in team_prop["gm_ids"]:
        await query.answer("⛔ Non sei il proprietario.", show_alert=True)
        return

    pre     = db.get_pareggio_preimpostato(asta_id)
    rifiuto = db.get_rifiuto_preimpostato(asta_id)

    await query.edit_message_text(
        *_testo_stato_preimpostazioni(dict(asta), pre, rifiuto),
        parse_mode="HTML",
        reply_markup=_kb_preimpostazioni(asta_id, pre, rifiuto),
    )


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
        db.set_anni_offerti(asta_id, anni)

        # Annuncio canale: il vincitore ha scelto gli anni
        channel_id = utils.get_channel_id()
        try:
            conclusa_at_c = datetime.fromisoformat(asta["conclusa_at"])
            scadenza_par_c = conclusa_at_c + timedelta(hours=settings.timeout_pareggio_ore())
            now_c = datetime.now(timezone.utc)
            residuo_c = scadenza_par_c - now_c
            ore_r = int(residuo_c.total_seconds() // 3600)
            min_r = int((residuo_c.total_seconds() % 3600) // 60)
            await context.bot.send_message(
                chat_id=channel_id,
                text=(
                    f"📋 <b>RFA {asta['giocatore']}</b> — offerta completata\n\n"
                    f"{team['nome']} offre <b>{asta['offerta_corrente']}M × {anni} ann{'o' if anni==1 else 'i'}</b>.\n\n"
                    f"Il proprietario ha ancora <b>{ore_r}h {min_r}m</b> "
                    f"(fino a {utils.format_dt(scadenza_par_c.isoformat())}) per pareggiare."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            await _log_warn(context, f"Annuncio canale scelta anni RFA fallito: {e}")

        # Controlla pre-pareggio prima di chiedere al proprietario
        eseguito = await _esegui_pre_pareggio_se_compatibile(context, asta_id, anni)
        if not eseguito:
            await _chiedi_pareggio(context, asta_id, anni)
        await _aggiorna_canale(context, asta_id)
        msg_prop = "ricevuto il pareggio automatico." if eseguito else "24 ore per decidere se pareggiare l'offerta."
        await query.edit_message_text(
            f"✅ Anni registrati: <b>{anni}</b> per <b>{asta['giocatore']}</b> a <b>{asta['offerta_corrente']}M</b>.\n\n"
            f"Il proprietario dei diritti ha {msg_prop}",
            parse_mode="HTML",
        )


async def _esegui_pre_pareggio_se_compatibile(context, asta_id: int, anni_vincitore: int) -> bool:
    """
    Controlla se c'è un pre-pareggio impostato dal proprietario e se è compatibile
    con gli anni offerti dal vincitore. Se sì, esegue il pareggio automaticamente.
    Restituisce True se il pareggio è stato eseguito, False altrimenti.
    """
    # Controlla prima se c'è un rifiuto preimpostato
    rifiuto = db.get_rifiuto_preimpostato(asta_id)
    asta_r = db.get_asta(asta_id)
    team_vince_r = tm.get_team_by_id(asta_r["offerente_team_id"]) if asta_r else None
    team_vince_nome_r = team_vince_r["nome"] if team_vince_r else "altra franchigia"

    esegui_rifiuto = (rifiuto == -1) or (rifiuto > 0 and anni_vincitore > rifiuto)

    if esegui_rifiuto:
        logger.info("Rifiuto preimpostato (valore=%d): asta_id=%d anni_vincitore=%d — firma al vincitore",
                    rifiuto, asta_id, anni_vincitore)
        db.set_rifiuto_preimpostato(asta_id, 0)
        db.clear_pareggio_preimpostato(asta_id)
        asta = db.get_asta(asta_id)
        team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])

        if rifiuto == -1:
            motivo = "Hai pre-impostato il rifiuto assoluto."
        else:
            motivo = f"Hai pre-impostato il rifiuto per offerte >{rifiuto} ann{'o' if rifiuto==1 else 'i'} e il vincitore ha offerto {anni_vincitore}."

        if team_prop:
            for gm_id in team_prop["gm_ids"]:
                try:
                    await context.bot.send_message(
                        chat_id=gm_id,
                        text=(
                            f"❌ <b>Rifiuto eseguito automaticamente per {asta['giocatore']}</b>\n\n"
                            f"{motivo}\n"
                            f"Il giocatore passa a <b>{team_vince_nome_r}</b> con "
                            f"{asta['offerta_corrente']}M × {anni_vincitore} ann{'o' if anni_vincitore==1 else 'i'}."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    await _log_warn(context, f"Notifica rifiuto auto proprietario {gm_id}: {e}")

        await _registra_firma_finale(context, asta_id, anni_vincitore, asta["offerente_team_id"])
        return True

    pre = db.get_pareggio_preimpostato(asta_id)
    if not pre:
        return False

    importo_pre = pre["importo"]
    anni_pre    = pre["anni"]

    asta = db.get_asta(asta_id)
    vec_comp = asta["vecchio_compenso"] or 0
    sotto_soglia = asta["offerta_corrente"] < soglia_pareggio(vec_comp) and vec_comp > 20

    # Verifica che gli anni del pre-pareggio siano validi per questa fascia
    min_importo_par = importo_minimo_pareggio(asta["offerta_corrente"], vec_comp)
    min_anni_validi = 1 if sotto_soglia else anni_minimi(min_importo_par)
    if anni_pre < min_anni_validi:
        logger.warning(
            "Pre-pareggio anni %d non validi per fascia (min %d): asta_id=%d — annullato",
            anni_pre, min_anni_validi, asta_id
        )
        db.clear_pareggio_preimpostato(asta_id)
        return False

    # Se sopra soglia: anni vincitore deve essere <= anni pre-impostati
    # Se sotto soglia: gli anni sono liberi, il pre-pareggio scatta sempre
    if not sotto_soglia and anni_vincitore > anni_pre:
        logger.info("Pre-pareggio non compatibile: vincitore %d anni > pre %d anni", anni_vincitore, anni_pre)
        db.clear_pareggio_preimpostato(asta_id)
        return False
    team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])

    logger.info("Pre-pareggio automatico: asta_id=%d importo=%d anni=%d", asta_id, importo_pre, anni_pre)
    db.clear_pareggio_preimpostato(asta_id)

    # Notifica il proprietario che il pareggio è scattato automaticamente
    if team_prop:
        for gm_id in team_prop["gm_ids"]:
            try:
                await context.bot.send_message(
                    chat_id=gm_id,
                    text=(
                        f"⚡ <b>Pre-pareggio eseguito automaticamente!</b>\n\n"
                        f"<b>{asta['giocatore']}</b>: hai pareggiato a <b>{importo_pre}M × {anni_pre} ann{'o' if anni_pre==1 else 'i'}</b>.\n"
                        f"Il vincitore aveva offerto {anni_vincitore} ann{'o' if anni_vincitore==1 else 'i'}."
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Notifica pre-pareggio auto proprietario {gm_id}: {e}")

    # Esegui il pareggio
    await _registra_firma_finale(context, asta_id, anni_pre, asta["squadra_proprietaria"], pareggiato=True)
    return True


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
            return
        else:
            anni = anni_minimi(asta["offerta_corrente"])
            logger.info("Firma automatica RFA vincitore (anni minimi %d): asta_id=%d", anni, asta_id)
            db.set_anni_offerti(asta_id, anni)
            await _aggiorna_canale(context, asta_id)

            # Annuncio canale anni automatici
            channel_id = utils.get_channel_id()
            team_vince_auto = tm.get_team_by_id(asta["offerente_team_id"])
            team_vince_nome_auto = team_vince_auto["nome"] if team_vince_auto else "la franchigia vincitrice"
            try:
                conclusa_at_a = datetime.fromisoformat(asta["conclusa_at"])
                scadenza_par_a = conclusa_at_a + timedelta(hours=settings.timeout_pareggio_ore())
                now_a = datetime.now(timezone.utc)
                residuo_a = scadenza_par_a - now_a
                ore_a = int(residuo_a.total_seconds() // 3600)
                min_a = int((residuo_a.total_seconds() % 3600) // 60)
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=(
                        f"📋 <b>RFA {asta['giocatore']}</b> — anni assegnati automaticamente\n\n"
                        f"{team_vince_nome_auto} offre <b>{asta['offerta_corrente']}M × {anni} ann{'o' if anni==1 else 'i'}</b> "
                        f"(minimi per fascia — nessuna risposta entro il termine).\n\n"
                        f"Il proprietario ha ancora <b>{ore_a}h {min_a}m</b> "
                        f"(fino a {utils.format_dt(scadenza_par_a.isoformat())}) per pareggiare."
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                await _log_warn(context, f"Annuncio canale anni auto RFA fallito: {e}")

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

        # Controlla se c'è un pre-pareggio compatibile, altrimenti chiede al proprietario
        eseguito = await _esegui_pre_pareggio_se_compatibile(context, asta_id, anni)
        if not eseguito:
            await _chiedi_pareggio(context, asta_id, anni)
    except Exception as e:
        from handlers.helpers import log_job_error
        await log_job_error(context, "firma_automatica", e)


# ── fase pareggio RFA ─────────────────────────────────────────────────────────

async def _chiedi_pareggio(context, asta_id: int, anni_vincitore: int, schedula_job: bool = True):
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
        + (f"  📅 Almeno <b>{min_anni} ann{'o' if min_anni==1 else 'i'}</b>" if not sotto_soglia else f"  📅 Anni a tua scelta (non vincolati)")
        + f"{nota_fascia}\n\n"
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

    if schedula_job:
        # Il timer parte da conclusa_at + timeout, non dall'ora corrente
        # Così rispetta le 24h dalle regole indipendentemente da quando il vincitore sceglie gli anni
        conclusa_at = datetime.fromisoformat(asta["conclusa_at"])
        scadenza = conclusa_at + timedelta(hours=settings.timeout_pareggio_ore())
        residuo = max(5, (scadenza - datetime.now(timezone.utc)).total_seconds())
        context.job_queue.run_once(
            pareggio_automatico,
            when=residuo,
            data={"asta_id": asta_id},
            name=f"pareggio_auto_{asta_id}",
        )
    logger.info("Fase pareggio avviata: asta_id=%d schedula=%s", asta_id, schedula_job)


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
        await _chiedi_pareggio(context, asta_id, asta["anni_offerti"], schedula_job=False)
        await query.delete_message()
        return

    if scelta == "no":
        # Chiede conferma prima di rinunciare
        team_vince = tm.get_team_by_id(asta["offerente_team_id"])
        team_vince_nome = team_vince["nome"] if team_vince else "altra franchigia"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confermo, rinuncio", callback_data=f"pareggio:{asta_id}:no_confermato"),
            InlineKeyboardButton("← Indietro", callback_data=f"pareggio:{asta_id}:indietro"),
        ]])
        await query.edit_message_text(
            f"❌ <b>Confermi di rinunciare al pareggio?</b>\n\n"
            f"<b>{asta['giocatore']}</b> passerà a <b>{team_vince_nome}</b> "
            f"con {asta['offerta_corrente']}M × {asta['anni_offerti']} ann{'o' if asta['anni_offerti']==1 else 'i'}.\n\n"
            f"<i>Questa azione è irreversibile.</i>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if scelta == "no_confermato":
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
        await query.answer(
            f"❌ Cap insufficiente per pareggiare.\n"
            f"Importo pareggio: {importo}M\n"
            f"Cap effettivo (dopo liberazione vecchio contratto da {vec_comp}M): {cap_libero}M.\n"
            f"Riprova quando si libera cap.",
            show_alert=True,
        )
        return
    if slot_liberi <= 0:
        await query.answer(
            f"❌ Nessuno slot libero per pareggiare.\n"
            f"Slot virtuali già impegnati: {slot_impegnati}.\n"
            f"Riprova quando si libera uno slot.",
            show_alert=True,
        )
        return

    await query.edit_message_text(
        f"⚖️ <b>Confermi il pareggio?</b>\n\n"
        f"<b>{asta['giocatore']}</b> rimarrà con <b>{team['nome']}</b>\n"
        f"💰 <b>{importo}M × {anni} ann{'o' if anni==1 else 'i'}</b>\n\n"
        f"<i>Questa azione è irreversibile.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confermo il pareggio", callback_data=f"pareggio_confirm:{asta_id}:{anni}:{importo}"),
            InlineKeyboardButton("← Indietro", callback_data=f"pareggio:{asta_id}:indietro"),
        ]]),
    )


async def pareggio_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Conferma definitiva del pareggio."""
    query = update.callback_query
    await query.answer()

    _, asta_id_s, anni_s, importo_s = query.data.split(":")
    asta_id = int(asta_id_s)
    anni    = int(anni_s)
    importo = int(importo_s)
    asta    = db.get_asta(asta_id)

    if not asta or asta["stato"] != "PAREGGIO":
        await query.edit_message_text("✅ Fase pareggio già conclusa.")
        return

    team = tm.get_team_by_gm(query.from_user.id)
    if not team or team["id"] != asta["squadra_proprietaria"]:
        await query.edit_message_text("⛔ Non sei il proprietario di questo RFA.")
        return

    await query.edit_message_text(
        f"✅ Pareggio confermato!\n"
        f"<b>{asta['giocatore']}</b> rimane con <b>{team['nome']}</b>\n"
        f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}",
        parse_mode="HTML",
    )
    await _registra_firma_finale(context, asta_id, anni, asta["squadra_proprietaria"], importo_override=importo, pareggiato=True)


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
    await _registra_firma_finale(context, asta_id, anni, asta["squadra_proprietaria"], importo_override=importo, pareggiato=True)


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
    query=None, importo_override: int = None, pareggiato: bool = False
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

    # Annuncio nel canale principale lega
    main_channel_id = utils.load_globals().get("main_channel_id")
    if main_channel_id:
        try:
            if asta["tipo"] == "RFA" and pareggiato:
                # Proprietario ha pareggiato
                testo_main = (
                    f"⚖️ <b>{team_nome} pareggia {asta['giocatore']}</b>\n"
                    f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}"
                )
            elif asta["tipo"] == "RFA" and not pareggiato:
                # Proprietario non ha pareggiato — firma il vincitore
                team_prop = tm.get_team_by_id(asta["squadra_proprietaria"])
                team_prop_nome = team_prop["nome"] if team_prop else "il proprietario"
                firma_label = f"🖊️ {gm_nome} firma" if gm_nome else "🖊️ Firma"
                testo_main = (
                    f"❌ {team_prop_nome} non pareggia\n"
                    f"<b>{firma_label} {asta['giocatore']}</b> con <b>{team_nome}</b>\n"
                    f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}"
                )
            else:
                # FA normale
                firma_label = f"🖊️ {gm_nome} firma" if gm_nome else "🖊️ Firma"
                testo_main = (
                    f"<b>{firma_label} {asta['giocatore']}</b> con <b>{team_nome}</b>\n"
                    f"💰 {importo}M × {anni} ann{'o' if anni==1 else 'i'}"
                )
            await context.bot.send_message(
                chat_id=main_channel_id,
                text=testo_main,
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
        CallbackQueryHandler(firma_callback,                pattern=r"^firma:\d+:\d+$"),
        CallbackQueryHandler(pareggio_callback,             pattern=r"^pareggio:\d+:(si|no|no_confermato|indietro)$"),
        CallbackQueryHandler(pareggio_anni_callback,        pattern=r"^pareggio_anni:\d+:\d+$"),
        CallbackQueryHandler(pareggio_confirm_callback,     pattern=r"^pareggio_confirm:\d+:\d+:\d+$"),
        CallbackQueryHandler(firma_prop_callback,           pattern=r"^firma_prop:\d+$"),
        CallbackQueryHandler(firma_prop_anni_callback,      pattern=r"^firma_prop_anni:\d+:\d+$"),
        CallbackQueryHandler(lascia_callback,               pattern=r"^lascia:\d+$"),
        CallbackQueryHandler(pre_pareggio_anni_callback,    pattern=r"^pre_par_anni:\d+$"),
        CallbackQueryHandler(pre_pareggio_set_callback,     pattern=r"^pre_par_set:\d+:\d+$"),
        CallbackQueryHandler(pre_pareggio_annulla_callback, pattern=r"^pre_par_annulla:\d+$"),
        CallbackQueryHandler(pre_rifiuto_callback,          pattern=r"^pre_rifiuto:\d+$"),
        CallbackQueryHandler(pre_rifiuto_set_callback,      pattern=r"^pre_rifiuto_set:\d+:-?\d+$"),
        CallbackQueryHandler(pre_rifiuto_annulla_callback,  pattern=r"^pre_rifiuto_annulla:\d+$"),
        CallbackQueryHandler(pre_rifiuto_indietro_callback, pattern=r"^pre_rifiuto_indietro:\d+$"),
    ]
