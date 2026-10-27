"""
Pubblica la versione Linux in una release SEPARATA da quella Windows, nel repository pubblico delle release.
Tag "linux-vX.Y.Z-bN", mai segnata come "latest" (quella resta l'installer Windows, letto dai PC Windows per
aggiornarsi). Contiene voxarium-linux.tar.gz e linux_version.json, che i PC Linux trovano con pc_updater.
Uso: python publish_linux_release.py "note"
"""

import hashlib
import json
import os
import sys
import tempfile
import urllib.error

import create_linux_package
from pc_version import PC_BUILD, PC_VERSION, UPDATE_REPO
from pc_updater import LINUX_MANIFEST_NAME, LINUX_TAG_PREFIX
from publish_release import API, INTERNAL_NOTE, _request, _token

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_NAME = "voxarium-linux.tar.gz"

INSTALL_TEXT = """## Install (Ubuntu / Debian)

```sh
tar xzf voxarium-linux.tar.gz
sh voxarium/install.sh
```

Run it as your normal user (not root): `sudo` is asked once, only to install Python and Java if they are missing and
to open the ports in `ufw` if it is active. Voxarium then runs as a background service (systemd, starts with the PC)
and the panel opens in the browser at `http://IP-of-the-PC:8080`. The Android app connects as with the Windows version.
Installed copies update themselves from the panel.

*This is the Linux version. For Windows 10/11 download [Setup_Voxarium.exe](https://github.com/QMon31/Voxarium/releases/latest/download/Setup_Voxarium.exe) (all downloads: [README](https://github.com/QMon31/Voxarium#download)).*

*Voxarium is not an official Minecraft product and is not associated with Mojang or Microsoft.*"""


def main(notes: str):
    notes = notes.replace(INTERNAL_NOTE, "").strip() or f"Voxarium for Linux v{PC_VERSION}"
    token = _token()
    tag = f"{LINUX_TAG_PREFIX}v{PC_VERSION}-b{PC_BUILD}"
    os.chdir(BASE_DIR)
    out = os.path.join(tempfile.mkdtemp(prefix="voxarium_linux_"), PACKAGE_NAME)
    create_linux_package.build(out)
    with open(out, "rb") as f:
        pkg = f.read()
    manifest = {
        "version": PC_VERSION,
        "build": PC_BUILD,
        "setup_url": f"https://github.com/{UPDATE_REPO}/releases/download/{tag}/{PACKAGE_NAME}",
        "sha256": hashlib.sha256(pkg).hexdigest(),
        "size": len(pkg),
        "notes": notes,
    }
    try:
        release = _request("POST", f"{API}/repos/{UPDATE_REPO}/releases", token, json.dumps({
            "tag_name": tag,
            "name": f"Voxarium for Linux v{PC_VERSION} (Build {PC_BUILD})",
            "body": "## What's new\n\n" + notes + "\n\n---\n\n" + INSTALL_TEXT,
            "draft": False,
            "prerelease": False,
            "make_latest": "false",  # "latest" resta la release Windows: i PC Windows si aggiornano da lì
        }).encode("utf-8"))
        upload_base = release["upload_url"].split("{")[0]
        _request("POST", f"{upload_base}?name={PACKAGE_NAME}", token, pkg, "application/gzip")
        _request("POST", f"{upload_base}?name={LINUX_MANIFEST_NAME}", token,
                 json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"ERRORE GitHub {e.code}: {e.read().decode('utf-8', 'replace')}"
                 + ("\n(Se il tag esiste già: aumentare PC_BUILD in pc_version.py)" if e.code == 422 else ""))
    print(f"Pubblicata {tag}: {release['html_url']}")
    # La sezione Download del README pubblico deve puntare al pacchetto Linux appena pubblicato
    import publish_source
    publish_source.update_readme_download(tag, PC_VERSION, PC_BUILD)


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]).strip())
