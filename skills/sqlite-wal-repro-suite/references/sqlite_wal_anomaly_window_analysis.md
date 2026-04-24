# SQLite WAL anomaly-window analysis

This note documents the analysis method for a **captured first-failure window** from the WAL repro app.

Use it when you already have one run directory from `walrepro_plan2_capture_tracefs.sh` or `walrepro_loop_until_detect.sh`.

---

## 1) Define the two important timestamps

Use `logcat_WalRepro.txt` as the app-side truth source.

- `T_detect`: the first `phase=DETECT ... ts_mono_ns=<N>` line
  - this is the closest timestamp to “the app first knew the DB content was wrong”
- `T_report`: the later `phase=FAIL ... ts_mono_ns=<N>` line
  - this includes stop/join/report overhead and is usually **after** the real bad write

Treat any `phase=SNAPSHOT` I/O as **post-failure artifact capture**, not causal workload.

---

## 2) Align the three evidence streams

For a given run directory, align these files:

1. `logcat_WalRepro.txt`
   - locate `T_detect`, `T_report`, `DETECT` reason, `quick_check` text, and any `phase=CKPT` rows near the failure
2. `tracefs_trace.txt`
   - this gives thread-scoped syscall/f2fs timing around the same since-boot clock family
3. `dmesg_f2fs_wb_filtered.txt`
   - this gives the detailed `F2FS_WB` path for the selected inode (`db`, `wal`, or `shm`)

Operational rule:

- first ask whether the first abnormality happens on **WAL inode** or **main DB inode**
- only after that ask which exact kernel statement or state transition looks wrong

---

## 3) Decide which inode to prioritize

### Case A: `T_detect` is **before** observed main-DB checkpoint writeback

Interpretation:

- the first visible bad state likely appears on the WAL side
- later main-DB checkpoint/writeback may just be cleanup or fallout

Action:

- rerun or inspect with `--klogTarget wal`
- compare WAL `sync`, `truncate`, `clear_and_dec`, and `zero_tail` behavior against a clean baseline

### Case B: `T_detect` is **after** observed main-DB checkpoint writeback

Interpretation:

- the suspicion shifts toward checkpoint-to-main-db writeback

Action:

- rerun or inspect with `--klogTarget db`
- focus on main-db `write_iter`, `sync_file`, and large-folio writeback state transitions

---

## 4) What to compare against normal runs

Always keep at least one clean baseline run with the same workload shape.

Normal WAL-side baseline usually looks like repeated patterns such as:

- `ENTER`
- `FOLIO`
- `clear_and_dec`
- `zero_tail`
- `EXIT`

Checkpoint-heavy clean runs may also show normal WAL truncate-to-zero after checkpoint.

The point of the baseline is not that every line must match; it is to expose **which class of event first deviates** in the failing run.

---

## 5) Suspicious signals inside the anomaly window

Look for these first:

- `err!=0` on WAL or main-db writeback
- `f2fs_sync_file_exit` returning an unexpected error near `T_detect`
- dirty-page / dirty-subrange accounting that decreases too early or skips expected cleanup
- truncate/reset timing that is earlier than expected
- a syscall/f2fs sequence that exists only in the failing run, not in the baseline

This method is especially useful when you have both:

- thread-scoped syscall trace (`tracefs_trace.txt`)
- inode-scoped kernel detail (`dmesg_f2fs_wb_filtered.txt`)

Then you can map:

- userspace thread + syscall sequence
- to F2FS event timing
- to `F2FS_WB` internal state transitions

---

## 6) Practical workflow

1. Find the first `phase=DETECT` line in `logcat_WalRepro.txt`
2. Extract a small trace window around `T_detect` from `tracefs_trace.txt`
3. Read `klog_target.txt` to confirm which inode was instrumented
4. Compare the same time region in `dmesg_f2fs_wb_filtered.txt`
5. Compare that sequence against a clean run with the same workload arguments
6. Decide whether the next run should stay on `--klogTarget wal` or switch to `--klogTarget db`

---

## 7) What this method does and does not prove

What it gives you:

- a fixed captured window around first detection
- a concrete syscall/f2fs/kernel-log sequence to reason about
- a way to separate WAL-first faults from checkpoint-to-main-db faults

What it does **not** give you by itself:

- mathematical proof of the exact first corrupted byte
- deterministic replay of the same concurrency schedule
- proof that later log lines are causal rather than fallout

It is a narrowing method: identify the earliest abnormal window first, then deepen instrumentation only on the path that actually leads that window.
