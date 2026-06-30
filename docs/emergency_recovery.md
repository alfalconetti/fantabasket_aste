# Emergency Recovery — Fantabasket Aste Bot

Questa guida permette di riavviare il bot su qualsiasi macchina in caso di emergenza (server giù, corrente mancante, ecc.).

## Requisiti

- Docker e Docker Compose installati
- Git installato
- Accesso al canale log o al gruppo admin su Telegram
- Token del bot (solo il dev ce l'ha) — oppure creare un bot di emergenza (vedi sotto)

---

## Procedura standard (dev disponibile)

### 1. Scarica il backup
Vai sul canale log o nel gruppo admin e scarica l'ultimo zip di backup. Contiene:
- `data/aste.db` — il database completo
- `config/globals.json`
- `config/teams.json`
- `config/settings.json`
- `config/fa_players.csv`

### 2. Clona la repo
```bash
git clone https://github.com/alfalconetti/fantabasket-aste.git
cd fantabasket-aste
```

### 3. Estrai il backup
```bash
unzip backup_YYYYMMDD.zip -d restore/
mkdir -p data config
cp restore/data/aste.db data/
cp restore/config/* config/
```

### 4. Crea il file .env
```bash
cat << 'ENVEOF' > .env
BOT_TOKEN=il_tuo_token
ENVEOF
```

### 5. Avvia
```bash
docker compose up -d --build
docker compose logs -f
```

Il bot si avvia e dopo 5 secondi recupera automaticamente le aste in stato CHIUSA o PAREGGIO.

---

## Procedura di emergenza (dev non disponibile)

Se il dev non è raggiungibile è possibile creare un bot temporaneo di emergenza.

### 1. Crea un nuovo bot su BotFather
1. Apri Telegram e cerca `@BotFather`
2. Manda `/newbot`
3. Scegli un nome (es. "Fantabasket Aste Emergency")
4. Scegli uno username (es. `fantabasket_aste_emergency_bot`)
5. BotFather ti darà un token — salvalo

### 2. Aggiungi il bot ai gruppi
Il nuovo bot va aggiunto come amministratore a:
- Il canale aste
- Il gruppo admin
- Il canale log (se configurato)

### 3. Aggiorna globals.json
Apri `config/globals.json` e aggiorna i campi se necessario (es. `channel_id` se hai ricreato il canale).

### 4. Crea il .env con il nuovo token
```bash
cat << 'ENVEOF' > .env
BOT_TOKEN=token_del_bot_di_emergenza
ENVEOF
```

### 5. Segui i passi 2-5 della procedura standard

---

## Note importanti

- Il file `.env` non è mai in git e non viene mai mandato nei backup — è responsabilità del dev tenerlo al sicuro
- Il database contiene tutte le aste, offerte e contratti — è il file più importante
- I config in `config/` contengono i dati della lega (cap, slot, GM) — vengono inclusi nel backup
- Dopo il ripristino verifica con `/listteams` e `/aste` che tutto sia corretto
- Se ci sono aste in corso i GM coinvolti riceveranno automaticamente i messaggi pendenti entro 5 secondi dall'avvio

---

## Ripristino su server principale

Quando il server principale torna disponibile, sposta tutto lì e spegni il bot di emergenza.

⚠️ **Non avviare mai due istanze con lo stesso token contemporaneamente** — causano conflitti. Con token diversi non ci sono problemi tecnici, ma i due bot hanno DB separati quindi il DB del bot di emergenza va trasferito sul server principale prima di spegnere quello di emergenza.

### 1. Esporta il DB aggiornato dal computer di emergenza
```bash
cd fantabasket-aste
zip -r backup_emergency.zip data/aste.db config/
```

### 2. Trasferisci sull'M910q
```bash
scp backup_emergency.zip user@indirizzo-server:~/bots/fantabasket-aste/
```

### 3. Sul server principale
```bash
cd ~/bots/fantabasket-aste
unzip -o backup_emergency.zip
docker compose restart
```

### 4. Spegni il bot di emergenza
Sul computer di emergenza:
```bash
docker compose down
```

Verifica che il bot principale risponda prima di spegnere quello di emergenza.

---

## Contatti

In caso di problemi contatta il dev su Telegram.
