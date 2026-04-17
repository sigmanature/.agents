# F2FS writeback KV logs (bold printk, inode-filtered)

This reference is a practical template for adding **table-friendly `k=v` logs**
to writeback hot paths (e.g. `f2fs_write_cache_folios`) without drowning in noise.

It matches the common “Pixel user build” reality:
- tracefs/kprobes may be blocked by SELinux
- printk is the most reliable always-on channel

## Core pattern

1) **Always log in k=v rows**
   - makes grep + table queries possible
2) **Always include stable IDs**
   - actor: `cpu`, `pid`, `comm`
   - object: `ino` (+ optionally `folio_index`, `pos`, `len`)
3) **Always include a filter**
   - inode whitelist is the simplest: only log when `inode->i_ino == target`
4) **Prefer a runtime knob**
   - sysfs-backed `dbg_*_ino1/ino2` fields on `struct f2fs_sb_info`
5) **Be bold with printk level when needed**
   - if logs must survive busy dmesg, use `pr_emerg` temporarily

## Minimal macro (copy/paste)

```c
#define F2FS_WCF_KLOG(subtag, fmt, ...) \
	pr_emerg("F2FS_WCF " subtag " fn=%s cpu=%d pid=%d comm=%s " fmt "\n", \
		 __func__, raw_smp_processor_id(), task_pid_nr(current), current->comm, \
		 ##__VA_ARGS__)
```

## Minimal inode filter helper

```c
static inline bool f2fs_dbg_is_wcf_ino(const struct inode *inode)
{
	struct f2fs_sb_info *sbi;
	unsigned long ino1, ino2;

	if (!inode)
		return false;
	sbi = F2FS_I_SB(inode);
	ino1 = READ_ONCE(sbi->dbg_wcf_ino1);
	ino2 = READ_ONCE(sbi->dbg_wcf_ino2);
	return (ino1 && inode->i_ino == ino1) || (ino2 && inode->i_ino == ino2);
}
```

## Where to log in writeback

Recommended “bold but survivable” points (inode-filtered):
- **ENTER**: one row per `write_cache_folios` call (range + sync mode)
- **STATE**: per dirty range (`pos/end/r_len`) before/after `write_single_data_folio`
- **SKIP**: only when mapping mismatch / beyond EOF / trimmed end_pos / retry-not-dirty
- **EXIT**: one row per call with totals (`nwritten`, `err`)

## How to use

1) Determine target inode(s) on device:

```bash
adb shell su -c 'stat -c "%i %n" /data/user/0/<pkg>/databases/<db>'
adb shell su -c 'stat -c "%i %n" /data/user/0/<pkg>/databases/<db>-wal'
```

2) Set sysfs filter on the f2fs mount (path varies by device):

```bash
# example (adjust mount sysfs path):
adb shell su -c 'echo 40974 > /sys/fs/f2fs/<dev>/dbg_wcf_ino1'
adb shell su -c 'echo 40975 > /sys/fs/f2fs/<dev>/dbg_wcf_ino2'
```

3) Grep the kernel log:

```bash
adb shell su -c 'dmesg -T | grep -E \"^.*F2FS_WCF\"'
```

## Health rules (what looks suspicious)

- Many `SKIP mapping_mismatch=1` for the target inode around the first-failure window
- Frequent `beyond_eof=1` on folios you expected to be fully inside `i_size`
- `endpos_trimmed=1` combined with a write that should have been aligned to page boundaries
- Any non-zero `err` from `write_single_data_folio` for the target inode

