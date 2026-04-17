package com.learnos.sqlitewalrepro;

import android.content.Context;
import android.database.Cursor;
import android.database.SQLException;
import android.database.sqlite.SQLiteDatabase;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.zip.CRC32;

@RunWith(AndroidJUnit4.class)
public class WalCheckpointReproTest {
    private static final String TAG = "WalRepro";

    @Test
    public void runWalCheckpointLoop() throws Exception {
        Bundle args = InstrumentationRegistry.getArguments();
        ReproArgs cfg = ReproArgs.from(args);

        Context ctx = InstrumentationRegistry.getInstrumentation().getTargetContext();
        String runId = String.format(Locale.US, "run_%d", System.currentTimeMillis());
        final int openMode = Context.MODE_PRIVATE | Context.MODE_ENABLE_WRITE_AHEAD_LOGGING;

        File dbFile = ctx.getDatabasePath(cfg.dbName);
        File walFile = new File(dbFile.getAbsolutePath() + "-wal");
        File shmFile = new File(dbFile.getAbsolutePath() + "-shm");

        logKv("START", runId, 0, "db", dbFile.getAbsolutePath(),
                "seconds", cfg.seconds,
                "writers", cfg.writers,
                "readers", cfg.readers,
                "updatesPerTxn", cfg.updatesPerTxn,
                "blobBytes", cfg.blobBytes,
                "rows", cfg.rows,
                "checkpoint", cfg.checkpointMode,
                "synchronous", cfg.synchronous);

        // Prepare schema on a single connection first (keeps init deterministic).
        SQLiteDatabase initDb = ctx.openOrCreateDatabase(cfg.dbName, openMode, null);
        // Enable WAL before any worker threads open their own connections.
        enableWal(initDb, cfg, runId);
        configureDb(initDb, cfg);
        initSchemaAndSeed(initDb, cfg);
        initDb.close();

        final long endNs = SystemClock.elapsedRealtimeNanos() + cfg.seconds * 1_000_000_000L;
        final AtomicInteger globalIter = new AtomicInteger(0);
        final AtomicBoolean stop = new AtomicBoolean(false);
        final AtomicReference<Failure> failure = new AtomicReference<>(null);

        final CountDownLatch started = new CountDownLatch(cfg.writers + cfg.readers + 1);
        final List<Thread> threads = new ArrayList<>();

        // Writers: each uses its own SQLite connection.
        for (int wi = 0; wi < cfg.writers; wi++) {
            final int writerId = wi;
            Thread t = new Thread(() -> {
                SQLiteDatabase db = null;
                try {
                    db = ctx.openOrCreateDatabase(cfg.dbName, openMode, null);
                    configureDb(db, cfg);
                    started.countDown();
                    while (!stop.get() && SystemClock.elapsedRealtimeNanos() < endNs) {
                        int iter = globalIter.incrementAndGet();
                        runOneIteration(db, cfg, iter);
                        if (iter % 50 == 0 && writerId == 0) {
                            logKv("PROGRESS", runId, iter,
                                    "db_size", safeLen(dbFile),
                                    "wal_size", safeLen(walFile),
                                    "shm_size", safeLen(shmFile));
                        }
                    }
                } catch (Throwable t1) {
                    failure.compareAndSet(null, new Failure(globalIter.get(),
                            "writer_" + writerId + ":" + t1.getClass().getSimpleName() + ":" + t1.getMessage(), t1));
                    stop.set(true);
                } finally {
                    if (db != null) db.close();
                }
            }, "wal-writer-" + wi);
            t.start();
            threads.add(t);
        }

        // Readers: optional background read pressure.
        for (int ri = 0; ri < cfg.readers; ri++) {
            final int readerId = ri;
            Thread t = new Thread(() -> {
                SQLiteDatabase db = null;
                try {
                    db = ctx.openOrCreateDatabase(cfg.dbName, openMode, null);
                    configureDb(db, cfg);
                    started.countDown();
                    XorShift64 rng = new XorShift64(((long) cfg.seed << 16) ^ readerId);
                    while (!stop.get() && SystemClock.elapsedRealtimeNanos() < endNs) {
                        int id = 1 + (int) (Math.floorMod(rng.nextLong(), cfg.rows));
                        Cursor c = db.rawQuery("SELECT crc FROM t WHERE id=?",
                                new String[]{String.valueOf(id)});
                        if (c.moveToFirst()) {
                            c.getInt(0);
                        }
                        c.close();
                        SystemClock.sleep(5);
                    }
                } catch (Throwable t1) {
                    failure.compareAndSet(null, new Failure(globalIter.get(),
                            "reader_" + readerId + ":" + t1.getClass().getSimpleName() + ":" + t1.getMessage(), t1));
                    stop.set(true);
                } finally {
                    if (db != null) db.close();
                }
            }, "wal-reader-" + ri);
            t.start();
            threads.add(t);
        }

        // Checker: fail-fast detection to shrink first-failure window.
        Thread checker = new Thread(() -> {
            SQLiteDatabase db = null;
            try {
                db = ctx.openOrCreateDatabase(cfg.dbName, openMode, null);
                configureDb(db, cfg);
                started.countDown();
                int lastCheckedIter = -1;
                while (!stop.get() && SystemClock.elapsedRealtimeNanos() < endNs) {
                    int iter = globalIter.get();
                    if (cfg.checkEvery > 0 && iter > 0 &&
                            iter != lastCheckedIter &&
                            (iter % cfg.checkEvery == 0)) {
                        lastCheckedIter = iter;
                        String qc = quickCheck(db);
                        if (!"ok".equalsIgnoreCase(qc)) {
                            logKv("DETECT", runId, iter,
                                    "detector", "quick_check",
                                    "qc", qc);
                            failure.compareAndSet(null, new Failure(iter, "quick_check:" + qc, null));
                            stop.set(true);
                            break;
                        }
                        if (cfg.patternSample > 0) {
                            String pat = patternCheck(db, cfg, iter);
                            if (pat != null) {
                                logKv("DETECT", runId, iter,
                                        "detector", "pattern",
                                        "detail", pat);
                                failure.compareAndSet(null, new Failure(iter, "pattern:" + pat, null));
                                stop.set(true);
                                break;
                            }
                        }
                    }
                    SystemClock.sleep(20);
                }
            } catch (Throwable t1) {
                failure.compareAndSet(null, new Failure(globalIter.get(),
                        "checker:" + t1.getClass().getSimpleName() + ":" + t1.getMessage(), t1));
                stop.set(true);
            } finally {
                if (db != null) db.close();
            }
        }, "wal-checker");
        checker.start();
        threads.add(checker);

        // Wait all threads started (best-effort).
        started.await(10, TimeUnit.SECONDS);

        // Main loop: wait for stop or timeout.
        while (!stop.get() && SystemClock.elapsedRealtimeNanos() < endNs) {
            SystemClock.sleep(50);
        }
        stop.set(true);

        // Join workers.
        for (Thread t : threads) {
            t.join(TimeUnit.SECONDS.toMillis(10));
        }

        Failure f = failure.get();
        int iter = globalIter.get();
        if (f != null) {
            logKv("FAIL", runId, f.iter, "reason", f.reason);
            snapshotArtifacts(ctx, runId, f.iter, dbFile, walFile, shmFile);
            if (f.throwable != null) {
                throw new AssertionError("workload failed: " + f.reason, f.throwable);
            }
            throw new AssertionError("workload failed: " + f.reason);
        }

        logKv("DONE", runId, iter, "failed", 0);
    }

    private static void configureDb(SQLiteDatabase db, ReproArgs cfg) {
        // Keep the syscall sequence stable and "hard" on durability.
        // NOTE: Some PRAGMAs return rows; prefer rawQuery(...).close().
        // journal_mode is configured once via enableWal().
        db.rawQuery("PRAGMA wal_autocheckpoint=0", null).close();
        db.rawQuery("PRAGMA mmap_size=0", null).close();
        db.rawQuery("PRAGMA synchronous=" + cfg.synchronous, null).close();
    }

    private static void enableWal(SQLiteDatabase db, ReproArgs cfg, String runId) {
        // Android has special handling around WAL; using the framework helper
        // makes behavior closer to real apps that enable WAL explicitly.
        boolean enabled = db.enableWriteAheadLogging();
        String mode1 = pragmaString(db, "PRAGMA journal_mode=WAL");
        String mode2 = pragmaString(db, "PRAGMA journal_mode");
        logKv("WAL", runId, 0,
                "enableWAL", enabled ? 1 : 0,
                "journal_mode_set", mode1,
                "journal_mode_now", mode2,
                "synchronous", cfg.synchronous);
    }

    private static String pragmaString(SQLiteDatabase db, String sql) {
        Cursor c = db.rawQuery(sql, null);
        try {
            if (!c.moveToFirst()) {
                return "no_row";
            }
            String v = c.getString(0);
            return (v == null) ? "null" : v;
        } finally {
            c.close();
        }
    }

    private static void initSchemaAndSeed(SQLiteDatabase db, ReproArgs cfg) {
        db.execSQL("CREATE TABLE IF NOT EXISTS meta(k INTEGER PRIMARY KEY, v INTEGER)");
        db.execSQL("INSERT OR IGNORE INTO meta(k,v) VALUES(1,0)");
        db.execSQL("CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY, gen INTEGER, payload BLOB, crc INTEGER)");

        // Seed rows if needed.
        Cursor c = db.rawQuery("SELECT COUNT(*) FROM t", null);
        int existing = 0;
        if (c.moveToFirst()) {
            existing = c.getInt(0);
        }
        c.close();
        if (existing >= cfg.rows) {
            return;
        }

        db.beginTransaction();
        try {
            for (int id = 1; id <= cfg.rows; id++) {
                int gen = 0;
                byte[] payload = makePayload(cfg, id, gen);
                int crc = crc32(payload);
                db.execSQL("INSERT OR REPLACE INTO t(id, gen, payload, crc) VALUES(?,?,?,?)",
                        new Object[]{id, gen, payload, crc});
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    private static void runOneIteration(SQLiteDatabase db, ReproArgs cfg, int iter) {
        db.beginTransaction();
        try {
            db.execSQL("UPDATE meta SET v=v+1 WHERE k=1");

            XorShift64 rng = new XorShift64(((long) cfg.seed << 32) ^ iter);
            for (int i = 0; i < cfg.updatesPerTxn; i++) {
                int id = 1 + (int) (Math.floorMod(rng.nextLong(), cfg.rows));
                int gen = iter; // deterministic generation marker
                byte[] payload = makePayload(cfg, id, gen);
                int crc = crc32(payload);
                db.execSQL("UPDATE t SET gen=?, payload=?, crc=? WHERE id=?",
                        new Object[]{gen, payload, crc, id});
            }

            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }

        // Explicit checkpoint (TRUNCATE recommended to exercise truncate path).
        db.rawQuery("PRAGMA wal_checkpoint(" + cfg.checkpointMode + ")", null).close();
    }

    private static String quickCheck(SQLiteDatabase db) {
        Cursor c = db.rawQuery("PRAGMA quick_check", null);
        try {
            if (!c.moveToFirst()) {
                return "no_row";
            }
            return c.getString(0);
        } finally {
            c.close();
        }
    }

    private static String patternCheck(SQLiteDatabase db, ReproArgs cfg, int iter) {
        XorShift64 rng = new XorShift64(((long) cfg.seed << 48) ^ (iter * 1315423911L));
        for (int i = 0; i < cfg.patternSample; i++) {
            int id = 1 + (int) (Math.floorMod(rng.nextLong(), cfg.rows));
            Cursor c = db.rawQuery("SELECT gen, crc FROM t WHERE id=?", new String[]{String.valueOf(id)});
            try {
                if (!c.moveToFirst()) {
                    return "missing_row id=" + id;
                }
                int gen = c.getInt(0);
                int crcStored = c.getInt(1);
                byte[] expected = makePayload(cfg, id, gen);
                int crcExpected = crc32(expected);
                if (crcStored != crcExpected) {
                    return "crc_mismatch id=" + id + " gen=" + gen +
                            " crcStored=" + crcStored + " crcExpected=" + crcExpected;
                }
            } finally {
                c.close();
            }
        }
        return null;
    }

    private static byte[] makePayload(ReproArgs cfg, int id, int gen) {
        byte[] out = new byte[cfg.blobBytes];
        long s = ((long) id << 32) ^ (gen & 0xffffffffL) ^ (cfg.seed * 0x9E3779B97F4A7C15L);
        XorShift64 rng = new XorShift64(s);
        int off = 0;
        while (off < out.length) {
            long v = rng.nextLong();
            int n = Math.min(8, out.length - off);
            for (int i = 0; i < n; i++) {
                out[off + i] = (byte) (v & 0xff);
                v >>>= 8;
            }
            off += n;
        }
        // Add a small ASCII header to aid manual inspection.
        byte[] hdr = ("ID=" + id + " GEN=" + gen + "\n").getBytes(StandardCharsets.US_ASCII);
        System.arraycopy(hdr, 0, out, 0, Math.min(hdr.length, out.length));
        return out;
    }

    private static int crc32(byte[] payload) {
        CRC32 crc = new CRC32();
        crc.update(payload);
        return (int) crc.getValue();
    }

    private static void snapshotArtifacts(Context ctx, String runId, int iter,
                                          File db, File wal, File shm) {
        File outDir = new File(ctx.getExternalFilesDir(null),
                "wal_repro_artifacts/" + runId + "/iter_" + iter);
        if (!outDir.mkdirs() && !outDir.isDirectory()) {
            Log.e(TAG, "snapshot mkdir failed path=" + outDir.getAbsolutePath());
            return;
        }
        copyIfExists(db, new File(outDir, "repro.db"));
        copyIfExists(wal, new File(outDir, "repro.db-wal"));
        copyIfExists(shm, new File(outDir, "repro.db-shm"));
        logKv("SNAPSHOT", runId, iter, "out", outDir.getAbsolutePath(),
                "db_size", safeLen(db), "wal_size", safeLen(wal), "shm_size", safeLen(shm));
    }

    private static final class Failure {
        final int iter;
        final String reason;
        final Throwable throwable;

        Failure(int iter, String reason, Throwable throwable) {
            this.iter = iter;
            this.reason = reason;
            this.throwable = throwable;
        }
    }

    private static void copyIfExists(File src, File dst) {
        if (src == null || !src.exists()) {
            return;
        }
        try (FileInputStream in = new FileInputStream(src);
             FileOutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[1 << 20];
            int r;
            while ((r = in.read(buf)) >= 0) {
                out.write(buf, 0, r);
            }
            out.getFD().sync();
        } catch (IOException e) {
            Log.e(TAG, "copy failed src=" + src.getAbsolutePath() + " dst=" + dst.getAbsolutePath(), e);
        }
    }

    private static long safeLen(File f) {
        try {
            return (f != null && f.exists()) ? f.length() : -1;
        } catch (Exception ignored) {
            return -2;
        }
    }

    private static void logKv(String phase, String runId, int iter, Object... kv) {
        long tsNs = SystemClock.elapsedRealtimeNanos();
        long tsMs = System.currentTimeMillis();
        long tid = android.os.Process.myTid();
        StringBuilder sb = new StringBuilder(256);
        sb.append("phase=").append(phase)
                .append(" run=").append(runId)
                .append(" iter=").append(iter)
                .append(" ts_mono_ns=").append(tsNs)
                .append(" ts_wall_ms=").append(tsMs)
                .append(" tid=").append(tid);
        for (int i = 0; i + 1 < kv.length; i += 2) {
            sb.append(' ').append(kv[i]).append('=').append(kv[i + 1]);
        }
        Log.i(TAG, sb.toString());
    }
}
