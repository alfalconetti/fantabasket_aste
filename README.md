# Fantabasket Aste Bot

Bot Telegram per gestire aste di free agency (FA e RFA) per una lega Fantabasket.

Sviluppato con [Claude](https://claude.ai) (Anthropic).

## Stack

- Python 3.12 + python-telegram-bot 21.6
- SQLite (aste, offerte, contratti, watch)
- Docker Compose

## Struttura

```
├── bot.py                  # Entry point, error handler, recupero stati al restart
├── database.py             # Query SQLite
├── teams.py                # Lettura/scrittura JSON squadre
├── utils.py                # Config, CSV giocatori, normalizzazione, formatting canale
├── settings.py             # Lettura settings.json (costanti di business)
├── scheduler.py            # Job periodico: scadenze e notifiche
├── handlers/
│   ├── helpers.py          # Funzioni condivise (aggiorna_canale, notifica_watchers, log_warn, log_job_error)
│   ├── dev.py              # Comandi diagnostici riservati al dev_id
│   ├── offerte.py          # Flusso offerte FA/RFA
│   ├── firma.py            # Flusso firma, pareggio RFA
│   ├── user.py             # Comandi utente: /me /watched /lista_fa /autocap /autoslot /silenzia
│   └── admin.py            # Comandi admin
├── docs/
│   ├── guida_gm.md         # Guida per i GM
│   └── guida_admin.md      # Guida per gli admin
├── CHANGELOG.md            # Storia delle versioni
└── config/                 # NON in git — dati sensibili
    ├── globals.json
    ├── teams.json
    ├── settings.json
    └── fa_players.csv
```

## Setup

### 1. Config

Crea la cartella `config/` con i seguenti file:

**globals.json**
```json
{
  "admin_ids": [123456789],
  "channel_id": -1001234567890,
  "main_channel_id": null,
  "mercato_aperto": true,
  "fase": "offseason",
  "admin_group_id": null,
  "dev_id": 123456789,
  "log_channel_id": null,
  "stagione_corrente": "2026"
}
```

**teams.json**
```json
{
  "teams": [
    {
      "id": "team01",
      "nome": "Nome Squadra",
      "gm_ids": [123456789],
      "gm_nome": "Nome GM",
      "cap_disponibile": 35,
      "slot_disponibili": 3,
      "cap_penalizzato": 0
    }
  ]
}
```

**settings.json**
```json
{
  "durata_asta_ore": 18,
  "timeout_firma_fa_ore": 48,
  "timeout_firma_rfa_ore": 12,
  "timeout_pareggio_ore": 24,
  "rilancio_minimo": 1,
  "cap_offseason": 165,
  "cap_regular": 150,
  "slot_minimi_rs": 2,
  "minimo_contrattuale": 1,
  "fascia_bassa_max": 20,
  "fascia_media_max": 34,
  "soglia_anni_2": 20,
  "soglia_anni_3": 35,
  "paginazione_aste": 8,
  "paginazione_fa": 8,
  "notifica_minuti_scadenza": 15,
  "slot_massimo": 15,
  "cap_massimo": 165
}
```

`offerta_massima` non è più una chiave di settings.json: dalla v20 viene calcolata come
`cap_regular - (slot_minimi_rs - 1) * minimo_contrattuale`.

**fa_players.csv**
```
nome,fantamedia,firmato
LeBron James,42.3,0
Stephen Curry,38.1,0
```

### 2. Variabili d'ambiente

Crea `.env`:
```
BOT_TOKEN=il_tuo_token
HEALTHCHECK_URL=https://hc-ping.com/tuo-uuid
```

`HEALTHCHECK_URL` è opzionale: se assente, il ping periodico viene semplicemente saltato.

### 3. Avvio

```bash
docker compose up -d --build
docker compose logs -f
```

## Documentazione

- [Guida GM](docs/guida_gm.md)
- [Guida Admin](docs/guida_admin.md)
- [Emergency Recovery](docs/emergency_recovery.md)
- [Changelog](CHANGELOG.md)

## Comandi utente

| Comando | Descrizione |
|---------|-------------|
| `/start` | Benvenuto e guida |
| `/offri` | Fai un'offerta su un'asta aperta |
| `/nuova_fa <giocatore>` | Apri asta FA |
| `/lista_fa` | Lista giocatori FA disponibili |
| `/aste` | Aste in corso |
| `/listteams` | Squadre e cap |
| `/watched` | Aste che stai seguendo |
| `/me` | Tua situazione cap e slot |
| `/autocap <importo>` | Aggiunge cap in emergenza (notifica admin) |
| `/autoslot <importo>` | Aggiunge slot in emergenza (notifica admin) |
| `/silenzia <asta_id>` | Smetti di seguire un'asta |
| `/annulla` | Esci da qualsiasi operazione |

## Comandi admin

| Comando | Descrizione |
|---------|-------------|
| `/nuova_rfa <giocatore> <team_id> <vecchio_compenso>` | Apre asta RFA |
| `/chiudi_asta <id>` | Chiude forzatamente un'asta |
| `/annulla_asta <id>` | Annulla un'asta |
| `/annulla_offerta <id>` | Annulla ultima offerta |
| `/reset_rfa <nuova_stagione>` | Cambia stagione corrente |
| `/set_cap <team_id> <valore>` | Imposta cap squadra |
| `/add_cap <team_id> <importo>` | Aggiunge/sottrae cap |
| `/set_slot <team_id> <valore>` | Imposta slot squadra |
| `/add_slot <team_id> <importo>` | Aggiunge/sottrae slot |
| `/set_cap_penalizzato <team_id> <valore>` | Imposta penalità cap |
| `/set_fase <offseason\|regular>` | Cambia fase e scala cap |
| `/apri_mercato` / `/chiudi_mercato` | Gestisce mercato FA |
| `/listteams` | Lista squadre con ID e cap virtuale |
| `/team <team_id>` | Situazione cap/slot dettagliata di una squadra (come `/me`, per admin) |
| `/all_aste [stato]` | Lista tutte le aste con filtro opzionale per stato |
| `/riapri_asta <asta_id> [ore]` | Riporta asta CHIUSA/PAREGGIO ad APERTA con nuova scadenza |
| `/ripubblica_asta <asta_id>` | Ripubblica il messaggio canale di un'asta se cancellato per errore |
| `/force_esito <asta_id>` | Rimanda il messaggio di firma/pareggio al GM senza reboottare |
| `/estendi_asta <asta_id> <ore>` | Sposta la scadenza in avanti di N ore |
| `/sposta_asta <asta_id> <YYYY-MM-DDTHH:MM>` | Imposta scadenza precisa (ora di Roma) |
| `/stato_asta <asta_id>` | Dump completo del record DB di un'asta |
| `/job_status` | Lista job attivi nella JobQueue con prossima esecuzione |
| `/aste` | Lista aste con ID |
| `/admin` | Lista comandi admin |
| `/reboot` | Riavvia il bot (solo dev) |
