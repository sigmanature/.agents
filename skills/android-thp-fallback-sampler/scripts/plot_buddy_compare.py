#!/usr/bin/env python3
import argparse
import re
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def parse_buddy_log(path):
    ts, o2 = [], []
    pat = re.compile(
        r"(\d+\.\d+)\s+Node \d+, zone\s+\w+\s+(\d+)\s+(\d+)\s+(\d+)"
    )
    with open(path) as f:
        for line in f:
            m = pat.match(line)
            if m:
                ts.append(float(m.group(1)))
                o2.append(int(m.group(4)))
    if not ts:
        raise ValueError(f"No buddyinfo data in {path}")
    t0 = ts[0]
    return np.array([t - t0 for t in ts], dtype=float), np.array(o2, dtype=float)


def derivative(t, v):
    dt = t[1:] - t[:-1]
    dv = v[1:] - v[:-1]
    mask = dt > 0
    return (t[1:] + t[:-1])[mask] / 2, (dv / dt)[mask]


def main():
    p = argparse.ArgumentParser(description="Compare buddyinfo order-2 between two devices")
    p.add_argument("--a", required=True, help="Device A buddy_01s.log")
    p.add_argument("--b", required=True, help="Device B buddy_01s.log")
    p.add_argument("--a-label", default="Device A")
    p.add_argument("--b-label", default="Device B")
    p.add_argument("--out", default=None, help="Output SVG path")
    p.add_argument("--title", default="Buddyinfo Order-2 Comparison")
    args = p.parse_args()

    ta, va = parse_buddy_log(args.a)
    tb, vb = parse_buddy_log(args.b)
    dta, dva = derivative(ta, va)
    dtb, dvb = derivative(tb, vb)

    print(f"{args.a_label}: {len(ta)} samples, {ta[-1]:.0f}s elapsed, "
          f"order2 range [{int(va.min())}, {int(va.max())}]")
    print(f"{args.b_label}: {len(tb)} samples, {tb[-1]:.0f}s elapsed, "
          f"order2 range [{int(vb.min())}, {int(vb.max())}]")
    print(f"{args.a_label} derivative std: {dva.std():.0f} blocks/sample")
    print(f"{args.b_label} derivative std: {dvb.std():.0f} blocks/sample")

    fig, axes = plt.subplots(2, 2, figsize=(16, 10), sharex="col")
    fig.suptitle(args.title, fontsize=14, fontweight="bold")

    ax0 = axes[0, 0]
    ax0.plot(ta / 60, va / 1000, alpha=0.6, linewidth=0.5, label=args.a_label)
    ax0.plot(tb / 60, vb / 1000, alpha=0.6, linewidth=0.5, label=args.b_label)
    ax0.set_ylabel("Order-2 blocks (×1000)")
    ax0.set_title("Raw Order-2 Buddy Count (16KB blocks)")
    ax0.legend(fontsize=9)
    ax0.grid(True, alpha=0.3)

    ax1 = axes[0, 1]
    win = 30
    for t, v, label, c in [(ta, va, args.a_label, "C0"), (tb, vb, args.b_label, "C1")]:
        roll = []
        for i in range(len(t)):
            j = np.searchsorted(t, t[i] - win)
            roll.append(v[j:i+1].std() if i > j else 0)
        ax1.plot(t / 60, roll, alpha=0.8, linewidth=0.8, color=c, label=label)
    ax1.set_ylabel("std dev (blocks)")
    ax1.set_title(f"Order-2 Volatility (rolling {win}s window)")
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    ax2 = axes[1, 0]
    ax2.axhline(0, color="gray", linewidth=0.5)
    ax2.plot(dta / 60, dva / 1000, alpha=0.5, linewidth=0.3, color="C0", label=args.a_label)
    ax2.plot(dtb / 60, dvb / 1000, alpha=0.5, linewidth=0.3, color="C1", label=args.b_label)
    ax2.set_xlabel("Time (minutes)")
    ax2.set_ylabel("d(order2)/dt ×1000 blocks/s")
    ax2.set_title("Derivative (instantaneous change rate)")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    ax3 = axes[1, 1]
    bins = np.linspace(-8000, 8000, 80)
    ax3.hist(dva, bins=bins, alpha=0.5, label=args.a_label, color="C0")
    ax3.hist(dvb, bins=bins, alpha=0.5, label=args.b_label, color="C1")
    ax3.axvline(0, color="gray", linewidth=0.5)
    ax3.set_xlabel("d(order2)/dt (blocks/sample)")
    ax3.set_ylabel("Frequency")
    ax3.set_title("Derivative Distribution")
    ax3.legend(fontsize=9)

    plt.tight_layout()

    if args.out is None:
        out = os.path.join(os.path.dirname(args.a) or ".", "buddy_compare.svg")
    else:
        out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved {out}")

    ratio = dvb.std() / dva.std() if dva.std() > 0 else float("inf")
    print(f"\nDerivative std ratio (B/A): {ratio:.2f}x")
    if ratio > 1.5:
        print(f"→ {args.b_label} oscillates {ratio:.1f}× more than {args.a_label}")


if __name__ == "__main__":
    main()
