# Changelog — Fantabasket Aste Bot

## v1 — Base
Prima versione funzionante. Struttura iniziale del progetto con bot.py, database.py, teams.py, utils.py, scheduler.py. Flusso offerte FA e RFA base. Cap e slot dal JSON. Messaggi nel canale. Deep link dal canale per offrire.

## v2 — Cap virtuale e Watch
- Cap virtuale: somma offerte vincenti su aste aperte, controlla prima di ogni offerta
- Slot virtuale: come il cap, conta aste vincenti aperte
- Sistema watch: 🔔 nel canale, watch automatico per chi offre, notifiche push ad ogni rilancio
- Prima offerta contestuale in `/nuova_fa`
- `/me` con situazione cap e slot live
- Annuncio firma separato nel canale

## v3 — Notifica 15 minuti
- Notifica push ai watcher 15 minuti prima della scadenza con vincitore attuale
- Flag `notifica_15min` nel DB per non mandare duplicati

## v4 — Flusso RFA completo
- Logica RFA completa: 12h anni vincitore, PAREGGIO 24h
- Soglie pareggio per fascia (≤20, 21-34, ≥35) con vecchio compenso
- Anni liberi nel pareggio quando offerta sotto soglia
- Firma proprietario senza offerte
- Bottone ← Indietro nel pareggio
- Check cap e slot proprietario prima del pareggio
- `libera_cap` chiamato solo in `_registra_firma_finale` e `lascia_callback`
- Scala cap/slot anche per RFA alla firma (il giocatore RFA non occupava slot)
- Vecchio compenso liberato alla chiusura in tutti i casi
- Max 1 RFA per team per stagione

## v5 — Match giocatori e comandi admin
- Normalizzazione diacritici: Doncic = Dončić
- Sistema "intendevi?": fuzzy matching sul nome completo
- Match per cognome esatto
- `/listteams` con ID cliccabile per admin
- `/set_cap` e `/set_slot` admin live (modifica JSON senza restart)
- `/apri_mercato` e `/chiudi_mercato`

## v6 — Comandi pubblici e gestione aste
- `/aste` pubblico con lista aste in corso e ID
- `/chiudi_asta` admin con conferma
- `/annulla_asta` admin con conferma
- `/annulla` nei fallback ConversationHandler
- `conversation_timeout=300` (5 minuti)

## v7 — Timezone e messaggi canale
- Timezone Roma con `zoneinfo` (gestione automatica ora legale/solare)
- Proprietario RFA visibile nel messaggio canale e in `/aste`
- Bold nome squadra in `/listteams`
- Guida utenti nello `/start`

## v8 — Lista FA e watch avanzato
- `/lista_fa` con paginazione, fantamedia, pallino 🟡 per aste in corso, cliccabile
- Match per cognome con gestione omonimi (bottoni per scegliere)
- `/watched` con lista aste seguite
- `/listteams` e `/aste` pubblici
- Feedback privato quando inizi a seguire un'asta
- Notifica 15 minuti include vincitore e importo
- Messaggio pareggio include nome squadra vincitrice
- Ogni stato in attesa mostra "Per annullare: /annulla"
- CSV con colonne `nome`, `fantamedia`, `firmato`; giocatori FA firmati automaticamente
- Giocatori firmati spariscono dalla lista FA

## v9 — Fix critici cap/slot RFA
- Bug critico: cap e slot ora scalano correttamente alla firma RFA
- Vecchio compenso liberato dal cap proprietario alla chiusura in tutti i casi
- Check cap e slot prima del pareggio con formula `+ vecchio_compenso`
- Check slot prima del pareggio (il giocatore RFA non occupava slot)
- `scala_cap` e `libera_cap` in teams.py

## v10 — Fase offseason/regular e fix vari
- `/set_fase offseason|regular` scala cap di tutte le squadre con `cap_penalizzato`
- `"fase"` in globals.json, `"cap_penalizzato"` in teams.json
- `/me` mostra cap RS previsto in offseason con penalità
- Pallino 🟡 in `/lista_fa` anche per aste CHIUSE
- Notifica GM vincitore + messaggio canale su annullamento asta
- Check cap nel pareggio usa `+ vecchio_compenso`
- Check slot nel pareggio e nella firma proprietario senza offerte

## v11 — Refactoring architettura e messaggi ricchi
- Stato ANNULLATA nel DB (distinto da CONCLUSA)
- Migrazioni DB automatiche in `_migrate()`
- Refactoring cap RFA centralizzato: `_registra_firma_finale` gestisce sempre `libera_cap`
- `_concludi_rfa_senza_contratto` per chiusura senza firma
- Messaggi canale ricchi: timestamp offerte, chi deve fare cosa, orari firma
- Notifiche admin group quando impossibile contattare GM
- `admin_group_id` in globals.json
- Testi più esplicativi ovunque
- Fix annullamento RFA: il cap non viene mai toccato

## v12 — Settings e refactoring struttura
- `settings.json` con tutte le costanti di business modificabili live senza restart
- `settings.py` con shortcut per tutte le costanti
- `handlers/helpers.py` con funzioni condivise (aggiorna_canale, notifica_watchers, notifica_admin_group)
- `handlers/user.py` con /me, /watched, /lista_fa, watch_callback
- `handlers/offerte.py` solo flusso offerte
- `handlers/me.py` eliminato
- Import circolari rimossi
- Notifica watcher alla firma
- Vecchio compenso RFA visibile nel messaggio canale

## v13 — Nuovi comandi e miglioramenti
- Notifica proprietario RFA ad ogni nuova offerta sul suo giocatore
- `/reset_rfa` con anteprima e conferma
- `/set_cap_penalizzato` admin
- `/admin` con lista comandi admin
- Error handler globale: errori non gestiti in privato al dev
- `"dev_id"` in globals.json
- `cap_penalizzato` invece di `penalita` nel JSON

## v14 — Fix e fuzzy cognome
- Fix crash `update.message` → `update.effective_message` in `nuova_fa`
- Slot esauriti → esce dalla conversazione invece di restare in attesa
- Fuzzy cognome con gestione omonimi: `Corry` trova tutti i Curry con bottoni
- "Message is not modified" ignorato silenziosamente
- Cap/slot virtuali in `/listteams`
- Info utente nel messaggio di errore al dev
- README.md e .gitignore

## v15 — Autocap, autoslot e add_cap
- `/autocap` per GM: aggiunge cap con notifica admin e dev per verifica
- `/autoslot` per GM: aggiunge slot con notifica admin e dev
- `/add_cap` admin: aggiunge/sottrae cap (accetta negativi)
- `/add_slot` admin: aggiunge/sottrae slot (accetta negativi)
- Notifica GM quando admin tocca cap/slot
- `/annulla_offerta` admin: elimina ultima offerta, ripristina precedente, aggiorna canale

## v16 — Canale log e fix critici
- Canale log (`log_channel_id` in globals.json) per tutti i warning critici
- `log_warn()` in helpers usata ovunque
- Network errors sul canale log invece che ignorati
- Orario in tutti i messaggi del canale log
- Fix recupero stati pendenti al restart: aste CHIUSE e PAREGGIO ripristinate 5s dopo avvio
- Fix ordine operazioni `firma_callback` RFA: prima DB, poi proprietario, poi canale
- Fix cap/slot virtuali includono stato PAREGGIO
- Fix `reset_rfa` FOREIGN KEY: elimina record collegati prima delle aste
- `/add_slot` e `/autoslot` completi
- `TimedOut`/`NetworkError` sul canale log non in privato

## v17 — Stagioni e /reboot
- `/reboot` solo dev_id: invia SIGTERM e Docker riavvia
- Colonna `stagione` nelle aste e `stagione_corrente` in globals.json
- `team_ha_rfa_stagione` filtra per stagione corrente
- `/reset_rfa <nuova_stagione>` aggiorna stagione invece di eliminare aste (storico conservato)
- Nome admin e orario nelle notifiche cap/slot ai GM
- Fix nota anni in `chiedi_anni` usa settings invece di valori hardcodati
- Fix formattazione PAREGGIO nel canale (nome squadra non più ripetuto)
- Check cap virtuale negativo dopo `annulla_offerta` con warning all'admin
- Fix tag HTML non validi (`<importo>` → `&lt;importo&gt;`) nei messaggi di help
- Fix `/autoslot` CommandHandler allineato al nome corretto
- Notifica sul canale log quando il bot si riavvia con aste recuperate

## v18 — Notifiche, silenzia e documentazione
- Warning cap virtuale negativo al gruppo admin dopo modifiche cap admin (set_cap, add_cap)
- Notifica watcher quando asta viene annullata
- Notifica GM + watcher quando offerta viene annullata con /annulla_offerta
- Fix AUTOCAP → Autocap e AUTO_SLOT → Autoslot nel testo notifiche
- Fuzzy cognome con omonimi: prima chiede conferma del cognome, poi mostra lista giocatori
- /silenzia <asta_id> — smette di seguire un'asta specifica
- Hint "Per silenziare: /silenzia <id>" in ogni notifica di rilancio
- README.md aggiornato con struttura completa e credito a Claude
- docs/guida_gm.md e docs/guida_admin.md creati
- CHANGELOG.md aggiornato

## v19 — Backup, healthcheck e main channel
- Backup zip automatico (DB + config) 2x/giorno sul canale log, 1x/settimana sul gruppo admin
- Healthchecks.io ping ogni 5 minuti (`HEALTHCHECK_URL` in `.env`)
- `gm_nome` in teams.json — annuncio firma nel canale lega: "GM firma Giocatore con Squadra"
- `main_channel_id` in globals.json — annunci firma separati nel canale principale lega
- `aiohttp` nei requirements per il ping healthcheck

## v20 — /team e formula offerta massima

### Nuove funzionalità
- `/team <team_id>` solo admin — mostra cap, slot, offerte vincenti e stima cap RS di qualsiasi squadra (identico a `/me` dal punto di vista delle informazioni, ma accessibile per tutti i team)
- Formula `offerta_massima` derivata: `cap_regular - (slot_minimi_rs - 1) * minimo_contrattuale`
  - Rimossa la chiave `"offerta_massima"` da settings.json (ora è un calcolo)
  - Tre nuove chiavi in settings.json: `"cap_regular"`, `"slot_minimi_rs"`, `"minimo_contrattuale"`
  - Il valore si aggiorna automaticamente se cambiano i parametri, senza restart

### Roadmap (prossime versioni)
- **Rich messages Bot API 10.1** — messaggi canale con formattazione avanzata (bold/italic nativo, link inline, anteprime giocatore) non appena python-telegram-bot aggiornerà il supporto
- **Storico contratti per squadra** — comando `/contratti <team_id>` con lista firme della stagione corrente

## v21 — Error handling sui job periodici
- **Bug fix critico**: gli errori dentro i job di `JobQueue` (`check_scadenze`, `firma_automatica`, `pareggio_automatico`, `recupera_stati_pendenti`, `backup_giornaliero`, `backup_settimanale`) non passavano mai per `app.add_error_handler`, che copre solo gli update handler. Un'eccezione in uno di questi job restava visibile solo nei log del container, mai sul canale Telegram né al dev — causa probabile di notifiche di scadenza mancate senza alcun avviso visibile
- Nuova funzione `log_job_error()` in `handlers/helpers.py`: cattura l'eccezione, logga il traceback completo, lo invia al canale log e in privato al dev
- Tutti i job periodici ora sono avvolti in try/except che chiama `log_job_error`, senza alcuna modifica alla logica esistente all'interno
- Un'eccezione su una singola asta dentro `check_scadenze` non resta più invisibile: viene notificata su Telegram. Il giro corrente del job si interrompe comunque al punto dell'errore (comportamento invariato), ma il giro successivo (60s dopo) riprende regolarmente da capo

## v22 — Comandi di recupero emergenza
- `/ripubblica_asta <asta_id>` solo admin — pubblica un nuovo messaggio nel canale per un'asta esistente e aggiorna `canale_msg_id` nel DB. Utile se il messaggio originale viene cancellato per errore: senza questo comando `aggiorna_canale` continua a fallire ad ogni rilancio finché non si sistema manualmente il DB
- `/force_esito <asta_id>` solo admin — rimanda il messaggio di firma/pareggio appropriato al GM senza dover reboottare il bot. In base allo stato dell'asta: CHIUSA con vincitore → rimanda `chiedi_anni`; CHIUSA RFA senza offerte → rimanda il messaggio al proprietario; PAREGGIO → rimanda `_chiedi_pareggio`. Non modifica il DB, si limita a reinviare i messaggi e rischedulare i timeout

## v23 — Comandi admin avanzati e comandi dev

### Fix
- Rimosso annuncio firma duplicato su `channel_id`: ora viene mandato solo su `main_channel_id`. Il messaggio dell'asta nel canale viene già aggiornato con ✅ CONTRATTO FIRMATO da `aggiorna_canale`, l'annuncio separato era ridondante

### Nuovi comandi admin
- `/estendi_asta <asta_id> <ore>` — sposta la scadenza di un'asta in avanti di N ore, aggiorna il messaggio nel canale
- `/sposta_asta <asta_id> <YYYY-MM-DDTHH:MM>` — imposta una scadenza precisa (ora di Roma), utile per allineare più aste che scadono la stessa sera
- `/stato_asta <asta_id>` — dump completo di tutti i campi DB di un'asta con storico offerte, per debug rapido senza aprire sqlite3
- `/job_status` — lista tutti i job attivi nella JobQueue con nome e prossima esecuzione prevista, utile per verificare che i timeout automatici siano ancora schedulati dopo un recovery

### Nuovo file handlers/dev.py
Comandi riservati al solo `dev_id` (non visibili in /admin), per osservare lo stato del sistema senza aprire sqlite3:
- `/dev_aste_stato` — tutte le aste raggruppate per stato (APERTA, CHIUSA, PAREGGIO, CONCLUSA, ANNULLATA) con conteggio
- `/dev_watched` — tutti i watcher attivi raggruppati per asta con user_id
- `/dev_rfa` — tutte le aste RFA della stagione corrente con stato e team proprietario
- `/dev_firme [N]` — ultime N firme concluse con importo, anni e team (default 10, max 50)

## v24 — Robustezza input admin e nome admin nelle notifiche

### Fix
- Sostituito `update.message` con `update.effective_message` in tutti i comandi di `admin.py` — `update.message` può essere `None` in alcuni contesti (reply a messaggi forwarded, callback, ecc.) causando crash; `effective_message` funziona in qualsiasi contesto
- Messaggi di errore per argomenti non validi resi descrittivi: "❌ Serve l'ID numerico dell'asta. Usa /aste per vedere gli ID in corso." invece del generico "❌ ID non valido."

### Miglioramenti notifiche
- Aggiunto helper `_admin_label(user)` — costruisce uniformemente "Nome (@username)" o "Nome" se l'username manca
- Il nome dell'admin che ha eseguito il comando compare ora in tutte le notifiche rilevanti:
  - Annullamento asta → GM vincitore, proprietario RFA, watcher, canale
  - Annullamento offerta → GM, watcher, canale
  - Apertura/chiusura mercato FA → canale log
  - Estensione/spostamento scadenza asta → canale log

## v25 — Trasparenza azioni admin nel canale

- Chiusura asta anticipata da admin → annuncio nel canale aste con nome admin + notifica ai watcher (escluso il GM vincitore che viene già contattato per la firma)
- Apertura nuova RFA da admin → annuncio separato nel canale aste con nome admin (oltre al messaggio dell'asta già pubblicato)
- Estensione scadenza asta → annuncio nel canale aste con ore aggiunte e nuova scadenza, oltre al canale log
- Spostamento scadenza asta → annuncio nel canale aste con nuova scadenza, oltre al canale log

## v26 — /all_aste, /riapri_asta e notifica GM vincitore

- `/all_aste [stato]` solo admin — lista tutte le aste con filtro opzionale per stato (aperta, chiusa, pareggio, conclusa, annullata). Senza argomenti mostra le ultime 30. Utile per trovare l'ID di un'asta non più visibile in `/aste`
- `/riapri_asta <asta_id> [ore]` solo admin — riporta un'asta da CHIUSA o PAREGGIO ad APERTA con nuova scadenza (default 18h). Cancella i job pendenti di firma/pareggio, notifica nel canale e avvisa i watcher
- Chiusura anticipata da admin: il GM vincitore riceve un messaggio in privata che lo avvisa della chiusura anticipata prima della richiesta di firma; il proprietario RFA (se presente) viene avvisato che riceverà a breve la richiesta di pareggio

## v27 — Avviso riavvio, stagione FA, apertura RFA nel messaggio

- Avviso riavvio incondizionato sul canale log ad ogni restart, con versione e orario — prima arrivava solo se c'erano aste pendenti da recuperare
- Aggiunta costante `BOT_VERSION` in `bot.py` — compare nel messaggio di riavvio
- Stagione corrente ora salvata anche nelle aste FA (prima era `None`)
- "Aperta da X" integrato direttamente nel messaggio dell'asta RFA nel canale, invece di un secondo messaggio separato; rimosso il `send_message` ridondante
