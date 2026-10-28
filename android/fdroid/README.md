# Voxarium su F-Droid

Preparato il 27/10/2026. Prima versione per F-Droid: **v4.12.5 (Build 102)**, tag `android-v4.12.5-b102` nel
repository pubblico `QMon31/Voxarium`. Richiesta inviata il 27/10/2026: merge request
https://gitlab.com/fdroid/fdroiddata/-/merge_requests/54519 (ramo `com.minecraftserver.manager`; la ricetta là punta al commit pubblico `2adfe8b`, con icona e screenshot).

## Come funziona

- **Due varianti dagli stessi sorgenti**. `Flavor.SELF_UPDATE` è `true` in `flavors/ota/` (app normale, si aggiorna
  da sola dal PC) e `false` in `flavors/fdroid/`. Nella variante F-Droid non ci sono il permesso
  `REQUEST_INSTALL_PACKAGES`, il provider `AppUpdateProvider`, il pulsante "Controlla aggiornamenti ora" né il
  controllo periodico di `version.json`; R8 toglie il codice rimasto. Il titolo "Sicurezza & aggiornamenti OTA"
  diventa "Sicurezza". F-Droid non accetta app che scaricano e installano APK da sole.
- **Sul PC di sviluppo** si compila come sempre con `build_apk.py` (variante normale, OTA).
  `python build_apk.py --fdroid` crea `Voxarium-fdroid.apk` per provare la variante F-Droid (mai in OTA).
- **F-Droid compila con Gradle** (`build.gradle`, `settings.gradle`, `gradlew`): `gradlew assembleFdroidRelease`.
  `gradlew assembleOtaRelease` dà l'app normale (non firmata). Il package dell'app non sta più in
  `AndroidManifest.xml` (Gradle non lo vuole lì): è in `build.gradle` e in `build_apk.py` (`PACKAGE`).
- **Testi della scheda** in inglese e italiano: `fastlane/metadata/android/` (F-Droid li legge dal codice pubblico).
  Per ogni versione pubblicata su F-Droid va aggiunto `changelogs/<versionCode>.txt`.
- **Ricetta**: `com.minecraftserver.manager.yml`, va nel repository `fdroiddata` (non qui).

## Come mandare la richiesta (lo fa l'utente)

1. Account su gitlab.com (anche con l'accesso di GitHub).
2. Fork di `gitlab.com/fdroid/fdroiddata`.
3. Nel fork, nuovo file `metadata/com.minecraftserver.manager.yml` con il contenuto di `com.minecraftserver.manager.yml`.
4. Merge request verso `fdroid/fdroiddata` con il modello "App inclusion".
5. La revisione richiede di solito da qualche settimana a qualche mese; eventuali correzioni arrivano nei commenti.

## Versioni successive

Dopo ogni versione dell'app pubblicata (OTA) e copiata nel codice pubblico con `publish_source.py --push`, mettere
il tag `android-v<versione>-b<versionCode>` sul commit del repository pubblico: F-Droid lo trova da solo
(`UpdateCheckMode: Tags ^android-v`) e compila la nuova versione.

## Da sapere

- **Firma**: F-Droid firma l'APK con la sua chiave. Chi ha l'app installata dal PC (OTA) non può aggiornarla con
  quella di F-Droid né viceversa: deve disinstallarla prima. Per usare la nostra firma servirebbe una build
  riproducibile (APK identico bit per bit), da valutare dopo l'ingresso.
- **Nome**: nella scheda l'app si chiama solo Voxarium; "Minecraft" compare solo per dire a cosa serve (è un marchio
  Mojang). L'id dell'app (`com.minecraftserver.manager`) non si può cambiare senza perdere gli aggiornamenti.
- **Anti-feature**: il revisore potrebbe aggiungere `NonFreeNet` perché l'app serve a gestire un server Minecraft
  (gioco non libero). È solo un'etichetta, non blocca l'ingresso.
- **Screenshot**: si possono aggiungere in `fastlane/metadata/android/en-US/images/phoneScreenshots/` (1.png, ...),
  senza indirizzi o PIN veri.
