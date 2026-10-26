# Voxarium

**Voxarium** è un programma per Windows che serve a gestire un server di **Minecraft Java Edition** dal proprio PC,
da un pannello semplice, senza usare la riga di comando. Si controlla dal PC, dal browser e dal telefono, in casa e
da fuori casa.

Questo repository contiene **solo l'installer e gli aggiornamenti** del programma: il codice sorgente non è pubblico.

**[Scarica l'ultima versione (Setup_Voxarium.exe)](https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases/latest/download/Setup_Voxarium.exe)**
· [Tutte le versioni e le novità](https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases)

---

## Cosa fa

### Il server
- **Avvio, arresto, riavvio** e arresto forzato con un clic; i giocatori vengono avvisati in chat prima di un riavvio.
- **Riavvio programmato** (per esempio ogni 4 ore) e **riavvio automatico dopo un crash**.
- **Modalità Eco**: spegne il server quando resta vuoto per un po', per risparmiare energia.
- **Controllo anti-lag**: avvisa quando il server rallenta (TPS bassi) e può ripulire gli oggetti a terra.
- **Console in tempo reale**, con invio di comandi, e **chat** per scrivere ai giocatori dal pannello.
- **Errori e crash** raccolti e spiegati in una scheda dedicata.

### Creare e gestire i server
- **Creazione di un server da zero** in pochi passi: Forge, NeoForge, Fabric, Paper o Vanilla, nella versione di
  Minecraft scelta (EULA, porta e RAM vengono impostate in automatico).
- **Più server sullo stesso PC**, con il passaggio da uno all'altro.
- **RAM del server** scelta dalle impostazioni, con le ottimizzazioni consigliate (Aikar's Flags).
- **Editor di `server.properties`** con spiegazioni, e **file manager** per modificare i file del server dal browser.

### Mod e plugin
- Elenco delle mod installate con **scheda informazioni** (versione, autori, dipendenze...), attivazione e
  disattivazione senza cancellarle.
- **Store di mod e plugin** (catalogo [Modrinth](https://modrinth.com)): ricerca, installazione con un clic,
  disinstallazione, informazioni complete e **aggiornamenti delle mod** già installate.

### Giocatori
- Giocatori online, whitelist, ban, operatori e azioni rapide (teletrasporto, modalità di gioco, oggetti...).

### Backup
- **Backup completo** di tutta la cartella del server in un file ZIP, con avanzamento e tempo rimanente.
- Prima di partire mostra **quanto peserà il backup** e se c'è spazio sul Desktop e su Google Drive.
- **Copia automatica su Google Drive** (serve **Google Drive per desktop** installato e collegato sul PC).
- **Ripristino** di un backup con un clic.

### Rete e accesso remoto
- **Apertura automatica della porta** del server sul router (UPnP) e **indirizzo da copiare** da dare agli amici.
- **Pannello dal telefono o da un altro PC** della rete di casa.
- **Accesso da internet** in HTTPS, protetto da **PIN** (il PIN è sempre obbligatorio da fuori casa).
- **App Android** con notifiche (server acceso o spento, crash, backup completato...).
- Modalità **Remoto**: un altro PC può installare Voxarium solo per controllare il server che gira sul PC principale.

### Risorse del PC
- Uso di CPU, RAM e disco del PC e del server Minecraft, con grafici dell'ultimo minuto e delle ultime 24 ore.

---

## Requisiti
- **Windows 10 o 11** (64 bit).
- **Java** adatto alla versione di Minecraft scelta (il programma controlla se c'è e lo segnala).
- Spazio su disco per il server e per i backup.
- Per la copia dei backup su Drive: **Google Drive per desktop** installato e collegato al proprio account.

Python e tutto il resto necessario al pannello sono già compresi nell'installer.

## Installazione
1. Scarica **[Setup_Voxarium.exe](https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases/latest/download/Setup_Voxarium.exe)**.
2. Aprilo e scegli la modalità:
   - **Host**: questo PC ospita il server Minecraft (la scelta normale);
   - **Remoto**: questo PC controlla soltanto un server che gira su un altro PC.
3. Al termine il programma si apre. Crea un nuovo server oppure scegli la cartella di un server che hai già.
4. Dalle **Impostazioni** cambia subito il **PIN** (quello predefinito blocca l'accesso da internet finché non lo cambi).

Windows può mostrare un avviso perché l'installer non è firmato da un editore noto: scegli "Ulteriori informazioni"
e poi "Esegui comunque".

## Aggiornamenti
Il programma controlla da solo, ogni ora, se in questa pagina c'è una nuova versione. Quando c'è, nel pannello compare
una barra con il pulsante **"Aggiorna ora"**: con il server Minecraft spento, il programma scarica la nuova versione,
ne verifica l'integrità (SHA-256), la installa e si riapre da solo. Impostazioni, server e mondi restano dove sono.

## Sicurezza
- Dalla rete di casa e da internet il pannello chiede il **PIN** (da 6 a 12 cifre).
- L'accesso da internet usa **HTTPS** con un certificato generato sul tuo PC; l'app Android lo riconosce dalla sua impronta.
- La porta del pannello in HTTP non viene mai aperta sul router: da fuori casa si passa solo dalla porta HTTPS.
- Gli avvisi mandati al telefono sono firmati dal PC, così nessun altro può inviare avvisi falsi.

---

*Voxarium non è un prodotto ufficiale di Minecraft e non è approvato né collegato a Mojang o Microsoft.
"Minecraft" è un marchio di Mojang Synergies AB.*
