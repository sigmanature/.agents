package com.learnos.sqlitewalrepro;

import android.os.Bundle;

public final class ReproArgs {
    public final String dbName;
    public final long seconds;
    public final int writers;
    public final int readers;
    public final int updatesPerTxn;
    public final int blobBytes;
    public final int rows;
    public final int checkEvery;
    public final int patternSample;
    public final String checkpointMode;
    public final String synchronous;
    public final long seed;

    private ReproArgs(String dbName,
                      long seconds,
                      int writers,
                      int readers,
                      int updatesPerTxn,
                      int blobBytes,
                      int rows,
                      int checkEvery,
                      int patternSample,
                      String checkpointMode,
                      String synchronous,
                      long seed) {
        this.dbName = dbName;
        this.seconds = seconds;
        this.writers = writers;
        this.readers = readers;
        this.updatesPerTxn = updatesPerTxn;
        this.blobBytes = blobBytes;
        this.rows = rows;
        this.checkEvery = checkEvery;
        this.patternSample = patternSample;
        this.checkpointMode = checkpointMode;
        this.synchronous = synchronous;
        this.seed = seed;
    }

    public static ReproArgs from(Bundle args) {
        String dbName = getString(args, "dbName", "repro.db");
        long seconds = getLong(args, "seconds", 300);
        int writers = Math.max(1, getInt(args, "writers", 1));
        int readers = Math.max(0, getInt(args, "readers", 0));
        int updatesPerTxn = getInt(args, "updatesPerTxn", 200);
        int blobBytes = Math.max(16, getInt(args, "blobBytes", 4096));
        int rows = Math.max(1, getInt(args, "rows", 2048));
        int checkEvery = Math.max(0, getInt(args, "checkEvery", 1));
        int patternSample = Math.max(0, getInt(args, "patternSample", 10));
        String checkpointMode = getString(args, "checkpoint", "TRUNCATE");
        String synchronous = getString(args, "synchronous", "FULL");
        long seed = getLong(args, "seed", 0xC0FFEE);

        return new ReproArgs(dbName, seconds, writers, readers, updatesPerTxn,
                blobBytes, rows, checkEvery, patternSample, checkpointMode,
                synchronous, seed);
    }

    private static String getString(Bundle args, String key, String def) {
        if (args == null) return def;
        String v = args.getString(key);
        return v != null ? v : def;
    }

    private static int getInt(Bundle args, String key, int def) {
        if (args == null) return def;
        String v = args.getString(key);
        if (v == null) return def;
        try {
            return Integer.parseInt(v);
        } catch (NumberFormatException ignored) {
            return def;
        }
    }

    private static long getLong(Bundle args, String key, long def) {
        if (args == null) return def;
        String v = args.getString(key);
        if (v == null) return def;
        try {
            return Long.parseLong(v);
        } catch (NumberFormatException ignored) {
            return def;
        }
    }
}
