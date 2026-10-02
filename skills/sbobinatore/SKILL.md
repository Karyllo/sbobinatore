---
name: sbobinatore
description: Usa questa skill per qualsiasi domanda o attività sui corsi universitari dell'utente (Politecnico di Milano) gestiti da `sbob`. Copre le domande di studio ("cosa ha detto il prof sui diodi?", "spiegami il BJT dai miei appunti", "in che lezione abbiamo fatto la matrice di copertura?"), l'aggiornamento di un corso (scaricare registrazioni, trascrivere, generare appunti), la preparazione di un esame (riassunti, prerequisiti, monolite per NotebookLM, temi d'esame), la conversione di PDF di slide in Markdown e il controllo della qualità di sbobine e appunti. Usala anche quando l'utente nomina un corso, una lezione, "le sbobine", "gli appunti di <materia>" o WeBeep.
---

# sbobinatore: lavorare con i corsi dell'utente

`sbob` gestisce, corso per corso: registrazioni → audio → trascrizioni → appunti → mappa per l'agente.
I file stanno su disco, quindi li leggi direttamente. La CLI serve per **trovare** cosa leggere e per **produrre** file nuovi.

## Come lanciare i comandi
- Usa `sbob …`. Se il comando non esiste, sbob non è installato: indica all'utente l'installazione del README (`uv tool install "sbobinatore[all] @ git+https://github.com/Karyllo/sbobinatore"`, poi `sbob init`).
- La config si trova in `$SBOB_CONFIG`, poi `./sbob.toml`, poi `~/.config/sbob/sbob.toml`. Se `sbob corsi` non trova nessun corso, dillo all'utente invece di indovinare i path.
- **Aggiungi sempre `--json`.** Su stdout esce un solo oggetto JSON, i log vanno su stderr.
- Leggi l'exit code:
  - `0`: ok
  - `1`: errore. Leggi `error`.
  - `2`: parziale. Leggi `failed`: alcuni file sono andati, altri no.
  - `3`: **serve l'utente**. Mostragli `needs_human` e `action` (cookie Webex scaduto, chiave API mancante, quota esaurita, login NotebookLM) e fermati. Non riprovare in loop.

## Dove stanno le cose (per ogni corso)
```
<cartella corso>/
  mappa/INDICE.md        ← LEGGI QUESTO PER PRIMO: lezioni in ordine, riassunto, concetti, prerequisiti, link
  mappa/concetti/<X>.md  ← dove un concetto è introdotto, ripreso, e per quali lezioni è prerequisito
  mappa/schede.json      ← gli stessi dati in JSON
  appunti/<stem>_appunti.md   ← dispense complete (fonte principale per spiegare)
  trascrizioni/<stem>.md      ← parlato del docente, con [mm:ss] all'inizio dei paragrafi
  video/ audio/ merge/ .sbob/ (stato interno, costi, cache: non toccare)
<root>/mappa/INDICE.md e catalog.json   ← tutti i corsi; concetti condivisi tra corsi
```
- Lo **stem** è `YYYY-MM-DD_<corso>_<tipo>NN`, con tipo `lez`, `ese`, `lab`, `sem` o `tde`. Lo stesso stem lega video, audio, trascrizione e appunti.
- Ogni `.md` ha un frontmatter YAML: `corso`, `data`, `tipo`, `numero`, `backend`, `modello`.

## Flussi di lavoro

### Domanda di studio
1. Se non sai quale corso è, lancia `sbob corsi --json`.
2. Leggi `<corso>/mappa/INDICE.md`, o `<root>/mappa/INDICE.md` se la domanda tocca più corsi. Spesso basta per capire quali lezioni contano.
3. Lancia `sbob cerca "<termini>" --corso <slug> --json`.
   - Tutti i termini devono comparire. Per una frase esatta metti `"tra virgolette"` dentro la query.
   - Restringi con `--in appunti`, `--in trascrizioni`, `--in mappa` o `--in materiale`.
   - Ogni risultato riporta `lezione`, `data`, `sezione`, `minuto` e `file`.
   - Se trovi pochi risultati, prova sinonimi o un singolo termine.
4. Apri **solo** gli appunti pertinenti, nella sezione indicata.
5. Rispondi citando la fonte, per esempio "Lezione 03 del 24/09, sezione *Polarizzazione*". Se serve la parola esatta del docente, cita la trascrizione con il minuto: "lez03, min 12:30".
6. Distingui teoria (`lez`) da esercitazione (`ese`). Se gli appunti non coprono l'argomento, dillo chiaramente e non inventare.

### "Cosa devo sapere prima di…" / "da dove parto"
- Usa `mappa/concetti/<X>.md`, che contiene le sezioni "Introdotto in" e "Prerequisito per".
- Usa anche il campo **Prerequisiti** delle lezioni in `INDICE.md`.

### Aggiornare un corso
1. Lancia `sbob status <slug> --json`. Per ogni lezione ti dice quali passi sono fatti e qual è il prossimo.
2. Lancia `sbob run <slug> --dry-run --json` e mostra all'utente cosa verrebbe fatto.
3. Se la simulazione prevede appunti per più di 2 lezioni, segnala che ci sono chiamate a pagamento e chiedi conferma.
4. Lancia `sbob run <slug> --json`. La catena è download → audio → trascrivi → appunti → mappa. Si ferma da sola con exit 3 se serve l'utente.
5. Lancia `sbob verifica <slug> --json` e riferisci gli `errore`.

Puoi lanciare anche un solo passo: `sbob audio|trascrivi|appunti|mappa <slug> [--solo <stem>] [--force] --json`.

### Registrazioni dall'archivio del Poli (recman) tramite Chrome
L'archivio `onlineservices.polimi.it/recman_frontend/...` usa codici di sessione monouso. Fuori dal browser dell'utente risponde con errore `POLIJ_049001`, quindi `sorgente = archives` non funziona. Procedura con **Claude in Chrome**, solo dopo il consenso dell'utente, in sola lettura:
1. Chiedi all'utente il link dell'archivio, quello che si apre da WeBeep. Aprilo in una scheda nuova e verifica che `tbody.TableDati-tbody` esista. In caso di `POLIJ_...` serve un link fresco.
2. Con `javascript_tool`:
   - trova il link `a.paginator_link` che contiene `action=plen_0` (la vista "tutte") e caricalo con `fetch(..., {credentials:"include"})`;
   - leggi le righe: `td[1]` = data `dd/mm/yyyy HH:MM`, `td[3]` = forma didattica, `td[4]` = argomento, `td[0] a.Link` = link "Riproduci";
   - salva l'elenco in `sessionStorage`. Lo strumento non restituisce URL con codici di sessione, ed è giusto così.
3. Per ogni riga: con JS imposta `location.href` sul link "Riproduci", aspetta 3 secondi, poi leggi con `tabs_context_mcp` l'URL Webex finale (`.../recording/<id>/playback`). Per tornare **riapri il link dell'archivio**: "indietro" non funziona, perché Webex riscrive la cronologia.
4. Scrivi `<corso>/link.txt`, una riga per registrazione: `link<TAB>dd/mm/yyyy HH:MM<TAB>forma<TAB>argomento` (le righe con `#` sono commenti). Chiudi la scheda e svuota `sessionStorage`.
5. `sbob download <corso> --dry-run --json`, mostra i nomi all'utente, poi lancia senza `--dry-run`.
   - La data dell'archivio prevale su quella di Webex, che a volte è sbagliata.
   - La forma didattica decide il tipo: `lez`, `lab`, `ese`.
   - L'argomento va nel frontmatter.

### Preparare un esame
- Riassunto del corso: leggi `mappa/INDICE.md`.
- Un file unico da caricare su NotebookLM o da dare a un LLM: `sbob merge <slug> --modo monolite --json`. Esce in `merge/`.
- Temi d'esame: `sbob merge <slug> --modo tde --da materiale --json`.
- PDF di slide o dispense in Markdown: `sbob pdf <file|cartella> --corso <slug> --json`. Esce in `.sbob/md/` e da lì si trova con `cerca --in materiale`.

### Qualità
- `sbob verifica [<slug>] --json` segnala:
  - appunti molto più corti della trascrizione, cioè contenuto perso;
  - trascrizioni troncate rispetto alla durata dell'audio;
  - loop di ripetizione;
  - file parziali;
  - buchi nella numerazione;
  - mappa non aggiornata.
- Ogni problema ha un `suggerimento` con il comando per risolverlo.

## Regole
- **Non modificare mai** `appunti/`, `trascrizioni/` o `video/` a mano, e non cancellare file. Per rigenerare usa i comandi con `--force`, solo se l'utente è d'accordo.
- **Non lanciare `run` su tutti i corsi** senza una conferma esplicita.
- Prima di comandi che costano (`appunti`, `trascrivi`, `pdf` su molti file), mostra il `--dry-run`.
- Exit 3 significa che serve l'utente: spiega cosa deve fare e fermati.
  - Cookie Webex: va copiato da politecnicomilano.webex.com, poi si lancia `sbob cookie ticket <valore>`.
  - Chiavi API: vanno nel `.env` accanto a `sbob.toml`.
- `mappa/` è generata: si rigenera con `sbob mappa <slug>`, o con `sbob indice` senza LLM. Non modificarla a mano.
- Quando citi qualcosa, indica sempre lezione e data, così l'utente può controllare.
