"""
Differenze tra Windows e Linux in un solo posto: script di avvio del server (run.bat / run.sh),
file degli argomenti di Forge/NeoForge (win_args.txt / unix_args.txt) e comando java letto dallo script.
Su Windows tutto resta come prima.
Qui ci sono anche i salvataggi sicuri dei file (write_text_atomic / write_json_atomic): mai un file scritto a metà.
"""

import json
import os
import shlex
import shutil
import stat
import threading
import time
from typing import Any, Callable, List, Optional

IS_WINDOWS = os.name == "nt"

_write_lock = threading.Lock()


def write_text_atomic(path: str, text: str, newline: Optional[str] = None) -> None:
    """Scrive il file in modo che non resti mai a metà: prima un .tmp accanto, poi lo scambio (os.replace).
    Se il PC si spegne durante il salvataggio resta il file vecchio intero."""
    tmp = path + ".tmp"
    with _write_lock:
        with open(tmp, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Su Windows lo scambio fallisce per un attimo se un antivirus o un editor ha il file aperto
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    break
                time.sleep(0.2)
        # File tenuto aperto a lungo da un altro programma (Paper acceso tiene aperto server.properties): lo
        # scambio non riesce mai ma la scrittura sì. Prima il salvataggio falliva e restava il .tmp.
        try:
            shutil.copyfile(tmp, path)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


def write_json_atomic(path: str, data: Any, indent: int = 2, keep_backup: bool = False) -> None:
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    # La copia .bak si aggiorna solo da un file leggibile: un file rovinato non deve sostituire la copia buona
    if keep_backup and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
            shutil.copy2(path, path + ".bak")
        except (OSError, ValueError, UnicodeDecodeError):
            pass
    write_text_atomic(path, text)


def download_file(url: str, dest: str, user_agent: str, timeout: int = 40, hashes: Optional[dict] = None,
                  progress: Optional[Callable[[int, int], None]] = None, require_zip: Optional[bool] = None) -> int:
    """Scarica url in dest senza lasciare mai un file a metà: prima <dest>.part, poi i controlli e lo scambio.
    hashes = {"sha1": ..., "sha256": ..., "sha512": ...} (quelli noti; vuoti/None ignorati): se l'impronta non
    torna il file viene scartato. Un .jar deve essere un archivio valido (un download interrotto o una pagina
    d'errore al posto del file facevano poi fallire l'avvio del server). Restituisce i byte scaricati."""
    import hashlib
    import urllib.request
    import zipfile
    checks = {k: hashlib.new(k) for k, v in (hashes or {}).items() if v}
    part = dest + ".part"
    done = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                for h in checks.values():
                    h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
            out.flush()
            os.fsync(out.fileno())
        if total and done != total:
            raise IOError(f"download incompleto ({done} di {total} byte)")
        if done == 0:
            raise IOError("file vuoto")
        for algo, h in checks.items():
            if h.hexdigest().lower() != str(hashes[algo]).strip().lower():
                raise IOError(f"verifica {algo.upper()} del file non riuscita (file danneggiato o diverso)")
        if require_zip is None:
            require_zip = dest.lower().endswith((".jar", ".jar.disabled"))
        if require_zip and not zipfile.is_zipfile(part):
            raise IOError("il file scaricato non è un .jar valido")
        os.replace(part, dest)
        return done
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def read_json_with_backup(path: str) -> Any:
    """Legge un file JSON; se è rovinato (es. PC spento a metà salvataggio) ne conserva una copia in
    <file>.corrotto e prova <file>.bak. Solleva l'errore originale se non si legge nessuno dei due."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not os.path.exists(path + ".bak"):
            # Prima copia di riserva subito, non al primo salvataggio (che può arrivare dopo settimane)
            try:
                shutil.copy2(path, path + ".bak")
            except OSError:
                pass
        return data
    except (ValueError, UnicodeDecodeError) as e:
        try:
            shutil.copy2(path, path + ".corrotto")
        except OSError:
            pass
        bak = path + ".bak"
        if os.path.isfile(bak):
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[Avviso] {os.path.basename(path)} rovinato ({e}): ripristinato dalla copia {os.path.basename(bak)}")
                return data
            except (OSError, ValueError, UnicodeDecodeError):
                pass
        raise

# Script di avvio creato dal pannello e cercato nella cartella del server
LAUNCH_SCRIPT = "run.bat" if IS_WINDOWS else "run.sh"
LAUNCH_SCRIPT_CANDIDATES = ("run.bat", "start.bat") if IS_WINDOWS else ("run.sh", "start.sh")
# Argomenti java di Forge/NeoForge moderni: l'installer li scrive in entrambi i formati
FORGE_ARGS_FILE = "win_args.txt" if IS_WINDOWS else "unix_args.txt"


def write_launch_script(target_dir: str, label: str, launch_cmd: str) -> str:
    """Scrive run.bat (Windows) o run.sh eseguibile (Linux) con la riga java; restituisce il percorso."""
    path = os.path.join(target_dir, LAUNCH_SCRIPT)
    if IS_WINDOWS:
        write_text_atomic(path, f"@echo off\nREM Voxarium {label} Launcher\n{launch_cmd} %*\npause\n")
    else:
        write_text_atomic(path, f'#!/bin/sh\n# Voxarium {label} Launcher\ncd "$(dirname "$0")"\n{launch_cmd} "$@"\n',
                          newline="\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


JAVA_NAMES = ("java", "java.exe", "javaw", "javaw.exe")


def java_token_end(line: str) -> int:
    """Fine del primo elemento della riga: il programma java, anche tra virgolette con spazi ("C:/Program Files/...")."""
    s = line
    if s.startswith('"'):
        end = s.find('"', 1)
        return len(s) if end < 0 else end + 1
    end = s.find(" ")
    return len(s) if end < 0 else end


def is_java_line(line: str) -> bool:
    """Riga che avvia Java: "java ...", "@java ...", "java.exe ...", "C:/.../bin/java.exe" ... o "%JAVA_HOME%/bin/java" ...
    Prima valeva solo "java ...": con il percorso completo il server partiva tramite cmd e il "pause" in fondo a
    run.bat teneva acceso il processo (arresto lungo 3 minuti e crash mai riconosciuti)."""
    s = line.strip()
    if s.startswith("@"):
        s = s[1:].lstrip()
    if not s or s.lower().startswith(("rem ", "::", "#")):
        return False
    tok = s[:java_token_end(s)].strip('"').replace("\\", "/")
    return tok.rsplit("/", 1)[-1].lower() in JAVA_NAMES


def split_java_line(line: str, server_dir: str = "") -> List[str]:
    """Riga "java ..." dello script di avvio → argomenti per Popen, con "nogui" in fondo.
    Su Linux un win_args.txt (server copiato da Windows) diventa unix_args.txt se c'è: il primo usa ';' come separatore."""
    s = line.strip()
    if s.startswith("@"):
        s = s[1:].lstrip()   # "@java ...": in cmd vuol dire solo "non mostrare la riga"
    if "%*" in s:
        # "%*" = argomenti passati allo script: al loro posto "nogui", se la riga non lo ha già (era "nogui nogui")
        s = " ".join(s.replace("%*", "" if "nogui" in s else "nogui").split())
    elif "$@" in s:
        s = s.replace('"$@"', "").replace("$@", "").strip()
        if "nogui" not in s:
            s += " nogui"
    elif "nogui" not in s:
        s += " nogui"
    if IS_WINDOWS:
        # In cmd le virgolette raggruppano e basta: prima arrivavano a Java dentro il nome ("forge-1.16.5.jar" con le
        # virgolette, "Unable to access jarfile") e i server Forge 1.16.5 / 1.12.2 creati dal pannello non partivano.
        # %JAVA_HOME% e le altre variabili si sostituiscono come farebbe cmd
        return [os.path.expandvars(p).replace('"', "") if "%" in p else p.replace('"', "")
                for p in shlex.split(s, posix=False)]
    parts = shlex.split(s, posix=True)
    for i, p in enumerate(parts):
        if p.startswith("@") and p.endswith("win_args.txt"):
            unix = p[1:-len("win_args.txt")] + "unix_args.txt"
            if os.path.isfile(os.path.join(server_dir, unix)):
                parts[i] = "@" + unix
    return parts


def script_fallback_command(script: str) -> List[str]:
    """Comando per eseguire lo script di avvio quando non contiene una riga java leggibile."""
    if IS_WINDOWS:
        return ["cmd.exe", "/c", script, "nogui"]
    return ["sh", script, "nogui"]
