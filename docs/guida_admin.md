# Guida Admin — Fantabasket Aste Bot

## Gestione mercato

`/apri_mercato` — apre il mercato FA
`/chiudi_mercato` — chiude il mercato FA (le RFA non sono bloccate)
`/set_fase offseason|regular` — cambia fase e scala automaticamente il cap di tutte le squadre sottraendo o aggiungendo `15 + cap_penalizzato` per ogni squadra

## Gestione squadre

`/list` — lista asciutta di tutte le squadre con nome, ID e GM. Utile per trovare rapidamente un team_id.

`/listteams` — lista squadre con ID cliccabile, cap totale, cap libero (virtuale) e slot liberi
`/team <team_id>` — situazione completa di una singola squadra: cap totale, cap virtualmente impegnato, cap effettivamente libero, stima cap in regular season, slot, e tutte le offerte vincenti in corso. È la stessa vista che un GM ottiene con `/me`, ma per qualunque squadra. Utile per controlli puntuali senza dover scorrere `/list` — lista asciutta di tutte le squadre con nome, ID e GM. Utile per trovare rapidamente un team_id.

`/listteams`.
`/set_cap <team_id> <valore>` — imposta il cap disponibile a un valore assoluto
`/add_cap <team_id> <importo>` — aggiunge o sottrae cap (accetta negativi)
`/set_slot <team_id> <valore>` — imposta gli slot disponibili
`/add_slot <team_id> <importo>` — aggiunge o sottrae slot (accetta negativi)
`/set_cap_penalizzato <team_id> <valore>` — imposta la penalità cap

Quando modifichi cap o slot, il GM della squadra riceve automaticamente una notifica con il tuo nome e l'orario.

## Gestione RFA

`/nuova_rfa <giocatore> <team_id> <vecchio_compenso>` — apre un'asta RFA. Il nome non richiede accenti (Doncic = Dončić). Ogni team può avere al massimo una RFA per stagione.

`/reset_rfa <nuova_stagione>` — a inizio nuova stagione aggiorna la stagione corrente (es. `/reset_rfa 2027`). Lo storico delle aste precedenti viene conservato nel DB. Mostra conferma prima di procedere.

## Gestione aste

`/aste` — lista aste in corso con ID
`/chiudi_asta <id>` — chiude forzatamente un'asta. Se c'è un vincitore avvia il flusso firma. Se è RFA senza offerte avvia il flusso proprietario.
`/annulla_asta <id>` — annulla un'asta senza vincitore. Notifica tutti gli interessati (vincitore, proprietario RFA, watcher).
`/annulla_offerta <id>` — elimina l'ultima offerta e ripristina la precedente. Notifica il GM a cui viene annullata e i watcher. Warning automatico se il cap virtuale diventa negativo.

Tutti i comandi che modificano lo stato di un'asta o del mercato includono ora il nome dell'admin nella notifica inviata ai GM e nel canale log, per tracciabilità completa delle azioni.

`/annulla_offerta <asta_id>` controlla preventivamente se l'annullamento porterebbe il cap virtuale del nuovo vincitore in negativo. Se sì, richiede conferma dal gruppo admin prima di procedere.

`/all_aste [stato]` — lista tutte le aste con filtro opzionale per stato (`aperta`, `chiusa`, `pareggio`, `conclusa`, `annullata`). Senza argomenti mostra le ultime 30. Indispensabile per trovare l'ID di un'asta non più visibile in `/aste`, ad esempio per usare `/force_esito` o `/stato_asta`.

`/riapri_asta <asta_id> [ore]` — riporta un'asta da CHIUSA, PAREGGIO o ANNULLATA ad APERTA con una nuova scadenza. Se l'operazione porterebbe il cap virtuale di una squadra in negativo, invia una richiesta di conferma al gruppo admin prima di procedere. Per aste ANNULLATE chiede sempre conferma. Default 18h se non specificato. Cancella automaticamente i job di firma/pareggio pendenti, aggiorna il messaggio nel canale, notifica i watcher e manda un annuncio nel canale.

## Comandi di osservazione e diagnostica

`/firme [N]` — ultime N firme concluse con nome squadra e anni contratto (default 10). Utile per aggiornare i roster dopo le firme.

`/crea_offerta <team_id> <asta_id> <importo>` — registra un'offerta per conto di un team specifico senza ConversationHandler. Tutti i check vengono applicati. L'azione viene loggata sul canale log con il nome dell'admin. Utile in caso di emergenza o per correggere situazioni particolari.

`/stato_asta <asta_id>` — dump completo di tutti i campi DB di un'asta con storico offerte. Alternativa rapida all'aprire sqlite3 manualmente.

Per comandi di diagnostica avanzata (log, job attivi, cap dettagliato) usa `/dev` — disponibile solo per il dev_id configurato in globals.json.

## Comandi di recupero emergenza

`/ripubblica_asta <asta_id>` — se il messaggio di un'asta nel canale viene cancellato per errore, questo comando pubblica un nuovo messaggio aggiornato e corregge il riferimento nel DB. Senza questo intervento `aggiorna_canale` continua a fallire (con avviso sul canale log) ad ogni rilancio finché non si sistema manualmente.

`/forza_aggiornamento <asta_id>` — riedita forzatamente il messaggio canale esistente con i dati attuali dal DB. Utile quando `aggiorna_canale` è andato in timeout silenzioso e il messaggio è rimasto obsoleto (es. non mostra l'ultima offerta). A differenza di `/ripubblica_asta` non invia un nuovo messaggio: edita quello già presente. Se l'edit fallisce (messaggio cancellato, timeout di nuovo) suggerisce automaticamente di usare `/ripubblica_asta`.

`/force_esito <asta_id>` — rimanda manualmente il messaggio di firma o pareggio al GM interessato, senza dover reboottare il bot. Funziona solo su aste in stato CHIUSA o PAREGGIO. Non modifica il DB né cambia lo stato dell'asta: si limita a reinviare i messaggi e rischedulare i timeout automatici. Utile quando il GM non ha ricevuto il messaggio (es. aveva bloccato il bot temporaneamente) o quando il job automatico è saltato senza che se ne accorgesse nessuno.

## Diagnostica e manutenzione

`/admin` — lista comandi admin
`/reboot` — riavvia il bot (solo dev). Docker lo riporta su automaticamente.

## Note operative

- Il cap virtuale include le offerte vincenti su aste APERTE e in PAREGGIO
- Modifiche manuali al cap/slot possono creare cap virtuale negativo — il bot manda warning automatici al gruppo admin
- In caso di restart del bot, le aste in CHIUSA e PAREGGIO vengono automaticamente ripristinate dopo 5 secondi
- Tutti i warning critici finiscono nel canale log configurato in `globals.json`
- Le eccezioni nei job periodici (controllo scadenze, firma automatica, pareggio automatico, recupero stati al riavvio, backup) vengono ora segnalate anche sul canale log e in privato al dev, oltre che nei log del container — prima della v21 restavano visibili solo nei log Docker
- L'offerta massima consentita su una singola asta non è più un valore fisso in `settings.json`: viene calcolata come `cap_regular - (slot_minimi_rs - 1) * minimo_contrattuale`, così si aggiorna automaticamente se cambiano questi parametri
