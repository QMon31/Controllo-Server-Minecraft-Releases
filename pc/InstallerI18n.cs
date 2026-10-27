using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace MinecraftControlCenterInstaller
{
    /// <summary>
    /// Lingua dell'installazione. Il codice scrive le scritte in italiano come sempre; ogni scritta delle finestre
    /// (anche quelle cambiate dopo, come i messaggi di avanzamento) viene tradotta con la tabella qui sotto, cercando
    /// prima il testo intero, poi i modelli con {0} e infine riga per riga. Cambiando lingua si ritraducono tutte.
    /// Una scritta nuova va aggiunta a Rows, altrimenti resta in italiano.
    /// </summary>
    static class I18n
    {
        public static readonly string[] Codes = { "it", "en", "es", "fr", "de", "pt" };
        public static readonly string[] Names = { "Italiano", "English", "Español", "Français", "Deutsch", "Português" };
        public static string Lang = "it";

        // { italiano, inglese, spagnolo, francese, tedesco, portoghese }
        static readonly string[][] Rows = {
            new[] { "Installazione - Voxarium (Desktop Edition)", "Setup - Voxarium (Desktop Edition)", "Instalación - Voxarium (Desktop Edition)", "Installation - Voxarium (Desktop Edition)", "Installation - Voxarium (Desktop Edition)", "Instalação - Voxarium (Desktop Edition)" },
            new[] { "Installazione di Voxarium", "Voxarium Setup", "Instalación de Voxarium", "Installation de Voxarium", "Voxarium-Installation", "Instalação do Voxarium" },
            new[] { "Procedura guidata • Protezione Host & Rete • Scelta Ruolo", "Setup wizard • Host & Network protection • Role choice", "Asistente • Protección del host y la red • Elección del rol", "Assistant • Protection hôte et réseau • Choix du rôle", "Assistent • Host- & Netzwerkschutz • Rollenwahl", "Assistente • Proteção do host e da rede • Escolha da função" },
            new[] { "Lingua", "Language", "Idioma", "Langue", "Sprache", "Idioma" },
            new[] { "Annulla", "Cancel", "Cancelar", "Annuler", "Abbrechen", "Cancelar" },
            new[] { "Avanti >", "Next >", "Siguiente >", "Suivant >", "Weiter >", "Avançar >" },
            new[] { "< Indietro", "< Back", "< Atrás", "< Retour", "< Zurück", "< Voltar" },
            new[] { "Installa", "Install", "Instalar", "Installer", "Installieren", "Instalar" },
            new[] { "Fine", "Finish", "Finalizar", "Terminer", "Fertig", "Concluir" },

            // Passo 0: benvenuto e controlli
            new[] { "Benvenuto nella procedura guidata di Voxarium.", "Welcome to the Voxarium setup wizard.", "Te damos la bienvenida al asistente de Voxarium.", "Bienvenue dans l'assistant d'installation de Voxarium.", "Willkommen beim Voxarium-Installationsassistenten.", "Bem-vindo ao assistente de instalação do Voxarium." },
            new[] { "Questa applicazione ti consente di avviare, monitorare e gestire il tuo server sia in locale su questo PC, sia in remoto da un altro computer o smartphone con protezioni di rete attive.",
                    "This application lets you start, monitor and manage your server both locally on this PC and remotely from another computer or smartphone, with network protection enabled.",
                    "Esta aplicación te permite iniciar, supervisar y gestionar tu servidor tanto en local en este PC como en remoto desde otro ordenador o smartphone, con protecciones de red activas.",
                    "Cette application vous permet de démarrer, surveiller et gérer votre serveur aussi bien en local sur ce PC qu'à distance depuis un autre ordinateur ou un smartphone, avec les protections réseau actives.",
                    "Mit dieser Anwendung kannst du deinen Server lokal auf diesem PC oder aus der Ferne von einem anderen Computer oder Smartphone starten, überwachen und verwalten – mit aktivem Netzwerkschutz.",
                    "Este aplicativo permite iniciar, monitorar e gerenciar seu servidor tanto localmente neste PC quanto remotamente de outro computador ou smartphone, com proteções de rede ativas." },
            new[] { "Diagnostica di Sistema e Sicurezza", "System and Security Check", "Diagnóstico del sistema y seguridad", "Diagnostic du système et sécurité", "System- und Sicherheitsprüfung", "Diagnóstico do sistema e segurança" },
            new[] { "[✓] Protezione Host e Rete: Sanitizzazione comandi, protezione directory di Windows e blocco accessi esterni attivi.",
                    "[✓] Host and network protection: command sanitization, Windows folder protection and external access blocking are active.",
                    "[✓] Protección del host y la red: saneamiento de comandos, protección de carpetas de Windows y bloqueo de accesos externos activos.",
                    "[✓] Protection hôte et réseau : nettoyage des commandes, protection des dossiers Windows et blocage des accès externes actifs.",
                    "[✓] Host- und Netzwerkschutz: Befehlsbereinigung, Schutz der Windows-Ordner und Sperre externer Zugriffe sind aktiv.",
                    "[✓] Proteção do host e da rede: sanitização de comandos, proteção das pastas do Windows e bloqueio de acessos externos ativos." },
            new[] { "[✓] Interfaccia Desktop Nativa Windows integrata con Microsoft Edge WebView2.", "[✓] Native Windows desktop interface built on Microsoft Edge WebView2.", "[✓] Interfaz de escritorio nativa de Windows integrada con Microsoft Edge WebView2.", "[✓] Interface de bureau Windows native intégrée à Microsoft Edge WebView2.", "[✓] Native Windows-Desktopoberfläche mit Microsoft Edge WebView2.", "[✓] Interface nativa do Windows integrada ao Microsoft Edge WebView2." },
            new[] { "[✓] Runtime del pannello incluso nell'installazione (nessuna installazione di Python richiesta).", "[✓] Panel runtime included in the setup (no Python installation needed).", "[✓] Entorno del panel incluido en la instalación (no hace falta instalar Python).", "[✓] Environnement du panneau inclus dans l'installation (aucune installation de Python requise).", "[✓] Laufzeitumgebung des Panels ist enthalten (keine Python-Installation nötig).", "[✓] Ambiente do painel incluído na instalação (não é preciso instalar o Python)." },
            new[] { "[✓] Java rilevato: il server Minecraft potrà essere avviato in modalità Host.", "[✓] Java found: the Minecraft server can be started in Host mode.", "[✓] Java detectado: el servidor de Minecraft podrá iniciarse en modo Host.", "[✓] Java détecté : le serveur Minecraft pourra être démarré en mode Hôte.", "[✓] Java gefunden: Der Minecraft-Server kann im Host-Modus gestartet werden.", "[✓] Java detectado: o servidor Minecraft poderá ser iniciado no modo Host." },
            new[] { "[!] Java non rilevato: per la modalità Host installare Java 17 o 21 prima di avviare il server Minecraft.", "[!] Java not found: for Host mode, install Java 17 or 21 before starting the Minecraft server.", "[!] Java no detectado: para el modo Host, instala Java 17 o 21 antes de iniciar el servidor de Minecraft.", "[!] Java non détecté : pour le mode Hôte, installez Java 17 ou 21 avant de démarrer le serveur Minecraft.", "[!] Java nicht gefunden: Für den Host-Modus vor dem Start des Minecraft-Servers Java 17 oder 21 installieren.", "[!] Java não detectado: para o modo Host, instale o Java 17 ou 21 antes de iniciar o servidor Minecraft." },

            // Passo 1: ruolo del PC
            new[] { "Seleziona dove si trova il tuo server Minecraft:", "Choose where your Minecraft server is:", "Elige dónde está tu servidor de Minecraft:", "Choisissez où se trouve votre serveur Minecraft :", "Wähle, wo sich dein Minecraft-Server befindet:", "Escolha onde está o seu servidor Minecraft:" },
            new[] { "Server Host Locale", "Local Host Server", "Servidor host local", "Serveur hôte local", "Lokaler Host-Server", "Servidor host local" },
            new[] { "[ QUESTO COMPUTER ]", "[ THIS COMPUTER ]", "[ ESTE ORDENADOR ]", "[ CET ORDINATEUR ]", "[ DIESER COMPUTER ]", "[ ESTE COMPUTADOR ]" },
            new[] { "I file del server Minecraft e Java sono su questo computer.", "The Minecraft server files and Java are on this computer.", "Los archivos del servidor de Minecraft y Java están en este ordenador.", "Les fichiers du serveur Minecraft et Java sont sur cet ordinateur.", "Die Minecraft-Serverdateien und Java sind auf diesem Computer.", "Os arquivos do servidor Minecraft e o Java estão neste computador." },
            new[] { "Installa: gestione completa, rilevamento crash, monitoraggio RAM/CPU e backup.", "Installs: full management, crash detection, RAM/CPU monitoring and backups.", "Instala: gestión completa, detección de fallos, uso de RAM/CPU y copias.", "Installe : gestion complète, détection des crashs, suivi RAM/CPU et sauvegardes.", "Installiert: Verwaltung, Absturzerkennung, RAM/CPU-Überwachung und Backups.", "Instala: gestão completa, detecção de falhas, uso de RAM/CPU e backups." },
            new[] { "Controllo Remoto (PC Secondario)", "Remote Control (Secondary PC)", "Control remoto (PC secundario)", "Contrôle à distance (PC secondaire)", "Fernsteuerung (Zweit-PC)", "Controle remoto (PC secundário)" },
            new[] { "[ SERVER SU UN ALTRO PC ]", "[ SERVER ON ANOTHER PC ]", "[ SERVIDOR EN OTRO PC ]", "[ SERVEUR SUR UN AUTRE PC ]", "[ SERVER AUF ANDEREM PC ]", "[ SERVIDOR EM OUTRO PC ]" },
            new[] { "Il server Minecraft gira su un ALTRO computer (PC secondario o mini-PC).", "The Minecraft server runs on ANOTHER computer (secondary PC or mini PC).", "El servidor de Minecraft funciona en OTRO ordenador (PC secundario o mini PC).", "Le serveur Minecraft tourne sur un AUTRE ordinateur (PC secondaire ou mini-PC).", "Der Minecraft-Server läuft auf einem ANDEREN Computer (Zweit-PC oder Mini-PC).", "O servidor Minecraft roda em OUTRO computador (PC secundário ou mini PC)." },
            new[] { "Installa il client leggero sul PC principale: Java non usa RAM o CPU su questo PC.", "Installs the lightweight client on your main PC: Java uses no RAM or CPU here.", "Instala el cliente ligero en tu PC principal: Java no usa RAM ni CPU aquí.", "Installe le client léger sur le PC principal : Java n'utilise ni RAM ni CPU ici.", "Installiert den schlanken Client: Java belegt auf diesem PC weder RAM noch CPU.", "Instala o cliente leve no PC principal: o Java não usa RAM nem CPU aqui." },
            new[] { "[✓] Protezione Host Attiva: Questo computer ospiterà il server in modo isolato.", "[✓] Host protection active: this computer will host the server in isolation.", "[✓] Protección del host activa: este ordenador alojará el servidor de forma aislada.", "[✓] Protection de l'hôte active : cet ordinateur hébergera le serveur de façon isolée.", "[✓] Host-Schutz aktiv: Dieser Computer betreibt den Server isoliert.", "[✓] Proteção do host ativa: este computador hospedará o servidor de forma isolada." },
            new[] { "• Riconoscimento automatico della cartella del server e porta Minecraft.", "• Automatic detection of the server folder and Minecraft port.", "• Detección automática de la carpeta del servidor y del puerto de Minecraft.", "• Détection automatique du dossier du serveur et du port Minecraft.", "• Automatische Erkennung von Serverordner und Minecraft-Port.", "• Detecção automática da pasta do servidor e da porta do Minecraft." },
            new[] { "• Comandi e percorsi file protetti e sanitizzati contro injection e traversal.", "• Commands and file paths protected and sanitized against injection and traversal.", "• Comandos y rutas de archivos protegidos y saneados contra injection y traversal.", "• Commandes et chemins de fichiers protégés et nettoyés contre l'injection et le traversal.", "• Befehle und Dateipfade gegen Injection und Traversal geschützt und bereinigt.", "• Comandos e caminhos de arquivos protegidos e sanitizados contra injection e traversal." },
            new[] { "• Monitoraggio automatico dello stato del server e gestione crash integrata.", "• Automatic server status monitoring and built-in crash handling.", "• Supervisión automática del estado del servidor y gestión de fallos integrada.", "• Surveillance automatique de l'état du serveur et gestion intégrée des crashs.", "• Automatische Überwachung des Serverstatus und integrierte Absturzbehandlung.", "• Monitoramento automático do estado do servidor e gestão de falhas integrada." },
            new[] { "Indirizzo Server Minecraft Host (Automatico o IP Manuale):", "Minecraft Host Server address (automatic or manual IP):", "Dirección del servidor host de Minecraft (automática o IP manual):", "Adresse du serveur hôte Minecraft (automatique ou IP manuelle) :", "Adresse des Minecraft-Host-Servers (automatisch oder manuelle IP):", "Endereço do servidor host do Minecraft (automático ou IP manual):" },
            new[] { "Rileva Subito", "Detect now", "Detectar ahora", "Détecter", "Jetzt suchen", "Detectar agora" },
            new[] { "Lascia 'auto' per connetterti automaticamente all'avvio (scansione LAN & Cloud).", "Leave 'auto' to connect automatically at startup (LAN & Cloud scan).", "Deja 'auto' para conectarte automáticamente al iniciar (búsqueda en LAN y nube).", "Laissez 'auto' pour vous connecter automatiquement au démarrage (recherche LAN et cloud).", "Lass 'auto' stehen, um dich beim Start automatisch zu verbinden (LAN- & Cloud-Suche).", "Deixe 'auto' para conectar automaticamente ao iniciar (busca na LAN e na nuvem)." },
            new[] { "Ricerca automatica del PC Host nella rete di casa in corso...", "Searching for the Host PC on the home network...", "Buscando el PC host en la red de casa...", "Recherche du PC hôte sur le réseau domestique...", "Host-PC wird im Heimnetz gesucht...", "Procurando o PC host na rede de casa..." },
            new[] { "Server host rilevato con successo: {0}", "Host server found: {0}", "Servidor host detectado: {0}", "Serveur hôte détecté : {0}", "Host-Server gefunden: {0}", "Servidor host detectado: {0}" },
            new[] { "Nessun server risponde attualmente. Lascia 'auto' per rilevamento automatico all'avvio.", "No server is responding right now. Leave 'auto' to detect it automatically at startup.", "Ningún servidor responde ahora mismo. Deja 'auto' para detectarlo automáticamente al iniciar.", "Aucun serveur ne répond pour le moment. Laissez 'auto' pour une détection automatique au démarrage.", "Gerade antwortet kein Server. Lass 'auto' stehen, um ihn beim Start automatisch zu finden.", "Nenhum servidor responde no momento. Deixe 'auto' para detecção automática ao iniciar." },
            new[] { "Verifica in corso...", "Checking...", "Comprobando...", "Vérification...", "Wird geprüft...", "Verificando..." },
            new[] { "[✓] Connessione riuscita! Il PC secondario risponde correttamente.", "[✓] Connected! The secondary PC is responding correctly.", "[✓] ¡Conexión correcta! El PC secundario responde bien.", "[✓] Connexion réussie ! Le PC secondaire répond correctement.", "[✓] Verbindung erfolgreich! Der Zweit-PC antwortet korrekt.", "[✓] Conexão bem-sucedida! O PC secundário responde corretamente." },
            new[] { "[!] Nessuna risposta all'indirizzo specificato. Verifica che il PC sia acceso.", "[!] No response at the given address. Check that the PC is switched on.", "[!] No hay respuesta en la dirección indicada. Comprueba que el PC esté encendido.", "[!] Aucune réponse à l'adresse indiquée. Vérifiez que le PC est allumé.", "[!] Keine Antwort unter der angegebenen Adresse. Prüfe, ob der PC eingeschaltet ist.", "[!] Nenhuma resposta no endereço indicado. Verifique se o PC está ligado." },
            new[] { "Inserisci un indirizzo valido per il server remoto (es. http://192.168.1.50:8080).", "Enter a valid address for the remote server (e.g. http://192.168.1.50:8080).", "Introduce una dirección válida para el servidor remoto (p. ej. http://192.168.1.50:8080).", "Saisissez une adresse valide pour le serveur distant (ex. http://192.168.1.50:8080).", "Gib eine gültige Adresse für den entfernten Server ein (z. B. http://192.168.1.50:8080).", "Informe um endereço válido para o servidor remoto (ex.: http://192.168.1.50:8080)." },
            new[] { "Attenzione", "Warning", "Atención", "Attention", "Achtung", "Atenção" },

            // Passo 2: cartella
            new[] { "Scegli la cartella di installazione sul computer.", "Choose the installation folder on this computer.", "Elige la carpeta de instalación en el ordenador.", "Choisissez le dossier d'installation sur l'ordinateur.", "Wähle den Installationsordner auf dem Computer.", "Escolha a pasta de instalação no computador." },
            new[] { "I file dell'applicazione verranno salvati nel percorso seguente:", "The application files will be saved in the following location:", "Los archivos de la aplicación se guardarán en la siguiente ruta:", "Les fichiers de l'application seront enregistrés à l'emplacement suivant :", "Die Programmdateien werden in diesem Ordner gespeichert:", "Os arquivos do aplicativo serão salvos no seguinte local:" },
            new[] { "Cartella di destinazione:", "Destination folder:", "Carpeta de destino:", "Dossier de destination :", "Zielordner:", "Pasta de destino:" },
            new[] { "Sfoglia...", "Browse...", "Examinar...", "Parcourir...", "Suchen...", "Procurar..." },
            new[] { "Seleziona la cartella di destinazione:", "Select the destination folder:", "Selecciona la carpeta de destino:", "Sélectionnez le dossier de destination :", "Zielordner auswählen:", "Selecione a pasta de destino:" },
            new[] { "Specificare una cartella di destinazione valida.", "Please enter a valid destination folder.", "Indica una carpeta de destino válida.", "Indiquez un dossier de destination valide.", "Bitte einen gültigen Zielordner angeben.", "Informe uma pasta de destino válida." },
            new[] { "Spazio su disco richiesto: circa 65 MB (Host Server Completo)", "Disk space required: about 65 MB (full Host Server)", "Espacio en disco necesario: unos 65 MB (servidor host completo)", "Espace disque requis : environ 65 Mo (serveur hôte complet)", "Benötigter Speicherplatz: ca. 65 MB (vollständiger Host-Server)", "Espaço em disco necessário: cerca de 65 MB (servidor host completo)" },
            new[] { "Include tutti i componenti di gestione e sicurezza.", "Includes all management and security components.", "Incluye todos los componentes de gestión y seguridad.", "Inclut tous les composants de gestion et de sécurité.", "Enthält alle Verwaltungs- und Sicherheitskomponenten.", "Inclui todos os componentes de gerenciamento e segurança." },
            new[] { "Spazio su disco richiesto: circa 1 MB (Client Leggero)", "Disk space required: about 1 MB (lightweight client)", "Espacio en disco necesario: aprox. 1 MB (cliente ligero)", "Espace disque requis : environ 1 Mo (client léger)", "Benötigter Speicherplatz: ca. 1 MB (schlanker Client)", "Espaço em disco necessário: cerca de 1 MB (cliente leve)" },
            new[] { "Nessun consumo di CPU/RAM per Java su questo PC.", "No CPU/RAM used by Java on this PC.", "Java no consume CPU ni RAM en este PC.", "Aucune CPU/RAM utilisée par Java sur ce PC.", "Java belegt auf diesem PC weder CPU noch RAM.", "O Java não consome CPU/RAM neste PC." },
            new[] { "Spazio su disco richiesto: circa 65 MB", "Disk space required: about 65 MB", "Espacio en disco necesario: unos 65 MB", "Espace disque requis : environ 65 Mo", "Benötigter Speicherplatz: ca. 65 MB", "Espaço em disco necessário: cerca de 65 MB" },
            new[] { "Include tutti i componenti del server manager.", "Includes all server manager components.", "Incluye todos los componentes del gestor del servidor.", "Inclut tous les composants du gestionnaire de serveur.", "Enthält alle Komponenten der Serververwaltung.", "Inclui todos os componentes do gerenciador do servidor." },

            // Passo 3: opzioni
            new[] { "Seleziona le opzioni e le protezioni di sicurezza desiderate:", "Choose the options and security protections you want:", "Elige las opciones y protecciones de seguridad que quieras:", "Choisissez les options et protections de sécurité souhaitées :", "Wähle die gewünschten Optionen und Schutzfunktionen:", "Escolha as opções e proteções de segurança desejadas:" },
            new[] { "Crea collegamento sul Desktop (con icona Minecraft 3D)", "Create a Desktop shortcut (with 3D Minecraft icon)", "Crear acceso directo en el escritorio (con icono 3D de Minecraft)", "Créer un raccourci sur le Bureau (avec icône Minecraft 3D)", "Desktop-Verknüpfung erstellen (mit 3D-Minecraft-Symbol)", "Criar atalho na Área de Trabalho (com ícone 3D do Minecraft)" },
            new[] { "Crea gruppo di avvio nel Menu Start di Windows", "Create a group in the Windows Start menu", "Crear grupo en el menú Inicio de Windows", "Créer un groupe dans le menu Démarrer de Windows", "Gruppe im Windows-Startmenü erstellen", "Criar grupo no menu Iniciar do Windows" },
            new[] { "Avvia automaticamente quando accendi il computer (Consigliato)", "Start automatically when the computer turns on (recommended)", "Iniciar automáticamente al encender el ordenador (recomendado)", "Démarrer automatiquement à l'allumage de l'ordinateur (recommandé)", "Beim Einschalten des Computers automatisch starten (empfohlen)", "Iniciar automaticamente ao ligar o computador (recomendado)" },
            new[] { "Rigenera e forza il collegamento sul Desktop (risolve se eliminato o mancante)", "Recreate the Desktop shortcut (fixes it if deleted or missing)", "Regenerar el acceso directo del escritorio (si se borró o falta)", "Recréer le raccourci du Bureau (s'il a été supprimé ou manque)", "Desktop-Verknüpfung neu erstellen (falls gelöscht oder fehlend)", "Recriar o atalho da Área de Trabalho (se foi apagado ou falta)" },
            new[] { "Registra nell'elenco 'Installazione Applicazioni' di Windows (Disinstallatore pulito)", "Add to the Windows 'Installed apps' list (clean uninstaller)", "Registrar en la lista 'Aplicaciones instaladas' de Windows (desinstalador limpio)", "Ajouter à la liste 'Applications installées' de Windows (désinstallation propre)", "In der Windows-Liste 'Installierte Apps' eintragen (saubere Deinstallation)", "Registrar na lista 'Aplicativos instalados' do Windows (desinstalação limpa)" },
            new[] { "Nota: L'avvio automatico garantisce che il server Minecraft e il pannello siano subito attivi all'accensione del PC, pronti per giocare o per il controllo da cellulare anche se il monitor o la finestra non sono ancora stati aperti.",
                    "Note: automatic startup makes sure the Minecraft server and the panel are running as soon as the PC turns on, ready for playing or for control from your phone even if the screen or window hasn't been opened yet.",
                    "Nota: el inicio automático hace que el servidor de Minecraft y el panel estén activos nada más encender el PC, listos para jugar o para controlarlos desde el móvil aunque aún no se haya abierto la pantalla o la ventana.",
                    "Remarque : le démarrage automatique garantit que le serveur Minecraft et le panneau sont actifs dès l'allumage du PC, prêts pour jouer ou pour le contrôle depuis le téléphone même si l'écran ou la fenêtre n'ont pas encore été ouverts.",
                    "Hinweis: Durch den Autostart laufen Minecraft-Server und Panel direkt nach dem Einschalten des PCs – bereit zum Spielen oder zur Steuerung per Handy, auch wenn Bildschirm oder Fenster noch nicht geöffnet wurden.",
                    "Observação: a inicialização automática garante que o servidor Minecraft e o painel fiquem ativos assim que o PC é ligado, prontos para jogar ou para o controle pelo celular mesmo que a tela ou a janela ainda não tenham sido abertas." },

            // Passo 4: installazione
            new[] { "Installazione e configurazione in corso...", "Installing and configuring...", "Instalando y configurando...", "Installation et configuration en cours...", "Installation und Einrichtung laufen...", "Instalando e configurando..." },
            new[] { "Inizializzazione file...", "Preparing files...", "Preparando archivos...", "Préparation des fichiers...", "Dateien werden vorbereitet...", "Preparando arquivos..." },
            new[] { "Creazione cartella di destinazione...", "Creating the destination folder...", "Creando la carpeta de destino...", "Création du dossier de destination...", "Zielordner wird erstellt...", "Criando a pasta de destino..." },
            new[] { "Estratto: {0}", "Extracted: {0}", "Extraído: {0}", "Extrait : {0}", "Entpackt: {0}", "Extraído: {0}" },
            new[] { "Configurazione parametri di avvio...", "Setting up startup options...", "Configurando las opciones de inicio...", "Configuration des options de démarrage...", "Startoptionen werden eingerichtet...", "Configurando as opções de inicialização..." },
            new[] { "Regole firewall per l'accesso dal telefono (porte 8080 e 8443)...", "Firewall rules for phone access (ports 8080 and 8443)...", "Reglas del firewall para el acceso desde el móvil (puertos 8080 y 8443)...", "Règles du pare-feu pour l'accès depuis le téléphone (ports 8080 et 8443)...", "Firewall-Regeln für den Zugriff per Handy (Ports 8080 und 8443)...", "Regras do firewall para o acesso pelo celular (portas 8080 e 8443)..." },
            new[] { "Creazione collegamenti Desktop e Menu Start...", "Creating Desktop and Start menu shortcuts...", "Creando accesos directos en el escritorio y el menú Inicio...", "Création des raccourcis du Bureau et du menu Démarrer...", "Verknüpfungen auf Desktop und im Startmenü werden erstellt...", "Criando atalhos na Área de Trabalho e no menu Iniciar..." },
            new[] { "Configurazione avvio automatico con Windows...", "Setting up automatic startup with Windows...", "Configurando el inicio automático con Windows...", "Configuration du démarrage automatique avec Windows...", "Autostart mit Windows wird eingerichtet...", "Configurando a inicialização automática com o Windows..." },
            new[] { "Registrazione disinstallatore in Windows...", "Registering the uninstaller in Windows...", "Registrando el desinstalador en Windows...", "Enregistrement du programme de désinstallation dans Windows...", "Deinstallationsprogramm wird in Windows registriert...", "Registrando o desinstalador no Windows..." },
            new[] { "Completato!", "Done!", "¡Completado!", "Terminé !", "Fertig!", "Concluído!" },
            new[] { "Nota durante l'installazione: {0}", "Note during setup: {0}", "Aviso durante la instalación: {0}", "Remarque pendant l'installation : {0}", "Hinweis während der Installation: {0}", "Aviso durante a instalação: {0}" },
            new[] { "Installazione", "Setup", "Instalación", "Installation", "Installation", "Instalação" },

            // Passo 5: fine
            new[] { "Installazione Completata con Successo!", "Setup completed successfully!", "¡Instalación completada con éxito!", "Installation terminée avec succès !", "Installation erfolgreich abgeschlossen!", "Instalação concluída com sucesso!" },
            new[] { "Voxarium è stato installato e configurato con le protezioni di sicurezza attive.", "Voxarium has been installed and configured with security protection enabled.", "Voxarium se ha instalado y configurado con las protecciones de seguridad activas.", "Voxarium a été installé et configuré avec les protections de sécurité actives.", "Voxarium wurde mit aktiven Schutzfunktionen installiert und eingerichtet.", "O Voxarium foi instalado e configurado com as proteções ativas." },
            new[] { "Puoi avviare l'applicazione in qualsiasi momento dal collegamento sul Desktop o dal Menu Start.", "You can start the application at any time from the Desktop shortcut or the Start menu.", "Puedes iniciar la aplicación cuando quieras desde el acceso directo del escritorio o el menú Inicio.", "Lancez l'application quand vous voulez depuis le raccourci du Bureau ou le menu Démarrer.", "Du kannst die Anwendung jederzeit über die Desktop-Verknüpfung oder das Startmenü starten.", "Abra o aplicativo quando quiser pelo atalho da Área de Trabalho ou pelo menu Iniciar." },
            new[] { "Modalità attiva: Server Host Locale (Questo PC)", "Active mode: Local Host Server (this PC)", "Modo activo: servidor host local (este PC)", "Mode actif : serveur hôte local (ce PC)", "Aktiver Modus: Lokaler Host-Server (dieser PC)", "Modo ativo: servidor host local (este PC)" },
            new[] { "Modalità attiva: Server Host Locale (Questo PC ospita il server)", "Active mode: Local Host Server (this PC hosts the server)", "Modo activo: servidor host local (este PC aloja el servidor)", "Mode actif : serveur hôte local (ce PC héberge le serveur)", "Aktiver Modus: Lokaler Host-Server (dieser PC betreibt den Server)", "Modo ativo: servidor host local (este PC hospeda o servidor)" },
            new[] { "Modalità attiva: Controllo Remoto (Connesso a {0})", "Active mode: Remote Control (connected to {0})", "Modo activo: control remoto (conectado a {0})", "Mode actif : contrôle à distance (connecté à {0})", "Aktiver Modus: Fernsteuerung (verbunden mit {0})", "Modo ativo: controle remoto (conectado a {0})" },
            new[] { "Avvia Voxarium adesso", "Launch Voxarium now", "Iniciar Voxarium ahora", "Lancer Voxarium maintenant", "Voxarium jetzt starten", "Iniciar o Voxarium agora" },

            // Disinstalla.bat (niente & | < > ^ % nelle traduzioni: rompono il file batch)
            new[] { "DISINSTALLAZIONE VOXARIUM", "UNINSTALLING VOXARIUM", "DESINSTALACIÓN DE VOXARIUM", "DÉSINSTALLATION DE VOXARIUM", "DEINSTALLATION VON VOXARIUM", "DESINSTALAÇÃO DO VOXARIUM" },
            new[] { "Arresto processi in corso...", "Stopping running processes...", "Deteniendo los procesos en curso...", "Arrêt des processus en cours...", "Laufende Prozesse werden beendet...", "Encerrando os processos em execução..." },
            new[] { "Eliminazione avvio automatico con Windows...", "Removing automatic startup with Windows...", "Eliminando el inicio automático con Windows...", "Suppression du démarrage automatique avec Windows...", "Autostart mit Windows wird entfernt...", "Removendo a inicialização automática com o Windows..." },
            new[] { "Eliminazione collegamenti...", "Removing shortcuts...", "Eliminando accesos directos...", "Suppression des raccourcis...", "Verknüpfungen werden entfernt...", "Removendo atalhos..." },
            new[] { "I collegamenti, l'avvio automatico e la registrazione sono stati rimossi.", "Shortcuts, automatic startup and the Windows registration have been removed.", "Se han eliminado los accesos directos, el inicio automático y el registro.", "Les raccourcis, le démarrage automatique et l'enregistrement ont été supprimés.", "Verknüpfungen, Autostart und Registrierung wurden entfernt.", "Os atalhos, a inicialização automática e o registro foram removidos." },
            new[] { "Vuoi eliminare anche le impostazioni (PIN, canale notifiche, elenco server)?", "Do you also want to delete the settings (PIN, notification channel, server list)?", "¿Quieres borrar también los ajustes (PIN, canal de notificaciones, lista de servidores)?", "Voulez-vous aussi supprimer les paramètres (PIN, canal de notifications, liste des serveurs) ?", "Sollen auch die Einstellungen gelöscht werden (PIN, Benachrichtigungskanal, Serverliste)?", "Deseja apagar também as configurações (PIN, canal de notificações, lista de servidores)?" },
            new[] { "I mondi Minecraft e i backup NON vengono toccati.", "Minecraft worlds and backups are NOT touched.", "Los mundos de Minecraft y las copias de seguridad NO se tocan.", "Les mondes Minecraft et les sauvegardes ne sont PAS touchés.", "Minecraft-Welten und Backups werden NICHT angetastet.", "Os mundos do Minecraft e os backups NÃO são alterados." },
            // Tasti della domanda: il secondo vuol dire sempre "conserva"
            new[] { "SN", "YN", "SN", "ON", "JN", "SN" },
            new[] { "[S] elimina tutto   [N] conserva le impostazioni per una futura reinstallazione", "[Y] delete everything   [N] keep the settings for a future reinstall", "[S] borrar todo   [N] conservar los ajustes para reinstalar en el futuro", "[O] tout supprimer   [N] garder les paramètres pour une future réinstallation", "[J] alles löschen   [N] Einstellungen für eine spätere Neuinstallation behalten", "[S] apagar tudo   [N] manter as configurações para uma futura reinstalação" },
            new[] { "La cartella del programma verra' eliminata.", "The program folder will be deleted.", "Se borrará la carpeta del programa.", "Le dossier du programme sera supprimé.", "Der Programmordner wird gelöscht.", "A pasta do programa será apagada." },
            new[] { "Premi un tasto per completare...", "Press any key to finish...", "Pulsa una tecla para terminar...", "Appuyez sur une touche pour terminer...", "Zum Abschließen eine Taste drücken...", "Pressione uma tecla para concluir..." },
            new[] { "Impostazioni eliminate. Puoi eliminare manualmente la cartella:", "Settings deleted. You can delete the folder manually:", "Ajustes borrados. Puedes borrar la carpeta a mano:", "Paramètres supprimés. Vous pouvez supprimer le dossier manuellement :", "Einstellungen gelöscht. Den Ordner kannst du von Hand löschen:", "Configurações apagadas. Você pode apagar a pasta manualmente:" },
            new[] { "Impostazioni conservate in:", "Settings kept in:", "Ajustes conservados en:", "Paramètres conservés dans :", "Einstellungen bleiben erhalten in:", "Configurações mantidas em:" },
        };

        static Dictionary<string, string[]> exact;
        static List<KeyValuePair<Regex, string[]>> templates;
        static readonly Dictionary<Control, string> originals = new Dictionary<Control, string>();
        static bool applying;

        static void Build()
        {
            if (exact != null) return;
            exact = new Dictionary<string, string[]>();
            templates = new List<KeyValuePair<Regex, string[]>>();
            foreach (string[] row in Rows)
            {
                if (row[0].Contains("{0}"))
                {
                    string pattern = "^" + Regex.Replace(Regex.Escape(row[0]), @"\\\{(\d)}", "(?<g$1>.*?)") + "$";
                    templates.Add(new KeyValuePair<Regex, string[]>(new Regex(pattern, RegexOptions.Singleline), row));
                }
                else exact[row[0]] = row;
            }
        }

        public static int IndexOf(string code)
        {
            return Array.IndexOf(Codes, code);
        }

        /// <summary>Lingua di partenza: quella già scelta in un'installazione precedente, poi quella di Windows, altrimenti l'inglese.</summary>
        public static string Detect(params string[] launcherConfigs)
        {
            foreach (string cfg in launcherConfigs)
            {
                try
                {
                    if (!File.Exists(cfg)) continue;
                    Match m = Regex.Match(File.ReadAllText(cfg), "\"language\"\\s*:\\s*\"([a-z]{2})\"");
                    if (m.Success && IndexOf(m.Groups[1].Value) >= 0) return m.Groups[1].Value;
                }
                catch { }
            }
            string windows = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            return IndexOf(windows) >= 0 ? windows : "en";
        }

        /// <summary>Traduzione di un testo italiano nella lingua scelta (il testo stesso se non è in tabella).</summary>
        public static string T(string it)
        {
            if (string.IsNullOrEmpty(it) || Lang == "it") return it;
            Build();
            int col = IndexOf(Lang);
            if (col <= 0) return it;
            string[] row;
            if (exact.TryGetValue(it, out row)) return row[col];
            foreach (KeyValuePair<Regex, string[]> tpl in templates)
            {
                Match m = tpl.Key.Match(it);
                if (!m.Success) continue;
                string res = tpl.Value[col];
                for (int i = 0; i < 10; i++)
                {
                    Group g = m.Groups["g" + i];
                    if (g.Success) res = res.Replace("{" + i + "}", g.Value);
                }
                return res;
            }
            if (it.IndexOf('\n') >= 0)
            {
                // Testo su più righe: ogni riga si traduce da sola, gli a capo restano dove sono
                string[] parts = Regex.Split(it, "(\r?\n)");
                for (int i = 0; i < parts.Length; i += 2) parts[i] = T(parts[i]);
                return string.Concat(parts);
            }
            return it;
        }

        public static DialogResult ShowMessage(string text, string caption, MessageBoxButtons buttons, MessageBoxIcon icon)
        {
            return MessageBox.Show(T(text), T(caption), buttons, icon);
        }

        /// <summary>Traduce la finestra e tutto quello che contiene, anche le scritte cambiate o aggiunte in seguito.</summary>
        public static void Attach(Control c)
        {
            // Campi in cui scrive l'utente (cartella, indirizzo) e scelta della lingua: mai tradotti
            if (c is TextBoxBase || c is ComboBox || originals.ContainsKey(c)) return;
            // Nelle scritte "&" è un carattere normale ("Host & Rete"), non la lettera sottolineata di una scorciatoia
            if (c is Label) ((Label)c).UseMnemonic = false;
            originals[c] = c.Text;
            c.TextChanged += delegate(object s, EventArgs e) {
                if (applying) return;
                originals[c] = c.Text;
                Apply(c);
            };
            c.ControlAdded += delegate(object s, ControlEventArgs e) { Attach(e.Control); };
            foreach (Control child in c.Controls) Attach(child);
            Apply(c);
        }

        static void Apply(Control c)
        {
            string t = T(originals[c]);
            if (c.Text == t) return;
            applying = true;
            try { c.Text = t; }
            finally { applying = false; }
        }

        public static void SetLanguage(string code)
        {
            if (IndexOf(code) < 0) return;
            Lang = code;
            foreach (Control c in new List<Control>(originals.Keys)) Apply(c);
        }
    }
}
