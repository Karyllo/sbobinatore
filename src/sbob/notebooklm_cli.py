"""Interfaccia al CLI `notebooklm` (notebooklm-py), usata dal backend di trascrizione e dal passo `notebook`.

Argomenti sempre in lista (niente shell). I test sostituiscono `nb` con un finto CLI.
"""

from __future__ import annotations

import json
import subprocess

from sbob.core.report import NeedsHuman

_AUTH_HINTS = ("login", "auth", "sign in", "expired", "not logged")


def nb(*args: str) -> tuple[str, str, int]:
    """(stdout, stderr, returncode). Lo stdout è solo il JSON: gli avvisi della libreria vanno su stderr."""
    res = subprocess.run(["notebooklm", *args], capture_output=True, text=True)
    return res.stdout.strip(), res.stderr.strip(), res.returncode


def parse_json(stdout: str) -> dict:
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def check_auth(stderr: str, rc: int, stdout: str = "") -> None:
    text = f"{stderr} {parse_json(stdout).get('message', '')}".lower()
    if rc != 0 and any(h in text for h in _AUTH_HINTS):
        raise NeedsHuman("NotebookLM non autenticato", action="notebooklm login")


def _run(*args: str) -> dict:
    out, err, rc = nb(*args)
    check_auth(err, rc, out)
    data = parse_json(out)
    if rc != 0 or data.get("error"):
        raise RuntimeError(f"notebooklm {args[0]}: {data.get('message') or err or out or f'codice {rc}'}")
    return data


def find_or_create_notebook(title: str) -> str:
    for n in _run("list", "--json").get("notebooks", []):
        if n.get("title") == title:
            return n["id"]
    return create_notebook(title)


def create_notebook(title: str) -> str:
    nb_id = _run("create", title, "--json").get("notebook", {}).get("id")
    if not nb_id:
        raise RuntimeError("creazione taccuino fallita: nessun id")
    return nb_id


def list_sources(notebook: str) -> list[dict]:
    return _run("source", "list", "-n", notebook, "--json").get("sources", [])


def add_file(notebook: str, path: str, title: str) -> str:
    """Carica un file come sorgente e ne restituisce l'id. Il titolo che mostra NotebookLM è il nome del file."""
    sid = _run("source", "add", path, "--title", title, "--type", "file", "-n", notebook, "--json").get("source", {}).get("id")
    if not sid:
        raise RuntimeError("caricamento sorgente fallito: nessun id")
    return sid


def wait_source(notebook: str, source_id: str) -> None:
    _run("source", "wait", source_id, "-n", notebook, "--json")


def delete_source(notebook: str, source_id: str) -> None:
    _run("source", "delete", source_id, "-n", notebook, "--yes", "--json")


def set_persona(notebook: str, text: str) -> None:
    _run("configure", "-n", notebook, "--persona", text, "--json")


def ask(notebook: str, question: str) -> dict:
    """Domanda al taccuino: {"risposta", "riferimenti": [{"source_id", ...}]}. Il testo passa da stdin (`--prompt-file -`),
    non sulla riga di comando. Continua la conversazione in corso del taccuino (mai `--new`, che la cancella)."""
    res = subprocess.run(["notebooklm", "ask", "--prompt-file", "-", "-n", notebook, "--json"],
                         input=question, capture_output=True, text=True)
    check_auth(res.stderr, res.returncode, res.stdout)
    data = parse_json(res.stdout)
    if res.returncode != 0 or data.get("error"):
        raise RuntimeError(f"notebooklm ask: {data.get('message') or res.stderr.strip() or 'errore'}")
    refs = data.get("references") or data.get("citations") or []
    return {"risposta": data.get("answer") or data.get("response") or "", "riferimenti": refs}


def auth_status() -> tuple[bool, str]:
    """(ok, dettaglio) per `sbob doctor`."""
    try:
        out, err, rc = nb("auth", "check", "--json")
    except FileNotFoundError:
        return False, "CLI notebooklm non installato"
    ok = rc == 0 and parse_json(out).get("status") == "ok"
    return ok, "autenticato" if ok else "non autenticato"
