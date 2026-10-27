"""
Prepara il pacchetto winget di Voxarium (QMon31.Voxarium) per la versione in pc_version.py: i tre file YAML che si
inviano al repository microsoft/winget-pkgs (cartella manifests/q/QMon31/Voxarium/<versione>/).

Uso: python make_winget_manifest.py [percorso di Setup_Voxarium.exe]
L'impronta SHA-256 si calcola dall'installer indicato (predefinito: quello compilato qui), che deve essere IDENTICO a
quello della release pubblicata: si esegue dopo `build_deploy.py --publish`. L'installer si installa senza domande con
--silent (Installer.cs) e si registra in "App installate" come ControlloServerMinecraft con la versione vera.
"""

import datetime
import hashlib
import os
import sys

from pc_version import PC_BUILD, PC_VERSION, UPDATE_REPO

ROOT = os.path.dirname(os.path.abspath(__file__))
IDENTIFIER = "QMon31.Voxarium"
MANIFEST_VERSION = "1.9.0"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def build(setup_path):
    tag = f"v{PC_VERSION}-b{PC_BUILD}"
    url = f"https://github.com/{UPDATE_REPO}/releases/download/{tag}/Setup_Voxarium.exe"
    out = os.path.join(ROOT, "winget", "manifests", "q", "QMon31", "Voxarium", PC_VERSION)
    os.makedirs(out, exist_ok=True)
    head = f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{{kind}}.{MANIFEST_VERSION}.schema.json\n\n"
    files = {
        f"{IDENTIFIER}.yaml": head.format(kind="version") + f"""PackageIdentifier: {IDENTIFIER}
PackageVersion: {PC_VERSION}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: {MANIFEST_VERSION}
""",
        f"{IDENTIFIER}.installer.yaml": head.format(kind="installer") + f"""PackageIdentifier: {IDENTIFIER}
PackageVersion: {PC_VERSION}
InstallerType: exe
Scope: user
InstallModes:
- silent
- silentWithProgress
InstallerSwitches:
  Silent: --silent
  SilentWithProgress: --silent
UpgradeBehavior: install
ReleaseDate: {datetime.date.today().isoformat()}
AppsAndFeaturesEntries:
- DisplayName: Voxarium
  Publisher: QMon31
  ProductCode: ControlloServerMinecraft
Installers:
- Architecture: x64
  InstallerUrl: {url}
  InstallerSha256: {sha256(setup_path)}
ManifestType: installer
ManifestVersion: {MANIFEST_VERSION}
""",
        f"{IDENTIFIER}.locale.en-US.yaml": head.format(kind="defaultLocale") + f"""PackageIdentifier: {IDENTIFIER}
PackageVersion: {PC_VERSION}
PackageLocale: en-US
Publisher: QMon31
PublisherUrl: https://github.com/QMon31
PublisherSupportUrl: https://github.com/{UPDATE_REPO}/issues
PackageName: Voxarium
PackageUrl: https://github.com/{UPDATE_REPO}
License: GPL-3.0
LicenseUrl: https://github.com/{UPDATE_REPO}/blob/main/LICENSE
ShortDescription: Control panel for self-hosted Minecraft Java Edition servers, from the PC, a browser and an Android app.
Description: |-
  Voxarium runs and manages a Minecraft Java Edition server on your own PC without the command line: start, stop and
  scheduled restarts, live console and chat, Forge/NeoForge/Fabric/Paper/Vanilla server creation, Modrinth mod store,
  backups, automatic port opening and secure access from the internet with a PIN, plus an Android companion app.
  Not an official Minecraft product; not approved by or associated with Mojang or Microsoft.
Moniker: voxarium
Tags:
- minecraft-server
- server-manager
- forge
- fabric
- modrinth
- self-hosted
ReleaseNotesUrl: https://github.com/{UPDATE_REPO}/releases/tag/{tag}
ManifestType: defaultLocale
ManifestVersion: {MANIFEST_VERSION}
""",
    }
    for name, text in files.items():
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print(f"Pacchetto winget pronto in {out}")
    return out


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "Setup_Voxarium.exe"))
