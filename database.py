import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "/data/aste.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS aste (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo                 TEXT    NOT NULL CHECK(tipo IN ('RFA','FA')),
                giocatore            TEXT    NOT NULL,
                squadra_proprietaria TEXT,
                vecchio_compenso     INTEGER,
                canale_msg_id        INTEGER,
                notifica_15min       INTEGER NOT NULL DEFAULT 0,
                stato                TEXT    NOT NULL DEFAULT 'APERTA'
                                             CHECK(stato IN ('APERTA','CHIUSA','PAREGGIO','CONCLUSA','ANNULLATA')),
                offerta_corrente     INTEGER NOT NULL DEFAULT 0,
                offerente_team_id    TEXT,
                anni_offerti         INTEGER,
                anni_contratto       INTEGER,
                creata_at            TEXT    NOT NULL,
                scade_at             TEXT    NOT NULL,
                conclusa_at          TEXT,
                firmato_at           TEXT
            );

            CREATE TABLE IF NOT EXISTS offerte (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                asta_id   INTEGER NOT NULL REFERENCES aste(id),
                team_id   TEXT    NOT NULL,
                importo   INTEGER NOT NULL,
                timestamp TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contratti (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                asta_id    INTEGER NOT NULL REFERENCES aste(id),
                giocatore  TEXT    NOT NULL,
                team_id    TEXT    NOT NULL,
                importo    INTEGER NOT NULL,
                anni       INTEGER NOT NULL,
                ruolo      TEXT    DEFAULT NULL,
                firmato_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS aste_watch (
                asta_id INTEGER NOT NULL REFERENCES aste(id),
                gm_id   INTEGER NOT NULL,
                PRIMARY KEY (asta_id, gm_id)
            );

            CREATE INDEX IF NOT EXISTS idx_aste_stato   ON aste(stato);
            CREATE INDEX IF NOT EXISTS idx_offerte_asta ON offerte(asta_id);
            CREATE INDEX IF NOT EXISTS idx_watch_asta   ON aste_watch(asta_id);
        """)

    # Migrazioni additive: aggiunge colonne se non esistono già
    _migrate()


def _migrate():
    """Aggiunge colonne nuove a DB esistenti senza perdere dati."""
    migrazioni = [
        "ALTER TABLE aste ADD COLUMN anni_contratto INTEGER",
        "ALTER TABLE aste ADD COLUMN firmato_at TEXT",
        "ALTER TABLE aste ADD COLUMN notifica_15min INTEGER NOT NULL DEFAULT 0",
    ]
    with get_conn() as conn:
        for sql in migrazioni:
            try:
                conn.execute(sql)
            except Exception:
                pass  # colonna già esistente, ignora


# ── aste ──────────────────────────────────────────────────────────────────────

def crea_asta(tipo, giocatore, squadra_proprietaria, creata_at, scade_at, vecchio_compenso=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO aste
               (tipo, giocatore, squadra_proprietaria, vecchio_compenso, creata_at, scade_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tipo, giocatore, squadra_proprietaria, vecchio_compenso, creata_at, scade_at),
        )
        return cur.lastrowid


def set_canale_msg_id(asta_id, msg_id):
    with get_conn() as conn:
        conn.execute("UPDATE aste SET canale_msg_id=? WHERE id=?", (msg_id, asta_id))


def get_asta(asta_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM aste WHERE id=?", (asta_id,)).fetchone()


def get_aste_aperte():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM aste WHERE stato='APERTA' ORDER BY scade_at"
        ).fetchall()


def get_aste_chiuse():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM aste WHERE stato='CHIUSA'"
        ).fetchall()


def get_aste_in_pareggio():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM aste WHERE stato='PAREGGIO' ORDER BY conclusa_at"
        ).fetchall()


def giocatore_gia_in_asta(giocatore):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM aste WHERE giocatore=? AND stato IN ('APERTA','CHIUSA','PAREGGIO')",
            (giocatore,)
        ).fetchone()
        return row is not None


def team_ha_rfa_stagione(team_id: str) -> bool:
    """True se il team ha già usato la sua RFA questa stagione (qualsiasi stato)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM aste WHERE squadra_proprietaria=? AND tipo='RFA'",
            (team_id,),
        ).fetchone()
        return row is not None


def aggiorna_offerta(asta_id, team_id, importo, nuova_scadenza):
    with get_conn() as conn:
        conn.execute(
            """UPDATE aste
               SET offerta_corrente=?, offerente_team_id=?, scade_at=?, notifica_15min=0
               WHERE id=?""",
            (importo, team_id, nuova_scadenza, asta_id),
        )


def chiudi_asta(asta_id, conclusa_at):
    with get_conn() as conn:
        conn.execute(
            "UPDATE aste SET stato='CHIUSA', conclusa_at=? WHERE id=?",
            (conclusa_at, asta_id),
        )


def set_anni_offerti(asta_id, anni):
    with get_conn() as conn:
        conn.execute(
            "UPDATE aste SET anni_offerti=?, stato='PAREGGIO' WHERE id=?",
            (anni, asta_id),
        )


def concludi_asta(asta_id, anni_contratto=None, firmato_at=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE aste SET stato='CONCLUSA', anni_contratto=?, firmato_at=? WHERE id=?",
            (anni_contratto, firmato_at, asta_id),
        )


def annulla_asta(asta_id):
    with get_conn() as conn:
        conn.execute("UPDATE aste SET stato='ANNULLATA' WHERE id=?", (asta_id,))


# ── offerte ───────────────────────────────────────────────────────────────────

def registra_offerta(asta_id, team_id, importo, timestamp):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO offerte (asta_id, team_id, importo, timestamp) VALUES (?,?,?,?)",
            (asta_id, team_id, importo, timestamp),
        )


def get_offerte(asta_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM offerte WHERE asta_id=? ORDER BY id", (asta_id,)
        ).fetchall()


# ── contratti ─────────────────────────────────────────────────────────────────

def registra_contratto(asta_id, giocatore, team_id, importo, anni, ruolo, firmato_at):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO contratti (asta_id, giocatore, team_id, importo, anni, ruolo, firmato_at)
               VALUES (?,?,?,?,?,?,?)""",
            (asta_id, giocatore, team_id, importo, anni, ruolo, firmato_at),
        )


# ── notifica 15min ────────────────────────────────────────────────────────────

def notifica_15min_inviata(asta_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT notifica_15min FROM aste WHERE id=?", (asta_id,)
        ).fetchone()
        return bool(row["notifica_15min"]) if row else True


def segna_notifica_15min(asta_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE aste SET notifica_15min=1 WHERE id=?", (asta_id,))


# ── cap/slot virtuale ─────────────────────────────────────────────────────────

def get_cap_virtuale(team_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(offerta_corrente), 0) as totale
               FROM aste
               WHERE offerente_team_id=? AND stato IN ('APERTA','PAREGGIO')""",
            (team_id,),
        ).fetchone()
        return row["totale"] if row else 0


def get_slot_virtuali(team_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as totale
               FROM aste
               WHERE offerente_team_id=? AND stato IN ('APERTA','PAREGGIO')""",
            (team_id,),
        ).fetchone()
        return row["totale"] if row else 0


def get_rfa_proprietario(team_id: str) -> list:
    """RFA attive (APERTA/CHIUSA/PAREGGIO) di cui il team è proprietario."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT giocatore, vecchio_compenso FROM aste
               WHERE squadra_proprietaria=? AND tipo='RFA'
               AND stato IN ('APERTA','CHIUSA','PAREGGIO')""",
            (team_id,),
        ).fetchall()


def get_offerte_vincenti_team(team_id: str) -> list:
    with get_conn() as conn:
        return conn.execute(
            """SELECT id, tipo, giocatore, offerta_corrente, scade_at
               FROM aste
               WHERE offerente_team_id=? AND stato IN ('APERTA','PAREGGIO')
               ORDER BY scade_at""",
            (team_id,),
        ).fetchall()


# ── gestione offerte ─────────────────────────────────────────────────────────

def annulla_ultima_offerta(asta_id: int) -> dict | None:
    """
    Elimina l'ultima offerta e ripristina quella precedente (o 0 se era la prima).
    Resetta notifica_15min a 0.
    Ritorna dict con info per aggiornare il canale, o None se non c'era offerta.
    """
    with get_conn() as conn:
        offerte = conn.execute(
            "SELECT * FROM offerte WHERE asta_id=? ORDER BY id", (asta_id,)
        ).fetchall()

        if not offerte:
            return None

        ultima = offerte[-1]
        conn.execute("DELETE FROM offerte WHERE id=?", (ultima["id"],))

        if len(offerte) >= 2:
            precedente = offerte[-2]
            nuovo_importo = precedente["importo"]
            nuovo_team = precedente["team_id"]
        else:
            # era la prima offerta
            nuovo_importo = 0
            nuovo_team = None

        # calcola nuova scadenza: 18h dall'offerta precedente o dalla creazione
        from datetime import datetime, timezone, timedelta
        import settings as _s
        ore = _s.durata_asta_ore()
        if len(offerte) >= 2:
            base_dt = datetime.fromisoformat(offerte[-2]["timestamp"])
        else:
            asta = conn.execute("SELECT creata_at FROM aste WHERE id=?", (asta_id,)).fetchone()
            base_dt = datetime.fromisoformat(asta["creata_at"])
        nuova_scadenza = (base_dt.replace(tzinfo=timezone.utc) + timedelta(hours=ore)).isoformat()

        conn.execute(
            """UPDATE aste SET offerta_corrente=?, offerente_team_id=?,
               scade_at=?, notifica_15min=0 WHERE id=?""",
            (nuovo_importo, nuovo_team, nuova_scadenza, asta_id),
        )
        return {
            "offerta_eliminata": ultima["importo"],
            "team_eliminato": ultima["team_id"],
            "nuovo_importo": nuovo_importo,
            "nuovo_team": nuovo_team,
            "nuova_scadenza": nuova_scadenza,
        }


# ── watch ─────────────────────────────────────────────────────────────────────

def add_watch(asta_id: int, gm_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO aste_watch (asta_id, gm_id) VALUES (?,?)",
            (asta_id, gm_id),
        )


def remove_watch(asta_id: int, gm_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM aste_watch WHERE asta_id=? AND gm_id=?",
            (asta_id, gm_id),
        )


def is_watching(asta_id: int, gm_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM aste_watch WHERE asta_id=? AND gm_id=?",
            (asta_id, gm_id),
        ).fetchone()
        return row is not None


def get_watchers(asta_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT gm_id FROM aste_watch WHERE asta_id=?", (asta_id,)
        ).fetchall()
        return [r["gm_id"] for r in rows]
