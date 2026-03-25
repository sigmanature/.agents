---
name: wechat-wxapkg-and-apk-batch-tools
description: >
  Use this skill whenever the user asks to 批量下载微信小程序包(wxapkg) from a URL list (often with Cookie/Token headers)
  OR asks to 批量安装一批 APK 到一台或多台 Android 设备 (adb install)
  OR asks to 下载Top应用压缩包/解压/批量安装（top50/top100/top200/top-app 区间等）。
  This skill provides repeatable workflows and repo-local CLI tools that generate manifests/logs for auditing and retry.
---

## What this skill does
This skill wraps 3 repeatable workflows via repo-local CLIs:

1) **Batch download wxapkg** (WeChat mini-program packages) from a `urls.txt` list, optionally sending auth headers from `headers.json`.
   - Tool: [`wxapkg_batch_download.py`](tools/wxapkg_batch_download.py:1)

2) **Batch install APKs** from a directory to one or more Android devices.
   - Tool: [`apk_batch_install.py`](tools/apk_batch_install.py:1)

3) **Top apps pipeline**: download ZIP(s) → unzip locally → install all contained APKs to connected device(s).
   - Wrapper tool: [`top_apps_zip_pipeline.py`](tools/top_apps_zip_pipeline.py:1)
   - Underlying implementation already exists in:
     - ZIP URL mapping: [`WidgetAutomationTest._get_zip_urls()`](launcher_ui/widget_automation_test.py:130)
     - Pipeline method: [`WidgetAutomationTest.download_and_install_apps()`](launcher_ui/widget_automation_test.py:347)

## Preconditions / assumptions
- For wxapkg download: URLs are directly downloadable; if auth is needed, user can provide `headers.json` (Cookie/Token/etc.).
- For APK install: `adb` must be available in PATH and devices must be in `device` state (`adb devices`).
- For Top apps pipeline: at least one device must be connected and usable; the pipeline installs APKs.

## Quick start (recommended usage order)
Pick **exactly one** of these entry workflows depending on what you have:

1) **You want Top apps by rank (top50/top100/top200/51-100/etc.)**
   - Use Workflow C first (it already includes: ZIP links → download → unzip → install).
   - Command: [`top_apps_zip_pipeline.py`](tools/top_apps_zip_pipeline.py:1)

2) **You already have a directory of APK files**
   - Use Workflow B.
   - Command: [`apk_batch_install.py`](tools/apk_batch_install.py:1)

3) **You have a URL list of wxapkg (WeChat mini-program packages)**
   - Use Workflow A.
   - Command: [`wxapkg_batch_download.py`](tools/wxapkg_batch_download.py:1)

## Scripts overview (inputs → outputs)
| Workflow | Script | Required input | Optional input | Main outputs |
|---|---|---|---|---|
| A: wxapkg download | [`wxapkg_batch_download.py`](tools/wxapkg_batch_download.py:1) | `--urls urls.txt` | `--headers-json headers.json`, `--output-dir`, `--workers`, `--retries` | `files/`, `manifest.jsonl`, `failed_urls.txt` |
| B: APK install | [`apk_batch_install.py`](tools/apk_batch_install.py:1) | `apk_dir/` | `--serial ...` (repeat), `--output-dir`, `--timeout` | `install_log.jsonl`, `installed_packages.txt`, `failed_apks.txt` |
| C: Top ZIP pipeline | [`top_apps_zip_pipeline.py`](tools/top_apps_zip_pipeline.py:1) | `--top-app 200` or `--top-app 51-100` | `--device1/--device2`, `--output-dir`, `--clean` | output dir with `downloads/` cache + device installs |

---

## Workflow A — Batch download wxapkg
### 1) Ask for inputs
You need:
- Path to `urls.txt` (one URL per line; allow blank lines and `#` comments)
- Optional `headers.json` (JSON object/dict) applied to all requests
- Optional output directory path (otherwise use default)

### 2) Run the downloader
Command:
- [`wxapkg_batch_download.py`](tools/wxapkg_batch_download.py:1)

Example:
```bash
python3 tools/wxapkg_batch_download.py \
  --urls ./data/urls.txt \
  --headers-json ./data/headers.json \
  --output-dir ./output/wxapkg_run_001 \
  --workers 6 \
  --retries 2
```

### 3) Verify outputs
Check in output dir:
- `files/` contains downloaded binaries
- `manifest.jsonl` exists and has one line per URL
- `failed_urls.txt` lists failures for retry

### 4) Troubleshooting
- HTTP 401/403: headers are missing/expired (Cookie/Token)
- HTTP 404: URL stale
- HTTP 429/5xx: increase retries/backoff, reduce workers

---

## Workflow B — Batch install APKs
### 1) Ask for inputs
You need:
- Path to APK directory containing `*.apk`
- Optional device serial list
  - If not provided, tool auto-detects via `adb devices`
- Optional output directory

### 2) Run the installer
Command:
- [`apk_batch_install.py`](tools/apk_batch_install.py:1)

Example (auto-detect devices):
```bash
python3 tools/apk_batch_install.py ./apks --output-dir ./output/apk_install_run_001
```

Example (specific devices):
```bash
python3 tools/apk_batch_install.py ./apks \
  --serial ABC123 \
  --serial DEF456 \
  --output-dir ./output/apk_install_run_002
```

### 3) Verify outputs
In output dir:
- `install_log.jsonl` has per-device results
- `installed_packages.txt` lists APKs that succeeded on *all* devices
- `failed_apks.txt` lists APK filenames that failed on *any* device

### 4) Troubleshooting
- No devices found: `adb devices` shows none in `device` state (unauthorized/offline)
- `INSTALL_FAILED_*`: inspect `install_log.jsonl` stderr; common causes are version downgrade, signature mismatch, low storage

---

## Workflow C — Download Top apps ZIP(s) → unzip → install
Use this when the user asks for:
- “下载 top50/top100/top200/top500 应用压缩包并安装”
- “top-app 51-100 / 101-200 区间下载解压安装”

### 1) Ask for inputs
You need:
- `--top-app` value (e.g. `200` or `51-100`)
- Optional device serial(s): `--device1`, `--device2` (if omitted, the underlying code may auto-detect depending on environment)
- Optional `--output-dir`
- Optional `--clean` (default `folder`)

### 2) Run the pipeline wrapper
Command:
- [`top_apps_zip_pipeline.py`](tools/top_apps_zip_pipeline.py:1)

Examples:
```bash
python3 tools/top_apps_zip_pipeline.py --top-app 200
```

```bash
python3 tools/top_apps_zip_pipeline.py \
  --top-app 51-100 \
  --device1 ABC123 \
  --output-dir ./output/top_51_100 \
  --clean folder
```

### 3) What it produces
- Output dir contains `downloads/` with cached ZIP(s) and extracted APK(s)
- Installs all extracted APKs via `adb install -r`

### 4) Troubleshooting
- If URL download fails: check the hardcoded mapping in [`WidgetAutomationTest._get_zip_urls()`](launcher_ui/widget_automation_test.py:130)
- If install fails: check device state (`adb devices`) and logs under output dir

---

## Reporting format (when you run any workflow)
After running, summarize:
- Inputs (paths, number of URLs/APKs, device serials)
- Output directory
- Success/failure counts
- Where the manifest/log files are
