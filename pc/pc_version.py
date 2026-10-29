"""Versione del programma PC: unica fonte usata dal backend, dal controllo aggiornamenti e dalla pubblicazione."""

PC_VERSION = "4.22.6"
PC_BUILD = 108  # +1 a ogni release pubblicata (come versionCode dell'APK)

# Repository GitHub PUBBLICO che contiene solo gli installer; il codice resta nel repository privato
UPDATE_REPO = "QMon31/Voxarium"
# Nome del repository fino al 27/10/2026: GitHub reindirizza i vecchi indirizzi al nome nuovo. I PC con versioni
# precedenti accettano solo installer che iniziano con questo nome, quindi le release lo usano ancora per l'installer.
# Non creare mai un repository con questo nome: interromperebbe il reindirizzamento.
LEGACY_UPDATE_REPO = "QMon31/Controllo-Server-Minecraft-Releases"
