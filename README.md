# sbobinatore (`sbob`)

Dalle registrazioni delle lezioni del Politecnico di Milano agli appunti, in un unico comando:

**registrazioni Webex → audio → trascrizione → appunti → mappa per lo studio**

- Gli **appunti** sono dispense in Markdown con le formule in LaTeX, pronte per Obsidian. Non sono riassunti: tutto quello che dice il docente rimane.
- La **mappa** raccoglie riassunti, concetti e prerequisiti di ogni lezione, tenuti separati dagli appunti. Serve a un assistente AI (Claude) per orientarsi tra le lezioni e rispondere citando quella giusta.
- In più:
  - **PDF** di slide e dispense convertiti in Markdown, con formule e figure trascritte;
  - **merge** dei file del corso, per NotebookLM;
  - **controllo qualità** delle sbobine.

## Installazione

Funziona su macOS e Linux. Serve una volta sola.

1. Installa i programmi di sistema (su macOS con [Homebrew](https://brew.sh)):
   ```bash
   brew install ffmpeg aria2
   ```
2. Installa [uv](https://docs.astral.sh/uv/), il gestore di Python. Pensa lui a Python e a tutte le librerie:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. Installa sbob:
   ```bash
   uv tool install "sbobinatore[all] @ git+https://github.com/Karyllo/sbobinatore"
   ```
4. Configura e verifica:
   ```bash
   sbob init
   ```
   ```bash
   sbob doctor
   ```
   `sbob init` chiede la cartella dei corsi, il modello per gli appunti e le chiavi API. Le chiavi restano solo sul tuo computer. `sbob doctor` controlla che non manchi niente e, se manca qualcosa, ti dà il comando per sistemarlo.

Se usi Claude Code, aggiungi anche la skill, così Claude sa usare sbob e navigare i tuoi appunti:
```bash
sbob installa-skill
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
sbob run <corso>              # download → audio → trascrivi → appunti → mappa
sbob cerca "matrice di copertura" --corso <corso>
sbob verifica <corso>         # contenuto perso, trascrizioni troncate, numerazione
sbob pdf slide.pdf --corso <corso>
sbob merge <corso> --modo monolite
```
Ogni comando accetta `--json` (output per script e agenti) e `--help`.

### Registrazioni
- **Cookie Webex:** il download usa il cookie `ticket` di politecnicomilano.webex.com. Per salvarlo:
  ```bash
  sbob cookie ticket <valore>
  ```
  Quando scade, sbob si ferma e te lo dice.
- **Link delle registrazioni:** vanno messi in `link.txt` nella cartella del corso, uno per riga. L'archivio registrazioni del Poli usa link di sessione che funzionano solo nel browser. Con Claude Code e l'estensione Claude in Chrome puoi chiedere "scarica le registrazioni di \<corso\>": la skill raccoglie i link dal tuo browser, insieme a data, tipo (lezione, esercitazione, laboratorio) e argomento.

### Struttura di un corso
```
<corso>/
  video/  audio/  trascrizioni/  appunti/  merge/
  mappa/          ← riassunti, concetti e indice, per la navigazione
  .sbob/          ← stato interno (cache, costi): non toccare
```
I nomi dei file seguono la regola `AAAA-MM-GG_<corso>_<tipo>NN`, per esempio `2026-05-19_edp_lez13`.

## Crediti
Il download delle registrazioni usa [polimi_recordings_downloader](https://github.com/paolobasso99/polimi_recordings_downloader) di Paolo Basso (licenza MIT), in un fork con alcune correzioni.

## Licenza
Da definire.
