import json
import csv
import os
import unicodedata
import difflib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

GLOBALS_PATH = os.environ.get("GLOBALS_PATH", "/config/globals.json")
FA_CSV_PATH  = os.environ.get("FA_CSV_PATH",  "/config/fa_players.csv")

ROME = ZoneInfo("Europe/Rome")


def load_globals() -> dict:
    with open(GLOBALS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_admin_ids() -> list[int]:
    return load_globals()["admin_ids"]


def get_channel_id() -> int:
    return load_globals()["channel_id"]


def get_admin_group_id() -> int | None:
    return load_globals().get("admin_group_id")


def is_mercato_aperto() -> bool:
    return load_globals().get("mercato_aperto", True)


# ── normalizzazione diacritici ────────────────────────────────────────────────

def normalizza(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower().strip()


def get_fa_rows() -> list[dict]:
    rows = []
    with open(FA_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "nome":       row["nome"].strip(),
                "fantamedia": row.get("fantamedia", "").strip(),
                "firmato":    row.get("firmato", "0").strip(),
            })
    return rows


def get_fa_players() -> list[str]:
    return [r["nome"] for r in get_fa_rows() if r["firmato"] == "0"]


def segna_giocatore_firmato(nome: str):
    import shutil
    rows = get_fa_rows()
    tmp = FA_CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["nome", "fantamedia", "firmato"])
        writer.writeheader()
        for r in rows:
            if normalizza(r["nome"]) == normalizza(nome):
                r["firmato"] = "1"
            writer.writerow(r)
    shutil.move(tmp, FA_CSV_PATH)


def trova_giocatore_fa(input_nome: str) -> tuple[str | None, str | None, list[str]]:
    players = get_fa_players()
    norm_input = normalizza(input_nome)
    norm_map = {normalizza(p): p for p in players}

    if norm_input in norm_map:
        return norm_map[norm_input], None, []

    cognome_input = norm_input.split()[-1] if norm_input.split() else norm_input
    per_cognome = [p for norm_p, p in norm_map.items()
                   if norm_p.split()[-1] == cognome_input]
    if len(per_cognome) == 1:
        return per_cognome[0], None, []
    if len(per_cognome) > 1:
        return None, None, per_cognome

    # fuzzy sul cognome
    cognomi_map = {normalizza(p).split()[-1]: p for p in players}
    matches_cog = difflib.get_close_matches(cognome_input, cognomi_map.keys(), n=1, cutoff=0.75)
    if matches_cog:
        return None, cognomi_map[matches_cog[0]], []

    # fuzzy sul nome completo
    matches = difflib.get_close_matches(norm_input, norm_map.keys(), n=1, cutoff=0.7)
    if matches:
        return None, norm_map[matches[0]], []

    return None, None, []


def trova_giocatore_generico(input_nome: str, lista: list[str]) -> tuple[str | None, str | None]:
    norm_input = normalizza(input_nome)
    norm_map = {normalizza(p): p for p in lista}
    if norm_input in norm_map:
        return norm_map[norm_input], None
    matches = difflib.get_close_matches(norm_input, norm_map.keys(), n=1, cutoff=0.7)
    if matches:
        return None, norm_map[matches[0]]
    return None, None


# ── date/time ─────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def format_dt(s: str) -> str:
    dt = parse_iso(s).astimezone(ROME)
    return dt.strftime("%d/%m/%Y %H:%M")


def format_dt_short(s: str) -> str:
    """Solo giorno/mese ora:minuti — per storico offerte."""
    dt = parse_iso(s).astimezone(ROME)
    return dt.strftime("%d/%m %H:%M")


# ── formattazione messaggio canale ────────────────────────────────────────────

def build_canale_message(asta: dict, offerte: list, teams_map: dict) -> str:
    tipo_label = "🔴 RFA" if asta["tipo"] == "RFA" else "🟢 FREE AGENCY"

    if asta["tipo"] == "RFA" and asta["squadra_proprietaria"]:
        prop_nome = teams_map.get(asta["squadra_proprietaria"], asta["squadra_proprietaria"])
        vec_comp = asta["vecchio_compenso"]
        vec_str = f" · vecchio contratto: {vec_comp}M" if vec_comp else ""
        header = f"{tipo_label} — <b>{asta['giocatore']}</b> <i>(diritti: {prop_nome}{vec_str})</i>"
    else:
        header = f"{tipo_label} — <b>{asta['giocatore']}</b>"

    if offerte:
        righe = []
        for i, o in enumerate(offerte, 1):
            team_nome = teams_map.get(o["team_id"], o["team_id"])
            ts = format_dt_short(o["timestamp"])
            righe.append(f"  {i}. {team_nome} — {o['importo']}M <i>({ts})</i>")
        storico = "<i>Storico offerte:</i>\n" + "\n".join(righe)
    else:
        storico = "<i>Nessuna offerta ancora.</i>"

    if asta["offerta_corrente"] > 0 and asta["offerente_team_id"]:
        team_nome = teams_map.get(asta["offerente_team_id"], asta["offerente_team_id"])
        corrente = f"💰 <b>Offerta attuale: {asta['offerta_corrente']}M — {team_nome}</b>"
    else:
        corrente = "💰 <b>Nessuna offerta</b>"

    stato = asta["stato"]
    if stato == "APERTA":
        ultima = ""
        if offerte:
            ultima = f"\n🕐 Ultima offerta: {format_dt_short(offerte[-1]['timestamp'])}"
        footer = f"{corrente}{ultima}\n⏰ Scade: {format_dt(asta['scade_at'])}"

    elif stato == "CHIUSA":
        is_rfa = asta["tipo"] == "RFA"
        ore = 12 if is_rfa else 48
        ultima = f"\n🕐 Ultima offerta: {format_dt(offerte[-1]['timestamp'])}" if offerte else ""
        if asta["offerente_team_id"]:
            team_nome = teams_map.get(asta["offerente_team_id"], asta["offerente_team_id"])
            footer = (
                f"{corrente}{ultima}\n\n"
                f"🔒 <b>ASTA CHIUSA</b>\n"
                f"⏳ <b>{team_nome}</b> ha {ore}h per scegliere gli anni del contratto\n"
                f"<i>(dalla chiusura: {format_dt(asta['conclusa_at'])})</i>"
            )
        else:
            prop_nome = teams_map.get(asta["squadra_proprietaria"], "") if asta["squadra_proprietaria"] else ""
            footer = (
                f"{corrente}\n\n"
                f"🔒 <b>ASTA CHIUSA — nessuna offerta</b>\n"
                f"⏳ {prop_nome} può firmare o lasciare andare il giocatore"
            )

    elif stato == "PAREGGIO":
        anni = asta["anni_offerti"] if asta["anni_offerti"] else "?"
        team_vince = teams_map.get(asta["offerente_team_id"], asta["offerente_team_id"]) if asta["offerente_team_id"] else "?"
        prop_nome = teams_map.get(asta["squadra_proprietaria"], "") if asta["squadra_proprietaria"] else ""
        footer = (
            f"{corrente} × {anni} ann{'o' if anni == 1 else 'i'} <i>(offerta di {team_vince})</i>\n\n"
            f"⚖️ <b>IN ATTESA DI PAREGGIO</b>\n"
            f"⏳ <b>{prop_nome}</b> ha 24h per decidere se pareggiare\n"
            f"<i>(dalla chiusura: {format_dt(asta['conclusa_at'])})</i>"
        )

    elif stato == "ANNULLATA":
        footer = f"{corrente}\n\n❌ <b>ASTA ANNULLATA</b>"

    else:  # CONCLUSA
        team_firma = teams_map.get(asta["offerente_team_id"], asta["offerente_team_id"]) if asta["offerente_team_id"] else "?"
        anni_c = asta["anni_contratto"] if asta["anni_contratto"] else "?"
        firmato = f"\n✍️ Firmato: {format_dt(asta['firmato_at'])}" if asta["firmato_at"] else ""
        ultima = f"\n🕐 Ultima offerta: {format_dt(offerte[-1]['timestamp'])}" if offerte else ""
        footer = (
            f"💰 <b>{asta['offerta_corrente']}M × {anni_c} ann{'o' if anni_c == 1 else 'i'} — {team_firma}</b>"
            f"{ultima}{firmato}\n\n✅ <b>CONTRATTO FIRMATO</b>"
        )

    sep = "━━━━━━━━━━━━━━━━━━"
    return f"{header}\n{sep}\n\n{storico}\n\n{sep}\n{footer}"
