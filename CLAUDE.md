# sbobinatore (`sbob`): guida per lo sviluppo

Pipeline lezioni: **download → audio → trascrivi → appunti**, con in più merge, pdf e materiale.
Il piano completo (8 fasi) è in `~/.claude/plans/tocca-rifattorizzare-un-codice-flickering-crystal.md`.
I vecchi script **non vanno modificati**: si portano qui.

## Comandi
```bash
uv sync --all-extras          # dipendenze (SDK LLM, pdf, html sono extras)
uv run pytest -q              # test (devono restare verdi)
SBOB_CONFIG=$PWD/sbob.example.toml uv run sbob status elettronica --json
```

## Architettura (non cambiarla senza motivo)
- `config.py` carica `sbob.toml` (cerca in `$SBOB_CONFIG`, poi `./sbob.toml`, poi `~/.config/sbob/sbob.toml`) e il `.env` accanto. Produce `Settings` e `Course`.
- `core/`
  - `layout.py`: cartelle del corso
  - `naming.py`: **unica** regola di nome, stem `YYYY-MM-DD_<slug>_<tipo>NN[_parte]`
  - `keys.py`: chiavi `PREFIX_ACCOUNT1..N`
  - `ratelimit.py`
  - `batch.py`: `list_inputs`, `plan_jobs`, `atomic_write_text`
  - `manifest.py`: solo ciò che dai file non si ricava
  - `frontmatter.py`
  - `report.py`: `StepReport`, `Exit`, `NeedsHuman`
  - `status.py`
- `steps/<passo>.py` (o un package con `run` esportato in `__init__.py`) espone `run(ctx: StepContext) -> StepReport`. Le regole sono nel docstring di `steps/base.py`:
  - idempotente
  - scrittura atomica
  - **niente stdout** (si usa `ctx.log`)
  - un errore su un file va in `report.fail` e il lavoro continua
  - un errore che richiede l'utente si segnala con `raise NeedsHuman`
  - `ctx.dry_run` va rispettato
- `llm/`
  - `errors.py`: classificazione errori degli SDK
  - `base.py`: contratto provider-agnostico
  - `registry.py`: `Registry.role(name).complete(messages, item=, validate=)`. Qui stanno retry, rotazione chiavi, rate limit e costi. **Gli adapter non ritentano mai.**
  - `cost.py`
- `cli.py`: un comando per passo che chiama `_run_step`. `--json` stampa il report su stdout.

## Adapter LLM: specifica (implementata, tenerla come riferimento)
Ogni adapter è `Classe(name, key, conf)`. Crea il client SDK **una volta** in `__init__`, con import lazy dell'SDK. Espone `complete(messages, model, params) -> LLMResult`.
- **Non solleva eccezioni**: le cattura tutte e le mappa su `ErrorKind`:
  - 429 temporaneo → `RATE_LIMIT`
  - 429 con quota giornaliera o credito esaurito ("per day", "insufficient_quota", "credit", Gemini `RESOURCE_EXHAUSTED` con quota giornaliera) → `QUOTA`
  - 401/403 → `AUTH`
  - 5xx, timeout, errore di connessione → `SERVER`
  - safety o contenuto bloccato → `BLOCKED`
  - altri 4xx → `BAD_REQUEST`
  - tutto il resto → `OTHER`
- Classifica gli errori in base al codice di stato o al tipo di eccezione dell'SDK, non cercando "429" nel testo.
- Compila `usage`: input, output, cached e thinking token, quando il provider li fornisce. Compila anche `finish_reason`.
- Se `finish_reason` è `"length"` restituisce comunque il testo, senza errore: decide il chiamante.
- Mappa `thinking=True`:
  - Gemini: `ThinkingConfig(thinking_level="HIGH", include_thoughts=False)`
  - Anthropic: `thinking={"type":"enabled","budget_tokens": min(16000, max_tokens//2)}`, senza `temperature` e `top_p`, che con thinking non sono ammessi
  - OpenAI-compatibile: nessun parametro, conta il modello scelto (es. `deepseek-reasoner`); se `conf` ha `reasoning_effort`, lo passa
- `params.system` va nel system prompt nativo: `system_instruction` per Gemini, `system=` per Anthropic, messaggio `role=system` per OpenAI.
- Parti dei messaggi:
  - `ImagePart`: inline in base64 (Gemini `Part.from_bytes`, Anthropic `image` block, OpenAI `image_url` data URI)
  - `FilePart`: Gemini → Files API (upload, attesa `ACTIVE`, uso, `files.delete` in `finally`), riusando `_wait_for_file_active` da `~/Desktop/Karyl/script/python/sbobine universitarie copy/transcriber.py:165`; Anthropic → `document` block per i PDF; per OpenAI e per l'audio non Gemini → `BAD_REQUEST` "FilePart non supportato"
- Gemini: safety settings `BLOCK_ONLY_HIGH` sulle 4 categorie (`config.py` della copy, righe 104-113).
- OpenAI SDK: `max_retries=0` (i retry li fa il registry) e `timeout=params.timeout`.
- Riferimento da cui partire: `~/Desktop/Karyl/script/python/sbobine universitarie copy/llm_client.py`.
- Test: in `tests/test_llm_adapters.py` con SDK finti (monkeypatch), soprattutto sulla mappatura degli errori.

## Passi: cosa portare e da dove

**download** (`steps/download.py`)
- Funzioni: `run(ctx)` e `set_cookie(settings, nome, valore)`.
- Chiama `settings.downloader/.venv/bin/python -m prd <tipo> ... --output <layout.staging>` tramite subprocess con argomenti in lista; mai `shell=True`. Non importare `prd`: vincola typer 0.6.
  - `tipo` viene da `course.sorgente.tipo`.
  - Per `txt`, il file è `course.sorgente.file`, relativo alla cartella del corso, oppure `ctx.options["links"]`.
  - Passa sempre `--course <slug> --academic-year <anno>`.
- prd scrive in `staging/<slug> <anno>/YYYY-MM-DD HH-MM.mp4`. Se restano file `.aria2`, è un errore.
- Assegna i nomi con `naming.next_numbers(esistenti in video/, date, slug, ctx.options["tipo"])` e sposta in `video/`.
- Salva in `manifest.videos` l'ID video, che si legge dall'xlsx di prd (colonna Link). Così una ripetizione non rinumera.
- Un ticket scaduto (stderr di prd con "Try refreshing the ticket") → `NeedsHuman(action="sbob cookie ticket <valore>")`.
- `set_cookie` lancia `prd set-cookie`.
- Logica di riferimento: `~/polimi_recordings_downloader/process_matematica.py`.

**audio** (`steps/audio.py`)
- `ffmpeg -nostdin -y -i src -vn -c:a aac -b:a <settings.audio_bitrate> tmp`, poi rename atomico in `audio/<stem>.aac`.
- Input da `video/` con le estensioni di `core.status.VIDEO_EXT`. Usa `plan_jobs`.
- Riferimento: `~/Desktop/Karyl/SBobinatRe/1.video_audio /converti_audio.py` (attenzione allo spazio finale nel nome della cartella).

**trascrivi** (`steps/transcribe/`)
- Backend scelto da `ctx.options["backend"]` o da `course.trascrizione`.
- Output: `trascrizioni/<stem>.md` con frontmatter `frontmatter.lesson_meta(course, stem, backend=..., modello=..., sorgente="audio/<file>")`.
- `gemini.py` (default): `Registry.role("trascrizione").complete([Message.user(FilePart(audio), prompt)])`, con il prompt in `prompts/<lingua>/transcriber.md`.
  - Converte in `.m4a` i formati non nativi (`AUDIO_MIME_TYPES` e `GEMINI_NATIVE_FORMATS` della copy, `config.py`).
  - Fonte: `.../sbobine universitarie copy/transcriber.py:98-162`.
  - Se `finish_reason == "length"` (audio troppo lungo), divide l'audio in segmenti da 45 minuti con ffmpeg `-ss/-t`, trascrive ciascuno e unisce i risultati.
- `notebooklm.py`: porta `~/polimi_recordings_downloader/sbobina_notebooklm.py`. Correzioni:
  - subprocess con argomenti in lista
  - nessun conteggio fisso `== 22`: finisce quando la coda è vuota
  - massimo 3 tentativi per file
  - timeout di 12 minuti
  - massimo 10 sorgenti in volo
  - un `notebooklm` non autenticato → `NeedsHuman(action="notebooklm login")`
- `whisper_mlx.py`: porta `~/Desktop/Karyl/SBobinatRe/2.audio_testo/trascrizione.py` (funzioni `trascrivi`, `_formatta_markdown`) senza argparse né globali. Correzioni:
  - `SOGLIA` definita prima dei rami (oggi NameError alle righe 204/227)
  - nuova funzione `collapse_repetitions(text)` che riduce a una sola occorrenza le n-gram ripetute più di 4 volte di fila (esempio reale: `testo/2026-03-03_quanti_ese01.md`)
- `import_html.py`: porta `2.audio_testo/html2md.py` (markdownify). Toglie `.aac` dallo stem e usa `naming.parse` per normalizzare i nomi legacy.

**appunti** (`steps/notes/`)
- Porta da `~/Desktop/Karyl/script/python/sbobine universitarie copy/`:
  - `chunker.py`: identico; `CHUNK_SIZE_WORDS` da config, default 850
  - `workers.py` e `pipeline.py`
- Prompt in `prompts/<lingua>/{refiner,notes}.md`, testo identico a `_PROMPTS` in `config.py` della copy, con gli stessi placeholder `str.format`.
- Ruoli `refiner` e `notes` dal Registry, con override da `ctx.options["refiner"|"notes"]` tramite `parse_model_override`.
- Il controllo del "muro di testo" diventa `validate=`.
- Se il refiner fallisce si usa il chunk originale. Se le note di un chunk falliscono si inserisce il banner `⚠️ ERRORE` e il file va in `report.fail`, ma l'output viene comunque scritto con suffisso `.parziale.md`, **non** `_appunti.md`, così il run successivo lo rifà.
- Worker: `min(role.n_keys * 4, 20)` per DeepSeek, `min(role.n_keys, 5)` per Gemini. Il valore si configura con `[modelli.<ruolo>].workers`.
- Frontmatter con `argomenti`: si aggiunge al prompt notes la richiesta di chiudere ogni parte con la riga `ARGOMENTI: a; b; c`. Si estrae con una regex, si toglie dal corpo e si unisce nel frontmatter.
- Output in `appunti/<stem>_appunti.md`. In `report.cost` va `registry.tracker.summary()`, con `CostTracker(log_path=layout.costs)`.
- Test: il chunker dà lo stesso output dell'originale; la pipeline funziona con un FakeProvider (vedi `tests/test_registry.py`).

**merge** (`steps/merge.py`)
- Porta `~/Desktop/Karyl/SBobinatRe/4.merge_file/{merge.py, merge2.py, mergtde.py}` come `modo = split | monolite | tde`.
- Un solo `downgrade_headers`, che non tocca le righe dentro i blocchi ```` ``` ````.
- `TYPE_MAP` viene da `naming.TIPI`. `inizio_corso` viene dalla config del corso.
- Output in `merge/`.

**pdf** (`tools/pdf2md.py`, comando `sbob pdf <file|cartella> [--corso]`)
- Porta `~/Desktop/Karyl/SBobinatRe/pdf_markdown/pdf2md.py` sul ruolo `pdf` (thread, non multiprocessing: il rate limit è nel registry).
- Bug da correggere:
  1. una pagina fallita non va salvata come checkpoint (oggi riga 245)
  2. i checkpoint non vanno cancellati se mancano pagine (riga 279)
  3. `--force` deve funzionare anche quando l'output esiste (riga 295)

## Livello agente (fase 7)
- `sbob indice`: genera `<root>/INDICE.md` e `.sbob/catalog.json` dai frontmatter.
- `sbob cerca "<q>" [--corso] --json`: usa ripgrep se `shutil.which("rg")`, altrimenti Python.
- `skills/sbobinatore/SKILL.md`: i contenuti sono indicati nel piano.

## Stato attuale
Fasi 1–6 del piano completate, 71 test verdi (`uv run pytest -q`). Niente è stato provato con API reali: i test usano SDK e `prd` finti.
- **Core, CLI, contratto `--json`/exit code**: fatti.
- **Adapter LLM** (`llm/gemini.py`, `openai_compat.py`, `anthropic.py`) con `llm/errors.py` per la classificazione degli errori: fatti.
- **Passi**:
  - `download` (due fasi: piano, poi scarico solo degli ID nuovi), `audio`
  - `trascrivi` con i backend gemini (default), notebooklm, whisper, html
  - `appunti` (output parziale `.parziale.md` se un blocco fallisce)
  - `merge` (split, monolite, tde)
  - `pdf` (comando `sbob pdf`)
- **Menu guidato**: `sbob` senza argomenti.
- Deviazioni dal piano:
  - i prompt stanno in `src/sbob/prompts/` (pacchetto), non in `prompts/`
  - gli SDK e gli extra usati dai test stanno nel gruppo `dev` (`uv sync` basta)
  - il download riconosce le registrazioni dal nome prd `YYYY-MM-DD HH-MM`, non dall'ID, e legge gli ID dall'xlsx del piano
  - `pdf2md` scarta le immagini sotto 64 px (puntini, loghi) e deduplica per pagina

## Prova reale (2026-10-02, corso `prova` in `_prova/`, 5 minuti di elettronica)
Configurazione: `sbob.toml` e `.env` (link al `.env` della versione DeepSeek), entrambi esclusi da git.
- audio ✓
- trascrizione Gemini ✓ (19 secondi)
- appunti DeepSeek v4-flash/pro ✓ (52 secondi)
- merge ✓
- pdf Gemini nativo ✓ (4 pagine in circa 2 minuti)

Bug trovati e corretti:
- **DeepSeek V4 ragiona per default.** Il refiner esauriva gli 8192 token nel ragionamento e la risposta troncata veniva accettata, quindi si perdeva quasi tutta la lezione. Correzioni:
  - `thinking_param="deepseek"` nel provider invia `extra_body.thinking` enabled/disabled
  - il registry rifiuta `finish_reason="length"`, salvo `allow_truncated=True` (lo usa solo la trascrizione)
- `prd` non è installato nel suo venv: va lanciato con `PYTHONPATH=<clone>`.
- Ticket scaduto: prd dà un `KeyError 'downloadRecordingInfo'`, ora mappato su exit 3.
- Download reale ✓ (lezione da 85 minuti, 79 MB, nominata lez02, registrata nel manifest; il secondo run non riscarica). Bug corretto: prd crea `--output` solo quando genera l'xlsx, quindi la cartella va creata prima.
- PyMuPDF stampava su stdout e rompeva `--json`. Ora si usa `import pymupdf`, e in modalità `--json` stdout viene dirottato su stderr durante il passo.
- Il dry-run di `run` ora propaga le lezioni da un passo al successivo.
- Gemini: AFC disattivato (avviso inutile su stderr).

Costi: i prezzi non sono configurati, quindi `usd` vale 0, ma i token vengono contati. Vanno aggiunti `prezzi = {...}` per ruolo in `sbob.toml`.

PDF riscritto su richiesta dell'utente (sempre con modelli Gemini, DeepSeek o Qwen): il modello vede le pagine vere invece del testo estratto.
- Gemini e Anthropic ricevono sotto-PDF nativi.
- I provider OpenAI-compatibili (Qwen-VL) ricevono le pagine come PNG a 150 dpi.
- DeepSeek non ha visione.

## Fase 7: livello agente (fatta, provata sul corso `prova`)
- `sbob mappa <corso>` (ruolo `mappa`, default Gemini flash-lite): per ogni lezione con appunti produce una scheda in `<corso>/mappa/schede.json`:
  - riassunto
  - concetti, con ruolo introdotto o ripreso
  - prerequisiti

  Le lezioni vengono elaborate in ordine di data e al modello si passano i concetti già noti, così i nomi restano uniformi. Una scheda si rifà solo se cambia l'hash degli appunti. **Gli appunti non vengono toccati** (richiesta dell'utente: niente riassunti o argomenti negli appunti). Gli argomenti non vengono più chiesti nel prompt notes.
- `sbob indice` (`core/index.py`, senza LLM) genera `<corso>/mappa/INDICE.md`, `<corso>/mappa/concetti/*.md`, `<root>/mappa/INDICE.md` e `catalog.json`. I link sono Markdown relativi `[x](<path>)`.
- `sbob cerca` (`core/search.py`): la ricerca lavora per paragrafo, richiede che compaiano tutti i termini e ignora maiuscole e accenti. Per ogni risultato restituisce sezione e `[mm:ss]`.
- `sbob verifica` (`core/verify.py`) controlla:
  - rapporto appunti/trascrizione
  - parole al minuto rispetto alla durata dell'audio (per i `.aac` grezzi la durata si calcola contando i pacchetti, perché la stima di ffprobe sbaglia di 3×)
  - loop di ripetizione
  - file parziali e banner di errore
  - buchi nella numerazione
  - schede della mappa mancanti o vecchie
- Timestamp: Gemini scrive `[mm:ss]` a ogni paragrafo (si disattiva con `[trascrizione] timestamp=false`). Quando l'audio è diviso in segmenti, i tempi vengono spostati con `shift_timestamps`. Prima degli appunti vengono tolti con `strip_timestamps`.
- `mappa` è l'ultimo passo di `PIPELINE` (`run`).
- Skill: `skills/sbobinatore/SKILL.md`. **Non è ancora installata**: per installarla serve il consenso dell'utente (symlink in `~/.claude/skills/` più `sbob` nel PATH).

Altro emerso nella prova sulla lezione intera (85 minuti di Ricerca Operativa):
- Il piano gratuito di Gemini `gemini-3.5-flash` concede 20 richieste al giorno per modello, circa una lezione. Soluzione: `riserva` nel ruolo, cioè quando tutte le chiavi finiscono la quota giornaliera si passa a DeepSeek; il frontmatter riporta `modello: X (+ riserva Y)`.
- Classificazione dei 429 di Google: decide il `quotaId` (PerDay o PerMinute). La parola "billing" compare sempre, quindi non conta.
- Cache dei blocchi in `.sbob/cache/<stem>/`: se si interrompe si riprende, la chiave è modello più input. Si svuota con `--force` o quando la lezione riesce.
- Gemini: l'output si legge da `candidates_token_count`.
- `costs.jsonl` salva anche il messaggio d'errore troncato.
- Whisper: **non mantenuto** (decisione dell'utente). Resta per chi forka, non va sviluppato.

## Downloader (clone ~/polimi_recordings_downloader, branch `local-fixes`)
Approvato dall'utente. Commit sopra `main` (upstream):
1. Le 3 correzioni locali che c'erano già.
2. Archivio recman spostato su `onlineservices.polimi.it`:
   - dominio ricavato dal link
   - `urljoin` per i link assoluti
   - cookie `JSESSIONID` + `INGRESSCOOKIE` (il vecchio `SSL_JSESSIONID` vale per www11.ceda)
   - paginazione seguendo il link "tutte" (`action=plen_0`)
   - subject = `[Forma didattica] argomento`

   Provato offline sulla pagina salvata dall'utente. **Online NON funziona fuori dal browser:** con i cookie copiati, recman risponde 500 `POLIJ_049001`, a causa dei codici monouso `__pj1` e della sessione legata al browser.
   Soluzione adottata: raccolta via Claude in Chrome (procedura in SKILL.md), che produce un `link.txt` arricchito: `link<TAB>data archivio<TAB>forma<TAB>argomento`.
   `download.read_links_file` passa a prd solo i link e collega i metadati tramite l'ID video. La data dell'archivio prevale su quella di Webex: su EDP 2 registrazioni su 26 avevano la data Webex sbagliata.
   Provato su EDP: dry-run con 26 registrazioni, `lez01..18` e `lab01..08`.

Lato sbob:
- `read_plan` legge la forma didattica (Lezione→lez, Esercitazione→ese, Laboratorio→lab, Altro→sem) e numera per tipo.
- L'argomento va in `manifest.meta[stem].argomento`, poi nel frontmatter di trascrizioni e appunti, poi nel titolo della mappa.
- `--tipo` forza il tipo.

## Installazione (fatta il 2026-10-02)
- Skill: symlink `~/.claude/skills/sbobinatore` → `skills/sbobinatore`. Le modifiche al file valgono subito.
- CLI: `uv tool install -e ".[gemini,openai,anthropic,pdf,html]"`, eseguibile in `~/.local/bin/sbob` (editable, segue il repo). Se si aggiungono dipendenze, rilanciare lo stesso comando.
- **Manca la config globale** `~/.config/sbob/sbob.toml`: da fuori dal repo `sbob corsi` non vede corsi. Va scritta con l'utente (i path dei corsi veri), senza migrare dati.

## Installazione "plug and play" e pubblicazione
- Downloader: `downloader` in sbob.toml può essere un clone con `.venv`, oppure una cartella o un indirizzo git. In questi ultimi casi `uv run --no-project --with <spec> python -m prd`.
  Il default `DEFAULT_DOWNLOADER` è il fork `git+https://github.com/Karyllo/polimi_recordings_downloader@local-fixes`, pubblicato il 2026-10-02 (fork pubblico di paolobasso99, branch `local-fixes`).
  Il fork (branch `local-fixes` nel clone) contiene anche:
  - `packages = [{include="prd"}]` più lo script `prd`, perché la build upstream fallisce;
  - `click<8.2`, perché typer 0.6 si rompe con click recente;
  - le regex come stringhe raw.

  Provato con `git+file://…@local-fixes`: installa, avvia e arriva fino all'API Webex.
- `sbob doctor` (`core/doctor.py`), `sbob init` e `sbob aggiungi-corso` (`setup.py`, scrivono `~/.config/sbob/{sbob.toml,.env}`, con `.env` in modalità 600), `sbob installa-skill` (la skill entra nel wheel con force-include).
- I default personali sono stati tolti: root `~/sbob`, downloader il fork.
- README con installazione in 4 passi.
- **Pubblicato il 2026-10-02:** `github.com/Karyllo/sbobinatore` (pubblico, **senza licenza** per scelta dell'utente: da decidere più avanti; finché manca non accettare contributi esterni) e il fork `github.com/Karyllo/polimi_recordings_downloader`.
  - Commit con email noreply `199101937+Karyllo@users.noreply.github.com` (impostata in git config locale dei due repo).
  - Prova: `uvx --from "sbobinatore[all] @ git+https://github.com/Karyllo/sbobinatore" sbob doctor` da ambiente pulito installa tutto, downloader incluso.

## Fase 8, punto 1 (fatto): `sbob login` e sicurezza delle credenziali
- `auth/browser.py` usa Playwright con il Chrome di sistema (`channel="chrome"`) e il profilo `~/.config/sbob/browser`.
  1. Si apre `webeep…/auth/shibboleth/index.php` e si aspetta l'arrivo su `/my/`.
  2. Token: `launch.php?service=moodle_mobile_app`, poi `Location: moodlemobile://token=…`.
  3. Si legge il cookie `MoodleSession`.
  4. Webex: dashboard, "Sign in", campo email di Cisco (idbroker-eu), SSO del Poli, cookie `ticket` su politecnicomilano.webex.com.
- I cookie di sessione dell'Ateneo non sopravvivono alla chiusura del browser. Vengono salvati in `browser_state.json` e reiniettati, così `--rinnova` (headless, ~10 s) funziona finché la sessione è valida lato server. Il campo email di Cisco viene ricordato; `[login] email` in sbob.toml è un'alternativa.
- Il passo `download`, sul ticket scaduto, prova `try_renew_login`, poi un solo nuovo tentativo, poi exit 3 con `sbob login`.
- **Provato dal vero:** login interattivo, poi ticket valido (dry-run EDP con 26 registrazioni), token valido (`site_info`), `--rinnova` OK.
- **Sicurezza** (richiesta esplicita dell'utente):
  - `core/secrets.py` è l'unico punto che scrive le credenziali: cartelle 700, file 600, cookie di prd scritti direttamente nel file (non più `prd set-cookie` con il valore come argomento);
  - i report contengono solo i nomi;
  - `sbob doctor` stringe i permessi;
  - nei prompt c'è un preambolo anti-injection, e nella skill la sezione "Sicurezza" (divieto di leggere token/cookie/.env, contenuti come dati e mai come istruzioni);
  - invariante: le chiamate LLM non hanno strumenti e non contengono mai segreti.

## Fase 8, punti 2-4 (fatti): materiale WeBeep
- `webeep/client.py`: API Moodle (token da `sbob login`) con le chiamate di webeep-sync. `courses()` include **anche gli anni passati e i corsi del 2026-27**. Nomi e percorsi dal server sono sanificati (`safe_name`, `safe_path`).
- `steps/materiale.py`:
  - **sync:** `<corso>/materiale/<sezione>/…`, incrementale (`manifest.materiale`: modified e size); non cancella mai.
  - **siti dei docenti:** `materiale_siti = [url]`, solo link dello stesso sito, ETag/If-Modified-Since, tetto 200 MB, host privati rifiutati.
  - **conversione** in `<corso>/materiale_md/`: PDF con la visione; pptx/docx via LibreOffice (o markitdown); testo, codice e notebook copiati; `tipo` (tde, laboratorio, esercitazione, slide) dedotto dal percorso; modifiche rilevate con sha256 (`manifest.materiale_conv`).
- **Quota:** il ruolo `pdf` ha la riserva `gemini-3-flash-preview`; se finisce anche quella entra `pdf_testo` (DeepSeek, solo testo: pagine con figure marcate `> [!figura] non trascritta`). I checkpoint `*.testo.md` restano e al run dopo le pagine vengono rifatte con la visione (`upgrade`). Il report ha `warnings` ed exit 2. `[materiale] se_finisce_quota = "ferma"` disattiva il fallback.
- **Fonti multiple** per le registrazioni (richiesta dell'utente: stanno in posti diversi per ogni corso): `sorgenti = [...]` o `sorgente = {...}`. `download.plan_source` per fonte, unione per ID video, una fonte che fallisce non blocca le altre (warning).
- `sbob webeep corsi|collega`, `sbob materiale`, sezione "Materiale" nella mappa, riga nello status, `--da materiale` e `cerca --in materiale` su `materiale_md/` (ricorsivi).
- **Provato dal vero** su FRO 2025-26 (id 19827):
  - 104 file (26 MB) scaricati in 12 s, secondo giro "0 da scaricare";
  - PDF di 62 pagine → 6.600 parole in 3 min 48 s, formule LaTeX e 17 figure; ha usato la riserva perché il modello principale era senza quota;
  - il notebook è stato copiato, e la ricerca e la mappa vedono il materiale.
  - Bug trovato e corretto: `tipo` non riconosceva `laboratori`/`Lab03`.
- **Non ancora provato:** conversione `solo testo` con quota esaurita su dati veri (provata solo nei test), `.pptx` con LibreOffice, un sito personale reale.

## Fase 8, punto 5 (fatto): archivio recman automatico
- Scoperta: ogni corso WeBeep ha il modulo URL "Archivio registrazioni" → `aunicalogin…/getservizio.xml?id_servizio=2294&c_classe_webeep=<classe>`. È stabile e ce l'hanno anche i corsi degli anni passati. Dal browser di `sbob login` (anche headless) passa dall'accesso di Ateneo e apre recman con una sessione valida.
- `auth/recman.py`:
  - `archive_entries` prende i moduli via API (`is_archive_url`; attenzione: nella pagina c'è anche `id_servizio=2292` = assistenza);
  - `collect` carica la vista "tutte", legge le righe e risolve ogni "Riproduci" con `ctx.request.get(max_redirects=0)` (header Location), con ripiego sulla navigazione vera;
  - `write_links` usa lo stesso formato arricchito di link.txt.
- Fonte `{tipo = "archivio"}` (con `webeep_id` o `url`) in `download.archive_links`: scrive `<corso>/link_archivio.txt` e poi procede come una fonte txt. `sbob link <corso> [--url]` raccoglie e basta.
- **Provato dal vero:**
  - Fisica Sper. II: 10/10 in 23 s;
  - EDP: 26/26 identiche (ID, date, forme) alla raccolta manuale con Claude in Chrome, in 51 s;
  - `sbob download edp --dry-run` con `sorgenti = [archivio, txt]`: 26 pianificate, nessun duplicato.
- Il metodo `link.txt` resta la base e la riserva (richiesta esplicita dell'utente). La procedura con Claude in Chrome resta in SKILL.md come ultima riserva.

## Archivio anni precedenti: decisioni finali dell'utente (2026-10-02), DA IMPLEMENTARE (nessun codice scritto)
- Layout: `<corso>/archivio/<anno>/` con le stesse sottocartelle e un suo `.sbob/`. Config: `archivio = { "2024-25" = <id WeBeep> }` nel corso, più `archivio_docente` opzionale.
- Comando: **`sbob archivio <corso> <azione> [anno]`** (un solo comando con azioni `aggiungi`, `docenti`, `elenco`). `--archivio <anno>` su tutti i passi, `run` compreso; con `--archivio scegli` elenco interattivo degli anni configurati. Sia l'aggiunta con comando sia la scelta da elenco, con `aggiungi` come predefinito.
- `aggiungi`: cerca tra i corsi WeBeep (`WebeepClient.courses()`, ha anche gli anni passati) quelli con lo **stesso codice** (primi 6 cifre del fullname) e **stesso docente**. Docente = ultima parentesi non numerica del fullname (attenzione a "(1)", "(2)" e a "[Semestre 2] (DOCENTE"), confrontato per insieme di parole senza accenti/maiuscole. Senza anno: checkbox interattivo, o tutti se non c'è terminale.
- Se non c'è lo stesso docente: **avvisa** (exit 3, elenco dei docenti disponibili) e non aggiunge. `sbob archivio <corso> docenti` fa scegliere il docente (elenco per codice e anni) e lo salva in `archivio_docente`; `aggiungi --docente` vale una volta sola.
- **Ricerca:** di default solo l'anno in corso; se non trova niente cerca anche negli archivi (e lo dice); `--archivi` li include sempre.
- Frontmatter `edizione: <anno>` ovunque (`core/frontmatter.lesson_meta`, `steps/materiale`, `steps/pdf`). L'edizione passata si ottiene derivando un `Course` (cartella = `<corso>/archivio/<anno>`, `anno_accademico` = anno, `webeep_id` = id dell'archivio, fonti di default `[archivio, txt]`) e riusando i passi com'erano.
- Indice/mappa del corso principale: sezione per edizione e concetti incrociati tra anni (`core/index.render_course`, `render_all`, catalog.json); `status` e menu ("lavora sull'archivio dell'anno precedente").
- File da toccare: `config.py` (Course.archivio, archivio_docente), nuovo `core/archivio.py`, `cli.py`, `core/index.py`, `core/search.py`, `core/status.py`, `menu.py`, `setup.py` (`q` per le chiavi del dizionario TOML), test, README, skill.

## Fase 9 (richiesta dell'utente 2026-10-02, DA PIANIFICARE e implementare dopo l'archivio)
1. **Scegliere quali corsi sincronizzare.** L'utente è iscritto a moltissimi corsi su WeBeep e non vuole sincronizzare tutto. Oggi sbob lavora solo sui corsi che stanno in sbob.toml con `webeep_id` (quindi nulla si sincronizza da solo), ma manca il modo comodo di sceglierli: serve un comando interattivo (`sbob webeep scegli`: elenco con spunte, filtro per anno, crea le voci `[corsi.<slug>]` con `webeep_id` e cartella) e un `sbob aggiorna` che lancia la catena solo sui corsi scelti. Opzione per "sincronizza tutto" solo se esplicita.
2. **NotebookLM automatico per corso** (API non ufficiale, `notebooklm-py`, già usata per la trascrizione):
   - un taccuino per corso, creato in automatico;
   - **un file `.md` per cartella**, con il nome della cartella, che contiene tutti i `.md` già convertiti al suo interno (slide, PDF, esercitazioni, appunti e così via). È la stessa logica di `steps/merge.py` (limite 500k parole per sorgente), applicata cartella per cartella;
   - **aggiornamento incrementale**: se il contenuto di una cartella cambia (hash), si sostituisce solo quella sorgente nel taccuino; lo stato (id sorgente e hash) sta nel manifest;
   - obiettivo dell'utente: il taccuino sempre aggiornato con lezioni, materiale e tutte le informazioni, per chiudere l'automazione controllata da un agente.
   - Da verificare: limiti di sorgenti per taccuino, rate limit e stabilità dell'API non ufficiale, login (`notebooklm login`, `NeedsHuman`).

## Da fare
1. Config globale con i corsi veri. Prova online dell'archivio recman con i cookie `JSESSIONID`/`INGRESSCOOKIE`.
2. **Fase 8, spazio corso**: passo `materiale` con webeep-sync (`steps/materiale.py` è nella mappa di `get_step`, ma non esiste).
3. Dopo la fase 8 (scelte dell'utente): ricerca semantica, quiz e Anki, tracker di studio, server MCP.
4. Rimandato: rilevamento automatico delle registrazioni nuove su WeBeep.
5. Prezzi per ruolo in `sbob.toml` (oggi `usd` = 0).
6. **Archivio degli anni precedenti (fase 7b: dopo la fase 7, prima della migrazione dati). DA NON IMPLEMENTARE ORA.**
   - Obiettivo: per una materia di quest'anno, recuperare i contenuti WeBeep di un anno precedente (stesso professore) senza creare un nuovo corso in config. L'archivio è una sotto-cartella della materia.
   - Layout: `<corso>/archivio/<anno>/` con le stesse sottocartelle del corso (`video`, `audio`, `trascrizioni`, `appunti`, `materiale`) e un suo `.sbob/` (manifest separato).
   - Config: `archivio = { "2024-25" = "<url webeep>" }` dentro `[corsi.<slug>]`. Il comando `sbob archivio <corso> aggiungi <anno> <url>` lo scrive in sbob.toml.
   - Tutti i passi accettano `--archivio <anno>`, anche `run`. Si riusa lo stesso `Layout` puntato sulla cartella dell'archivio, senza duplicare logica: per esempio `Layout(base=<corso>/archivio/<anno>)` e un `Course` derivato con lo stesso slug e `anno_accademico=<anno>`.
   - `sbob status <corso>` mostra anche l'archivio. Il menu guidato offre "lavora sull'archivio dell'anno precedente".
   - Frontmatter con `edizione: <anno>`, così indice, cerca e skill distinguono e incrociano le lezioni dei due anni (es. "questo è spiegato meglio nella lezione dell'anno scorso"). L'indice va esteso alle sotto-cartelle dell'archivio.
   - Registrazioni: con la sorgente `webeep` già esistente (cookie `MoodleSession` + `ticket`). Il materiale (slide, PDF) arriva con il passo `materiale` della fase 8.
   - Da verificare quando ci arriviamo: se webeep-sync vede anche i corsi degli anni passati. Altrimenti si valuta il download diretto dei file da WeBeep con il cookie Moodle.
7. **Migrazione dati: ULTIMA, solo quando l'utente la chiede.**
