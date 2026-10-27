package com.minecraftserver.manager;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.text.Spanned;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.WeakHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Traduzione dell'app con chiavi identificative (stesse regole del pannello web).
 * res/raw/i18n_it.json è il riferimento (chiave → italiano); i18n_en/es/fr/de/pt.json hanno le stesse chiavi.
 * Nel codice: t("chiave", valore0, valore1…) con {0}, {1}… nella frase. Nei layout: android:tag con
 * "t:chiave" (testo), "h:chiave" (suggerimento), "d:chiave" (descrizione), separati da spazi; "|valore" aggiunge un valore.
 * Solo i testi che arrivano in italiano dal programma sul PC (console, avvisi, messaggi d'errore) si riconoscono
 * dal testo con fromPc(); per loro c'è un controllo prima di ogni disegno dello schermo.
 */
public final class I18n {
    public static final String PREF_KEY = "app_language";
    /** Codici e nomi nella propria lingua, nell'ordine dell'elenco. */
    public static final String[] CODES = {"it", "en", "es", "fr", "de", "pt"};
    public static final String[] NAMES = {"Italiano", "English", "Español", "Français", "Deutsch", "Português"};

    /** Modi di traduzione per le viste che contengono tante righe (console, chat). */
    public static final int LINES_CONSOLE = 1;
    public static final int LINES_CHAT = 2;

    private static String lang = null;
    private static Map<String, String> itDict = new HashMap<>();
    private static Map<String, String> cur = new HashMap<>();

    // Testi italiani dal PC: frasi fisse in una tabella, frasi con {0}, {1}… come espressioni regolari raggruppate per inizio
    private static Map<String, String> exact = new HashMap<>();
    private static Map<String, List<Template>> byStart = new HashMap<>();
    private static List<Template> loose = new ArrayList<>();
    private static final Map<String, String> cache = new HashMap<>();

    // Testi scritti con t(): testo mostrato → {chiave, valori}, per riscriverli al cambio lingua
    private static final Map<String, Object[]> renderedFixed = new HashMap<>();
    private static final Map<String, Object[]> renderedArgs = new LinkedHashMap<String, Object[]>(256, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Object[]> eldest) { return size() > 1500; }
    };

    private static final Map<TextView, String[]> textState = new WeakHashMap<>();
    private static final Map<TextView, String[]> hintState = new WeakHashMap<>();
    private static final Map<View, String[]> descState = new WeakHashMap<>();
    private static final Map<TextView, Integer> lineModes = new WeakHashMap<>();
    private static final Pattern PLACEHOLDER = Pattern.compile("\\{(\\d+)\\}");
    private static final Pattern KEY = Pattern.compile("^[a-z]+\\.[a-z0-9_]+$");
    private static final Pattern AFFIX = Pattern.compile("^(/?)([\\s\\S]*?)(:|\\.\\.\\.)?$");
    private static final Pattern HTTP_ERROR = Pattern.compile("^(HTTP \\d+: )([\\s\\S]+)$");
    private static final Pattern CHAT_LINE = Pattern.compile("^(\\[[^\\]]*\\] )?Server: (.+)$");

    private static final class Template {
        Pattern re;
        int[] order;
        String out;
        int fixed;
    }

    private I18n() {}

    // ---------- lingua ----------

    public static synchronized void ensure(Context ctx) {
        if (lang != null) return;
        SharedPreferences prefs = ctx.getSharedPreferences("MinecraftControllerPrefs", Context.MODE_PRIVATE);
        String saved = prefs.getString(PREF_KEY, null);
        if (saved == null) {
            // Prima volta: la lingua del telefono se c'è, altrimenti l'inglese
            String device = java.util.Locale.getDefault().getLanguage();
            saved = indexOf(device) >= 0 ? device : "en";
        }
        load(ctx, saved);
    }

    public static String getLanguage() { return lang == null ? "it" : lang; }

    public static String getLanguageName() { return NAMES[Math.max(0, indexOf(getLanguage()))]; }

    public static int indexOf(String code) {
        for (int i = 0; i < CODES.length; i++) if (CODES[i].equals(code)) return i;
        return -1;
    }

    /**
     * Salva la lingua e riscrive la schermata sul posto: niente recreate(), che riaprirebbe l'app bloccata
     * (impronta da rimettere) e tornerebbe alla prima sezione. Le scritte fatte con t() si riscrivono dalla
     * loro chiave; quelle arrivate dal PC tornano all'italiano e si ritraducono.
     */
    public static void setLanguage(Activity activity, String code) {
        if (indexOf(code) < 0 || code.equals(getLanguage())) return;
        activity.getSharedPreferences("MinecraftControllerPrefs", Context.MODE_PRIVATE).edit().putString(PREF_KEY, code).apply();
        View root = activity.getWindow().getDecorView();
        List<Object[]> keyed = new ArrayList<>();
        collectKeyed(root, keyed);
        restoreItalian();
        load(activity, code);
        for (Object[] e : keyed) {
            String out = render((Object[]) e[2]);
            if (out == null) continue;
            if ("t".equals(e[1])) ((TextView) e[0]).setText(out);
            else if ("h".equals(e[1])) ((TextView) e[0]).setHint(out);
            else ((View) e[0]).setContentDescription(out);
        }
        translateTree(root);
        root.invalidate();
    }

    /** Viste con testo, suggerimento o descrizione scritti da t(): {vista, "t"/"h"/"d", {chiave, valori}}. */
    private static void collectKeyed(View v, List<Object[]> out) {
        if (v == null) return;
        Object[] rec = renderedOf(v.getContentDescription());
        if (rec != null) out.add(new Object[]{v, "d", rec});
        if (v instanceof TextView) {
            TextView tv = (TextView) v;
            rec = renderedOf(tv.getHint());
            if (rec != null) out.add(new Object[]{tv, "h", rec});
            if (!(tv instanceof EditText)) {
                rec = renderedOf(tv.getText());
                if (rec != null) out.add(new Object[]{tv, "t", rec});
            }
        }
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) collectKeyed(g.getChildAt(i), out);
        }
    }

    /** {chiave, valori} del testo scritto con t(); i valori scritti a loro volta con t() diventano {chiave}. */
    private static synchronized Object[] renderedOf(CharSequence cs) {
        if (cs == null || cs.length() == 0) return null;
        String s = cs.toString();
        Object[] rec = renderedFixed.get(s);
        if (rec != null) return rec;
        rec = renderedArgs.get(s);
        if (rec == null) return null;
        Object[] args = ((Object[]) rec[1]).clone();
        for (int i = 0; i < args.length; i++) {
            Object[] inner = args[i] instanceof String ? renderedFixed.get(args[i]) : null;
            if (inner != null) args[i] = inner;
        }
        return new Object[]{rec[0], args};
    }

    /** Riscrive {chiave, valori} nella lingua attuale. */
    private static String render(Object[] rec) {
        Object[] args = rec.length > 1 ? ((Object[]) rec[1]).clone() : new Object[0];
        for (int i = 0; i < args.length; i++) {
            if (args[i] instanceof Object[]) args[i] = render((Object[]) args[i]);
        }
        return t((String) rec[0], args);
    }

    /** Rimette il testo italiano dove c'è ancora la traduzione fatta da fromPc (non dove il codice ha già scritto altro). */
    private static synchronized void restoreItalian() {
        for (Map.Entry<TextView, String[]> e : new ArrayList<>(textState.entrySet())) {
            TextView tv = e.getKey();
            if (tv != null && tv.getText() != null && tv.getText().toString().equals(e.getValue()[1])) tv.setText(e.getValue()[0]);
        }
        for (Map.Entry<TextView, String[]> e : new ArrayList<>(hintState.entrySet())) {
            TextView tv = e.getKey();
            if (tv != null && tv.getHint() != null && tv.getHint().toString().equals(e.getValue()[1])) tv.setHint(e.getValue()[0]);
        }
        for (Map.Entry<View, String[]> e : new ArrayList<>(descState.entrySet())) {
            View v = e.getKey();
            CharSequence d = v == null ? null : v.getContentDescription();
            if (d != null && d.toString().equals(e.getValue()[1])) v.setContentDescription(e.getValue()[0]);
        }
    }

    private static synchronized void load(Context ctx, String code) {
        if (itDict.isEmpty()) itDict = readDict(ctx, R.raw.i18n_it);
        lang = code;
        cur = "it".equals(code) ? itDict : readDict(ctx, rawId(code));
        if (cur.isEmpty()) { lang = "it"; cur = itDict; }
        exact = new HashMap<>();
        byStart = new HashMap<>();
        loose = new ArrayList<>();
        cache.clear();
        textState.clear();
        hintState.clear();
        descState.clear();
        renderedFixed.clear();
        renderedArgs.clear();
        if ("it".equals(lang)) return;
        for (Map.Entry<String, String> e : itDict.entrySet()) {
            String it = e.getValue();
            String out = cur.get(e.getKey());
            if (out == null) continue;
            if (!PLACEHOLDER.matcher(it).find()) { exact.put(it, out); continue; }
            Template t = new Template();
            StringBuilder re = new StringBuilder("^");
            List<Integer> order = new ArrayList<>();
            Matcher m = PLACEHOLDER.matcher(it);
            int last = 0;
            while (m.find()) {
                re.append(Pattern.quote(it.substring(last, m.start()))).append("([\\s\\S]*?)");
                order.add(Integer.parseInt(m.group(1)));
                last = m.end();
            }
            re.append(Pattern.quote(it.substring(last))).append("$");
            t.re = Pattern.compile(re.toString());
            t.order = new int[order.size()];
            for (int i = 0; i < order.size(); i++) t.order[i] = order.get(i);
            t.out = out;
            t.fixed = PLACEHOLDER.matcher(it).replaceAll("").length();
            if (it.length() >= 3 && it.substring(0, 3).indexOf('{') < 0) {
                String k = it.substring(0, 3);
                List<Template> list = byStart.get(k);
                if (list == null) { list = new ArrayList<>(); byStart.put(k, list); }
                list.add(t);
            } else {
                loose.add(t);
            }
        }
        // Prima i modelli con più testo fisso: "Informazioni su questa {0}" vince su "Informazioni su {0}"
        java.util.Comparator<Template> specific = (a, c) -> c.fixed - a.fixed;
        for (List<Template> list : byStart.values()) Collections.sort(list, specific);
        Collections.sort(loose, specific);
    }

    private static Map<String, String> readDict(Context ctx, int res) {
        Map<String, String> out = new HashMap<>();
        if (res == 0) return out;
        try (InputStream in = ctx.getResources().openRawResource(res)) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] b = new byte[16384];
            int n;
            while ((n = in.read(b)) > 0) buf.write(b, 0, n);
            JSONObject dict = new JSONObject(new String(buf.toByteArray(), StandardCharsets.UTF_8));
            Iterator<String> keys = dict.keys();
            while (keys.hasNext()) {
                String k = keys.next();
                out.put(k, dict.getString(k));
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    private static int rawId(String code) {
        switch (code) {
            case "it": return R.raw.i18n_it;
            case "en": return R.raw.i18n_en;
            case "es": return R.raw.i18n_es;
            case "fr": return R.raw.i18n_fr;
            case "de": return R.raw.i18n_de;
            case "pt": return R.raw.i18n_pt;
            default: return 0;
        }
    }

    // ---------- testi con chiave ----------

    /**
     * Testo della chiave nella lingua scelta, con {0}, {1}… sostituiti dai valori. Se non è una chiave è una
     * frase italiana (testi dal PC): la si traduce riconoscendola dal dizionario.
     */
    public static String t(String key, Object... args) {
        String s;
        synchronized (I18n.class) {
            s = cur.get(key);
            if (s == null) s = itDict.get(key);
        }
        if (s == null) return KEY.matcher(key).matches() ? key : fromPc(key);
        String out = fill(s, args);
        synchronized (I18n.class) {
            if (args.length == 0) renderedFixed.put(out, new Object[]{key});
            else renderedArgs.put(out, new Object[]{key, args.clone()});
        }
        return out;
    }

    private static String fill(String template, Object[] args) {
        if (args.length == 0) return template;
        StringBuffer sb = new StringBuffer();
        Matcher p = PLACEHOLDER.matcher(template);
        while (p.find()) {
            int n = Integer.parseInt(p.group(1));
            String part = n < args.length ? (args[n] instanceof CharSequence ? fromPc(args[n].toString()) : String.valueOf(args[n])) : p.group();
            p.appendReplacement(sb, Matcher.quoteReplacement(part));
        }
        p.appendTail(sb);
        return sb.toString();
    }

    // ---------- testi italiani che arrivano dal PC ----------

    public static String fromPc(CharSequence text) {
        if (text == null) return null;
        String s = text.toString();
        if (lang == null || "it".equals(lang)) return s;
        String key = s.replaceAll("[ \\t\\r]+", " ").replaceAll(" *\\n *", "\n").trim();
        if (key.isEmpty()) return s;
        String out = lookup(key);
        if (out == null) return s;
        int a = 0, z = s.length();
        while (a < z && Character.isWhitespace(s.charAt(a))) a++;
        while (z > a && Character.isWhitespace(s.charAt(z - 1))) z--;
        return s.substring(0, a) + out + s.substring(z);
    }

    private static synchronized String lookup(String key) {
        String out = exact.get(key);
        if (out != null) return out;
        if (cache.containsKey(key)) return cache.get(key);
        List<Template> candidates = new ArrayList<>();
        if (key.length() >= 3 && byStart.containsKey(key.substring(0, 3))) candidates.addAll(byStart.get(key.substring(0, 3)));
        candidates.addAll(loose);
        for (Template t : candidates) {
            Matcher m = t.re.matcher(key);
            if (!m.matches()) continue;
            Map<Integer, String> parts = new HashMap<>();
            for (int i = 0; i < t.order.length; i++) parts.put(t.order[i], m.group(i + 1));
            StringBuffer sb = new StringBuffer();
            Matcher p = PLACEHOLDER.matcher(t.out);
            while (p.find()) {
                String part = parts.get(Integer.parseInt(p.group(1)));
                p.appendReplacement(sb, Matcher.quoteReplacement(part == null ? p.group() : fromPc(part)));
            }
            p.appendTail(sb);
            out = sb.toString();
            break;
        }
        // "HTTP 409: messaggio del PC": si traduce il messaggio
        if (out == null) {
            Matcher h = HTTP_ERROR.matcher(key);
            if (h.matches()) {
                String msg = fromPc(h.group(2));
                if (!msg.equals(h.group(2))) out = h.group(1) + msg;
            }
        }
        // "/comando", "Etichetta:" e "Suggerimento...": frasi del dizionario con "/", ":" o "..." aggiunti
        if (out == null) {
            Matcher m = AFFIX.matcher(key);
            if (m.matches() && (!m.group(1).isEmpty() || m.group(3) != null) && !m.group(2).isEmpty()) {
                String core = fromPc(m.group(2));
                if (!core.equals(m.group(2))) out = m.group(1) + core + (m.group(3) == null ? "" : m.group(3));
            }
        }
        if (cache.size() > 5000) cache.clear();
        cache.put(key, out);
        return out;
    }

    /** Righe di console e chat: si traducono solo quelle del programma, mai quelle di Minecraft o dei giocatori. */
    private static String trLines(String text, int mode) {
        String[] lines = text.split("\n", -1);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (mode == LINES_CONSOLE) {
                if (line.startsWith("[Pannello")) line = fromPc(line);
                else if (lines.length == 1) line = fromPc(line);  // messaggio unico (es. "console vuota")
            } else {
                Matcher m = CHAT_LINE.matcher(line);
                if (m.matches()) {
                    line = (m.group(1) == null ? "" : m.group(1)) + "Server: " + fromPc(m.group(2));
                } else if (line.startsWith("↳ Sistema: ")) {
                    line = "↳ " + fromPc("Sistema") + ": " + line.substring("↳ Sistema: ".length());
                } else if (lines.length == 1) {
                    line = fromPc(line);
                }
            }
            if (i > 0) sb.append('\n');
            sb.append(line);
        }
        return sb.toString();
    }

    // ---------- viste ----------

    /** Console e chat: tradotte riga per riga. */
    public static void setLineMode(TextView view, int mode) {
        if (view != null) lineModes.put(view, mode);
    }

    /** Scrive i testi delle chiavi dei layout (android:tag) e traduce prima di ogni disegno i testi arrivati dal PC. */
    public static void attach(final View root) {
        if (root == null) return;
        applyTags(root);
        translateTree(root);
        root.getViewTreeObserver().addOnPreDrawListener(new ViewTreeObserver.OnPreDrawListener() {
            @Override
            public boolean onPreDraw() {
                if (lang != null && !"it".equals(lang)) translateTree(root);
                return true;
            }
        });
    }

    /** android:tag="t:chiave h:chiave d:chiave": testo, suggerimento e descrizione dalla chiave ("|valore" per {0}). */
    public static void applyTags(View v) {
        if (v == null) return;
        Object tag = v.getTag();
        if (tag instanceof String && ((String) tag).length() > 2 && ((String) tag).charAt(1) == ':') {
            for (String part : ((String) tag).split(" ")) {
                if (part.length() < 3 || part.charAt(1) != ':') continue;
                String[] kv = part.substring(2).split("\\|");
                Object[] args = new Object[kv.length - 1];
                System.arraycopy(kv, 1, args, 0, args.length);
                String out = t(kv[0], args);
                char what = part.charAt(0);
                if (what == 't' && v instanceof TextView) ((TextView) v).setText(out);
                else if (what == 'h' && v instanceof TextView) ((TextView) v).setHint(out);
                else if (what == 'd') v.setContentDescription(out);
            }
        }
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) applyTags(g.getChildAt(i));
        }
    }

    public static void translateTree(View v) {
        if (lang == null || "it".equals(lang) || v == null) return;
        translateDescription(v);
        if (v instanceof TextView) translateTextView((TextView) v);
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) translateTree(g.getChildAt(i));
        }
    }

    private static void translateDescription(View v) {
        CharSequence d = v.getContentDescription();
        if (d == null || d.length() == 0) return;
        String cur = d.toString();
        String[] rec = descState.get(v);
        if (rec != null && cur.equals(rec[1])) return;
        if (renderedOf(cur) != null) return;
        String out = fromPc(cur);
        if (!out.equals(cur)) { descState.put(v, new String[]{cur, out}); v.setContentDescription(out); }
    }

    private static void translateTextView(TextView tv) {
        CharSequence hint = tv.getHint();
        if (hint != null && hint.length() > 0) {
            String cur = hint.toString();
            String[] rec = hintState.get(tv);
            if ((rec == null || !cur.equals(rec[1])) && renderedOf(cur) == null) {
                String out = fromPc(cur);
                if (!out.equals(cur)) { hintState.put(tv, new String[]{cur, out}); tv.setHint(out); }
            }
        }
        if (tv instanceof EditText) return;  // quello che scrive l'utente non si tocca
        CharSequence cs = tv.getText();
        if (cs == null || cs.length() == 0) return;
        if (cs instanceof Spanned && ((Spanned) cs).getSpans(0, cs.length(), Object.class).length > 0) return;
        String cur = cs.toString();
        String[] rec = textState.get(tv);
        if (rec != null && cur.equals(rec[1])) return;
        if (renderedOf(cur) != null) return;  // già scritto con la sua chiave
        Integer mode = lineModes.get(tv);
        String out = mode == null ? fromPc(cur) : trLines(cur, mode);
        if (out.equals(cur)) { textState.remove(tv); return; }
        textState.put(tv, new String[]{cur, out});
        tv.setText(out);
    }

    // ---------- Toast e finestre ----------

    /** Come Toast.makeText; un testo italiano arrivato dal PC si traduce. */
    public static Toast makeText(Context ctx, CharSequence text, int duration) {
        ensure(ctx);
        return Toast.makeText(ctx, fromPc(text), duration);
    }

    /** Come AlertDialog.Builder; i testi italiani arrivati dal PC si traducono, i layout prendono le loro chiavi. */
    public static class Builder extends AlertDialog.Builder {
        public Builder(Context ctx) { super(ctx); ensure(ctx); }
        public Builder(Context ctx, int theme) { super(ctx, theme); ensure(ctx); }

        @Override public AlertDialog.Builder setTitle(CharSequence t) { return super.setTitle(fromPc(t)); }
        @Override public AlertDialog.Builder setMessage(CharSequence t) { return super.setMessage(fromPc(t)); }
        @Override public AlertDialog.Builder setPositiveButton(CharSequence t, DialogInterface.OnClickListener l) { return super.setPositiveButton(fromPc(t), l); }
        @Override public AlertDialog.Builder setNegativeButton(CharSequence t, DialogInterface.OnClickListener l) { return super.setNegativeButton(fromPc(t), l); }
        @Override public AlertDialog.Builder setNeutralButton(CharSequence t, DialogInterface.OnClickListener l) { return super.setNeutralButton(fromPc(t), l); }

        @Override
        public AlertDialog.Builder setItems(CharSequence[] items, DialogInterface.OnClickListener l) {
            return super.setItems(fromPcAll(items), l);
        }

        @Override
        public AlertDialog.Builder setSingleChoiceItems(CharSequence[] items, int checked, DialogInterface.OnClickListener l) {
            return super.setSingleChoiceItems(fromPcAll(items), checked, l);
        }

        /** Anche le finestre fatte con create() + show() e quelle con una vista propria (setView) si traducono. */
        @Override
        public AlertDialog create() {
            final AlertDialog d = super.create();
            d.setOnShowListener(new DialogInterface.OnShowListener() {
                @Override
                public void onShow(DialogInterface dialog) {
                    if (d.getWindow() != null) attach(d.getWindow().getDecorView());
                }
            });
            return d;
        }
    }

    private static CharSequence[] fromPcAll(CharSequence[] items) {
        if (items == null) return null;
        CharSequence[] out = new CharSequence[items.length];
        for (int i = 0; i < items.length; i++) out[i] = fromPc(items[i]);
        return out;
    }
}
