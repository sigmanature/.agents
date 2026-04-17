# On-device triggers that commonly cause SQLite writes (Douyin/Huoshan/UC class apps)

Goal: provide **human/script-triggerable** actions that tend to generate SQLite write load in large third-party apps.

Constraints:
- No packet capture.
- No reversing.
- Only on-device UI operations (manual or via `adb shell input ...` / Monkey).

## Quick interpretation guide (expected write “type”)

- **WAL growth**: database likely in WAL mode; many commits append to `*.db-wal` until checkpoint.
- **New-table/row writes**: insert-heavy patterns (history, cache index, event logs).
- **Frequent small transactions**: like/follow toggles, counters, “mark as read”, state updates.
- **Large number of small writes**: lots of tiny inserts/updates (analytics, exposure logs, scroll events).
- **BLOB-heavy / larger transactions**: thumbnails, drafts metadata, rich message payloads (sometimes SQLite, sometimes files + SQLite index).

## Trigger catalog (10+ actions)

These are intentionally phrased as “do X in the UI” so you can reproduce without knowing internal table names.

### Feed / recommendation (Douyin/Huoshan)
1) **Cold start + stay on Home 30s**
   - Expected: new rows for app/session start, config cache, analytics; possible WAL bootstrap + bursts.
2) **Scroll feed continuously (e.g., 50–100 swipes)**
   - Expected: heavy “exposure / view history / recommendation feedback” logging; lots of small inserts; WAL growth.
3) **Open a video detail page → back → repeat**
   - Expected: per-item watch-state updates + cache index; frequent small transactions.
4) **Watch a single item to near-end**
   - Expected: progress/resume position updates; small updates; WAL growth.
5) **Like / unlike (toggle repeatedly)**
   - Expected: frequent small transactions (insert/delete or update); WAL growth but mostly tiny writes.
6) **Follow / unfollow a creator**
   - Expected: follow relationship table updates; local cache refresh markers; small transactions.

### Comments / interactions (Douyin/Huoshan)
7) **Enter comments → scroll comments → switch sort tabs**
   - Expected: comment cache + “read/seen” markers; many small inserts/updates; WAL growth.
8) **Post a comment (text) then delete**
   - Expected: insert + delete/update; possible multi-step transactions; WAL growth.
9) **Favorite/collect a video; manage favorites list**
   - Expected: new-row writes + list index updates; small transactions.
10) **Share (copy link / in-app share sheet)**
   - Expected: event log row inserts (share action, channel); many small writes.

### Search (Douyin/Huoshan/UC)
11) **Search: type keywords + click suggestions/hot list**
   - Expected: search history + suggestion cache; frequent small writes.
12) **Search results: switch tabs (综合/视频/用户/话题…)**
   - Expected: cache index updates + exposure logs; many small writes.

### Account / settings (all)
13) **Login → logout → login (or account switch)**
   - Expected: token/session tables updated, settings sync markers, push registration cache; bursty WAL growth.
14) **Toggle settings (autoplay, notifications)**
   - Expected: small settings table writes; frequent tiny transactions.

### Draft / publish / download (Douyin/Huoshan/UC where applicable)
15) **Create a draft; keep editing to trigger autosave**
   - Expected: high-frequency “draft metadata” updates; many small transactions (or fewer larger ones).
16) **Start a download/offline cache; observe progress**
   - Expected: download task table updates + progress writes; potentially very frequent updates.

### IM / live (Douyin/Huoshan if available)
17) **Enter chat; send 20 short messages**
   - Expected: message table inserts + conversation index updates; lots of tiny inserts; WAL growth.
18) **Watch live; send a few comments/gifts (even without paying)**
   - Expected: live chat/event logs; many small writes; WAL growth.

### Browser/WebView-heavy (UC + in-app browsers)
19) **Browse: open 10 pages + back/forward**
   - Expected: WebView sqlite stores (history/cookies/visited links) steady small writes; WAL growth.
20) **Bookmark a page; edit bookmark; remove bookmark**
   - Expected: bookmark table insert/update/delete; small transactions.
21) **Open 10 tabs; close all**
   - Expected: tab/session restore DB updates; frequent small writes.

## Scriptability note (optional)

- You can approximate “scroll many times” with:
  - `adb shell input swipe x1 y1 x2 y2 <duration_ms>`
- You can approximate “search” with:
  - `adb shell input text 'keyword'` + `adb shell input keyevent KEYCODE_ENTER`

Coordinate-based automation depends on device resolution/UI layout; prefer **manual** or **Monkey** when you only need “more writes”, not exact semantics.

