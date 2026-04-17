# Root adb: locate an app’s SQLite DB files (including WAL/SHM)

This is a **copy/paste** recipe for rooted Android devices (Magisk `su`).

If you need quoting help (`su -c`, `find (...)`, pipes, redirects), read:
- `references/adb_execution_reference.md`

## 0) Identify the package name (`<pkg>`)

List third-party apps:

```bash
adb shell pm list packages -3 | sed 's/^package://'
```

Search likely names (example keywords):

```bash
adb shell pm list packages | grep -iE 'douyin|aweme|huoshan|ugc\\.live|ucmobile|uc'
```

Confirm the package exists:

```bash
adb shell pm path <pkg.name>
```

Get the current foreground app/activity:

```bash
adb shell dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity' | head
```

## 1) Quick list: the canonical DB directory

Most apps keep SQLite DBs here:
- `/data/user/0/<pkg>/databases/` (same as `/data/data/<pkg>/databases/` on most devices)
- Some apps/components may use **DE (Direct Boot)** storage:
  - `/data/user_de/0/<pkg>/databases/`

```bash
PKG=<pkg.name>
adb shell su -c "ls -al /data/user/0/$PKG/databases 2>/dev/null || ls -al /data/user_de/0/$PKG/databases 2>/dev/null || ls -al /data/data/$PKG/databases 2>/dev/null"
```

## 2) Enumerate likely SQLite files (DB + WAL/SHM/journal)

This is intentionally **portable** (no `find -printf`).

```bash
PKG=<pkg.name>
adb shell su -c 'sh -c "
  base=/data/user/0/'\"$PKG\"';
  for d in \"\$base/databases\" \"\$base/app_webview\" \"\$base/app_chrome\"; do
    [ -d \"\$d\" ] || continue;
    echo ==== \"\$d\" ====;
    find \"\$d\" -maxdepth 5 -type f \\( \
      -name \"*.db\" -o -name \"*.sqlite\" -o -name \"*.sqlite3\" -o \
      -name \"*-wal\" -o -name \"*-shm\" -o -name \"*-journal\" -o \
      -name \"*.db-journal\" -o -name \"*.db-wal\" -o -name \"*.db-shm\" \
    \\) -exec ls -l {} \\; 2>/dev/null;
  done
"'
```

Notes:
- Many Android apps use **WAL mode**, so you’ll commonly see `xxx.db`, `xxx.db-wal`, `xxx.db-shm`.
- If you see frequent `*-wal` size changes, that’s a strong hint of **ongoing write transactions**.
- WebView-based apps can write to Chromium sqlite stores under `app_webview/Default/...` (cookies/history/etc.).

## 3) “Largest DB-ish files” quick view

```bash
PKG=<pkg.name>
adb shell su -c "du -a /data/user/0/$PKG/databases 2>/dev/null | sort -n | tail -n 30"
```

If `sort -n` is missing, fall back to unsorted:

```bash
PKG=<pkg.name>
adb shell su -c "du -a /data/user/0/$PKG/databases 2>/dev/null | tail -n 30"
```

## 4) Non-root fallback (debuggable apps only)

If the app is debuggable, you can sometimes use `run-as`:

```bash
adb shell run-as <pkg.name> ls -al databases
adb exec-out run-as <pkg.name> cat databases/<some.db> > some.db
```

## 5) Verify “a write happened” (mtime/size changes)

If your goal is “did this UI operation cause SQLite writes?”, check:
- `*.db-wal` mtime/size changes (WAL mode is common)
- `*.db` mtime may stay unchanged while `*-wal` grows

This is a portable before/after snapshot. Run it **before** the UI action, then run it **again** after:

```bash
PKG=<pkg.name>
adb shell su -c 'sh -c "
  for base in /data/user/0 /data/user_de/0 /data/data; do
    d=$base/'\"$PKG\"'/databases;
    [ -d \"\$d\" ] || continue;
    echo ==== \"\$d\" ====;
    ls -lt \"\$d\" | head -n 30;
    echo ---- stat (mtime + size) ----;
    stat -c \"%Y %y %s %n\" \"\$d\"/*.db* 2>/dev/null | sort -n;
    break;
  done
"'
```

Optional (less universal; depends on `find -mmin` support in toybox/busybox):

```bash
PKG=<pkg.name>
adb shell su -c "find /data/user/0/$PKG/databases -maxdepth 1 -type f -name '*.db*' -mmin -2 -print 2>/dev/null || true"
```
