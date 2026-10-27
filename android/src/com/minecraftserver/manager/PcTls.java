package com.minecraftserver.manager;

import android.content.Context;
import android.content.SharedPreferences;

import java.net.Socket;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.regex.Pattern;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;
import javax.net.ssl.SSLSession;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509ExtendedTrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * HTTPS verso il PC Host: il PC usa un certificato autofirmato, riconosciuto dalla sua impronta
 * (SHA-256 del certificato DER, esadecimale minuscolo) salvata dopo il login o da un messaggio ntfy firmato.
 * Tutti gli altri siti (ntfy.sh, GitHub) restano verificati normalmente con i certificati di sistema.
 */
public final class PcTls {
    public static final String PREF_CERT_FP = "pc_cert_fp";
    private static final String PREFS_NAME = "MinecraftControllerPrefs";
    private static final Pattern FP_PATTERN = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern IPV4_PATTERN = Pattern.compile("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}");
    /** Indirizzo remoto del PC: https://<IPv4>[:porta] */
    public static final Pattern REMOTE_URL_PATTERN = Pattern.compile("https://(?:[0-9]{1,3}\\.){3}[0-9]{1,3}(?::[0-9]{1,5})?");

    private static Context sAppContext;
    private static boolean sInstalled = false;

    private PcTls() {}

    /** Imposta SSLSocketFactory e HostnameVerifier predefiniti di HttpsURLConnection (una volta per processo). */
    public static synchronized void install(Context ctx) {
        if (ctx != null && sAppContext == null) sAppContext = ctx.getApplicationContext();
        if (sInstalled) return;
        try {
            TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            tmf.init((KeyStore) null);
            X509TrustManager system = null;
            for (TrustManager tm : tmf.getTrustManagers()) {
                if (tm instanceof X509TrustManager) { system = (X509TrustManager) tm; break; }
            }
            if (system == null) return;
            SSLContext sc = SSLContext.getInstance("TLS");
            sc.init(null, new TrustManager[]{new PinningTrustManager(system)}, null);
            HttpsURLConnection.setDefaultSSLSocketFactory(sc.getSocketFactory());
            final HostnameVerifier systemVerifier = HttpsURLConnection.getDefaultHostnameVerifier();
            HttpsURLConnection.setDefaultHostnameVerifier(new HostnameVerifier() {
                @Override
                public boolean verify(String hostname, SSLSession session) {
                    if (systemVerifier.verify(hostname, session)) return true;
                    // Certificato del PC: vale solo per un indirizzo IP numerico (https://IP:porta), mai per un nome di dominio
                    if (hostname == null || !IPV4_PATTERN.matcher(hostname).matches()) return false;
                    try {
                        Certificate[] chain = session.getPeerCertificates();
                        return chain.length > 0 && chain[0] instanceof X509Certificate
                                && matchesSavedFingerprint((X509Certificate) chain[0]);
                    } catch (Exception e) {
                        return false;
                    }
                }
            });
            sInstalled = true;
        } catch (Exception ignored) {}
    }

    /** Normalizza un'impronta (minuscolo, senza due punti); "" se non valida. */
    public static String normalizeFingerprint(String fp) {
        if (fp == null) return "";
        String n = fp.trim().replace(":", "").toLowerCase(java.util.Locale.ROOT);
        return FP_PATTERN.matcher(n).matches() ? n : "";
    }

    /** Salva l'impronta del certificato del PC; ignora valori non validi. */
    public static void saveFingerprint(Context ctx, String fp) {
        String n = normalizeFingerprint(fp);
        if (n.isEmpty() || ctx == null) return;
        SharedPreferences prefs = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        if (!n.equals(prefs.getString(PREF_CERT_FP, ""))) prefs.edit().putString(PREF_CERT_FP, n).apply();
    }

    private static String savedFingerprint() {
        if (sAppContext == null) return "";
        return sAppContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).getString(PREF_CERT_FP, "");
    }

    static String fingerprintOf(X509Certificate cert) throws Exception {
        byte[] h = MessageDigest.getInstance("SHA-256").digest(cert.getEncoded());
        StringBuilder sb = new StringBuilder();
        for (byte b : h) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private static boolean matchesSavedFingerprint(X509Certificate cert) {
        String saved = savedFingerprint();
        if (saved.isEmpty()) return false;
        try {
            return MessageDigest.isEqual(saved.getBytes("UTF-8"), fingerprintOf(cert).getBytes("UTF-8"));
        } catch (Exception e) {
            return false;
        }
    }

    /** Certificati di sistema; se falliscono, accetta solo il certificato con l'impronta salvata del PC. */
    private static final class PinningTrustManager extends X509ExtendedTrustManager {
        private final X509TrustManager mSystem;

        PinningTrustManager(X509TrustManager system) { mSystem = system; }

        private void fallback(X509Certificate[] chain, CertificateException e) throws CertificateException {
            if (chain != null && chain.length > 0 && matchesSavedFingerprint(chain[0])) return;
            throw e;
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            try { mSystem.checkServerTrusted(chain, authType); } catch (CertificateException e) { fallback(chain, e); }
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType, Socket socket) throws CertificateException {
            try {
                if (mSystem instanceof X509ExtendedTrustManager) ((X509ExtendedTrustManager) mSystem).checkServerTrusted(chain, authType, socket);
                else mSystem.checkServerTrusted(chain, authType);
            } catch (CertificateException e) { fallback(chain, e); }
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType, SSLEngine engine) throws CertificateException {
            try {
                if (mSystem instanceof X509ExtendedTrustManager) ((X509ExtendedTrustManager) mSystem).checkServerTrusted(chain, authType, engine);
                else mSystem.checkServerTrusted(chain, authType);
            } catch (CertificateException e) { fallback(chain, e); }
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            mSystem.checkClientTrusted(chain, authType);
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType, Socket socket) throws CertificateException {
            mSystem.checkClientTrusted(chain, authType);
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType, SSLEngine engine) throws CertificateException {
            mSystem.checkClientTrusted(chain, authType);
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() { return mSystem.getAcceptedIssuers(); }
    }
}
