# Voxarium

**Voxarium** is a program for **Windows** and **Linux** for running a **Minecraft Java Edition** server on your own PC from a simple
control panel, without using the command line. You can manage it from the PC itself, from a browser and from your
phone, both at home and away from home.

Voxarium is **free and open source** (GNU GPL v3): this repository contains the source code of the PC program for Windows and Linux
([`pc/`](pc)) and of the Android app ([`android/`](android)), and the installers in the Releases.

## Download

Choose the version for your system:

<!-- download:start -->
| | **Windows 10 / 11** | **Linux** (Ubuntu / Debian) |
|---|---|---|
| **Download** | [⬇ Setup_Voxarium.exe](https://github.com/QMon31/Voxarium/releases/latest/download/Setup_Voxarium.exe) | [⬇ voxarium-linux.tar.gz](https://github.com/QMon31/Voxarium/releases/download/linux-v4.22.3-b105/voxarium-linux.tar.gz) |
| **Version** | always the latest | v4.22.3 (Build 105) |
| **Install** | open the file and follow the steps | `tar xzf voxarium-linux.tar.gz` then `sh voxarium/install.sh` (as your normal user) |
| **Release notes** | [Windows release](https://github.com/QMon31/Voxarium/releases/latest) | [Linux release](https://github.com/QMon31/Voxarium/releases/tag/linux-v4.22.3-b105) |

Both versions have the same control panel, work with the same Android app and update themselves from the panel.
<!-- download:end -->

[All versions and release notes](https://github.com/QMon31/Voxarium/releases)

The control panel is available in Italian, English, German, Spanish, French and Portuguese (choose the language in its header).

---

## What it does

### The server
- **Start, stop, restart** and force-stop with one click; players are warned in chat before a restart.
- **Scheduled restarts** (for example every 4 hours) and **automatic restart after a crash**.
- **Eco mode**: stops the server when it has been empty for a while, to save energy.
- **Anti-lag watchdog**: warns you when the server slows down (low TPS) and can clean up dropped items.
- **Live console** to send commands, and a **chat** to talk to the players from the panel.
- **Errors and crashes** collected and explained in a dedicated tab.

### Creating and managing servers
- **Create a server from scratch** in a few steps: Forge, NeoForge, Fabric, Paper or Vanilla, in the Minecraft
  version you choose (EULA, port and RAM are set up automatically).
- **Several servers on the same PC**, and switching between them.
- **Server RAM** chosen from the settings, with the recommended optimizations (Aikar's Flags).
- **`server.properties` editor** with explanations, and a **file manager** to edit the server files from the browser.

### Mods and plugins
- List of installed mods with an **info sheet** (version, authors, dependencies...), and enabling or disabling them
  without deleting them.
- **Mod and plugin store** (the [Modrinth](https://modrinth.com) catalog): search, one-click install, uninstall,
  full details and **updates for the mods** you already have.

### Players
- Online players, whitelist, bans, operators and quick actions (teleport, game mode, items...).

### Backups
- **Full backup** of the whole server folder into a ZIP file, with progress and time remaining.
- Before starting, it shows **how big the backup will be** and whether it fits on the Desktop and on Google Drive.
- **Automatic copy to Google Drive** (requires **Google Drive for desktop** installed and signed in on the PC).
- **One-click restore** of a backup.

### Network and remote access
- **Automatic port opening** on the router (UPnP) and an **address to copy** and give to your friends.
- **Control panel from your phone or another PC** on your home network.
- **Access from the internet** over HTTPS, protected by a **PIN** (the PIN is always required from outside your home).
- **Android app** with notifications (server started or stopped, crashes, backup completed...).
- **Remote** mode: another PC can install Voxarium just to control the server running on the main PC.

### PC resources
- CPU, RAM and disk usage of the PC and of the Minecraft server, with charts for the last minute and the last 24 hours.

---

## Requirements
- **Windows 10 or 11** (64-bit), or **Linux** with systemd (tested on Ubuntu; Debian and derivatives work the same way).
- **Java** suitable for the Minecraft version you choose (the program checks whether it is installed and tells you).
- Disk space for the server and for the backups.
- To copy backups to Drive: **Google Drive for desktop** installed and signed in to your account.

On Windows, Python and everything else the panel needs are already included in the installer. On Linux the install
script installs Python and Java with `sudo` if they are missing.

## Installation

### Windows
1. Download **[Setup_Voxarium.exe](https://github.com/QMon31/Voxarium/releases/latest/download/Setup_Voxarium.exe)**.
2. Open it and choose the mode:
   - **Host**: this PC runs the Minecraft server (the usual choice);
   - **Remote**: this PC only controls a server running on another PC.
3. When it finishes, the program opens. Create a new server or choose the folder of a server you already have.
4. In the **Settings**, change the **PIN** right away (the default PIN blocks access from the internet until you change it).

Windows may show a warning because the installer is not signed by a known publisher: choose "More info" and then
"Run anyway".

### Linux (Ubuntu / Debian)
1. Download **voxarium-linux.tar.gz** from the [Download](#download) table above.
2. In a terminal, as your normal user (not root):
   ```sh
   tar xzf voxarium-linux.tar.gz
   sh voxarium/install.sh
   ```
   `sudo` is asked once, only to install Python and Java if they are missing and to open the ports in `ufw` if it is active.
3. Voxarium runs as a background service (systemd, starts with the PC). Open the panel in the browser at
   `http://IP-of-the-PC:8080` and change the **PIN** in the **Settings**.

## Updates
Every hour the program checks this page for a new version. When there is one, a bar with an **"Aggiorna ora"**
(Update now) button appears in the panel: with the Minecraft server stopped, the program downloads the new version,
checks its integrity (SHA-256), installs it and reopens by itself. Settings, servers and worlds stay where they are.

## Security
- From your home network and from the internet, the panel asks for the **PIN** (6 to 12 digits).
- Access from the internet uses **HTTPS** with a certificate generated on your PC; the Android app recognizes it by its
  fingerprint.
- The panel's HTTP port is never opened on the router: from outside your home you can only get in through the HTTPS port.
- Notifications sent to the phone are signed by the PC, so nobody else can send fake ones.

## Source code
- [`pc/`](pc): Python backend (FastAPI) and web control panel, shared by both systems; Windows launcher and installer
  (C#); Linux install script and systemd service ([`pc/linux/`](pc/linux)).
- [`android/`](android): the Android app.
- How to build both: [BUILDING.md](BUILDING.md).

## License
Voxarium is released under the [GNU General Public License v3.0](LICENSE): you can use, study, modify and share it;
modified versions you distribute must be released under the same license, with their source code.
See [NOTICE.md](NOTICE.md) for the copyright and the additional permission for the Microsoft WebView2 libraries.

---

*Voxarium is not an official Minecraft product and is not approved by or associated with Mojang or Microsoft.
"Minecraft" is a trademark of Mojang Synergies AB.*
