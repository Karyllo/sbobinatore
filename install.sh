#!/bin/sh
# Installa sbob con un solo comando (macOS e Linux):
#   curl -LsSf https://raw.githubusercontent.com/Karyllo/sbobinatore/main/install.sh | sh
# Fa tre cose, e prima di ognuna controlla se c'è già: ffmpeg, uv (che porta Python e le librerie), sbob.
# Leggilo prima se vuoi: è corto. Non tocca nient'altro e non chiede mai le tue chiavi.
set -eu

REPO="git+https://github.com/Karyllo/sbobinatore"
say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# 1. ffmpeg (serve per l'audio)
if have ffmpeg && have ffprobe; then
  say "✓ ffmpeg già presente"
else
  say "→ installo ffmpeg"
  case "$(uname -s)" in
    Darwin)
      if have brew; then brew install ffmpeg
      else say "Serve Homebrew (il gestore di programmi del Mac): installalo da https://brew.sh e rilancia questo comando."; exit 1; fi ;;
    Linux)
      if have apt-get; then sudo apt-get install -y ffmpeg
      elif have dnf; then sudo dnf install -y ffmpeg
      elif have pacman; then sudo pacman -S --noconfirm ffmpeg
      else say "Installa ffmpeg con il gestore di pacchetti della tua distribuzione e rilancia."; exit 1; fi ;;
    *) say "Sistema non supportato: $(uname -s)"; exit 1 ;;
  esac
fi

# 2. uv (gestisce Python e tutte le librerie: non devi installare altro a mano)
if have uv; then
  say "✓ uv già presente"
else
  say "→ installo uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; export PATH
fi

# 3. sbob (versione base: Gemini + accesso al Poli)
say "→ installo sbob"
uv tool install --force "sbobinatore[base] @ $REPO"

say ""
say "Fatto. Chiudi e riapri il terminale, poi scrivi:  sbob init"
say "(per capire cosa fa:  sbob aiuto)"
