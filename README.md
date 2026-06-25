# Fantabasket Aste Bot

Bot Telegram per gestire aste di free agency (FA e RFA) per una lega Fantabasket.

## Stack

- Python 3.12 + python-telegram-bot 21.6
- SQLite (aste, offerte, contratti, watch)
- Docker Compose

## Struttura

```
├── bot.py                  # Entry point
├── database.py             # Query SQLite
├── teams.py                # Lettura/scrittura JSON squadre
├── utils.py                # Config, CSV giocatori, formatting messaggi canale
├── settings.py             # Lettura settings.json (costanti di business)
├── scheduler.py            # Job periodico: scadenze e notifiche
├── handlers/
│   ├── helpers.py          # Funzioni condivise tra handlers
│   ├── offerte.py          # Flusso offerte FA/RFA
│   ├── firma.py            # Flusso firma, pareggio RFA
│   ├── user.py             # Comandi utente: /me /watched /lista_fa
│   └── admin.py            # Comandi admin
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
  "mercato_aperto": true,
  "fase": "offseason",
  "admin_group_id": null,
  "dev_id": 123456789
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
  "fascia_bassa_max": 20,
  "fascia_media_max": 34,
  "soglia_anni_2": 20,
  "soglia_anni_3": 35,
  "paginazione_aste": 8,
  "paginazione_fa": 8,
  "notifica_minuti_scadenza": 15
}
```

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
```

### 3. Avvio

```bash
docker compose up -d --build
docker compose logs -f
```

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
| `/annulla` | Esci da qualsiasi operazione |

## Comandi admin

| Comando | Descrizione |
|---------|-------------|
| `/nuova_rfa <giocatore> <team_id> <vecchio_compenso>` | Apre asta RFA |
| `/chiudi_asta <id>` | Chiude forzatamente un'asta |
| `/annulla_asta <id>` | Annulla un'asta |
| `/reset_rfa` | Resetta flag RFA per nuova stagione |
| `/set_cap <team_id> <valore>` | Imposta cap squadra |
| `/set_slot <team_id> <valore>` | Imposta slot squadra |
| `/set_cap_penalizzato <team_id> <valore>` | Imposta penalità cap |
| `/set_fase <offseason\|regular>` | Cambia fase e scala cap |
| `/apri_mercato` / `/chiudi_mercato` | Gestisce mercato FA |
| `/listteams` | Lista squadre con ID |
| `/aste` | Lista aste con ID |
| `/admin` | Lista comandi admin |
