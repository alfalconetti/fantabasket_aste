"""
Job periodico che controlla le aste scadute e le chiude,
e invia notifiche prima della scadenza ai watcher.
"""
import logging
from datetime import datetime, timedelta, timezone

import database as db
import utils
import settings
from handlers.firma import chiedi_anni

logger = logging.getLogger(__name__)


async def check_scadenze(context):
    now = datetime.now(timezone.utc)
    minuti = settings.notifica_minuti_scadenza()
    soglia = now + timedelta(minutes=minuti)
    aste = db.get_aste_aperte()

    for asta in aste:
        scade_at = datetime.fromisoformat(asta["scade_at"])

        # ── chiusura ─────────────────────────────────────────────────────────
        if now >= scade_at:
            logger.info("Chiusura asta id=%d giocatore=%s", asta["id"], asta["giocatore"])
            db.chiudi_asta(asta["id"], now.isoformat())

            if asta["offerente_team_id"]:
                from handlers.helpers import aggiorna_canale
                await aggiorna_canale(context, asta["id"])
                await chiedi_anni(context, asta["id"])
            else:
                db.concludi_asta(asta["id"])
                from handlers.helpers import aggiorna_canale
                await aggiorna_canale(context, asta["id"])
                logger.info("Asta id=%d chiusa senza offerte", asta["id"])
            continue

        # ── notifica scadenza ─────────────────────────────────────────────────
        if now < scade_at <= soglia:
            if not db.notifica_15min_inviata(asta["id"]):
                db.segna_notifica_15min(asta["id"])
                watchers = db.get_watchers(asta["id"])
                if watchers:
                    import teams as tm
                    teams_map = {t["id"]: t["nome"] for t in tm.get_all_teams()}
                    vincitore = teams_map.get(asta["offerente_team_id"], "—") if asta["offerente_team_id"] else "nessuno"
                    testo = (
                        f"⏰ <b>{minuti} minuti alla scadenza!</b>\n"
                        f"🏀 <b>{asta['giocatore']}</b>\n"
                        f"Offerta attuale: <b>{asta['offerta_corrente']}M — {vincitore}</b>"
                    )
                    for gm_id in watchers:
                        try:
                            await context.bot.send_message(chat_id=gm_id, text=testo, parse_mode="HTML")
                        except Exception as e:
                            logger.warning("notifica scadenza a %d fallita: %s", gm_id, e)
                    logger.info("Notifica scadenza inviata per asta id=%d a %d watcher", asta["id"], len(watchers))
