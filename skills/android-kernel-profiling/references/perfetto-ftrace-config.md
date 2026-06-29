# Perfetto ftrace config on Pixel (Magisk root)

## Verified workflow

### 1. Config file (text protobuf format)

```
buffers { size_kb: 16384 fill_policy: RING_BUFFER }

data_sources {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "kmem/mm_page_alloc"
      # ... more tracepoints listed under ftrace_config
    }
  }
}

duration_ms: 15000
write_into_file: true
max_file_size_bytes: 268435456
```

**Key point**: fields go under `ftrace_config { }`, NOT directly under `config { }`.

### 2. Push + run (Magisk root device)

```bash
SERIAL=18281FDF6007HB

# Step A: push config to /data/misc/perfetto-configs/ (traced can read this dir)
adb -s "$SERIAL" shell "su -c 'mkdir -p /data/misc/perfetto-configs /data/misc/perfetto-traces'"
adb -s "$SERIAL" push my.cfg /data/local/tmp/my.cfg
adb -s "$SERIAL" shell "su -c 'cp /data/local/tmp/my.cfg /data/misc/perfetto-configs/my.cfg && chmod 644 /data/misc/perfetto-configs/my.cfg'"

# Step B: set any event filters BEFORE starting perfetto (config can't do this)
adb -s "$SERIAL" shell "su -c \"echo 'order >= 2' > /sys/kernel/tracing/events/kmem/mm_page_alloc/filter\""

# Step C: register kprobes BEFORE starting perfetto (persist across perfetto sessions)
adb -s "$SERIAL" shell "su -c 'echo p:f2fs_ra_start f2fs_readahead >> /sys/kernel/tracing/kprobe_events'"
adb -s "$SERIAL" shell "su -c 'echo r:f2fs_ra_end f2fs_readahead >> /sys/kernel/tracing/kprobe_events'"

# Step D: start workload, then perfetto as shell user (NOT su)
adb -s "$SERIAL" shell "perfetto --txt --background -c /data/misc/perfetto-configs/my.cfg -o /data/misc/perfetto-traces/my.pftrace"

# Step E: pull result
adb -s "$SERIAL" pull /data/misc/perfetto-traces/my.pftrace ./trace.pftrace
```

### 3. Why NOT su for perfetto?

- `perfetto` connects to the `traced` service (UID 9999).
- Root (`su`) causes "EnableTracing IPC request rejected" because of the TTY/permission mismatch.
- Shell user works fine because `traced` accepts connections from any app.
- Config file must be readable by shell user → must be in `/data/misc/perfetto-configs/` with `chmod 644` (not `/data/local/tmp/` which is root-only).

### 4. Why NOT use TTY?

- `adb shell -t` / `adb shell -tt` doesn't reliably fix the traced connection issue on Pixel.
- `--background` mode avoids the TTY requirement entirely.
- For long runs (9 min), `--background` returns immediately; poll via `perfetto --query` or wait for the duration to elapse.

### 5. Kprobe survival

- Kprobes registered via `/sys/kernel/tracing/kprobe_events` persist across perfetto sessions.
- Perfetto does NOT clear kprobe_events on start/stop.
- Include kprobe events in the config as `ftrace_events: "kprobes/f2fs_ra_start"`.
- Must be registered BEFORE perfetto starts.

### 6. event_filter NOT supported in perfetto config

- Perfetto's `ftrace_config` does not support `event_filter`.
- Set filters manually via tracefs before starting perfetto:
  ```bash
  adb shell "su -c \"echo 'order >= 2' > /sys/kernel/tracing/events/kmem/mm_page_alloc/filter\""
  ```

### 7. Cleanup between runs

```bash
# clear kprobes
adb -s "$SERIAL" shell "su -c 'echo > /sys/kernel/tracing/kprobe_events'"

# reset trace buffer
adb -s "$SERIAL" shell "su -c 'echo 0 > /sys/kernel/tracing/tracing_on; echo > /sys/kernel/tracing/trace'"

# clear perfetto old traces
adb -s "$SERIAL" shell "su -c 'rm -f /data/misc/perfetto-traces/*.pftrace'"
```

## Verified config: mTHP order=2 memstress (16 tracepoints + kprobes)

```
buffers { size_kb: 16384 fill_policy: RING_BUFFER }

data_sources {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_waking"
      ftrace_events: "binder/binder_transaction"
      ftrace_events: "binder/binder_transaction_received"
      ftrace_events: "kmem/mm_page_alloc"
      ftrace_events: "compaction/mm_compaction_begin"
      ftrace_events: "compaction/mm_compaction_end"
      ftrace_events: "vmscan/mm_vmscan_direct_reclaim_begin"
      ftrace_events: "vmscan/mm_vmscan_direct_reclaim_end"
      ftrace_events: "f2fs/f2fs_readpage"
      ftrace_events: "f2fs/f2fs_submit_read_bio"
      ftrace_events: "filemap/mm_filemap_add_to_page_cache"
      ftrace_events: "filemap/mm_filemap_fault"
      ftrace_events: "kprobes/f2fs_ra_start"
      ftrace_events: "kprobes/f2fs_ra_end"
    }
  }
}

data_sources {
  config {
    name: "linux.process_stats"
    process_stats_config {
      proc_stats_poll_ms: 1000
    }
  }
}

duration_ms: 540000     # 9 min for full memstress
# duration_ms: 15000    # 15s for smoke test

write_into_file: true
file_write_period_ms: 5000
max_file_size_bytes: 268435456
```
