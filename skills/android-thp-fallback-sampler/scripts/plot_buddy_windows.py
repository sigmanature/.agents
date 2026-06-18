#!/usr/bin/env python3
import argparse, os, re, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

def parse_buddy_log(path):
    ts, o2 = [], []
    pat = re.compile(r"(\d+\.\d+)\s+Node \d+, zone\s+\w+\s+(\d+)\s+(\d+)\s+(\d+)")
    with open(path) as f:
        for line in f:
            m = pat.match(line)
            if m:
                ts.append(float(m.group(1)))
                o2.append(int(m.group(4)))
    t0 = ts[0]
    return np.array(ts) - t0, np.array(o2, dtype=float)

def derivative(t, v):
    dt = t[1:] - t[:-1]
    dv = v[1:] - v[:-1]
    mask = dt > 0
    return (t[1:] + t[:-1])[mask] / 2, (dv / dt)[mask]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--a-label", default="1A (order=0)")
    p.add_argument("--b-label", default="2I (order=2)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--window-s", type=float, default=10)
    p.add_argument("--skip-empty", action="store_true", default=True)
    p.add_argument("--max-windows", type=int, default=200)
    args = p.parse_args()

    ta, va = parse_buddy_log(args.a)
    tb, vb = parse_buddy_log(args.b)
    dta, dva = derivative(ta, va)
    dtb, dvb = derivative(tb, vb)

    t_max = max(ta[-1], tb[-1])
    n_windows = min(args.max_windows, int(t_max / args.window_s) + 1)
    os.makedirs(args.out_dir, exist_ok=True)

    for wi in range(n_windows):
        t0 = wi * args.window_s
        t1 = t0 + args.window_s

        ma = (ta >= t0) & (ta < t1)
        mb = (tb >= t0) & (tb < t1)
        mda = (dta >= t0) & (dta < t1)
        mdb = (dtb >= t0) & (dtb < t1)

        if args.skip_empty and not (ma.any() or mb.any()):
            continue

        fig, (ax_r, ax_d) = plt.subplots(1, 2, figsize=(14, 5))

        if ma.any():
            ax_r.plot(ta[ma] - t0, va[ma] / 1000, "C0", lw=0.8, alpha=0.8,
                      label=args.a_label)
        if mb.any():
            ax_r.plot(tb[mb] - t0, vb[mb] / 1000, "C1", lw=0.8, alpha=0.8,
                      label=args.b_label)
        ax_r.set_ylabel("order-2 (×1000 blocks)")
        ax_r.set_title(f"Raw [{t0:.0f}s - {t1:.0f}s]")
        ax_r.legend(fontsize=8)
        ax_r.grid(True, alpha=0.3)

        ax_d.axhline(0, color="gray", lw=0.5)
        if mda.any():
            ax_d.plot(dta[mda] - t0, dva[mda] / 1000, "C0", lw=0.6, alpha=0.7)
        if mdb.any():
            ax_d.plot(dtb[mdb] - t0, dvb[mdb] / 1000, "C1", lw=0.6, alpha=0.7)
        ax_d.set_ylabel("d(order2)/dt (×1000 blocks/s)")
        ax_d.set_title(f"Derivative [{t0:.0f}s - {t1:.0f}s]")
        ax_d.grid(True, alpha=0.3)

        fig.tight_layout()
        fname = f"{args.out_dir}/win_{wi:04d}_{t0:.0f}s.svg"
        fig.savefig(fname, dpi=120, bbox_inches="tight")
        plt.close(fig)

    print(f"Generated {n_windows} windows in {args.out_dir}")

if __name__ == "__main__":
    main()
