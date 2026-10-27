package com.minecraftserver.manager;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

public class AppUpdateProvider extends ContentProvider {
    @Override
    public boolean onCreate() {
        return true;
    }

    /** L'unico aggiornamento scaricato: cache/update.apk (MainActivity.tryDownloadApk). */
    private File getApkFile() {
        if (getContext() == null) return null;
        return new File(getContext().getCacheDir(), "update.apk");
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File file = getApkFile();
        if (file != null && file.exists()) {
            return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
        }
        throw new FileNotFoundException("File non trovato: " + uri.getPath());
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        File file = getApkFile();
        if (projection == null) {
            projection = new String[] { OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE };
        }
        MatrixCursor cursor = new MatrixCursor(projection, 1);
        MatrixCursor.RowBuilder row = cursor.newRow();
        for (String col : projection) {
            if (OpenableColumns.DISPLAY_NAME.equals(col)) {
                row.add("Controllo_Server_Minecraft.apk");
            } else if (OpenableColumns.SIZE.equals(col)) {
                row.add(file != null && file.exists() ? file.length() : 0);
            } else {
                row.add(null);
            }
        }
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) { return null; }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) { return 0; }
}
