#!/usr/bin/env python3
import re, sys, os, gzip, collections

def parse_trace_to_folded(paths, top_n=10):
    stacks = collections.Counter()

    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", errors="replace") as f:
            current_task = None
            current_stack = []

            for line in f:
                ev = re.match(r"\s+(\S+)-(\d+)\s+\[\d+\].*mm_folio_deferred_split:", line)
                st = re.match(r"\s+=>\s+(\S+)", line)
                end = line.startswith(" ") and not line.startswith("          ")

                if ev:
                    if current_task and current_stack:
                        folded = current_task + ";" + ";".join(reversed(current_stack))
                        stacks[folded] += 1
                    current_task = ev.group(1)
                    current_stack = []
                elif st:
                    current_stack.append(st.group(1))
                elif not line.strip() and current_stack:
                    pass

            if current_task and current_stack:
                folded = current_task + ";" + ";".join(reversed(current_stack))
                stacks[folded] += 1

    return stacks

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <trace_dir> [--top N] [--out out.folded]")
        sys.exit(1)

    top_n = 50
    out_file = None
    paths = []

    for arg in sys.argv[1:]:
        if arg == "--top" or arg == "-n":
            continue
        elif arg.startswith("--top="):
            top_n = int(arg.split("=")[1])
        elif arg == "--out" or arg == "-o":
            continue
        elif arg.startswith("--out="):
            out_file = arg.split("=")[1]
        elif os.path.isdir(arg):
            paths.extend(sorted(
                os.path.join(arg, f) for f in os.listdir(arg)
                if f.startswith("trace") and not f.endswith(".gz") and not f.endswith(".txt")
            ))
        elif os.path.isfile(arg):
            paths.append(arg)

    stacks = parse_trace_to_folded(paths)

    top = sum(c for _, c in stacks.most_common(top_n))
    total = sum(stacks.values())

    if out_file:
        with open(out_file, "w") as f:
            for folded, count in stacks.most_common(top_n):
                f.write(f"{folded} {count}\n")
        print(f"Wrote {out_file} ({len(stacks)} stacks, top {top_n}={top}/{total} = {100*top/total:.1f}%)")
        return

    for folded, count in stacks.most_common(top_n):
        print(f"{folded} {count}")

    print(f"\ntotal={total}, top{top_n}={top} ({100*top/total:.1f}%)")

if __name__ == "__main__":
    main()
