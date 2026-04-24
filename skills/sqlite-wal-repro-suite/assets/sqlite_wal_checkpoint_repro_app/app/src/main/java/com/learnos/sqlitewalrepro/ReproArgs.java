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
    public final int maxRows;
    public final int updatePct;
    public final int insertPct;
    public final int replacePct;
    public final String checkpointMode;
    public final String synchronous;
    public final long seed;
    public final boolean checkpointThread;
    public final int checkpointEveryIters;
    public final int checkpointBurst;
    public final int checkpointSleepMs;
    public final String startGatePath;
    public final long startGateTimeoutMs;
    public final boolean snapshotOnDetect;

    private ReproArgs(String dbName,
                      long seconds,
                      int writers,
                      int readers,
                      int updatesPerTxn,
                      int blobBytes,
                      int rows,
                      int checkEvery,
                      int patternSample,
                      int maxRows,
                      int updatePct,
                      int insertPct,
                      int replacePct,
                      String checkpointMode,
                      String synchronous,
                      long seed,
                      boolean checkpointThread,
                      int checkpointEveryIters,
                      int checkpointBurst,
                      int checkpointSleepMs,
                      String startGatePath,
                      long startGateTimeoutMs,
                      boolean snapshotOnDetect) {
        this.dbName = dbName;
        this.seconds = seconds;
        this.writers = writers;
        this.readers = readers;
        this.updatesPerTxn = updatesPerTxn;
        this.blobBytes = blobBytes;
        this.rows = rows;
        this.checkEvery = checkEvery;
        this.patternSample = patternSample;
        this.maxRows = maxRows;
        this.updatePct = updatePct;
        this.insertPct = insertPct;
        this.replacePct = replacePct;
        this.checkpointMode = checkpointMode;
        this.synchronous = synchronous;
        this.seed = seed;
        this.checkpointThread = checkpointThread;
        this.checkpointEveryIters = checkpointEveryIters;
        this.checkpointBurst = checkpointBurst;
        this.checkpointSleepMs = checkpointSleepMs;
        this.startGatePath = startGatePath;
        this.startGateTimeoutMs = startGateTimeoutMs;
        this.snapshotOnDetect = snapshotOnDetect;
    }

    public static ReproArgs from(Bundle args) {
        String dbName = getString(args, "dbName", "repro.db");
        long seconds = getLong(args, "seconds", 300);
        int writers = Math.max(1, getInt(args, "writers", 1));
        int readers = Math.max(0, getInt(args, "readers", 0));
        int updatesPerTxn = Math.max(1, getInt(args, "updatesPerTxn", 200));
        int blobBytes = Math.max(16, getInt(args, "blobBytes", 4096));
        int rows = Math.max(1, getInt(args, "rows", 2048));
        int checkEvery = Math.max(0, getInt(args, "checkEvery", 1));
        int patternSample = Math.max(0, getInt(args, "patternSample", 10));
        int maxRows = Math.max(rows, getInt(args, "maxRows", rows));
        int updatePct = Math.max(0, getInt(args, "updatePct", 100));
        int insertPct = Math.max(0, getInt(args, "insertPct", 0));
        int replacePct = Math.max(0, getInt(args, "replacePct", 0));
        if (updatePct + insertPct + replacePct == 0) {
            updatePct = 100;
        }
        String checkpointMode = getString(args, "checkpoint", "TRUNCATE");
        String synchronous = getString(args, "synchronous", "FULL");
        long seed = getLong(args, "seed", 0xC0FFEE);
        boolean checkpointThread = getBoolean(args, "checkpointThread", false);
        int checkpointEveryIters = Math.max(1, getInt(args, "checkpointEveryIters", 1));
        int checkpointBurst = Math.max(1, getInt(args, "checkpointBurst", 1));
        int checkpointSleepMs = Math.max(0, getInt(args, "checkpointSleepMs", 0));
        String startGatePath = getString(args, "startGatePath", "");
        long startGateTimeoutMs = Math.max(0L, getLong(args, "startGateTimeoutMs", 30_000L));
        boolean snapshotOnDetect = getBoolean(args, "snapshotOnDetect", true);

        return new ReproArgs(dbName, seconds, writers, readers, updatesPerTxn,
                blobBytes, rows, checkEvery, patternSample, maxRows,
                updatePct, insertPct, replacePct, checkpointMode,
                synchronous, seed, checkpointThread, checkpointEveryIters,
                checkpointBurst, checkpointSleepMs, startGatePath,
                startGateTimeoutMs, snapshotOnDetect);
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

    private static boolean getBoolean(Bundle args, String key, boolean def) {
        if (args == null) return def;
        String v = args.getString(key);
        if (v == null) return def;
        if ("1".equals(v) || "true".equalsIgnoreCase(v) || "y".equalsIgnoreCase(v)) {
            return true;
        }
        if ("0".equals(v) || "false".equalsIgnoreCase(v) || "n".equalsIgnoreCase(v)) {
            return false;
        }
        return def;
    }
}
