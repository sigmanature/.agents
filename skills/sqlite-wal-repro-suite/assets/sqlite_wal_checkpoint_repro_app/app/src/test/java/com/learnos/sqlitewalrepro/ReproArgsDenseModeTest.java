package com.learnos.sqlitewalrepro;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.os.Bundle;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;

@RunWith(RobolectricTestRunner.class)
public class ReproArgsDenseModeTest {
    @Test
    public void defaultsRemainBackwardCompatible() {
        ReproArgs args = ReproArgs.from(new Bundle());

        assertEquals(1, args.writers);
        assertEquals(0, args.readers);
        assertEquals(200, args.updatesPerTxn);
        assertFalse(args.checkpointThread);
        assertEquals(1, args.checkpointEveryIters);
        assertEquals(1, args.checkpointBurst);
        assertEquals(0, args.checkpointSleepMs);
        assertEquals(100, args.updatePct);
        assertEquals(0, args.insertPct);
        assertEquals(0, args.replacePct);
        assertEquals(args.rows, args.maxRows);
    }

    @Test
    public void denseCheckpointArgsAreParsedAndClamped() {
        Bundle bundle = new Bundle();
        bundle.putString("checkpointThread", "1");
        bundle.putString("checkpointEveryIters", "0");
        bundle.putString("checkpointBurst", "-9");
        bundle.putString("checkpointSleepMs", "-5");

        ReproArgs args = ReproArgs.from(bundle);

        assertTrue(args.checkpointThread);
        assertEquals(1, args.checkpointEveryIters);
        assertEquals(1, args.checkpointBurst);
        assertEquals(0, args.checkpointSleepMs);
    }

    @Test
    public void mixedWriteArgsAreParsedAndClamped() {
        Bundle bundle = new Bundle();
        bundle.putString("rows", "256");
        bundle.putString("maxRows", "128");
        bundle.putString("updatePct", "60");
        bundle.putString("insertPct", "30");
        bundle.putString("replacePct", "10");

        ReproArgs args = ReproArgs.from(bundle);

        assertEquals(60, args.updatePct);
        assertEquals(30, args.insertPct);
        assertEquals(10, args.replacePct);
        assertEquals(256, args.maxRows);
    }

    @Test
    public void startGateAndSnapshotArgsAreParsed() {
        Bundle bundle = new Bundle();
        bundle.putString("startGatePath", "/sdcard/Android/data/com.learnos.sqlitewalrepro/files/gates/run.go");
        bundle.putString("startGateTimeoutMs", "-1");
        bundle.putString("snapshotOnDetect", "0");

        ReproArgs args = ReproArgs.from(bundle);

        assertEquals("/sdcard/Android/data/com.learnos.sqlitewalrepro/files/gates/run.go",
                args.startGatePath);
        assertEquals(0L, args.startGateTimeoutMs);
        assertFalse(args.snapshotOnDetect);
    }
}
