"""
Lettura centralizzata di settings.json.
Tutte le costanti di business vengono lette da qui.
Il file viene riletto a ogni chiamata — modificabile live senza restart.
"""
import json
import os

SETTINGS_PATH = os.environ.get("SETTINGS_PATH", "/config/settings.json")


def get() -> dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# Shortcut per le costanti più usate

def durata_asta_ore() -> int:
    return get()["durata_asta_ore"]

def timeout_firma_fa_ore() -> int:
    return get()["timeout_firma_fa_ore"]

def timeout_firma_rfa_ore() -> int:
    return get()["timeout_firma_rfa_ore"]

def timeout_pareggio_ore() -> int:
    return get()["timeout_pareggio_ore"]

def rilancio_minimo() -> int:
    return get()["rilancio_minimo"]

def fascia_bassa_max() -> int:
    return get()["fascia_bassa_max"]

def fascia_media_max() -> int:
    return get()["fascia_media_max"]

def soglia_anni_2() -> int:
    return get()["soglia_anni_2"]

def soglia_anni_3() -> int:
    return get()["soglia_anni_3"]

def paginazione_aste() -> int:
    return get()["paginazione_aste"]

def paginazione_fa() -> int:
    return get()["paginazione_fa"]

def notifica_minuti_scadenza() -> int:
    return get()["notifica_minuti_scadenza"]

def offerta_massima() -> int:
    """
    Calcolo derivato: cap_regular - (slot_minimi_rs - 1) * minimo_contrattuale
    Rappresenta il massimo che una squadra può spendere su un singolo giocatore
    restando in grado di riempire gli slot minimi rimanenti al minimo salariale.
    """
    s = get()
    return s["cap_regular"] - (s["slot_minimi_rs"] - 1) * s["minimo_contrattuale"]

def slot_massimo() -> int:
    return get()["slot_massimo"]

def cap_massimo() -> int:
    return get()["cap_massimo"]
