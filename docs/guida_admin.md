# Guida Admin — Fantabasket Aste Bot

## Gestione mercato

`/apri_mercato` — apre il mercato FA
`/chiudi_mercato` — chiude il mercato FA (le RFA non sono bloccate)
`/set_fase offseason|regular` — cambia fase e scala automaticamente il cap di tutte le squadre sottraendo o aggiungendo `15 + cap_penalizzato` per ogni squadra

## Gestione squadre

`/listteams` — lista squadre con ID cliccabile, cap totale, cap libero (virtuale) e slot liberi
`/team <team_id>` — situazione completa di una singola squadra: cap totale, cap virtualmente impegnato, cap effettivamente libero, stima cap in regular season, slot, e tutte le offerte vincenti in corso. È la stessa vista che un GM ottiene con `/me`, ma per qualunque squadra. Utile per controlli puntuali senza dover scorrere `/listteams`.
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
