"""Generate every table in the algorithm paper from the result JSONs.

Same discipline as the empirical paper: no number is typed here, each one is read
from a file an experiment wrote.  If a JSON is missing the script fails loudly
rather than emitting a plausible-looking table.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
RES = Path(__file__).resolve().parents[1] / "results"
OUT = Path(__file__).resolve().parents[1] / "tables"
OUT.mkdir(exist_ok=True)


def rows(name):
    p = RES / name
    if not p.exists():
        raise SystemExit(f"missing result file: {p}")
    d = json.loads(p.read_text(encoding="utf-8"))
    return {(r["delta"], r["deadline_days"], r.get("visible", True)): r
            for r in d["rows"]}


def pick(name, delta, deadline=None, visible=True):
    for (dl, dd, vis), r in rows(name).items():
        if abs(dl - delta) < 1e-9 and (deadline is None or abs(dd - deadline) < 1e-9) \
                and vis == visible:
            return r
    raise SystemExit(f"no row delta={delta} deadline={deadline} visible={visible} in {name}")


def agg(rs, key):
    v = np.array([r[key] for r in rs], dtype=float)
    v = v[~np.isnan(v)]
    sd = float(v.std(ddof=1)) if len(v) > 1 else 0.0
    return float(v.mean()), sd


def paired(a, b, key="pair_f1"):
    d = np.array([y[key] - x[key] for x, y in zip(a, b)], dtype=float)
    return float(d.mean()), (float(d.std(ddof=1)) if len(d) > 1 else 0.0)


# ---------------------------------------------------------------- main effect
SEED_BASE = ["e26_decision_timing.json", "e26_seed1.json", "e26_seed2.json",
             "e26_may_s3.json", "e26_may_s4.json", "e26_seed5.json",
             "e26_seed6.json", "e26_seed7.json", "e26_seed8.json"]
SEED_DEFER = ["e26_may_cliff.json", "e26_may_d05_s1.json", "e26_may_d05_s2.json",
              "e26_may_s3.json", "e26_may_s4.json", "e26_seed5.json",
              "e26_seed6.json", "e26_seed7.json", "e26_seed8.json"]
base_may = [pick(f, 0.0) for f in SEED_BASE]
defer_may = [pick(f, 0.5, 7.0) for f in SEED_DEFER]
base_jun = [pick("e26_june.json", 0.0), pick("e26_june_s1.json", 0.0),
            pick("e26_june_s2.json", 0.0), pick("e26_june_s3.json", 0.0),
            pick("e26_june_s4.json", 0.0)]
defer_jun = [pick("e26_june.json", 0.5, 7.0), pick("e26_june_s1.json", 0.5, 7.0),
             pick("e26_june_s2.json", 0.5, 7.0), pick("e26_june_s3.json", 0.5, 7.0),
             pick("e26_june_s4.json", 0.5, 7.0)]
inv_may = [pick("e26_ablation_invisible.json", 0.5, 7.0, visible=False),
           pick("e26_invis_s1.json", 0.5, 7.0, visible=False),
           pick("e26_invis_s2.json", 0.5, 7.0, visible=False),
           pick("e26_invis_s3.json", 0.5, 7.0, visible=False),
           pick("e26_invis_s4.json", 0.5, 7.0, visible=False)]


def line(label, rs, against=None):
    f1, sd = agg(rs, "pair_f1")
    p, _ = agg(rs, "pair_precision")
    r, _ = agg(rs, "pair_recall")
    f, _ = agg(rs, "dec_fmp")
    d = paired(against, rs)[0] if against else 0.0
    dsd = paired(against, rs)[1] if against else 0.0
    dz = f"{d:+.3f}$\\pm${dsd:.3f}" if against else "--"
    return f"{label} & {f1:.3f}$\\pm${sd:.3f} & {dz} & {p:.3f} & {r:.3f} & {f:.3f} \\\\"


L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"Setting & $F_1$ & paired $\Delta F_1$ & P & R & FMP \\", r"\midrule",
     r"\multicolumn{6}{l}{\emph{San Diego, May 2024 (exploratory)}} \\",
     line("arrival-time", base_may),
     line("deferred commit", defer_may, base_may),
     r"\addlinespace",
     r"\multicolumn{6}{l}{\emph{San Diego, June 2024 (later month)}} \\",
     line("arrival-time", base_jun),
     line("deferred commit", defer_jun, base_jun),
     r"\bottomrule", r"\end{tabular}"]
(OUT / "main_effect.tex").write_text("\n".join(L), encoding="utf-8")

# ------------------------------------------------------------------ ablation
L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"Arm & $F_1$ & $\Delta F_1$ vs arrival-time & P & R & FMP \\", r"\midrule",
     line("arrival-time decision", base_may[:5]),
     line("delay, no visibility", inv_may, base_may[:5]),
     line("delay + visibility", defer_may[:5], base_may[:5]),
     r"\bottomrule", r"\end{tabular}"]
(OUT / "ablation.tex").write_text("\n".join(L), encoding="utf-8")

# ---------------------------------------------------------------- stratified

EVIDENCE = Path(__file__).resolve().parent / "evidence"
with (EVIDENCE / "positive_pair_set_counts.csv").open(encoding="utf-8", newline="") as handle:
    pos = {r["bucket"]: r for r in csv.DictReader(handle)}
with (EVIDENCE / "wrong_merge_pair_set_counts.csv").open(encoding="utf-8", newline="") as handle:
    neg = {r["target_age_bucket"]: r for r in csv.DictReader(handle)}
buckets = (("<=1d", r"$\leq1$ d"), ("1-7d", r"$1$--$7$ d"),
           ("8-30d", r"$8$--$30$ d"))
L = [r"\begin{tabular}{lrrr}", r"\toprule",
     r"\multicolumn{4}{l}{Correct partition pairs, by gold-pair lag} \\",
     r"Lag & Common & Lost & New \\", r"\midrule"]
for bucket, label in buckets:
    p = pos[bucket]
    L.append(f"{label} & {int(p['both']):,} & {int(p['base_only']):,} & {int(p['defer_only']):,}" + r" \\")
L.extend([r"\midrule",
          r"\multicolumn{4}{l}{Wrong directed merge pairs, by target age} \\",
          r"Age & Common & Avoided & New \\", r"\midrule"])
for bucket, label in buckets:
    n = neg[bucket]
    L.append(f"{label} & {int(n['both']):,} & {int(n['arrival_only']):,} & {int(n['band_only']):,}" + r" \\")
L += [r"\bottomrule", r"\end{tabular}"]
(OUT / "stratified.tex").write_text("\n".join(L), encoding="utf-8")

# ------------------------------------------------------------------ external
ext = json.loads((RES / "e29_external_baseline.json").read_text(encoding="utf-8"))
L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"External linker (top-$q$) & edges & P & R & $F_1$ & FMP \\", r"\midrule"]
for row in ext["rows"]:
    L.append(f"{row['kept_share']:.4f} & {row['edges']:,} & {row['pair_precision']:.3f} & "
             f"{row['pair_recall']:.3f} & {row['pair_f1']:.3f} & {row['dec_fmp']:.3f} \\\\")
L += [r"\bottomrule", r"\end{tabular}"]
(OUT / "external.tex").write_text("\n".join(L), encoding="utf-8")

print("wrote tables/main_effect.tex, ablation.tex, stratified.tex, external.tex")
for name, a, b in (("May", base_may, defer_may), ("June", base_jun, defer_jun),
                   ("ablation", base_may, inv_may)):
    d, sd = paired(a, b)
    print(f"  {name:9s} delta F1 {d:+.4f} +/- {sd:.4f}")

# ------------------------------------------------- batch versus stream gap
e25 = json.loads((RES / "e25_batch_global.json").read_text(encoding="utf-8"))
L = [r"\begin{tabular}{rrrrrr}", r"\toprule",
     r"$\tau$ & uncapped & capped & streaming & gap & max \\", r"\midrule"]
for r in e25["rows"]:
    b, c, s = r["batch"], r["batch_capped"], r["stream"]
    L.append(f"{r['tau']:.2f} & {b['pair_f1']:.3f} & {c['pair_f1']:.3f} & "
             f"{s['pair_f1']:.3f} & {c['pair_f1']-s['pair_f1']:+.3f} & "
             f"{c['max_component']} \\\\")
L += [r"\bottomrule", r"\end{tabular}"]
(OUT / "batchgap.tex").write_text("\n".join(L), encoding="utf-8")

# ------------------------------------------------------- deadline sensitivity
dt = json.loads((RES / "e26_decision_timing.json").read_text(encoding="utf-8"))
by = {(r["delta"], r["deadline_days"]): r for r in dt["rows"]}

L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"Band & Nominal $D$ & $F_1$ & FMP & Mean & Max \\", r"\midrule"]
for delta in (0.05, 0.2):
    for D in (7.0, 14.0):
        r = by.get((delta, D))
        if r is None:
            continue
        L.append(f"{delta:.2f} & {int(D)} d & {r['pair_f1']:.4f} & "
                 f"{r['dec_fmp']:.4f} & {r['latency_mean']:.1f} d & {r['latency_max']} d" + r" \\")
    if delta == 0.05:
        L.append(r"\addlinespace")
L += [r"\bottomrule", r"\end{tabular}"]
(OUT / "deadline.tex").write_text("\n".join(L), encoding="utf-8")
print("wrote tables/batchgap.tex, deadline.tex")

# ------------------------------------------------------- threshold operating curve
L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"Arm & $F_1$ & P & R & FMP & coverage \\", r"\midrule"]
base_r = pick("e26_decision_timing.json", 0.0)
L.append(f"threshold $\\tau=0.0358$ (arrival) & {base_r['pair_f1']:.4f} & "
         f"{base_r['pair_precision']:.3f} & {base_r['pair_recall']:.3f} & "
         f"{base_r['dec_fmp']:.4f} & {base_r['dec_coverage']:.3f} \\\\")
for name, tau in (("e26_tau050.json", 0.05), ("e26_tau070.json", 0.07),
                  ("e26_tau100.json", 0.10)):
    r = pick(name, 0.0)
    L.append(f"threshold $\\tau={tau:.2f}$ & {r['pair_f1']:.4f} & "
             f"{r['pair_precision']:.3f} & {r['pair_recall']:.3f} & "
             f"{r['dec_fmp']:.4f} & {r['dec_coverage']:.3f} \\\\")
d0 = pick("e26_may_cliff.json", 0.5, 7.0)
L += [r"\midrule",
      f"\\textbf{{deferred, $\\delta=0.5$}} & \\textbf{{{d0['pair_f1']:.4f}}} & "
      f"\\textbf{{{d0['pair_precision']:.3f}}} & \\textbf{{{d0['pair_recall']:.3f}}} & "
      f"\\textbf{{{d0['dec_fmp']:.4f}}} & \\textbf{{{d0['dec_coverage']:.3f}}} \\\\",
      r"\bottomrule", r"\end{tabular}"]
(OUT / "operating.tex").write_text("\n".join(L), encoding="utf-8")
print("wrote tables/operating.tex")

# ------------------------------------------------------ staleness control
L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"Arm & $F_1$ & P & R & FMP & coverage \\", r"\midrule",
     f"arrival-time & {base_r['pair_f1']:.4f} & {base_r['pair_precision']:.3f} & "
     f"{base_r['pair_recall']:.3f} & {base_r['dec_fmp']:.4f} & "
     f"{base_r['dec_coverage']:.3f} \\\\"]
for name, lab in (("e26_stale30.json", "refuse merges older than 30 d"),
                  ("e26_stale14.json", "refuse merges older than 14 d"),
                  ("e26_stale07.json", "refuse merges older than 7 d")):
    r = json.loads((RES / name).read_text(encoding="utf-8"))["rows"][0]
    L.append(f"{lab} & {r['pair_f1']:.4f} & {r['pair_precision']:.3f} & "
             f"{r['pair_recall']:.3f} & {r['dec_fmp']:.4f} & "
             f"{r['dec_coverage']:.3f} \\\\")
L += [r"\midrule",
      f"\\textbf{{deferred, $\\delta=0.5$}} & \\textbf{{{d0['pair_f1']:.4f}}} & "
      f"\\textbf{{{d0['pair_precision']:.3f}}} & \\textbf{{{d0['pair_recall']:.3f}}} & "
      f"\\textbf{{{d0['dec_fmp']:.4f}}} & \\textbf{{{d0['dec_coverage']:.3f}}} \\\\",
      r"\bottomrule", r"\end{tabular}"]
(OUT / "staleness.tex").write_text("\n".join(L), encoding="utf-8")
print("wrote tables/staleness.tex")

# ---------------------------------------------------------- revision arm
e27 = json.loads((RES / "e27_retraction.json").read_text(encoding="utf-8"))
L = [r"\begin{tabular}{lrrrr}", r"\toprule",
     r"Arm & $F_1$ & FMP & coverage & recall \\", r"\midrule",
     f"no revision & {base_r['pair_f1']:.4f} & {base_r['dec_fmp']:.4f} & "
     f"{base_r['dec_coverage']:.3f} & {base_r['pair_recall']:.3f} \\\\"]
for r in e27["rows"]:
    L.append(f"revise after {int(r['revisit_days'])} d & {r['pair_f1']:.4f} & "
             f"{r['dec_fmp']:.4f} & {r['dec_coverage']:.3f} & "
             f"{r['pair_recall']:.3f} \\\\")
L += [r"\bottomrule", r"\end{tabular}"]
(OUT / "revise.tex").write_text("\n".join(L), encoding="utf-8")
print("wrote tables/revise.tex")

# ------------------------------------------------------ staleness control (5 seeds)
_st7 = [pick("e26_stale10.json", 0.0), pick("e26_stale10_s1.json", 0.0),
        pick("e26_stale10_s2.json", 0.0), pick("e26_stale10_s3.json", 0.0),
        pick("e26_stale10_s4.json", 0.0), pick("e26_stale10_s5.json", 0.0),
        pick("e26_stale10_s6.json", 0.0), pick("e26_stale10_s7.json", 0.0),
        pick("e26_stale10_s8.json", 0.0)]
L = [r"\begin{tabular}{lrrrr}", r"\toprule",
     r"Arm & seeds & $F_1$ & FMP & recall \\", r"\midrule",
     f"arrival-time & 9 & {agg(base_may,'pair_f1')[0]:.4f}$\\pm${agg(base_may,'pair_f1')[1]:.4f} & "
     f"{agg(base_may,'dec_fmp')[0]:.4f} & {agg(base_may,'pair_recall')[0]:.3f} \\\\"]
for name, lab, n in (("e26_stale30.json", "refuse merges older than 30 d", 1),
                     ("e26_stale14.json", "refuse merges older than 14 d", 1)):
    r = json.loads((RES / name).read_text(encoding="utf-8"))["rows"][0]
    L.append(f"{lab} & {n} & {r['pair_f1']:.4f} & {r['dec_fmp']:.4f} & "
             f"{r['pair_recall']:.3f} \\\\")
L.append(f"refuse merges older than 10 d & 9 & {agg(_st7,'pair_f1')[0]:.4f}$\\pm$"
         f"{agg(_st7,'pair_f1')[1]:.4f} & {agg(_st7,'dec_fmp')[0]:.4f} & "
         f"{agg(_st7,'pair_recall')[0]:.3f} \\\\")
L += [r"\midrule",
      f"deferred, $\\delta=0.5$ & 9 & {agg(defer_may,'pair_f1')[0]:.4f}$\\pm$"
      f"{agg(defer_may,'pair_f1')[1]:.4f} & {agg(defer_may,'dec_fmp')[0]:.4f} & "
      f"{agg(defer_may,'pair_recall')[0]:.3f} \\\\",
      r"\bottomrule", r"\end{tabular}"]
(OUT / "staleness.tex").write_text("\n".join(L), encoding="utf-8")
print("rewrote tables/staleness.tex with 5 seeds where available")

# ------------------------------------------------- end-to-end external system
_e31 = json.loads((RES / "e31_splink_sandiego.json").read_text(encoding="utf-8"))
_e32 = json.loads((RES / "e32_splink_supervised.json").read_text(encoding="utf-8"))
_e34 = json.loads((RES / "e34_splink_prior.json").read_text(encoding="utf-8"))
def _best(j, arm):
    rows = [r for r in j["rows"] if r.get("arm", "native") == arm]
    return max(rows, key=lambda r: r["pair_f1"])
_sn = _best(_e31, "native"); _sc = _best(_e31, "capped"); _ss = _best(_e32, "capped")
_sp = _best(_e34, "capped")
_d0 = pick("e26_may_cliff.json", 0.5, 7.0)
L = [r"\begin{tabular}{lrrrrr}", r"\toprule",
     r"Arm & $F_1$ & P & R & FMP & max on $\mathcal{K}$ \\", r"\midrule",
     f"Splink, native clustering & {_sn['pair_f1']:.4f} & {_sn['pair_precision']:.3f} & "
     f"{_sn['pair_recall']:.3f} & {_sn['dec_fmp']:.3f} & {_sn['max_pred_cluster']} \\\\",
     f"Splink, cap 20 & {_sc['pair_f1']:.4f} & {_sc['pair_precision']:.3f} & "
     f"{_sc['pair_recall']:.3f} & {_sc['dec_fmp']:.3f} & {_sc['max_pred_cluster']} \\\\",
     f"Splink, supervised + cap 20 & {_ss['pair_f1']:.4f} & {_ss['pair_precision']:.3f} & "
     f"{_ss['pair_recall']:.3f} & {_ss['dec_fmp']:.3f} & {_ss['max_pred_cluster']} \\\\",
     f"Splink, prior + cap 20 & {_sp['pair_f1']:.4f} & {_sp['pair_precision']:.3f} & "
     f"{_sp['pair_recall']:.3f} & {_sp['dec_fmp']:.3f} & {_sp['max_pred_cluster']} \\\\",
     r"\midrule",
     f"this pipeline, arrival-time & {agg(base_may,'pair_f1')[0]:.4f} & "
     f"{agg(base_may,'pair_precision')[0]:.3f} & {agg(base_may,'pair_recall')[0]:.3f} & "
     f"{agg(base_may,'dec_fmp')[0]:.3f} & {max(r['max_pred_cluster'] for r in base_may)} \\\\",
     f"this pipeline, deferred & {agg(defer_may,'pair_f1')[0]:.4f} & "
     f"{agg(defer_may,'pair_precision')[0]:.3f} & {agg(defer_may,'pair_recall')[0]:.3f} & "
     f"{agg(defer_may,'dec_fmp')[0]:.3f} & {max(r['max_pred_cluster'] for r in defer_may)} \\\\",
     r"\bottomrule", r"\end{tabular}"]
(OUT / "splink.tex").write_text("\n".join(L), encoding="utf-8")
print("wrote tables/splink.tex")

# ------------------------------------------------- deferral selection rules
_GATE = ["e26_sv2_s1.json", "e26_sv2_s2.json", "e26_sv2_s3.json", "e26_sv2_s4.json",
         "e26_sv2_s5.json", "e26_sv2_s6.json", "e26_sv2_s7.json", "e26_sv2_s8.json",
         "e26_svb_s2min.json"]
_GATE5 = ["e26_sv5_s1.json", "e26_sv5_s2.json", "e26_sv5_s3.json",
          "e26_sv5_s4.json", "e26_sv5_s5.json", "e26_sv5_s6.json",
          "e26_sv5_s7.json", "e26_sv5_s8.json", "e26_svb_s5min.json"]
_CUT = ["e26_stale10_s1.json", "e26_stale10_s2.json", "e26_stale10_s3.json",
        "e26_stale10_s4.json", "e26_stale10_s5.json", "e26_stale10_s6.json",
        "e26_stale10_s7.json", "e26_stale10_s8.json", "e26_stale10.json"]
gate = [pick(f, 0.5, 7.0) for f in _GATE]
gate5 = [pick(f, 0.5, 7.0) for f in _GATE5]
cut9 = [pick(f, 0.0) for f in _CUT]
L = [r"\begin{tabular}{lrrrr}", r"\toprule",
     r"Rule & $F_1$ & FMP & coverage & held \\", r"\midrule"]
for lab, arm in (("arrival-time", base_may),
                 ("band", defer_may),
                 ("gate $s=5$ d", gate5),
                 ("gate $s=2$ d", gate),
                 ("cutoff $X=10$ d", cut9)):
    f1 = agg(arm, "pair_f1")[0]
    f1s = agg(arm, "pair_f1")[1]
    fmp = agg(arm, "dec_fmp")[0]
    cov = agg(arm, "dec_coverage")[0]
    held = agg(arm, "deferred_share")[0] * 100 if any(r.get("deferred_share") for r in arm) else 0.0
    L.append(f"{lab} & {f1:.4f}$\\pm${f1s:.4f} & {fmp:.4f} & {cov:.3f} & "
             f"{held:.1f}\\% \\\\")
L += [r"\bottomrule", r"\end{tabular}"]
(OUT / "gate.tex").write_text("\n".join(L), encoding="utf-8")
print("wrote tables/gate.tex")
