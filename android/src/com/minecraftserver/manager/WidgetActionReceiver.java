package com.minecraftserver.manager;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Pulsanti Avvia / Riavvia / Ferma del widget. Ricevitore non esportato (android:exported="false"): ci arrivano solo
 * i PendingIntent del widget. Prima i comandi passavano dal ricevitore del widget, che deve essere esportato, e
 * qualunque app installata sul telefono poteva accendere, spegnere o riavviare il server col token salvato.
 */
public class WidgetActionReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent != null ? intent.getAction() : null;
        if (action == null) return;
        PcTls.install(context);
        if (ServerWidgetProvider.ACTION_WIDGET_START.equals(action)) {
            ServerWidgetProvider.sendServerActionAsync(context, "/api/server/start", "POST", "{}");
        } else if (ServerWidgetProvider.ACTION_WIDGET_RESTART.equals(action)) {
            ServerWidgetProvider.sendServerActionAsync(context, "/api/server/restart", "POST", "{\"countdown\":0,\"reason\":\"Riavvio rapido da Widget Android\"}");
        } else if (ServerWidgetProvider.ACTION_WIDGET_STOP.equals(action)) {
            ServerWidgetProvider.sendServerActionAsync(context, "/api/server/stop", "POST", "{\"force\":false,\"reason\":\"Arresto rapido da Widget Android\"}");
        }
    }
}
