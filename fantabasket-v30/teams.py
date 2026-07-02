"""
Gestione squadre: lettura/scrittura del JSON con lock thread-safe.
"""
import json
import threading
import os

TEAMS_PATH = os.environ.get("TEAMS_PATH", "/config/teams.json")

_lock = threading.Lock()


def _load() -> dict:
    with open(TEAMS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(TEAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all_teams() -> list[dict]:
    return _load()["teams"]


def get_team_by_id(team_id: str) -> dict | None:
    for t in get_all_teams():
        if t["id"] == team_id:
            return t
    return None


def get_team_by_gm(gm_telegram_id: int) -> dict | None:
    for t in get_all_teams():
        if gm_telegram_id in t["gm_ids"]:
            return t
    return None


def check_slot_virtuale(team_id: str, slot_impegnati: int) -> bool:
    team = get_team_by_id(team_id)
    if team is None:
        return False
    return (team["slot_disponibili"] - slot_impegnati) > 0


def scala_cap(team_id: str, importo: int):
    """Scala solo il cap (senza toccare gli slot)."""
    with _lock:
        data = _load()
        for t in data["teams"]:
            if t["id"] == team_id:
                t["cap_disponibile"] -= importo
                break
        _save(data)


def libera_cap(team_id: str, importo: int):
    """Aggiunge cap (libera il vecchio contratto). Usato alla chiusura RFA."""
    with _lock:
        data = _load()
        for t in data["teams"]:
            if t["id"] == team_id:
                t["cap_disponibile"] += importo
                break
        _save(data)


def scala_cap_slot(team_id: str, importo: int):
    """Scala cap e slot al momento della firma FA."""
    with _lock:
        data = _load()
        for t in data["teams"]:
            if t["id"] == team_id:
                t["cap_disponibile"] -= importo
                t["slot_disponibili"] -= 1
                break
        _save(data)


def set_cap(team_id: str, valore: int):
    with _lock:
        data = _load()
        for t in data["teams"]:
            if t["id"] == team_id:
                t["cap_disponibile"] = valore
                break
        _save(data)


def set_slot(team_id: str, valore: int):
    with _lock:
        data = _load()
        for t in data["teams"]:
            if t["id"] == team_id:
                t["slot_disponibili"] = valore
                break
        _save(data)
