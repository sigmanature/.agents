#!/usr/bin/env python3
"""Generate charts for madvise root cause analysis paper section."""
import json, os, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

plt.rcParams['font.family'] = 'AR PL UMing CN'
plt.rcParams['axes.unicode_minus'] = False
try:
    plt.rcParams['font.sans-serif'] = ['AR PL UMing CN']
except:
    pass

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else '/tmp/madvise_charts'
os.makedirs(OUT_DIR, exist_ok=True)

JSON_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'analysis_result.json')

with open(JSON_PATH) as f:
    d = json.load(f)

N = d['events']['madvise_dontneed']
vma_cause = d['classification']['mmap_vma_cause']
madv_cause = d['classification']['madvise_cause']
aligned_ok = d['classification']['aligned_both']
triggered_split = d['correlation']['triggered_split']

# ---- Chart 1: 饼图 - 不对齐根因分类 ----
fig, ax = plt.subplots(figsize=(6, 5))
labels = [f'VMA不对齐\n( {vma_cause/N*100:.1f}%)',
          f'madvise不对齐\n( {madv_cause/N*100:.1f}%)',
          f'双方对齐\n( {aligned_ok/N*100:.1f}%)']
sizes = [vma_cause, madv_cause, aligned_ok]
colors = ['#e74c3c', '#f39c12', '#2ecc71']
explode = (0.03, 0.03, 0)
wedges, texts, autotexts = ax.pie(sizes, explode=explode, labels=labels, colors=colors,
                                    autopct='', startangle=90, pctdistance=0.6)
ax.set_title(f'madvise(DONTNEED) 16KB 对齐问题根因分类\n总次数: {N:,}', fontsize=13, fontweight='bold')
# Add count to labels manually
for i, (w, s) in enumerate(zip(wedges, sizes)):
    ang = (w.theta2 - w.theta1) / 2. + w.theta1
    x = np.cos(np.deg2rad(ang))
    y = np.sin(np.deg2rad(ang))
    ax.annotate(f'{s:,}', xy=(x*0.6, y*0.6), ha='center', va='center', fontsize=11, fontweight='bold')
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'fig1_pie_root_cause.png'), dpi=150)
plt.close()
print(f'[saved] fig1_pie_root_cause.png')

# ---- Chart 2: 横向堆叠柱状图 - Top 5 + Others ----
pp = d['per_process']
sorted_pp = sorted(pp.items(), key=lambda x: x[1]['total_madvise'], reverse=True)
top5 = []
others = {'total_madvise': 0, 'madvise_cause': 0, 'mmap_vma_cause': 0, 'aligned_both': 0}
for i, (comm, s) in enumerate(sorted_pp):
    if i < 5:
        top5.append((comm, s))
    else:
        for k in others:
            others[k] += s[k]
all_procs = top5 + [('其他 (~200进程)', others)]

fig, ax = plt.subplots(figsize=(10, 4.5))
names = [x[0][:25] for x in all_procs]
y_pos = range(len(names))
bar_height = 0.6

vma_vals = [x[1]['mmap_vma_cause'] for x in all_procs]
madv_vals = [x[1]['madvise_cause'] for x in all_procs]
ok_vals = [x[1]['aligned_both'] for x in all_procs]

bars1 = ax.barh(y_pos, vma_vals, bar_height, label='VMA不对齐 (mmap是果)', color='#e74c3c')
bars2 = ax.barh(y_pos, madv_vals, bar_height, left=vma_vals, label='madvise不对齐 (madv是因)', color='#f39c12')
bars3 = ax.barh(y_pos, ok_vals, bar_height, left=[a+b for a,b in zip(vma_vals, madv_vals)],
                label='双方对齐', color='#2ecc71')

# Annotate percentages
for i, (name, s) in enumerate(all_procs):
    t = s['total_madvise']
    v_pct = s['mmap_vma_cause'] / t * 100
    m_pct = s['madvise_cause'] / t * 100
    ax.text(t + vma_vals[0]*0.015, i,
            f'VMA {v_pct:.0f}% | madv {m_pct:.0f}% | 共{t:,}',
            va='center', fontsize=8.5, color='#333')

ax.set_yticks(y_pos)
ax.set_yticklabels(names, fontsize=10)
ax.set_xlabel('madvise 次数', fontsize=11)
ax.set_title('各进程 madvise(DONTNEED) 16KB 不对齐根因分布', fontsize=13, fontweight='bold')
ax.legend(loc='lower right', fontsize=8.5)
ax.set_xlim(0, max(vma_vals) * 1.5)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'fig2_bar_per_process.png'), dpi=150)
plt.close()
print(f'[saved] fig2_bar_per_process.png')

# ---- Chart 3: 频率直方图 - VMA边界偏移分布 ----
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

# VMA start offset (from 50MB trace sample)
offsets_vma = {0: 45173, 4096: 37071, 8192: 36669, 12288: 30452}
labels_off = ['0x0000\n(对齐)', '0x1000\n(+4KB)', '0x2000\n(+8KB)', '0x3000\n(+12KB)']
vals_vma = [offsets_vma[0], offsets_vma[4096], offsets_vma[8192], offsets_vma[12288]]
total_vma = sum(vals_vma)
bars = ax1.bar(range(4), vals_vma, color=['#2ecc71', '#e74c3c', '#e74c3c', '#e74c3c'], edgecolor='white')
for bar, v in zip(bars, vals_vma):
    ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + total_vma*0.01,
             f'{v/total_vma*100:.1f}%', ha='center', fontsize=10, fontweight='bold')
ax1.set_xticks(range(4))
ax1.set_xticklabels(labels_off)
ax1.set_ylabel('次数', fontsize=11)
ax1.set_title('VMA 起始地址偏移分布\n(相对 16KB 边界)', fontsize=12, fontweight='bold')

# madvise start offset
offsets_madv = {0: 39412, 4096: 35564, 8192: 36043, 12288: 38346}
vals_madv = [offsets_madv[0], offsets_madv[4096], offsets_madv[8192], offsets_madv[12288]]
total_madv = sum(vals_madv)
bars = ax2.bar(range(4), vals_madv, color=['#2ecc71', '#e74c3c', '#e74c3c', '#e74c3c'], edgecolor='white')
for bar, v in zip(bars, vals_madv):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + total_madv*0.01,
             f'{v/total_madv*100:.1f}%', ha='center', fontsize=10, fontweight='bold')
ax2.set_xticks(range(4))
ax2.set_xticklabels(labels_off)
ax2.set_ylabel('次数', fontsize=11)
ax2.set_title('madvise 起始地址偏移分布\n(相对 16KB 边界)', fontsize=12, fontweight='bold')

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'fig3_hist_offset_dist.png'), dpi=150)
plt.close()
print(f'[saved] fig3_hist_offset_dist.png')

# ---- Chart 4: 触发split的madvise占比 饼图 ----
fig, ax = plt.subplots(figsize=(5, 4.5))
triggered = triggered_split
not_triggered = N - triggered_split
labels = [f'触发 deferred split\n({triggered:,} 次, {triggered/N*100:.1f}%)',
          f'未触发 split\n({not_triggered:,} 次, {not_triggered/N*100:.1f}%)']
sizes2 = [triggered, not_triggered]
colors2 = ['#e74c3c', '#bdc3c7']
wedges, _, _ = ax.pie(sizes2, labels=labels, colors=colors2, startangle=90,
                       autopct='', explode=(0.05, 0))
ax.set_title(f'madvise(DONTNEED) 中触发\ndeferred_split 的比例', fontsize=12, fontweight='bold')
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'fig4_pie_split_rate.png'), dpi=150)
plt.close()
print(f'[saved] fig4_pie_split_rate.png')

print(f'\n[all charts saved] {OUT_DIR}/')