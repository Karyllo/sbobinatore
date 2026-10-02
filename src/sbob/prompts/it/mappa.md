Costruisci la scheda di una lezione universitaria per la mappa del corso "{corso}". La mappa serve a un assistente AI per orientarsi tra le lezioni.

Rispondi SOLO con un oggetto JSON valido, senza testo prima o dopo, con questa forma:
{{"riassunto": "...", "concetti": [{{"nome": "...", "ruolo": "introdotto"}}], "prerequisiti": ["..."]}}

REGOLE:
- riassunto: 3-5 frasi su cosa viene spiegato e come (teoria, esempi, esercizi), in italiano.
- concetti: da 3 a 10 concetti chiave trattati nella lezione. "ruolo" vale "introdotto" se la lezione lo spiega per la prima volta, "ripreso" se lo usa o lo approfondisce.
- Nomi dei concetti: brevi (1-4 parole), al singolare, con l'iniziale maiuscola, senza sigle ambigue.
- Se un concetto è già nell'elenco qui sotto, RIUSA ESATTAMENTE quel nome.
- prerequisiti: concetti che bisogna già sapere per capire la lezione (anche se non sono del corso). Al massimo 5.

CONCETTI GIÀ PRESENTI NEL CORSO:
{concetti_esistenti}

--- APPUNTI ({lezione}) ---
{testo}
--- FINE APPUNTI ---
