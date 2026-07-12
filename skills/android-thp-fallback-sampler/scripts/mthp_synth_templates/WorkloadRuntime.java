package com.zzhao.mthp.synthetic;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.widget.TextView;
import android.app.Activity;
import android.app.Service;
import android.os.IBinder;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

public final class WorkloadRuntime {
    private static final String TAG = "ZZMthpSynth";
    private static final AtomicBoolean started = new AtomicBoolean(false);
    private static final List<byte[]> javaKeepAlive = new ArrayList<>();
    private static volatile JSONObject config;

    static {
        System.loadLibrary("mthpwork");
    }

    private WorkloadRuntime() {}

    public static String start(Context context, int processIndex, String processLabel) {
        try {
            if (!started.compareAndSet(false, true)) {
                return nativeStatus();
            }
            String json = readAsset(context, "profile.json");
            config = new JSONObject(json);
            startJavaChurn(config, processIndex);
            String result = nativeStart(
                    json,
                    nativeLibrarySearchPath(context),
                    context.getFilesDir().getAbsolutePath(),
                    processIndex,
                    processLabel == null ? "unknown" : processLabel);
            Log.i(TAG, "started processIndex=" + processIndex + " label=" + processLabel + " result=" + result);
            return result;
        } catch (Throwable t) {
            Log.e(TAG, "start failed", t);
            return "ERROR " + t;
        }
    }

    public static void startPeerServices(Context context) {
        try {
            JSONObject cfg = config;
            if (cfg == null) {
                cfg = new JSONObject(readAsset(context, "profile.json"));
                config = cfg;
            }
            int processCount = Math.max(1, Math.min(4, cfg.optInt("process_count", 1)));
            Class<?>[] services = new Class<?>[] {
                    WorkerService1.class, WorkerService2.class, WorkerService3.class
            };
            for (int i = 1; i < processCount; i++) {
                Intent intent = new Intent(context, services[i - 1]);
                intent.putExtra("process_index", i);
                context.startService(intent);
            }
        } catch (Throwable t) {
            Log.e(TAG, "startPeerServices failed", t);
        }
    }

    private static void startJavaChurn(JSONObject cfg, int processIndex) {
        int javaLiveMb = cfg.optInt("java_live_mb", 0);
        int objectKb = Math.max(4, cfg.optInt("java_object_kb", 64));
        int churnMs = Math.max(100, cfg.optInt("java_churn_ms", 1000));
        int gcPeriodMs = Math.max(0, cfg.optInt("gc_period_ms", 0));
        if (processIndex > 0) {
            javaLiveMb = Math.max(0, javaLiveMb / 3);
        }
        long requestedBytes = (long) javaLiveMb * 1024L * 1024L;
        long heapCapBytes = Math.max(16L * 1024L * 1024L, Runtime.getRuntime().maxMemory() * 3L / 4L);
        final int targetBytes = (int) Math.min((long) Integer.MAX_VALUE, Math.min(requestedBytes, heapCapBytes));
        if (requestedBytes > targetBytes) {
            Log.i(TAG, "cap java_live_mb from " + javaLiveMb + " to " + (targetBytes / 1024 / 1024) + " due to app heap limit");
        }
        final int allocBytes = objectKb * 1024;
        final int sleepMs = churnMs;
        final int gcMs = gcPeriodMs;
        Thread t = new Thread(() -> {
            long lastGc = System.currentTimeMillis();
            int salt = 1;
            while (true) {
                synchronized (javaKeepAlive) {
                    try {
                        while (totalBytesLocked() < targetBytes) {
                            byte[] arr = new byte[allocBytes];
                            for (int i = 0; i < arr.length; i += 4096) {
                                arr[i] = (byte) (salt++);
                            }
                            javaKeepAlive.add(arr);
                        }
                    } catch (OutOfMemoryError oom) {
                        Log.w(TAG, "java churn hit heap limit; keeping " + (totalBytesLocked() / 1024 / 1024) + " MiB", oom);
                        return;
                    }
                    int drop = javaKeepAlive.size() / 8;
                    for (int i = 0; i < drop && !javaKeepAlive.isEmpty(); i++) {
                        javaKeepAlive.remove(0);
                    }
                }
                if (gcMs > 0 && System.currentTimeMillis() - lastGc > gcMs) {
                    System.gc();
                    lastGc = System.currentTimeMillis();
                }
                try {
                    Thread.sleep(sleepMs);
                } catch (InterruptedException ignored) {
                }
            }
        }, "mthp-java-churn-" + processIndex);
        t.setDaemon(true);
        t.start();
    }

    private static String nativeLibrarySearchPath(Context context) {
        String[] abis = Build.SUPPORTED_ABIS;
        String abi = abis.length > 0 ? abis[0] : "x86_64";
        return context.getApplicationInfo().sourceDir + "!/lib/" + abi;
    }

    private static int totalBytesLocked() {
        long total = 0;
        for (byte[] arr : javaKeepAlive) {
            total += arr.length;
        }
        return total > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int) total;
    }

    private static String readAsset(Context context, String name) throws Exception {
        try (InputStream in = context.getAssets().open(name);
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) >= 0) {
                out.write(buf, 0, n);
            }
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static native String nativeStart(String json, String nativeLibraryDir, String filesDir, int processIndex, String processLabel);
    private static native String nativeStatus();

    public static class MainActivity extends Activity {
        @Override
        protected void onCreate(Bundle state) {
            super.onCreate(state);
            TextView tv = new TextView(this);
            tv.setTextSize(14);
            tv.setText("MTHP synthetic workload starting...\n" + getPackageName());
            setContentView(tv);
            new Thread(() -> {
                String result = WorkloadRuntime.start(this, 0, getProcessNameCompat(this));
                WorkloadRuntime.startPeerServices(this);
                new Handler(Looper.getMainLooper()).post(() -> tv.setText(result + "\n" + nativeStatus()));
            }, "mthp-main-start").start();
        }
    }

    public static class BaseWorkerService extends Service {
        protected int serviceIndex() { return 1; }

        @Override
        public void onCreate() {
            super.onCreate();
            WorkloadRuntime.start(this, serviceIndex(), getProcessNameCompat(this));
        }

        @Override
        public int onStartCommand(Intent intent, int flags, int startId) {
            WorkloadRuntime.start(this, intent == null ? serviceIndex() : intent.getIntExtra("process_index", serviceIndex()), getProcessNameCompat(this));
            return START_STICKY;
        }

        @Override
        public IBinder onBind(Intent intent) { return null; }
    }

    public static class WorkerService1 extends BaseWorkerService { protected int serviceIndex() { return 1; } }
    public static class WorkerService2 extends BaseWorkerService { protected int serviceIndex() { return 2; } }
    public static class WorkerService3 extends BaseWorkerService { protected int serviceIndex() { return 3; } }

    private static String getProcessNameCompat(Context context) {
        int pid = android.os.Process.myPid();
        ActivityManager am = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
        if (am != null) {
            List<ActivityManager.RunningAppProcessInfo> processes = am.getRunningAppProcesses();
            if (processes != null) {
                for (ActivityManager.RunningAppProcessInfo info : processes) {
                    if (info.pid == pid) {
                        return info.processName;
                    }
                }
            }
        }
        return context.getPackageName();
    }
}
