# Guida GM — Fantabasket Aste Bot

## Prima di tutto

Manda `/start` al bot in privato — obbligatorio almeno una volta per ricevere messaggi privati.

## Comandi disponibili

### Offerte
`/offri` — mostra la lista delle aste aperte. Scegli quella che ti interessa e scrivi il tuo importo. Il bot blocca automaticamente offerte invalide (troppo basse, cap insufficiente, slot esauriti).

`/nuova_fa <giocatore>` — apri un'asta per un giocatore free agent. Puoi scrivere solo il cognome, anche con piccoli errori di battitura (es. `Currry` trova `Curry`). Il bot ti chiederà subito quanto offri. Esempio: `/nuova_fa LeBron James`

`/annulla` — esci da qualsiasi operazione se il bot sembra bloccato ad aspettare qualcosa.

### Info
`/lista_fa` — lista paginata di tutti i giocatori FA disponibili con fantamedia. 🟡 significa che c'è già un'asta aperta. Clicca un giocatore per avere il comando pronto da copiare.

`/aste` — tutte le aste in corso con offerta attuale, scadenza e ID.

`/listteams` — tutte le squadre con cap disponibile, cap realmente libero e slot liberi.

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

Come sopra ma hai solo 12 ore per scegliere gli anni. Dopo che hai scelto, il proprietario ha 24 ore per decidere se pareggiare. Se pareggia il giocatore rimane con lui, altrimenti passa a te.
