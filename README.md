# sbobinatore (`sbob`)

Dalle registrazioni delle lezioni del Politecnico di Milano agli appunti, in un unico comando:

**registrazioni Webex → audio → trascrizione → appunti → mappa per lo studio**

- Gli **appunti** sono dispense in Markdown con le formule in LaTeX, pronte per Obsidian. Non sono riassunti: tutto quello che dice il docente rimane.
- La **mappa** raccoglie riassunti, concetti e prerequisiti di ogni lezione, tenuti separati dagli appunti. Serve a un assistente AI (Claude) per orientarsi tra le lezioni e rispondere citando quella giusta.
- In più:
  - **PDF** di slide e dispense convertiti in Markdown, con formule e figure trascritte;
  - **merge** dei file del corso, per NotebookLM;
  - **controllo qualità** delle sbobine.

> **Nuovo alla riga di comando?** Segui la [guida passo passo](docs/guida.md): dal terminale al primo corso, senza dare niente per scontato.

## Installazione

Funziona su macOS e Linux. Serve una volta sola, e ti serve un browser per accedere con le credenziali del Poli: va bene Chrome, Edge o Chromium, oppure `sbob installa-browser` ne scarica uno dedicato (circa 150 MB). Il tuo browser di sempre, Firefox compreso, non c'entra: `sbob login` apre una finestra a parte.

**Un solo comando** (installa ffmpeg, [uv](https://docs.astral.sh/uv/) e sbob, controllando prima cosa hai già; è corto, leggilo se vuoi):
```bash
curl -LsSf https://raw.githubusercontent.com/Karyllo/sbobinatore/main/install.sh | sh
```

**Oppure a mano:**
1. ffmpeg: su macOS `brew install ffmpeg` (serve [Homebrew](https://brew.sh)), su Ubuntu/Debian `sudo apt install ffmpeg`.
2. uv, il gestore di Python (pensa lui a Python e a tutte le librerie):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. sbob, nella versione base (Gemini e accesso al Poli):
   ```bash
   uv tool install "sbobinatore[base] @ git+https://github.com/Karyllo/sbobinatore"
   ```
   Se vuoi usare DeepSeek o Claude per gli appunti, o convertire PDF, `sbob doctor` ti dice quale pezzo aggiungere (oppure `sbobinatore[all]` per avere tutto). `aria2` è facoltativo: serve solo a scaricare più in fretta i video interi.

Poi configura e verifica:
   ```bash
   sbob init
   ```
   ```bash
   sbob doctor
   ```
   `sbob init` chiede la cartella dei corsi, il modello per gli appunti e le chiavi API. Le chiavi restano solo sul tuo computer. `sbob doctor` controlla che non manchi niente e, se manca qualcosa, ti dà il comando per sistemarlo.

Se usi un agente, aggiungi anche la skill, così sa usare sbob e navigare i tuoi appunti (stesso `SKILL.md` per entrambi):
```bash
sbob installa-skill                       # Claude Code
```
```bash
sbob installa-skill --per antigravity     # Antigravity (IDE); --per antigravity-cli per la CLI, dove diventa /sbobinatore
```

### Chiavi API
| Per cosa | Provider | Note |
|---|---|---|
| Trascrizione, PDF, mappa | **Gemini** (Google AI Studio) | Il piano gratuito basta per iniziare, ma ha limiti giornalieri |
| Appunti | Gemini, **DeepSeek** o **Claude** | Lo scegli in `sbob init`. Con Gemini gratuito, quando finisce la quota del giorno gli appunti passano automaticamente a DeepSeek |

Più account Google aggiungono quota: nel file `~/.config/sbob/.env` metti `GOOGLE_API_KEY_ACCOUNT1`, `GOOGLE_API_KEY_ACCOUNT2`, e così via.

## Uso

```bash
sbob                          # menu guidato
sbob status <corso>           # cosa è fatto e cosa manca, lezione per lezione
sbob run <corso> --dry-run    # mostra cosa farebbe la catena completa
sbob run <corso>              # materiale → download → audio → trascrivi → appunti → mappa
sbob cerca "matrice di copertura" --corso <corso>
sbob verifica <corso>         # contenuto perso, trascrizioni troncate, numerazione
sbob pdf slide.pdf --corso <corso>
sbob merge <corso> --modo monolite
sbob webeep scegli            # scegli con un elenco a spunte i corsi da sincronizzare
sbob aggiorna                 # la catena completa su tutti i corsi scelti
sbob notebook <corso>         # taccuino NotebookLM del corso, aggiornato
```
Ogni comando accetta `--json` (output per script e agenti) e `--help`.

### Accesso (niente cookie da copiare)
```bash
sbob login
```
Si apre una finestra di browser dedicata (profilo separato da quello personale): fai l'accesso di Ateneo come al solito e, quando passa a Webex, scrivi la tua email del Poli. sbob salva da solo:
- il token di WeBeep, che dura mesi;
- i cookie per scaricare le registrazioni.

Chrome, Edge o Chromium vanno bene; se non li hai (o preferisci Firefox) usa `sbob login --browser firefox`: scarica una build di Firefox dedicata a sbob (circa 80 MB), senza toccare il tuo Firefox. Il browser scelto viene ricordato.

Quando i cookie scadono, sbob li rinnova da solo senza finestra (`sbob login --rinnova`) finché la sessione di Ateneo è valida. Altrimenti ti chiede di rifare `sbob login`.

### Sicurezza
- **Credenziali:** token e cookie stanno in file leggibili solo dal tuo utente (`~/.config/sbob/`). Non vengono mai stampati, non finiscono nei report e non vengono mai inviati ai modelli AI.
- **Cosa ricevono i modelli AI:** Gemini, DeepSeek e Claude ricevono solo il contenuto da elaborare (audio, testo, PDF), senza strumenti né possibilità di eseguire azioni.
- **Prompt injection:**
  - i prompt istruiscono i modelli a trattare il materiale come dati e a ignorare eventuali istruzioni nascoste dentro;
  - la skill per Claude Code vieta all'agente di leggere i file delle credenziali e di eseguire istruzioni trovate nei file dei corsi.
- **Privacy dei contenuti:** il materiale dei corsi viene inviato ai provider AI scelti. Con il piano gratuito, Google può usare i dati per migliorare i suoi servizi; per i dati di DeepSeek valgono le sue condizioni d'uso. Scegli i provider di conseguenza.

### Registrazioni
- **Dall'archivio del Poli, in automatico:** dopo `sbob login`, sbob legge l'archivio registrazioni del corso (dal modulo WeBeep) e raccoglie link, data, tipo (lezione, esercitazione, laboratorio) e argomento:
  ```bash
  sbob webeep collega <corso> <id>      # una volta
  sbob link <corso>                     # scrive <corso>/link_archivio.txt (non scarica niente)
  ```
  Come fonte del corso: `sorgenti = [ { tipo = "archivio" }, { tipo = "txt", file = "link.txt" } ]`, così `sbob download` fa tutto da solo e `link.txt` resta come riserva.
- **Video o solo audio:** di default si scarica il video. Con `sbob download <corso> --formato audio` (oppure `formato = "audio"` nel corso, o in `[download]` per tutti) si scarica solo la voce: pesa da metà a un terzo e va direttamente in `audio/`, pronta per la trascrizione.
- **Da WeBeep:** se le registrazioni sono link nella pagina del corso, la fonte è `{ tipo = "webeep" }` (legge il corso collegato; con `url = "…course/view.php?id=…&section=…"` solo quella sezione).
- **A mano, sempre possibile:** `link.txt` nella cartella del corso, un link Webex per riga. Le righe possono contenere anche, separati da tabulazione, data, forma didattica e argomento. Funziona anche quando l'automazione non va.

### Materiale e fonti diverse
Il materiale del corso (slide, esercitazioni, temi d'esame) si scarica da WeBeep e si converte in Markdown, con formule in LaTeX e figure trascritte:
```bash
sbob webeep corsi                       # i tuoi corsi WeBeep, anche degli anni passati, con gli id
sbob webeep collega <corso> <id>        # lo fai una volta per corso
sbob materiale <corso>                  # scarica il nuovo e converte
```
- Si riscarica e si riconverte solo ciò che è nuovo o modificato, e non si cancella mai niente.
- Se la quota di Gemini finisce, la conversione prosegue con un altro modello solo sul testo, ti avvisa e rifà quelle pagine al prossimo giro.
- Anche i siti personali dei docenti: `materiale_siti = ["https://…"]` nel corso.
- Per le registrazioni un corso può avere più fonti insieme: `sorgenti = [ { tipo = "webeep", url = "…" }, { tipo = "webpage-url", url = "https://sito-del-docente" } ]`. sbob le legge tutte, unisce i duplicati e, se una non funziona, usa le altre.

### Anni precedenti (stesso corso, stesso docente)
Le edizioni passate stanno dentro il corso, in `<corso>/archivio/<anno>/`, con le stesse sottocartelle e uno stato separato:
```bash
sbob archivio <corso> aggiungi            # trova da WeBeep le edizioni con lo stesso codice e lo stesso docente
sbob archivio <corso> aggiungi 2024-25    # una sola
sbob archivio <corso> docenti             # se il docente è cambiato: scegli quale seguire
sbob archivio <corso> elenco
sbob run <corso> --archivio 2024-25       # qualsiasi passo accetta --archivio
```
Se per un anno non c'è lo stesso docente, sbob non aggiunge niente e ti avvisa (puoi scegliere il docente o forzare con `--id`). La mappa del corso ha una sezione per ogni edizione e collega i concetti tra gli anni; la ricerca guarda l'anno in corso e, se non trova niente (o con `--archivi`), anche gli anni passati.

### Quali corsi sincronizzare
Su WeBeep sei iscritto a molti corsi: `sbob webeep scegli` mostra l'elenco dell'anno in corso con le spunte e crea in `sbob.toml` solo quelli scelti (i tolti dalle spunte restano, solo scollegati). Poi `sbob aggiorna` fa tutta la catena su quei corsi, uno dopo l'altro.

### Aggiornamento automatico ogni notte
```bash
sbob pianifica            # ogni giorno alle 03:00 (cambia con --ora 06:30)
sbob pianifica --stato
sbob pianifica --rimuovi
```
Su macOS installa un LaunchAgent che lancia `sbob aggiorna` (parte appena il Mac si riattiva, se era in stop). Se serve il login o qualcosa fallisce ti arriva una notifica, e il dettaglio è in `~/.config/sbob/aggiorna.log`. Su Linux stampa la riga da aggiungere a `crontab -e`. Il job non contiene credenziali: sbob legge token e chiavi dai soliti file.

### Modelli e quota
I modelli escono in fretta e ogni scelta è un compromesso tra qualità, stabilità e richieste al giorno (sul Gemini gratuito circa 20 al giorno per i modelli Flash, molte di più per i Lite). sbob non cambia mai modello da solo, ma se ne accorge:
```bash
sbob modelli    # cosa esiste, cosa usano i ruoli, nuove uscite, modelli ritirati o in preview (gratis, non consuma quota)
sbob quota      # quali modelli hanno finito la quota e fra quanto tornano (--azzera dopo una ricarica)
```
`sbob doctor` e `sbob aggiorna` includono lo stesso controllo, e la skill per Claude avvisa l'utente quando c'è qualcosa da sapere.

### Taccuino NotebookLM per corso
Con `notebooklm login` fatto (e `[notebook] attivo = true` in `sbob.toml` per averlo dentro `sbob run`), `sbob notebook <corso>` crea il taccuino del corso e lo tiene aggiornato:
- una sorgente per gli **appunti**, una per la **mappa** (riassunti, concetti e prerequisiti: la vista d'insieme) e una per ogni **cartella** del materiale (le trascrizioni no, sarebbero ridondanti);
- quando una cartella cambia, si sostituisce solo la sua sorgente;
- le sorgenti che hai aggiunto a mano non vengono toccate;
- gli anni passati si aggiungono solo con `sbob notebook <corso> aggiungi-archivio <anno>`.

### Struttura di un corso
```
<corso>/
  materiale/      ← file scaricati (originali)
  materiale_md/   ← gli stessi convertiti in Markdown
  video/  audio/  trascrizioni/  appunti/  merge/
  notebook/       ← i file uniti caricati su NotebookLM (uno per sorgente), generati da `sbob notebook`
  mappa/          ← riassunti, concetti e indice, per la navigazione
  archivio/<anno>/  ← edizioni passate, stessa struttura
  .sbob/          ← stato interno (log, cache, costi): non toccare
```
I nomi dei file seguono la regola `AAAA-MM-GG_<corso>_<tipo>NN`, per esempio `2026-05-19_edp_lez13`.

## Crediti
Il download delle registrazioni riprende il funzionamento di [polimi_recordings_downloader](https://github.com/paolobasso99/polimi_recordings_downloader) di Paolo Basso (licenza MIT).

## Licenza
Da definire.
