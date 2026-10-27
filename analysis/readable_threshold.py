"""Complete threshold paths with a shared decision-region detail view.

The upper row preserves every finite observation; the lower row magnifies the
same paths and March-selected operating points. No smoothing or reselection.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from .readable_plot_common import (
    BLUE, INK, MM, MUTED, ORANGE, WIDTH_MM, panel, plt, read_json, result, style,
)


GRID = (0.01, 0.02, 0.0358, 0.05, 0.07, 0.1, 0.15,
        0.2, 0.3, 0.5, 0.7, 0.9, 0.99)
FAMILIES = (("gbt", BLUE, "o"), ("logit", ORANGE, "^"))
ZOOM_X = (0.28, 0.55)
ZOOM_Y = (0.30, 0.57)


def _clipped_segments(x, y, xlim, ylim):
    """Clip straight display segments to the zoom, without moving observations.

    Explicit segment clipping also keeps PDF geometry audits from treating
    invisible off-axis portions of a path as visible strokes over tick labels.
    """
    for x0, y0, x1, y1 in zip(x[:-1], y[:-1], x[1:], y[1:]):
        if not all(np.isfinite(v) for v in (x0, y0, x1, y1)):
            continue
        dx, dy = x1 - x0, y1 - y0
        enter, leave = 0.0, 1.0
        visible = True
        for p, q in ((-dx, x0 - xlim[0]), (dx, xlim[1] - x0),
                     (-dy, y0 - ylim[0]), (dy, ylim[1] - y0)):
            if p == 0:
                if q < 0:
                    visible = False
                    break
            elif p < 0:
                enter = max(enter, q / p)
            else:
                leave = min(leave, q / p)
        if visible and enter <= leave:
            yield [x0 + enter * dx, x0 + leave * dx], [y0 + enter * dy, y0 + leave * dy]


def _selected_label(ax, row, text, xytext, color, align="left"):
    """Place the label in vacant data space, with an unmasked leader."""
    ax.annotate(
        text, xy=(row["proxy_fmp"], row["recall"]), xytext=xytext,
        fontsize=9.5, color=color, ha=align, va="center",
        arrowprops={"arrowstyle": "-", "color": color, "lw": 0.7,
                    "shrinkA": 4, "shrinkB": 6},
        zorder=6,
    )


def threshold_curve(exp, run, protocol):
    """Export complete seed-13 arrival-policy risk–recall observations."""
    run = Path(run)
    selection = read_json(run / "reports" / "calibration_selection.json")
    grid = protocol["threshold"]["grid"]
    if tuple(grid) != GRID:
        raise ValueError("The complete frozen 13-threshold grid is required")
    budget = protocol["threshold"]["risk_budget"]
    min_fraction = protocol["threshold"]["min_merge_fraction"]
    min_known = protocol["threshold"]["min_adjudicable_merges"]
    rows = []
    for window, families in (("March", ("gbt", "logit")), ("May", ("gbt",))):
        for family in families:
            for tau in grid:
                row = result(run, window, family, 13, f"tau{tau:g}")
                row["volume_eligible"] = (
                    row["merges"] >= min_fraction * row["records"]
                    and row["known_merges"] >= min_known
                )
                row["risk_feasible"] = (
                    row["volume_eligible"] and row["proxy_fmp"] is not None
                    and row["proxy_fmp"] <= budget
                )
                row["selected_in_march"] = math.isclose(
                    tau, selection["families"][family]["tau"]
                )
                rows.append(row)

    fig, axes = plt.subplots(2, 2, figsize=(WIDTH_MM / MM, 108 / MM))
    fig.subplots_adjust(left=0.115, right=0.978, bottom=0.145, top=0.85,
                        wspace=0.27, hspace=0.78)
    missing = {}
    counts = {}
    for col, window in enumerate(("March", "May")):
        for row_index in (0, 1):
            ax = axes[row_index, col]
            zoom = row_index == 1
            xlim = ZOOM_X if zoom else (0, 1.05)
            ylim = ZOOM_Y if zoom else (0, 0.65)
            if not zoom:
                ax.add_patch(Rectangle(
                    (ZOOM_X[0], ZOOM_Y[0]), ZOOM_X[1] - ZOOM_X[0],
                    ZOOM_Y[1] - ZOOM_Y[0], facecolor="#F0F3F5",
                    edgecolor="#C7D0D6", linewidth=0.7, zorder=0,
                ))
            ax.axvline(budget, color=MUTED, lw=0.9, ls=(0, (3, 3)), zorder=1)
            for family, color, marker in FAMILIES:
                data = [r for r in rows if r["window"] == window and r["family"] == family]
                if not data:
                    continue
                if not zoom:
                    key = f"{window}_{family}"
                    missing[key] = sum(r["proxy_fmp"] is None for r in data)
                    counts[key] = {
                        "thresholds": len(data),
                        "finite_risk": sum(r["proxy_fmp"] is not None for r in data),
                        "volume_eligible": sum(r["volume_eligible"] for r in data),
                        "risk_feasible": sum(r["risk_feasible"] for r in data),
                    }
                x = [r["proxy_fmp"] if r["proxy_fmp"] is not None else np.nan for r in data]
                y = [r["recall"] if r["recall"] is not None else np.nan for r in data]
                # Input order is ascending threshold, never sorted by risk.
                segments = _clipped_segments(x, y, xlim, ylim) if zoom else [(x, y)]
                for sx, sy in segments:
                    ax.plot(sx, sy, color=color, lw=1.1, zorder=2)
                for r, xx, yy in zip(data, x, y):
                    if not np.isfinite(xx) or not np.isfinite(yy):
                        continue
                    if zoom and not (xlim[0] <= xx <= xlim[1] and ylim[0] <= yy <= ylim[1]):
                        continue
                    ax.scatter(
                        xx, yy, s=29 if zoom else 25, marker=marker,
                        facecolor=color if r["volume_eligible"] else "white",
                        edgecolor=color, linewidth=0.9, zorder=4, clip_on=zoom,
                    )
                    if r["selected_in_march"]:
                        ax.scatter(xx, yy, s=91 if zoom else 67, marker="D",
                                   facecolor="none", edgecolor=INK, linewidth=0.9,
                                   zorder=5, clip_on=zoom)
            ax.set(xlim=xlim, ylim=ylim)
            if zoom:
                ax.set_xticks([0.3, 0.4, 0.5])
                ax.set_yticks([0.3, 0.4, 0.5])
            else:
                ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
                ax.set_yticks([0, 0.2, 0.4, 0.6])
            panel(ax, chr(97 + row_index * 2 + col),
                  f"{window} · " + ("decision region" if zoom else "full grid"))
            style(ax, axis="y")

    selected = {(r["window"], r["family"]): r for r in rows if r["selected_in_march"]}
    _selected_label(axes[1, 0], selected["March", "gbt"], "GBT τ = .0358",
                    (0.288, 0.544), BLUE)
    _selected_label(axes[1, 0], selected["March", "logit"], "Logistic τ = .20",
                    (0.438, 0.362), ORANGE)
    _selected_label(axes[1, 1], selected["May", "gbt"], "March τ = .0358",
                    (0.305, 0.540), BLUE)

    handles = [
        Line2D([], [], marker="o", color=BLUE, lw=1.1, markersize=5),
        Line2D([], [], marker="^", color=ORANGE, lw=1.1, markersize=5),
        Line2D([], [], marker="o", color=MUTED, markerfacecolor="white", ls="none", markersize=5),
        Line2D([], [], color=MUTED, ls=(0, (3, 3)), lw=0.9),
    ]
    fig.legend(handles, ["GBT", "Logistic", "Below volume rule", "Budget = 0.40"],
               loc="upper center", bbox_to_anchor=(0.525, 1.002), ncol=4,
               handlelength=1.5, handletextpad=0.5, columnspacing=1.25,
               borderaxespad=0.4, fontsize=9.5)
    fig.text(0.019, 0.49, "Workflow-positive pair recall", ha="center", va="center",
             rotation=90, rotation_mode="anchor", fontsize=10)
    fig.text(0.55, 0.030, "Known-MERGE proxy FMP", ha="center", va="center", fontsize=10)

    exp.save(fig, "fig3_risk_recall", rows, {
        "question": "How do complete threshold tradeoffs and the March-selected operating points transfer to May?",
        "core_conclusion": "March-selected proxy-risk feasibility need not transfer to May; feasible threshold alternatives exist in the observed May grid.",
        "archetype": "quantitative grid",
        "panel_roles": ["Complete March calibration grid", "Complete May exploratory grid",
                        "March operating-point separation", "May transfer of the frozen threshold"],
        "n": "One fitted scorer per family (seed 13); 13 frozen thresholds per curve.",
        "uncertainty": "Descriptive fixed-stream paths; no independent threshold replicates or error bars.",
        "minimum_volume": {"merge_fraction": min_fraction, "known_merges": min_known},
        "risk": "Wrong known MERGEs / all known MERGEs. Unknown MERGEs are excluded from this denominator, not asserted correct.",
        "selection": "Diamond outlines mark March-selected thresholds in full and zoom views. The May marker does not assert May feasibility.",
        "selected_thresholds": {k: v["tau"] for k, v in selection["families"].items()},
        "threshold_order": list(grid),
        "curve_counts": counts,
        "undefined_risk_omissions": missing,
        "undefined_policy": "March logistic at tau .99 has no known MERGEs: risk is undefined, remains in Source Data, and is not plotted at zero.",
        "full_domain": {"x": [0, 1.05], "y": [0, 0.65]},
        "zoom_domain": {"x": list(ZOOM_X), "y": list(ZOOM_Y)},
        "zoom_rule": "Lower panels repeat the same paths within a shared decision region; all finite observations remain in the upper row.",
        "transform": "Straight connections in ascending threshold order; no smoothing, monotone interpolation, frontier selection, or reselection.",
        "source_mapping": "Same original result() metrics and protocol-driven family/grid selection; all 39 observations retained.",
        "adaptation": "Structural adaptation of the existing threshold renderer with original data and statistics preserved.",
        "height_mm": 108,
    })
