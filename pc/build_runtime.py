"""
Prepara il runtime Python incluso nell'installer: runtime/python
(Python embeddable ufficiale della stessa versione di chi compila + librerie di requirements.txt).
"""

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PY_VER = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
PY_TAG = f"python{sys.version_info.major}{sys.version_info.minor}"
RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")
OUT_DIR = os.path.join(RUNTIME_DIR, "python")
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"


def main():
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    embed_zip = os.path.join(RUNTIME_DIR, f"python-{PY_VER}-embed-amd64.zip")
    if not os.path.isfile(embed_zip):
        print(f"Download {EMBED_URL}")
        req = urllib.request.Request(EMBED_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, open(embed_zip, "wb") as out_f:
            shutil.copyfileobj(resp, out_f)

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(OUT_DIR)

    # Percorsi di ricerca: libreria standard, site-packages e la cartella dell'installazione (..) dove si trova app.py
    with open(os.path.join(OUT_DIR, f"{PY_TAG}._pth"), "w", encoding="ascii") as f:
        f.write(f"{PY_TAG}.zip\n.\nLib\\site-packages\n..\nimport site\n")

    site_packages = os.path.join(OUT_DIR, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-compile", "--target", site_packages,
         # Versioni esatte (requirements-lock.txt): la stessa build dà sempre le stesse librerie
         "-r", os.path.join(BASE_DIR, "requirements-lock.txt")],
        check=True,
    )

    check = subprocess.run(
        [os.path.join(OUT_DIR, "python.exe"), "-c", "import fastapi, uvicorn, websockets, psutil, pydantic, qrcode, PIL, cryptography; print('OK')"],
        capture_output=True, text=True,
    )
    if check.returncode != 0 or "OK" not in check.stdout:
        print(check.stdout, check.stderr)
        sys.exit("ERRORE: il runtime incluso non riesce a importare le librerie")
    print(f"Runtime pronto in {OUT_DIR}")


if __name__ == "__main__":
    main()
