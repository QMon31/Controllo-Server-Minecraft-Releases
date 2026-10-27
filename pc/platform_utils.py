"""
Differenze tra Windows e Linux in un solo posto: script di avvio del server (run.bat / run.sh),
file degli argomenti di Forge/NeoForge (win_args.txt / unix_args.txt) e comando java letto dallo script.
Su Windows tutto resta come prima.
"""

import os
import shlex
import stat
from typing import List

IS_WINDOWS = os.name == "nt"

# Script di avvio creato dal pannello e cercato nella cartella del server
LAUNCH_SCRIPT = "run.bat" if IS_WINDOWS else "run.sh"
LAUNCH_SCRIPT_CANDIDATES = ("run.bat", "start.bat") if IS_WINDOWS else ("run.sh", "start.sh")
# Argomenti java di Forge/NeoForge moderni: l'installer li scrive in entrambi i formati
FORGE_ARGS_FILE = "win_args.txt" if IS_WINDOWS else "unix_args.txt"


def write_launch_script(target_dir: str, label: str, launch_cmd: str) -> str:
    """Scrive run.bat (Windows) o run.sh eseguibile (Linux) con la riga java; restituisce il percorso."""
    path = os.path.join(target_dir, LAUNCH_SCRIPT)
    if IS_WINDOWS:
        with open(path, "w", encoding="utf-8") as f:
            f.write("@echo off\n")
            f.write(f"REM Voxarium {label} Launcher\n")
            f.write(f"{launch_cmd} %*\n")
            f.write("pause\n")
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\n")
            f.write(f"# Voxarium {label} Launcher\n")
            f.write('cd "$(dirname "$0")"\n')
            f.write(f'{launch_cmd} "$@"\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def split_java_line(line: str, server_dir: str = "") -> List[str]:
    """Riga "java ..." dello script di avvio → argomenti per Popen, con "nogui" in fondo.
    Su Linux un win_args.txt (server copiato da Windows) diventa unix_args.txt se c'è: il primo usa ';' come separatore."""
    s = line.strip()
    if "%*" in s:
        s = s.replace("%*", "nogui")
    elif "$@" in s:
        s = s.replace('"$@"', "").replace("$@", "").strip()
        if "nogui" not in s:
            s += " nogui"
    elif "nogui" not in s:
        s += " nogui"
    if IS_WINDOWS:
        return shlex.split(s, posix=False)
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
