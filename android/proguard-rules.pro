# Regole di R8 per l'APK (lette da build_apk.py insieme a bin/aapt_rules.pro).
# Le classi nominate nel manifest e nei layout (activity, servizio, receiver, widget, provider) le tiene già
# aapt2 in bin/aapt_rules.pro: tutto il resto viene rinominato con nomi corti e illeggibili (a, b, c...).
# L'app non usa riflessione né @JavascriptInterface: se un giorno servisse, la classe va tenuta qui con -keep.

# Numeri di riga negli errori: con bin/mapping/ si ritrovano classe e metodo originali (retrace)
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# Classi rinominate spostate tutte nel pacchetto principale (nasconde anche la struttura del codice)
-repackageclasses ''
-allowaccessmodification
