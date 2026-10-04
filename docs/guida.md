# Guida passo passo

Per chi non ha mai usato il terminale. Ogni passaggio dice **cosa scrivere**, **cosa succede** e **cosa fare se qualcosa non va**. Se ti perdi, scrivi `sbob aiuto`.

## Cos'è, in due righe

sbob prende le registrazioni delle tue lezioni (Politecnico di Milano) e ne ricava, in una cartella sul tuo computer:

- gli **appunti**, scritti bene, con le formule;
- una **mappa** del corso (cosa si spiega in ogni lezione, quali concetti, cosa serve sapere prima);
- un **taccuino NotebookLM** a cui puoi fare domande;
- il **materiale** del corso (slide, esercitazioni, esami) scaricato da WeBeep.

Poi tiene tutto aggiornato quando escono nuove lezioni.

## Cosa ti serve

- un Mac o un Linux (su Windows non l'abbiamo provato);
- un browser per accedere con le credenziali del Poli: Chrome, Edge o Chromium. Se non ce l'hai (o usi Firefox), non è un problema: `sbob installa-browser` ne scarica uno dedicato a sbob (circa 150 MB, una volta sola). Il tuo browser di tutti i giorni non c'entra e non viene toccato;
- una **chiave Gemini** gratuita, la prendi da [Google AI Studio](https://aistudio.google.com/apikey) in due minuti;
- un po' di pazienza il primo giorno (vedi "Quanto ci mette").

**Costi:** puoi farcela gratis. Il piano gratuito di Gemini ha però dei limiti giornalieri (pochi per i modelli Flash, circa 20 richieste al giorno, molti di più per i Lite), quindi un corso intero richiede qualche giorno. Se hai fretta puoi aggiungere altre chiavi o usare un provider a pagamento: sbob non ti addebita nulla da solo.

## 1. Aprire il terminale

Il terminale è una finestra dove si scrivono i comandi. Sul Mac: premi `⌘ + Spazio`, scrivi **Terminale** e premi Invio. Copia un comando alla volta dai riquadri qui sotto, incollalo e premi Invio.

## 2. Installare

Una volta sola, con **un comando** (installa ffmpeg, uv e sbob; controlla prima cosa hai già):
```bash
curl -LsSf https://raw.githubusercontent.com/Karyllo/sbobinatore/main/install.sh | sh
```
Su Mac, se non hai Homebrew (il gestore di programmi), il comando te lo chiede e ti indica [brew.sh](https://brew.sh): installalo e rilancia. Su Ubuntu/Debian chiede la password del computer per installare ffmpeg.

Se preferisci farlo a mano: ffmpeg (Mac: `brew install ffmpeg`, Ubuntu: `sudo apt install ffmpeg`), poi uv e sbob:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Chiudi e riapri il terminale, poi:
```bash
uv tool install "sbobinatore[base] @ git+https://github.com/Karyllo/sbobinatore"
```
(`aria2` non serve: velocizza solo il download dei video interi.)


Per controllare che sia andato tutto bene:
```bash
sbob aiuto
```
Se vedi la spiegazione di sbob, funziona.

> **"command not found: sbob"?** Chiudi e riapri il terminale. Se persiste, scrivi `uv tool update-shell` e riaprilo di nuovo.

## 3. Prima configurazione

```bash
sbob init
```
Ti fa qualche domanda: dove tenere i corsi sul computer, quale modello usare per gli appunti e la tua chiave Gemini. Le chiavi restano solo sul tuo computer, in un file che solo tu puoi leggere.

Poi:
```bash
sbob doctor
```
Controlla che non manchi niente. Se manca qualcosa, **ti dice il comando per sistemarlo**: copialo e rilancia `sbob doctor` finché è tutto a posto.

## 4. Accedere con il Poli

```bash
sbob login
```
Si apre una finestra di browser dedicata (separata dal tuo browser di tutti i giorni: Firefox, Chrome o altro). Se non trova Chrome, ti chiede il permesso di scaricarne uno dedicato (circa 150 MB, una volta sola). Accedi come fai di solito con le credenziali di Ateneo; quando passa a Webex ti chiede l'email del Poli. Tutto qui: sbob salva da solo i permessi che servono, senza che tu copi niente. Le tue password non le vede nessuno: le scrivi tu nella pagina del Poli.

**Se usi Firefox (o non vuoi Chrome né Chromium):** va bene, e il tuo Firefox di tutti i giorni non viene toccato. `sbob login --browser firefox` apre una finestra di **Firefox dedicato a sbob**, che scarica da solo la prima volta (circa 80 MB; se preferisci scaricarlo prima: `sbob installa-browser --browser firefox`). sbob ricorda la scelta, quindi anche i rinnovi automatici useranno quello.

Quando i permessi scadono (dopo giorni o mesi) sbob li rinnova da solo, e se non ci riesce ti dice di rifare `sbob login`.

## 5. Scegliere i corsi

```bash
sbob webeep scegli
```
Compare l'elenco dei tuoi corsi di WeBeep dell'anno in corso: muoviti con le frecce, spunta con la barra spaziatrice, conferma con Invio. Per ogni corso nuovo ti propone un nome breve (quello che userai nei comandi): va bene anche accettare il suggerimento.

Per vedere i corsi che hai scelto: `sbob corsi`.

## 6. Far partire tutto

Per un corso solo:
```bash
sbob run NOMECORSO
```
oppure per tutti quelli che hai scelto:
```bash
sbob aggiorna
```
Se preferisci essere guidato, scrivi solo `sbob` e usa il menu con le frecce.

Prima di far partire un corso intero puoi vedere cosa farebbe, senza fare niente: aggiungi `--dry-run`.

### Quanto ci mette

Dipende dai limiti del tuo piano. Come ordine di grandezza, su un corso di 30 lezioni: scaricare le registrazioni richiede una quindicina di minuti, la trascrizione con NotebookLM qualche minuto, gli appunti circa 5 minuti a lezione. Con il piano gratuito di Gemini le richieste giornaliere dei modelli Flash (circa 20 al giorno) finiscono presto, quelle dei Lite molto più tardi: **non è un errore**. sbob lo dice, passa da solo ai modelli di riserva, e quando anche quelli finiscono si ferma. Rilancia il giorno dopo e riprende da dove era arrivato, senza rifare niente.

`sbob quota` ti mostra quali modelli hanno finito la quota e fra quanto sbob li riprova (al massimo due ore: controllare non costa niente, e se la quota è tornata prima si riparte subito).

### A che punto sono?
```bash
sbob status NOMECORSO
```
Lezione per lezione, cosa è fatto e cosa manca.

## 7. Dove trovi le cose

Dentro la cartella del corso:

| Cartella | Cosa contiene |
|---|---|
| `appunti/` | gli appunti, uno per lezione (aprili con [Obsidian](https://obsidian.md) per vedere le formule) |
| `mappa/` | l'indice del corso: riassunto di ogni lezione, concetti, prerequisiti |
| `materiale/` | i file scaricati da WeBeep, come sono |
| `materiale_md/` | gli stessi, convertiti in testo leggibile (se hai attivato la conversione) |
| `trascrizioni/` | il testo grezzo di quello che dice il docente |
| `audio/` o `video/` | le registrazioni |
| `notebook/` | i file che finiscono su NotebookLM |

Non spostare né cancellare a mano `appunti/` e `trascrizioni/`: sbob se ne accorgerebbe. Per rifare una lezione usa i comandi (con `--force`).

## 8. Studiare

**Cercare un argomento** negli appunti:
```bash
sbob cerca "estremo superiore"
```
Ti dice in quale lezione e sezione se ne parla.

**Fare una domanda** al taccuino NotebookLM (serve averlo configurato, vedi sotto):
```bash
sbob notebook NOMECORSO chiedi "cosa serve sapere prima del teorema di Weierstrass?"
```
Risponde citando lezione e data, e dice da quali sorgenti ha preso l'informazione.

### Configurare NotebookLM (una volta)

```bash
uv tool install notebooklm-py
```
```bash
notebooklm login
```
Poi in `~/.config/sbob/sbob.toml` aggiungi (o scrivi `true` se c'è già):
```toml
[notebook]
attivo = true
```
Da quel momento `sbob run` e `sbob aggiorna` creano e aggiornano da soli il taccuino di ogni corso, con gli appunti, la mappa e il materiale (una sorgente per cartella).

## 9. Farlo usare a un assistente AI

sbob è pensato anche per essere guidato da un assistente (Claude Code, Antigravity…): gli insegni una volta come si usa e poi gli chiedi in italiano "spiegami X dai miei appunti" o "aggiorna Analisi".
```bash
sbob installa-skill                       # Claude Code
```
```bash
sbob installa-skill --per antigravity     # Antigravity
```
L'assistente sa cercare negli appunti, aggiornare i corsi, controllare i modelli e avvisarti quando serve un tuo intervento (per esempio un nuovo accesso). Non legge mai le tue credenziali: la skill glielo vieta.

## 10. Aggiornamento automatico ogni notte

```bash
sbob pianifica
```
Su Mac imposta un'attività che ogni notte alle 03:00 fa `sbob aggiorna` da sola e ti manda una notifica se serve qualcosa. Si toglie con `sbob pianifica --rimuovi`.

## Se qualcosa non va

| Cosa vedi | Cosa fare |
|---|---|
| `serve un tuo intervento` e `sbob login` | I permessi sono scaduti: scrivi `sbob login` |
| `Quota esaurita` | Normale sul piano gratuito. Aspetta il giorno dopo (di solito verso le 2:00) o aggiungi un'altra chiave. `sbob quota` dice quando |
| Il corso non ha il modulo "Archivio registrazioni" | Le registrazioni potrebbero essere in un post della bacheca annunci (sbob le trova) o sul sito del docente: vedi sotto |
| Una registrazione non si scarica | Scrivi `sbob doctor`; se il docente ha bloccato il download, sbob salva comunque l'audio |
| `sbob doctor` segnala una "novità sui modelli" | Scrivi `sbob modelli`. Non cambia niente da solo: ti dice solo cosa è uscito |
| Altro | `sbob doctor` poi `sbob COMANDO --help`. Il dettaglio tecnico è nel file `.sbob/logs/sbob.log` dentro la cartella del corso |

### Registrazioni in un posto strano

Le registrazioni stanno a volte in posti diversi: archivio del Poli, post in bacheca, sito del docente. sbob prova le fonti che gli indichi (`sorgenti` nel corso, in `~/.config/sbob/sbob.toml`) e le unisce. Come **riserva sempre valida** puoi mettere i link a mano, uno per riga, in un file `link.txt` nella cartella del corso.

## Privacy e sicurezza, in breve

- Le tue credenziali stanno in file leggibili solo da te e non vengono mai mostrate né inviate ai modelli.
- Ai modelli AI arriva solo il contenuto da elaborare (audio, testo, PDF), che i provider trattano secondo le loro condizioni: con il piano gratuito di Google i dati possono essere usati per migliorare i suoi servizi.
- I file con i risultati degli esami (liste di matricole e voti di altri studenti) vengono riconosciuti e **non** vengono mai convertiti né mandati al taccuino.
- Usa sbob per i **tuoi** appunti di studio: le registrazioni e il materiale dei docenti restano soggetti ai loro diritti.
