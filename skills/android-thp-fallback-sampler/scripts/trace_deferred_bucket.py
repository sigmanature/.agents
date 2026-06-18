#!/usr/bin/env python3
import re, sys, os, gzip, collections

def parse_trace_stream(paths):
    per_task = collections.defaultdict(lambda: {"exit_mmap": 0, "madvise_*": 0, "munmap": 0, "split (reclaim)": 0, "other": 0})
    total = 0

    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", errors="replace") as f:
            current_event = None
            stack_lines = []

            for line in f:
                ev = re.match(r"\s+(\S+)-(\d+)\s+\[\d+\].*mm_folio_deferred_split:", line)
                st = re.match(r"\s+=>\s+(\S+)", line)

                if ev:
                    if current_event:
                        path_type = classify_path(stack_lines)
                        per_task[current_event][path_type] += 1
                        total += 1
                    current_event = ev.group(1)
                    stack_lines = []
                elif st:
                    stack_lines.append(st.group(1))
                elif line.startswith("          ") and not line.strip().startswith("=>"):
                    pass

            if current_event and stack_lines:
                path_type = classify_path(stack_lines)
                per_task[current_event][path_type] += 1
                total += 1

    return per_task, total

def classify_path(stack):
    names = set(stack)
    if "exit_mmap" in names:
        return "exit_mmap"
    for n in names:
        if "madvise" in n:
            return "madvise_*"
        if "munmap" in n:
            return "munmap"
    if "split_folio_to_list" in names:
        return "split (reclaim)"
    return "other"

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <trace_file_or_dir> [trace_file_or_dir ...]")
        sys.exit(1)

    paths = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            paths.extend(sorted(
                os.path.join(arg, f) for f in os.listdir(arg)
                if f.startswith("trace") and not f.endswith(".gz") and not f.endswith(".txt")
            ))
        elif os.path.isfile(arg):
            paths.append(arg)

    per_task, total = parse_trace_stream(paths)

    if total == 0:
        print("No deferred_split events found")
        sys.exit(1)

    rows = [(t, d["exit_mmap"], d["madvise_*"], d["munmap"], d["split (reclaim)"], d["other"],
             d["exit_mmap"] + d["madvise_*"] + d["munmap"] + d["split (reclaim)"] + d["other"])
            for t, d in per_task.items()]
    rows.sort(key=lambda r: -r[5])

    print(f"Total deferred_split events: {total}")
    print(f"Unique tasks: {len(rows)}")
    print()
    print(f"{'TASK':<20} {'exit_mmap':>10} {'madvise':>10} {'munmap':>8} {'reclaim':>8} {'other':>8} {'TOTAL':>8} {'%':>6}")
    print("-" * 82)
    for task, ex, md, mu, rc, ot, tt in rows:
        pct = 100.0 * tt / total if total else 0
        bar = "█" * int(pct / 2)
        print(f"{task:<20} {ex:>10} {md:>10} {mu:>8} {rc:>8} {ot:>8} {tt:>8} {pct:>5.1f}%  {bar}")

    ex_tot = sum(r[1] for r in rows)
    md_tot = sum(r[2] for r in rows)
    mu_tot = sum(r[3] for r in rows)
    rc_tot = sum(r[4] for r in rows)
    ot_tot = sum(r[5] for r in rows)
    print(f"\nPath summary: exit_mmap={ex_tot} ({100*ex_tot/total:.1f}%)  "
          f"madvise_*={md_tot} ({100*md_tot/total:.1f}%)  "
          f"munmap={mu_tot} ({100*mu_tot/total:.1f}%)  "
          f"reclaim_split={rc_tot} ({100*rc_tot/total:.1f}%)  "
          f"other={ot_tot} ({100*ot_tot/total:.1f}%)")

if __name__ == "__main__":
    main()
