"""
Comandi riservati al dev (dev_id in globals.json).
Non compaiono in /admin e non sono accessibili agli altri admin.
Usati per osservare lo stato interno del sistema senza aprire sqlite3.

Comandi:
  /dev               — lista comandi dev
  /dev_aste_stato    — aste raggruppate per stato
  /dev_watched       — watcher attivi con nome GM/team
  /dev_rfa           — RFA attive nella stagione corrente
  /dev_firme [N]     — ultime N firme concluse (default 10)
  /dev_cap           — cap e slot virtuali con dettaglio aste che li occupano
  /dev_log [N]       — ultime N righe di log in memoria (default 30)
  /job_status        — job attivi nella JobQueue con prossima esecuzione
"""
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import database as db
import teams as tm
import utils

logger = logging.getLogger(__name__)


def is_dev(user_id: int) -> bool:
    return user_id == utils.load_globals().get("dev_id")


# ── /dev ──────────────────────────────────────────────────────────────────────

async def dev_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    testo = (
        "<b>Comandi dev</b>\n\n"
        "/dev_version — versione corrente del bot\n"
        "/dev_aste_stato — aste raggruppate per stato\n"
        "/dev_watched — watcher attivi con nome GM/team\n"
        "/dev_rfa — RFA attive stagione corrente\n"
        "/dev_firme [N] — ultime N firme concluse (default 10)\n"
        "/dev_cap — cap e slot virtuali con aste che li occupano\n"
        "/dev_log [N] — ultime N righe di log in memoria (default 30)\n"
        "/job_status — job attivi nella JobQueue\n"
    )
    await update.effective_message.reply_text(testo, parse_mode="HTML")


# ── /dev_aste_stato ───────────────────────────────────────────────────────────

async def dev_aste_stato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return

    aste = db.get_all_aste()
    if not aste:
        await update.effective_message.reply_text("Nessuna asta nel DB.")
        return

    gruppi: dict[str, list] = {}
    for a in aste:
        gruppi.setdefault(a["stato"], []).append(a)

    ordine = ["APERTA", "CHIUSA", "PAREGGIO", "CONCLUSA", "ANNULLATA"]
    righe  = ["📊 <b>Aste per stato</b>", ""]

    for stato in ordine:
        lista = gruppi.get(stato, [])
        if not lista:
            continue
        righe.append(f"<b>{stato} ({len(lista)})</b>")
        for a in lista:
            righe.append(
                f"  #{a['id']} {a['tipo']} — {a['giocatore']} "
                f"({a['offerta_corrente']}M, scade {utils.format_dt(a['scade_at'])})"
            )
        righe.append("")

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_watched ──────────────────────────────────────────────────────────────

async def dev_watched(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return

    rows = db.get_all_watchers()
    if not rows:
        await update.effective_message.reply_text("Nessun watcher attivo.")
        return

    # mappa user_id → nome team per reverse lookup
    tutti_team = tm.get_all_teams()
    user_to_team: dict[int, str] = {}
    for t in tutti_team:
        for gm_id in t.get("gm_ids", []):
            user_to_team[gm_id] = t.get("gm_nome") or t["nome"]

    gruppi: dict[int, dict] = {}
    for r in rows:
        aid = r["asta_id"]
        if aid not in gruppi:
            gruppi[aid] = {"giocatore": r["giocatore"], "stato": r["stato"], "watchers": []}
        nome = user_to_team.get(r["user_id"], str(r["user_id"]))
        gruppi[aid]["watchers"].append(nome)

    righe = [f"🔔 <b>Watcher attivi — {len(rows)} totali</b>", ""]
    for aid, info in sorted(gruppi.items()):
        righe.append(
            f"<b>#{aid} {info['giocatore']}</b> [{info['stato']}] — "
            f"{len(info['watchers'])} watcher"
        )
        righe.append(f"  {', '.join(info['watchers'])}")

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_rfa ──────────────────────────────────────────────────────────────────

async def dev_rfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return

    stagione = utils.load_globals().get("stagione_corrente", "—")
    rfa_list = db.get_rfa_stagione(stagione)

    if not rfa_list:
        await update.effective_message.reply_text(
            f"Nessuna RFA nella stagione <b>{stagione}</b>.", parse_mode="HTML"
        )
        return

    righe = [f"🏷️ <b>RFA stagione {stagione} ({len(rfa_list)})</b>", ""]
    for r in rfa_list:
        righe.append(
            f"#{r['id']} <b>{r['giocatore']}</b> — {r['stato']}\n"
            f"  Prop: {r['squadra_proprietaria']} | "
            f"Offerta: {r['offerta_corrente']}M | "
            f"Scade: {utils.format_dt(r['scade_at'])}"
        )

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_firme ────────────────────────────────────────────────────────────────

async def dev_firme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return

    try:
        n = int(context.args[0]) if context.args else 10
        if n <= 0 or n > 50:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ N deve essere un intero tra 1 e 50.")
        return

    firme = db.get_ultime_firme(n)
    if not firme:
        await update.effective_message.reply_text("Nessuna firma conclusa nel DB.")
        return

    righe = [f"✍️ <b>Ultime {len(firme)} firme</b>", ""]
    for f in firme:
        righe.append(
            f"#{f['id']} <b>{f['giocatore']}</b> → {f['offerente_team_id']}\n"
            f"  💰 {f['offerta_corrente']}M × {f['anni_offerti']} anni — "
            f"{utils.format_dt(f['conclusa_at'])}"
        )

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_cap ──────────────────────────────────────────────────────────────────

async def dev_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cap e slot virtuali di tutte le squadre con dettaglio delle aste che li occupano.
    """
    if not is_dev(update.effective_user.id):
        return

    tutti_team = tm.get_all_teams()
    aste_aperte = [dict(a) for a in db.get_aste_aperte()]
    # includi anche PAREGGIO
    aste_pareggio = [dict(a) for a in db.get_aste_in_pareggio()]
    tutte_aste = {a["id"]: a for a in aste_aperte + aste_pareggio}

    righe = ["💰 <b>Cap e slot virtuali</b>", ""]

    for team in sorted(tutti_team, key=lambda t: t["nome"]):
        tid = team["id"]
        cap_tot  = team["cap_disponibile"]
        slot_tot = team["slot_disponibili"]
        cap_virt  = db.get_cap_virtuale(tid)
        slot_virt = db.get_slot_virtuali(tid)
        cap_lib  = cap_tot - cap_virt
        slot_lib = slot_tot - slot_virt

        righe.append(
            f"<b>{team['nome']}</b> — "
            f"cap {cap_lib}/{cap_tot}M libero | "
            f"slot {slot_lib}/{slot_tot} liberi"
        )

        # aste che occupano cap/slot di questo team
        aste_team = [
            a for a in tutte_aste.values()
            if a.get("offerente_team_id") == tid
        ]
        for a in aste_team:
            righe.append(
                f"  #{a['id']} {a['giocatore']} — "
                f"{a['offerta_corrente']}M [{a['stato']}]"
            )
        if not aste_team:
            righe.append("  <i>nessuna asta in corso</i>")

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


async def dev_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra la versione corrente del bot."""
    if not is_dev(update.effective_user.id):
        return
    try:
        from bot import BOT_VERSION
    except ImportError:
        BOT_VERSION = "sconosciuta"
    await update.effective_message.reply_text(f"🤖 Bot versione: <b>{BOT_VERSION}</b>", parse_mode="HTML")


# ── /dev_log ──────────────────────────────────────────────────────────────────

async def dev_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra le ultime N righe di log tenute in memoria da log_buffer.py.
    Default N=30, max 100.
    """
    if not is_dev(update.effective_user.id):
        return

    try:
        n = int(context.args[0]) if context.args else 30
        if n <= 0 or n > 100:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ N deve essere un intero tra 1 e 100.")
        return

    try:
        import log_buffer
        righe = list(log_buffer.buffer)[-n:]
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Errore accesso log buffer: {e}")
        return

    if not righe:
        await update.effective_message.reply_text("Nessuna riga di log in memoria.")
        return

    testo = "\n".join(righe)
    if len(testo) > 3800:
        testo = "...\n" + testo[-3800:]

    await update.effective_message.reply_text(
        f"<pre>{testo}</pre>", parse_mode="HTML"
    )


# ── /job_status ───────────────────────────────────────────────────────────────

async def job_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return

    jobs = context.application.job_queue.jobs()
    if not jobs:
        await update.effective_message.reply_text("Nessun job attivo in questo momento.")
        return

    now = datetime.now(timezone.utc)
    righe = [f"⚙️ <b>Job attivi: {len(jobs)}</b>", ""]
    for job in sorted(jobs, key=lambda j: j.next_t or now):
        next_t = job.next_t
        if next_t:
            diff = next_t - now
            minuti = int(diff.total_seconds() // 60)
            secondi = int(diff.total_seconds() % 60)
            prossima = f"tra {minuti}m {secondi}s ({utils.format_dt(next_t.isoformat())})"
        else:
            prossima = "—"
        nome = job.name or "senza nome"
        righe.append(f"• <code>{nome}</code>\n  ↳ {prossima}")

    await update.effective_message.reply_text("\n".join(righe), parse_mode="HTML")


# ── handlers ──────────────────────────────────────────────────────────────────

def get_handlers():
    return [
        CommandHandler("dev",            dev_help),
        CommandHandler("dev_version",    dev_version),
        CommandHandler("dev_aste_stato", dev_aste_stato),
        CommandHandler("dev_watched",    dev_watched),
        CommandHandler("dev_rfa",        dev_rfa),
        CommandHandler("dev_firme",      dev_firme),
        CommandHandler("dev_cap",        dev_cap),
        CommandHandler("dev_log",        dev_log),
        CommandHandler("job_status",     job_status),
    ]
