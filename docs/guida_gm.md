# Guida GM — Fantabasket Aste Bot

## Prima di tutto

Manda `/start` al bot in privato — obbligatorio almeno una volta per ricevere messaggi privati.

## Comandi disponibili

### Offerte
`/offri` — mostra la lista delle aste aperte. Clicca il bottone dell'asta che ti interessa per aprire subito il flusso di offerta in chat privata col bot. I bottoni non scadono mai — puoi cliccarli anche ore dopo senza problemi. Se nel frattempo l'asta è chiusa, il bot te lo dice subito. Il bot blocca automaticamente offerte invalide (troppo basse, cap insufficiente, slot esauriti) sia prima di chiederti l'importo che al momento dell'inserimento.

`/nuova_fa <giocatore>` — apri un'asta per un giocatore free agent. Puoi scrivere solo il cognome, anche con piccoli errori di battitura (es. `Currry` trova `Curry`). Il bot ti chiederà subito quanto offri. Esempio: `/nuova_fa LeBron James`

`/offerta asta <asta_id> <importo>` — offerta diretta senza menu interattivo. Tutti i check vengono applicati (cap, slot, stato asta). Se l'offerta non è valida ti dice subito il motivo senza chiederti di reinserire. Utile per programmare offerte in anticipo con lo scheduling messaggi di Telegram — scrivi il messaggio, programmalo per l'orario che vuoi, e il bot lo eseguirà automaticamente.

`/annulla` — esci da qualsiasi operazione se il bot sembra bloccato ad aspettare qualcosa.

### Info
`/lista_fa` — lista giocatori FA disponibili. Clicca su un giocatore per aprire direttamente il flusso di offerta in chat privata col bot, senza dover scrivere nulla. 🟡 indica che c'è già un'asta aperta per quel giocatore.

`/aste` — tutte le aste in corso con offerta attuale, scadenza e ID.

`/listteams` — lista tutte le squadre con cap impegnato dai contratti, cap disponibile e cap libero (al netto delle aste in corso). Stessa logica per gli slot.

`/team <team_id>` — situazione dettagliata di una squadra specifica: cap, slot e offerte vincenti in corso. Usa /listteams per trovare il team_id.

`/watched` — aste che stai seguendo con stato attuale e offerta corrente.

`/me` — la tua situazione: cap disponibile, cap virtualmente impegnato sulle aste in corso, slot liberi, offerte vincenti attive. In offseason mostra anche il cap previsto in Regular Season e il peso dei tuoi giocatori RFA.

### Emergenza
`/autocap <importo>` — aggiunge cap immediatamente alla tua squadra in caso di emergenza (es. trade avvenuta di notte con admin non disponibile). La richiesta viene segnalata agli admin per verifica. Usare solo in caso di reale necessità — dichiarazioni false comportano penalità.

`/autoslot <importo>` — come autocap ma per gli slot.

### Notifiche
`/silenzia <asta_id>` — smetti di ricevere notifiche per un'asta specifica.

## Come funziona un'asta

Ogni offerta resetta il timer a 18 ore. Non puoi rilanciare su te stesso. Il bot controlla cap e slot in tempo reale tenendo conto di tutte le aste che stai vincendo contemporaneamente.

## Seguire un'asta

Dal bottone 🔔 nel canale puoi seguire un'asta. Riceverai notifica privata ad ogni rilancio, 15 minuti prima della scadenza e quando il contratto viene firmato. Chi fa un'offerta viene messo in follow automaticamente. In ogni notifica di rilancio trovi `/silenzia <id>` per smettere di seguire.

## Quando vinci un'asta FA

Ricevi un messaggio privato con i bottoni per scegliere gli anni (1, 2 o 3). Hai 48 ore per rispondere. Se non rispondi il contratto viene firmato automaticamente per 3 anni come penale. Ricorda: 20-34M → minimo 2 anni, 35M+ → obbligatori 3 anni.

## Quando vinci un'asta RFA

Come per la FA, ricevi un messaggio privato con i bottoni per scegliere gli anni (1, 2 o 3). Hai **12 ore** per rispondere — se non rispondi, vengono assegnati automaticamente gli anni minimi per la tua fascia (1 se <20M, 2 se 20-34M, 3 se ≥35M).

Dopo che hai scelto gli anni, il proprietario ha 24 ore **dalla fine dell'asta** per decidere se pareggiare. Il bot lo informa subito della tua offerta e della scadenza esatta.

## Se sei il proprietario di un RFA

Alla chiusura dell'asta ricevi un messaggio con:
- L'importo offerto dal vincitore
- La scadenza esatta entro cui devi decidere (24h dalla fine dell'asta)
- Eventuale avviso se l'offerta è sotto soglia (vedi fasce sotto)
- Il bottone **⚡ Pre-imposta pareggio**

Il vincitore ha fino a 12h per scegliere gli anni — poi riceverai il messaggio finale con tutti i dettagli per decidere se pareggiare.

### Pre-pareggio
Puoi pre-impostare il pareggio subito, senza aspettare che il vincitore scelga gli anni. Clicca **⚡ Pre-imposta pareggio** e scegli gli anni che vuoi offrire:

- **Se l'offerta è sopra soglia** (vedi fasce): il pareggio scatta automaticamente solo se il vincitore offre un numero di anni ≤ a quello che hai scelto. Altrimenti ti verrà chiesto normalmente.
- **Se l'offerta è sotto soglia**: il pareggio scatta automaticamente a prescindere dagli anni del vincitore — gli anni che scegli sono quelli del tuo contratto.

Dopo aver impostato il pre-pareggio puoi modificarlo o annullarlo in qualsiasi momento finché il vincitore non sceglie gli anni.

### Fasce contratto per il pareggio
- **Vecchio compenso ≤20M**: puoi pareggiare a qualsiasi cifra offerta
- **Vecchio compenso 21-34M**: se l'offerta è inferiore a 1/3 del vecchio compenso (arrotondato per difetto), devi pareggiare almeno a quella soglia — ma gli anni sono liberi. Se l'offerta è superiore, pareggi alla cifra offerta.
- **Vecchio compenso ≥35M**: come sopra ma la soglia è 1/2 del vecchio compenso.

Se pareggi, devi offrire almeno gli stessi anni del vincitore (eccetto quando sei sotto soglia, dove gli anni sono liberi).

### Nessuna offerta ricevuta
Se nessuno ha offerto per il tuo RFA, puoi firmarlo tu seguendo le stesse regole di soglia, oppure lasciarlo andare in free agency.
