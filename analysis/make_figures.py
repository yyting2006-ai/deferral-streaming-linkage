"""Evidence-led figures from the verified observation table.

Python / Matplotlib. Point offsets separate observations without changing values.
Uncertainty is sample SD (ddof=1), conditional on each fixed stream.
"""
from pathlib import Path
import argparse
import csv
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'analysis' / 'figure_data'
parser = argparse.ArgumentParser()
parser.add_argument('--data', type=Path, default=RUN / 'source_data.csv')
parser.add_argument('--baseline', type=Path, default=RUN / 'numeric_checks.json')
parser.add_argument('--out', type=Path, default=ROOT / 'build' / 'figures')
parser.add_argument('--qa', type=Path, default=ROOT / 'build' / 'figures' / 'qa')
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
args.qa.mkdir(parents=True, exist_ok=True)

with args.data.open(encoding='utf-8-sig', newline='') as f:
    DATA = list(csv.DictReader(f))
USED = set()

plt.rcParams.update({
    'font.family': 'sans-serif', 'font.sans-serif': ['Arial', 'DejaVu Sans'],
    'font.size': 8, 'axes.labelsize': 8, 'xtick.labelsize': 7.5,
    'ytick.labelsize': 7.5, 'legend.fontsize': 7.5,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.linewidth': .65, 'axes.edgecolor': '#5B5B62',
    'xtick.major.width': .65, 'ytick.major.width': .65,
    'xtick.major.size': 2.7, 'ytick.major.size': 2.7,
    'text.color': '#24242A', 'axes.labelcolor': '#24242A',
    'xtick.color': '#414149', 'ytick.color': '#414149',
    'pdf.fonttype': 42, 'svg.fonttype': 'none',
    'savefig.facecolor': 'white', 'figure.facecolor': 'white',
})
BASE = '#707C89'
SIGNAL = '#715BA6'
CONTROL = '#C28B55'
LINK = '#C9CBD2'
INK = '#24242A'
MUTED = '#777782'
SEEDS = [1, 2, 3, 4, 5, 6, 7, 8, 13]
FIVE = [1, 2, 3, 4, 13]


def get(panel, delta, metric='pair_f1', seeds=None, visible=None, source=None):
    rs = []
    for idx, row in enumerate(DATA):
        if (row['panel'] == panel and float(row['delta']) == delta
                and row['metric'] == metric
                and (visible is None or row['visible'] == str(visible))
                and (source is None or row['source'] == source)):
            rs.append(row)
            USED.add(idx)
    if seeds is not None:
        lookup = {int(r['seed']): float(r['value']) for r in rs}
        assert len(rs) == len(seeds) == len(lookup)
        return np.array([lookup[s] for s in seeds])
    return np.array([float(r['value']) for r in rs])


bf, df = get('2a', 0, seeds=SEEDS), get('2a', .5, seeds=SEEDS)
bm, dm = get('2b', 0, 'dec_fmp', SEEDS), get('2b', .5, 'dec_fmp', SEEDS)
ab = np.array([get('2c', 0, seeds=FIVE),
               get('2c', .5, seeds=FIVE, visible=False),
               get('2c', .5, seeds=FIVE, visible=True)])
jb, jd = get('2d', 0, seeds=FIVE), get('2d', .5, seeds=FIVE)
buckets = ['<=1d', '1-7d', '8-30d']
found = np.array([[get('3a', d, 'certified_pairs:' + k)[0]
                   for k in buckets] for d in [0, .5]])
wrong = np.array([[get('3b', d, 'wrong_merges:' + k)[0]
                   for k in buckets] for d in [0, .5]])
cd = get('3c', .1, source='e26_ch_dev_cal.json') - get('3c', 0, source='e26_ch_dev_cal.json')
ct = get('3c', .1, source='e26_ch_test.json') - get('3c', 0, source='e26_ch_test.json')
ap = np.array([get('3d', d, seeds=FIVE) for d in [.3, .5, .7]])
assert len(USED) == len(DATA), 'Every source observation must be used.'
assert np.all(df > bf) and np.all(jd > jb)
assert np.all(bm > .4) and np.all(dm < .4)

summary = {
    'may_delta_mean': float((df-bf).mean()), 'may_delta_sd': float((df-bf).std(ddof=1)),
    'june_delta_mean': float((jd-jb).mean()),
    'ablation_delta_mean': float((ab[1]-ab[0]).mean()),
    'ablation_means': ab.mean(axis=1).tolist(),
    'april_means': ap.mean(axis=1).tolist(), 'april_sd': ap.std(axis=1, ddof=1).tolist(),
    'wrong_merge_deltas': (wrong[1]-wrong[0]).tolist(),
    'chicago_dev_delta': float(cd[0]), 'chicago_test_delta': float(ct[0]),
}
baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
for key, value in summary.items():
    assert np.allclose(value, baseline[key], rtol=0, atol=1e-14), key
(args.qa / 'numeric_checks.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
(args.qa / 'observation_check.json').write_text(json.dumps({
    'input_rows': len(DATA), 'used_rows': len(USED), 'excluded_rows': 0,
    'uncertainty': 'sample SD, ddof=1', 'numeric_tolerance': 1e-14}, indent=2), encoding='utf-8')


def panel_title(fig, ax, label, title):
    p = ax.get_position()
    y = p.y1 + 9 / (fig.get_figheight() * 72)
    fig.text(p.x0 - 12 / (fig.get_figwidth() * 72), y, label,
             fontsize=10, fontweight='bold', va='bottom')
    fig.text(p.x0, y, title, fontsize=8.5, fontweight='bold', va='bottom')


def key(fig):
    handles = [
        Line2D([], [], marker='o', ls='', ms=4, mfc='white', mec=BASE,
               mew=.8, label='Arrival-time'),
        Line2D([], [], marker='s', ls='', ms=4, mfc=SIGNAL, mec=SIGNAL,
               label='Deferred, visible'),
    ]
    fig.legend(handles=handles, loc='upper right', bbox_to_anchor=(.98, .995),
               ncol=2, frameon=False, columnspacing=1.6, handletextpad=.35,
               borderaxespad=0)


def paired(ax, arrays, xs, colors, markers, spread=.055, means=True):
    n = len(arrays[0])
    offsets = np.linspace(-spread, spread, n)
    for j in range(n):
        ax.plot(np.array(xs) + offsets[j], [row[j] for row in arrays],
                color=LINK, lw=.65, zorder=1)
    for vals, x, c, m in zip(arrays, xs, colors, markers):
        ax.scatter(x + offsets, vals, s=15, marker=m, zorder=3,
                   facecolors='white' if c == BASE else c, edgecolors=c, linewidths=.7)
        if means:
            ax.errorbar(x + .16, vals.mean(), yerr=vals.std(ddof=1),
                        fmt='_', ms=6.5, color=c, lw=1.05, capsize=2.3,
                        capthick=.8, zorder=4)


def export(fig, name):
    fig.canvas.draw()
    # Local geometry check only; no external style-library dependency.
    axes = [ax.get_position().bounds for ax in fig.axes]
    for x, y, width, height in axes:
        assert x >= 0 and y >= 0 and x + width <= 1 and y + height <= 1
    overlaps = []
    for i, (x, y, width, height) in enumerate(axes):
        for j, (xx, yy, ww, hh) in enumerate(axes[i+1:], start=i+1):
            if min(x+width, xx+ww)-max(x,xx) > 1e-9 and min(y+height, yy+hh)-max(y,yy) > 1e-9:
                overlaps.append([i,j])
    assert not overlaps, overlaps
    (args.qa / f'{name}.geometry.json').write_text(json.dumps({
        'scope': 'Axes within canvas and non-overlapping; text/collision/font review remains visual.',
        'axes_bounds': axes, 'overlapping_axes': overlaps}, indent=2), encoding='utf-8')
    fig.savefig(args.out / f'{name}.pdf')
    fig.savefig(args.out / f'{name}.svg')
    fig.savefig(args.out / f'{name}.png', dpi=600)
    fig.savefig(args.out / f'{name}.tiff', dpi=600,
                pil_kwargs={'compression': 'tiff_lzw'})
    fig.savefig(args.qa / f'{name}.preview.png', dpi=300)
    plt.close(fig)


# Figure 2: primary evidence spans both rows; controls share the right column.
fig = plt.figure(figsize=(7.08, 3.9))
gs = fig.add_gridspec(2, 2, width_ratios=[1.4, 1],
                     left=.085, right=.975, bottom=.13, top=.85,
                     wspace=.44, hspace=.96)
a = fig.add_subplot(gs[:, 0])
b = fig.add_subplot(gs[0, 1])
c = fig.add_subplot(gs[1, 1])
key(fig)
paired(a, [bf, df], [0, .9], [BASE, SIGNAL], ['o', 's'])
paired(a, [jb, jd], [2, 2.9], [BASE, SIGNAL], ['o', 's'])
a.set(xlim=(-.28, 3.34), ylim=(.4, .535),
      xticks=[0, .9, 2, 2.9], xticklabels=['Arrival', 'Deferred', 'Arrival', 'Deferred'],
      yticks=[.40, .42, .44, .46, .48, .50, .52], ylabel='Pairwise F1')
a.tick_params(axis='x', length=0, pad=5)
a.spines['bottom'].set_visible(False)
for center, label, delta in [(.45, 'May  ·  n = 9', df-bf),
                            (2.45, 'June  ·  n = 5', jd-jb)]:
    a.text(center, .529, label, ha='center', va='center', fontsize=8, fontweight='bold')
    a.text(center, .521, f'ΔF1  {delta.mean():+.3f} ± {delta.std(ddof=1):.3f}',
           ha='center', va='center', fontsize=7.5, color=SIGNAL)
panel_title(fig, a, 'a', 'Linkage quality across months')

paired(b, [bm, dm], [0, 1], [BASE, SIGNAL], ['o', 's'])
b.axhline(.4, color='#96959E', lw=.75, ls=(0, (3, 3)), zorder=0)
b.text(1.28, .408, 'Budget 0.40', ha='right', fontsize=7.5, color=MUTED)
b.set(xlim=(-.28, 1.35), ylim=(.29, .47), xticks=[0, 1],
      xticklabels=['Arrival', 'Deferred'], yticks=[.30, .35, .40, .45],
      ylabel='False-merge proportion')
b.spines['bottom'].set_visible(False)
b.tick_params(axis='x', length=0, pad=4)
panel_title(fig, b, 'b', 'Conditional merge error')

paired(c, ab, [0, 1, 2], [BASE, CONTROL, SIGNAL], ['o', 'D', 's'])
c.set(xlim=(-.28, 2.35), ylim=(.40, .49), xticks=[0, 1, 2],
      xticklabels=['Arrival', 'Delay only', 'Visible'],
      yticks=[.40, .44, .48], ylabel='Pairwise F1')
c.spines['bottom'].set_visible(False)
c.tick_params(axis='x', length=0, pad=4)
panel_title(fig, c, 'c', 'Visibility ablation')
export(fig, 'fig2_main_result_nature')


# Figure 3: matched count units above, effects and selection sensitivity below.
fig, axes = plt.subplots(2, 2, figsize=(7.08, 3.85))
fig.subplots_adjust(left=.14, right=.97, bottom=.14, top=.85,
                    wspace=.53, hspace=.97)
key(fig)
for ax, arr, letter, title, xmax, ticks, xlabel in [
    (axes[0, 0], found, 'a', 'Correct pairs by pair lag', 2350,
     [0, 1000, 2000], 'Recovered correct pairs'),
    (axes[0, 1], wrong, 'b', 'Wrong merges by target age', 600,
     [0, 200, 400, 600], 'Wrong logged operations')]:
    y = np.arange(3)
    ax.hlines(y, arr[0], arr[1], color=LINK, lw=1.1, zorder=1)
    ax.scatter(arr[0], y, marker='o', s=25, facecolors='white',
               edgecolors=BASE, linewidths=.9, zorder=3)
    ax.scatter(arr[1], y, marker='s', s=22, c=SIGNAL, zorder=4)
    ax.set(ylim=(2.65, -.55), xlim=(-xmax*.025, xmax),
           yticks=y, yticklabels=['≤1 day', '1–7 days', '8–30 days'],
           xticks=ticks, xlabel=xlabel)
    ax.spines['left'].set_visible(False)
    ax.tick_params(axis='y', length=0, pad=5)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:,.0f}'))
    panel_title(fig, ax, letter, title)
axes[0, 1].text(200, 2.40, '200', ha='center', va='center', fontsize=7.5, color=SIGNAL)
axes[0, 1].text(508, 2.40, '508', ha='center', va='center', fontsize=7.5, color=BASE)

ax = axes[1, 0]
diffs = [df-bf, jd-jb, ab[1]-ab[0], cd, ct]
for i, vals in enumerate(diffs):
    color = SIGNAL if vals.mean() > 0 else CONTROL
    if len(vals) > 1:
        ax.errorbar(vals.mean(), i, xerr=vals.std(ddof=1), fmt='o',
                    color=color, ms=4, lw=1.2, capsize=2.5, zorder=3)
    else:
        ax.scatter(vals.mean(), i, c=color, marker='D', s=20, zorder=3)
ax.axvline(0, color='#9A99A3', lw=.7, ls=(0, (3, 3)), zorder=0)
ax.set(ylim=(4.55, -.55), xlim=(-.016, .055), xticks=[-.01, 0, .02, .04],
       yticks=np.arange(5), yticklabels=['May', 'June', 'No visibility', 'Chicago dev', 'Chicago test'],
       xlabel='Paired ΔF1')
ax.spines['left'].set_visible(False)
ax.tick_params(axis='y', length=0, pad=5)
panel_title(fig, ax, 'c', 'Effect size and transfer')

ax = axes[1, 1]
x = [.3, .5, .7]
mean, sd = ap.mean(axis=1), ap.std(axis=1, ddof=1)
ax.fill_between(x, mean-sd, mean+sd, color=SIGNAL, alpha=.14, linewidth=0, zorder=1)
for vals in ap.T:
    ax.plot(x, vals, color=LINK, lw=.75, zorder=2)
ax.plot(x, mean, color=SIGNAL, marker='s', ms=4, lw=1.4, zorder=3)
ax.set(xlim=(.27, .73), ylim=(.435, .515), xticks=x,
       yticks=[.44, .46, .48, .50], xlabel='Band width δ', ylabel='Pairwise F1')
panel_title(fig, ax, 'd', 'April band-width sensitivity')
export(fig, 'fig3_mechanism_nature')
print(f'Exported both figures; {len(USED)}/{len(DATA)} observations retained; numerical checks passed.')
