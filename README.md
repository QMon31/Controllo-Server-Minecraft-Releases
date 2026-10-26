# Voxarium

**Voxarium** is a Windows program for running a **Minecraft Java Edition** server on your own PC from a simple
control panel, without using the command line. You can manage it from the PC itself, from a browser and from your
phone, both at home and away from home.

This repository contains **only the installer and the updates** of the program: the source code is not public.

**[Download the latest version (Setup_Voxarium.exe)](https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases/latest/download/Setup_Voxarium.exe)**
· [All versions and release notes](https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases)

The program's interface is in Italian.

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
- **Windows 10 or 11** (64-bit).
- **Java** suitable for the Minecraft version you choose (the program checks whether it is installed and tells you).
- Disk space for the server and for the backups.
- To copy backups to Drive: **Google Drive for desktop** installed and signed in to your account.

Python and everything else the panel needs are already included in the installer.

## Installation
1. Download **[Setup_Voxarium.exe](https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases/latest/download/Setup_Voxarium.exe)**.
2. Open it and choose the mode:
   - **Host**: this PC runs the Minecraft server (the usual choice);
   - **Remote**: this PC only controls a server running on another PC.
3. When it finishes, the program opens. Create a new server or choose the folder of a server you already have.
4. In the **Settings**, change the **PIN** right away (the default PIN blocks access from the internet until you change it).

Windows may show a warning because the installer is not signed by a known publisher: choose "More info" and then
"Run anyway".

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

---

*Voxarium is not an official Minecraft product and is not approved by or associated with Mojang or Microsoft.
"Minecraft" is a trademark of Mojang Synergies AB.*
