"""
Comandi riservati al dev (dev_id in globals.json).
Non compaiono in /admin e non sono accessibili agli altri admin.
Usati per osservare lo stato interno del sistema senza aprire sqlite3.

Comandi:
  /dev_aste_stato    — aste raggruppate per stato
  /dev_watched       — watcher attivi per asta
  /dev_rfa           — RFA attive nella stagione corrente
  /dev_firme [N]     — ultime N firme concluse (default 10)
"""
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import database as db
import utils

logger = logging.getLogger(__name__)


def is_dev(user_id: int) -> bool:
    return user_id == utils.load_globals().get("dev_id")


# ── /dev_aste_stato ───────────────────────────────────────────────────────────

async def dev_aste_stato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra tutte le aste raggruppate per stato con conteggio.
    Snapshot veloce della situazione senza entrare in sqlite3.
    """
    if not is_dev(update.effective_user.id):
        return

    aste = db.get_all_aste()
    if not aste:
        await update.message.reply_text("Nessuna asta nel DB.")
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

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_watched ──────────────────────────────────────────────────────────────

async def dev_watched(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra tutti i watcher attivi raggruppati per asta.
    Utile per capire quante notifiche arriveranno su un'asta imminente.
    """
    if not is_dev(update.effective_user.id):
        return

    rows = db.get_all_watchers()
    if not rows:
        await update.message.reply_text("Nessun watcher attivo.")
        return

    # raggruppa per asta_id
    gruppi: dict[int, dict] = {}
    for r in rows:
        aid = r["asta_id"]
        if aid not in gruppi:
            gruppi[aid] = {"giocatore": r["giocatore"], "stato": r["stato"], "watchers": []}
        gruppi[aid]["watchers"].append(r["user_id"])

    righe = [f"🔔 <b>Watcher attivi — {len(rows)} totali</b>", ""]
    for aid, info in sorted(gruppi.items()):
        righe.append(
            f"<b>#{aid} {info['giocatore']}</b> [{info['stato']}] — "
            f"{len(info['watchers'])} watcher"
        )
        righe.append(f"  IDs: {', '.join(str(w) for w in info['watchers'])}")

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_rfa ──────────────────────────────────────────────────────────────────

async def dev_rfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra tutte le aste RFA della stagione corrente con stato e team proprietario.
    Utile per controllare chi ha già usato il jolly RFA.
    """
    if not is_dev(update.effective_user.id):
        return

    stagione = utils.load_globals().get("stagione_corrente", "—")
    rfa_list = db.get_rfa_stagione(stagione)

    if not rfa_list:
        await update.message.reply_text(
            f"Nessuna RFA nella stagione <b>{stagione}</b>.",
            parse_mode="HTML",
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

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")


# ── /dev_firme ────────────────────────────────────────────────────────────────

async def dev_firme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra le ultime N firme concluse con importo, anni e team.
    Default N=10. Uso: /dev_firme [N]
    """
    if not is_dev(update.effective_user.id):
        return

    try:
        n = int(context.args[0]) if context.args else 10
        if n <= 0 or n > 50:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ N deve essere un intero tra 1 e 50.")
        return

    firme = db.get_ultime_firme(n)
    if not firme:
        await update.message.reply_text("Nessuna firma conclusa nel DB.")
        return

    righe = [f"✍️ <b>Ultime {len(firme)} firme</b>", ""]
    for f in firme:
        righe.append(
            f"#{f['id']} <b>{f['giocatore']}</b> → {f['offerente_team_id']}\n"
            f"  💰 {f['offerta_corrente']}M × {f['anni_offerti']} anni — "
            f"{utils.format_dt(f['conclusa_at'])}"
        )

    await update.message.reply_text("\n".join(righe), parse_mode="HTML")


# ── handlers ──────────────────────────────────────────────────────────────────

def get_handlers():
    return [
        CommandHandler("dev_aste_stato", dev_aste_stato),
        CommandHandler("dev_watched",    dev_watched),
        CommandHandler("dev_rfa",        dev_rfa),
        CommandHandler("dev_firme",      dev_firme),
    ]
