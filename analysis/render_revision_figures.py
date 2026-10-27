"""Render the corrected protocol's manuscript figures from actual replay results.

Uses Matplotlib throughout; never synthesizes missing observations. Quantitative
exports require all prescribed inputs, including every matched seed. Optional
Nature figure QA scripts inspect the same final PDFs and measured plot geometry.
Run with --run-dir RUN --out OUTPUT --qa-tools PATH/TO/nature-figure/scripts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Rectangle, Patch
from matplotlib.transforms import ScaledTranslation
import numpy as np

MM = 25.4
WIDTH_MM = 180
INK = "#20262D"
MUTED = "#7E8995"
COLORS = {"arrival": "#73808C", "band": "#477CAB", "gate5": "#C77C45", "cutoff10": "#8B7CA8"}
DISPLAY = {"arrival": "Arrival", "band": "Band", "gate5": "Gate", "cutoff10": "Cutoff"}
ARMS = tuple(DISPLAY)
BINS = ["0-1d", "1-7d", "7-30d", ">30d"]
BIN_LABELS = ["0–1", ">1–7", ">7–30", ">30"]

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "svg.fonttype": "none", "pdf.fonttype": 42, "axes.linewidth": .65,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK, "text.color": INK, "axes.labelcolor": INK,
    "xtick.color": INK, "ytick.color": INK, "legend.frameon": False,
    "savefig.facecolor": "white", "figure.facecolor": "white",
})


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def panel(ax, letter, title):
    # Fixed point offsets keep panel-label anchors consistent across layouts.
    trans = ax.transAxes + ScaledTranslation(-23/72, 7/72, ax.figure.dpi_scale_trans)
    ax.text(0, 1, letter, transform=trans, fontsize=10, fontweight="bold", va="bottom", ha="left")
    ax.set_title(title, loc="left", pad=9, fontweight="bold")


def axes_style(ax):
    ax.tick_params(axis="both", length=3, width=.65, pad=3)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#E5E8EB", lw=.5)


class Exporter:
    def __init__(self, out, qa_tools=None):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.qa = self.out / "qa"
        self.qa.mkdir(exist_ok=True)
        self.qa_tools = Path(qa_tools) if qa_tools else None
        manifest=self.qa/"export_manifest.json"
        self.records = read_json(manifest) if manifest.exists() else []
        if self.qa_tools:
            sys.path.insert(0, str(self.qa_tools))
            from audit_panel_alignment import require_matplotlib_panel_alignment
            self.alignment = require_matplotlib_panel_alignment
        else:
            self.alignment = None

    def save(self, fig, name, source_rows, notes, *, schematic=False):
        fig.canvas.draw()
        if self.alignment:
            require_matplotlib_panel_alignment = self.alignment
            require_matplotlib_panel_alignment(fig, json_out=self.qa/f"{name}.alignment.json",
                           overlay_svg=self.qa/f"{name}.alignment.svg",
                           tolerance_pt=1.5, gutter_tolerance_pt=1.5,
                           require_panel_labels=True, strict=True)
        else:
            write_json(self.qa/f"{name}.alignment.json", {
                "verdict": "NOT AUDITABLE", "reason": "Supply --qa-tools for mandatory final geometry QA"})
        prefix = self.out/name
        # Fixed canvas: exports preserve the declared 180 mm publication width.
        fig.savefig(prefix.with_suffix(".pdf"))
        fig.savefig(prefix.with_suffix(".svg"))
        # The standalone SVG needs no external DTD; remove Matplotlib's optional declaration.
        svg_path=prefix.with_suffix(".svg")
        svg_text=re.sub(r"<!DOCTYPE[^>]*>", "", svg_path.read_text(encoding="utf-8"))
        # Canonicalize mathematically identity rotations emitted on unrotated text.
        svg_text=re.sub(r"rotate\([-+]?0(?:\.0+)?\s+[^)]*\)", "", svg_text)
        # SVG font sizes in user coordinates are equivalent to px within the
        # viewBox; explicit numeric presentation attributes preserve that scale.
        def canonical_font(match):
            tag=match.group(0)
            size=re.search(r"font-size:\s*([\d.]+)px;?",tag)
            if size:
                tag=tag.replace(size.group(0),"")
                tag=tag[:-1]+f' font-size="{size.group(1)}">'
            return tag
        svg_text=re.sub(r"<(?:text|tspan)\b[^>]*>",canonical_font,svg_text)
        svg_path.write_text(svg_text, encoding="utf-8")
        fig.savefig(prefix.with_suffix(".png"), dpi=600)
        plt.close(fig)
        if source_rows:
            write_csv(self.out/f"{name}.source_data.csv", source_rows)
        write_json(self.qa/f"{name}.notes.json", notes)
        status = {"figure": name, "width_mm": WIDTH_MM, "notes": notes,
                  "pdf_sha256": digest(prefix.with_suffix(".pdf")), "qa": {}}
        self.records=[r for r in self.records if r["figure"]!=name]
        if self.qa_tools:
            commands = {
                "text": ["audit_pdf_text.py", str(prefix.with_suffix(".pdf")), "--min-pt", "7", "--json"],
                "collision": ["audit_figure_collisions.py", str(prefix.with_suffix(".pdf")),
                              "--json-out", str(self.qa/f"{name}.collision.json"),
                              "--overlay-pdf", str(self.qa/f"{name}.collision.pdf")],
            }
            if schematic:
                commands["svg"] = ["audit_svg_schematic.py", str(prefix.with_suffix(".svg")),
                                   "--min-pt", "7", "--max-pt", "10", "--max-width-mm", "181", "--json"]
            for kind, args in commands.items():
                qa_env={**os.environ,"PYTHONIOENCODING":"utf-8"}
                proc = subprocess.run([sys.executable, str(self.qa_tools/args[0]), *args[1:]], capture_output=True,
                                      text=True,encoding="utf-8",env=qa_env)
                (self.qa/f"{name}.{kind}.txt").write_text(proc.stdout+proc.stderr, encoding="utf-8")
                status["qa"][kind] = {"exit_code": proc.returncode}
                if proc.returncode:
                    self.records.append(status)
                    write_json(self.qa/"export_manifest.json", self.records)
                    raise RuntimeError(f"{name}: {kind} QA failed; inspect {self.qa}")
        self.records.append(status)
        write_json(self.qa/"export_manifest.json", self.records)
        print(f"Exported {name}", flush=True)


def box(ax, xy, width, height, title, detail, fill="#F3F5F7", edge="#C9CED5"):
    x, y = xy
    ax.add_patch(Rectangle((x,y), width,height, facecolor=fill, edgecolor=edge, lw=.65))
    ax.text(x+width/2,y+height*(.77 if "\n" in detail else .67),title, ha="center",va="center",fontsize=8,fontweight="bold",linespacing=1.15)
    ax.text(x+width/2,y+height*.29,detail,ha="center",va="center",fontsize=8,linespacing=1.15)


def workflow(exp, protocol):
    fig, axes = plt.subplots(1,4,figsize=(WIDTH_MM/MM,88/MM),gridspec_kw={"wspace":.17})
    fig.subplots_adjust(left=.025,right=.985,bottom=.045,top=.88)
    headings = ["Data & protocol", "Retrieval & scoring", "Finalization policies", "Evidence"]
    rows = [
        [("San Diego 311", "2024 requests"), ("Jan–Feb", "Fit scorer"),
         ("March", "Threshold calibration"), ("May", "Exploratory analysis"),
         ("July", "Frozen validation")],
        [("Arrival fields", "Text, service,\naddress and time"), ("Candidate routes", "Address, block, token"),
         ("Pair features", "21 fixed features"), ("Pair scorer", "GBT or logistic"),
         ("Best candidate", "Score and identity")],
        [("Arrival", "Commit merge or new"), ("Band / gate", "Visible pending record"),
         ("Resolve pending", "Claim or 7-day timer"), ("One final event", "Immutable NEW / MERGE"),
         ("Controls", "Hidden pending; cutoff")],
        [("Paired quality", "5 scorer seeds"), ("Risk–recall", "Full threshold grid"),
         ("Mechanism", "Pair-set changes"), ("Causality", "Complete event prefixes"),
         ("Resources", "Latency and pending")],
    ]
    source=[]
    for i,(ax,title,entries) in enumerate(zip(axes,headings,rows)):
        ax.set(xlim=(0,1),ylim=(0,1)); ax.axis("off")
        ax.text(0,1.04,chr(97+i),transform=ax.transAxes,fontsize=10,fontweight="bold",va="bottom")
        ax.text(.12,1.04,title,transform=ax.transAxes,fontsize=8.5,fontweight="bold",va="bottom")
        for j,(name,detail) in enumerate(entries):
            y=.81-j*.197
            fill="#E9F0F6" if i==2 and j in (1,2,3) else "#F4F6F8"
            box(ax,(.015,y),.97,.165,name,detail,fill)
            source.append({"stage":i+1,"order":j+1,"component":name,"detail":detail})
            if (i==1 and j<4) or (i==2 and j in (1,2)):
                ax.add_patch(FancyArrowPatch((.5,y-.004),(.5,y-.029),arrowstyle="-|>",mutation_scale=7,lw=.7,color=INK))
    for first,second in zip(axes[:-1],axes[1:]):
        b1,b2=first.get_position(),second.get_position()
        fig.add_artist(FancyArrowPatch((b1.x1+.002,.49),(b2.x0-.002,.49),transform=fig.transFigure,
                                      arrowstyle="-|>",mutation_scale=8,lw=.8,color=INK))
    exp.save(fig,"fig1_workflow",source,{
        "question":"How is a frozen scorer evaluated under causal commitment policies?",
        "archetype":"schematic-led composite", "backend":"Python / Matplotlib",
        "evidence":"Protocol and causal-event-v2 implementation; diagram contains no quantitative results.",
        "panel_roles":["Temporal protocol","Shared inference","Commitment contrast","Validation evidence"],
        "abbreviations":"GBT: gradient-boosted trees; cutoff: 10-day comparison lag.",
        "provenance":"May is exploratory; July is a newly frozen retrospective validation, not a prospective deployment.",
        "font_floor_pt":8, "image_integrity":"Original editable vector schematic; no raster or generated measurement assets.",
    },schematic=True)


def result(run, window, family, seed, arm):
    path=run/"results"/f"{window}_{family}_s{seed}_{arm}.json"
    value=read_json(path)
    if not value["protocol_summary"]["complete"] or not value["protocol_summary"]["unique_finalizations"]:
        raise ValueError(f"Incomplete/duplicate replay: {path.name}")
    m,d=value["metrics"],value["decision"]
    tp,fp,npos=m["tp"],m["fp"],m["n_pos"]
    if m.get("capped"):
        raise ValueError(f"Pair enumeration capped: {path.name}")
    denom=tp+fp+npos
    known=d["dec_correct"]+d["dec_wrong"]
    return {"window":window,"family":family,"seed":seed,"arm":arm,"tau":value["job"]["tau"],
            "f1":2*tp/denom if denom else None,"recall":tp/npos if npos else None,
            "proxy_fmp":d["dec_wrong"]/known if known else None,
            "merges":d["decisions"],"known_merges":known,"records":m["n_records"],
            "true_positive_pairs":tp,"false_positive_pairs":fp,"gold_positive_pairs":npos,
            "result_file":path.name,"result_sha256":digest(path)}


def main_comparison(exp, run, protocol):
    seeds=protocol["core"]["seeds"]
    if sorted(seeds)!=[1,2,3,4,13]:
        raise ValueError("Expected frozen five-seed cohort")
    rows=[result(run,w,"gbt",s,a) for w in ("May","July") for s in seeds for a in ARMS]
    if any(r["f1"] is None for r in rows):
        raise ValueError("Undefined F1: do not silently delete a matched seed")
    fig, axes=plt.subplots(1,2,figsize=(WIDTH_MM/MM,79/MM),sharey=True)
    fig.subplots_adjust(left=.105,right=.98,bottom=.23,top=.84,wspace=.2)
    means=[]
    all_values=[r["f1"] for r in rows]
    bottom=math.floor((min(all_values)-.02)/.05)*.05
    top=math.ceil((max(all_values)+.02)/.05)*.05
    for ax,window,letter in zip(axes,("May","July"),"ab"):
        matrix=np.array([[next(r["f1"] for r in rows if r["window"]==window and r["seed"]==s and r["arm"]==a)
                          for a in ARMS] for s in seeds])
        offsets=np.linspace(-.12,.06,len(seeds))
        for vals,offset in zip(matrix,offsets):
            ax.plot(np.arange(4)+offset,vals,color="#B6BEC7",lw=.7,zorder=1,alpha=.8)
            for j,a in enumerate(ARMS):
                ax.scatter(j+offset,vals[j],s=20,color=COLORS[a],edgecolor="white",lw=.45,zorder=3)
        mu=matrix.mean(axis=0);sd=matrix.std(axis=0,ddof=1)
        for j,a in enumerate(ARMS):
            ax.errorbar(j+.22,mu[j],yerr=sd[j],fmt="s",color=INK,markersize=4,
                        capsize=3,elinewidth=1,lw=1,zorder=4)
            means.append({"window":window,"arm":a,"n":len(seeds),"mean_f1":float(mu[j]),"sd_f1":float(sd[j])})
        ax.set(xticks=range(4),xticklabels=[DISPLAY[a] for a in ARMS],xlim=(-.4,3.55),ylim=(bottom,top))
        panel(ax,letter,f"{window} · "+("exploratory" if window=="May" else "frozen validation"))
        axes_style(ax)
    axes[0].set_ylabel("Workflow-pair F1")
    fig.legend([Line2D([],[],marker="o",ls="-",color="#9CA7B3",markerfacecolor=COLORS["band"],markersize=4),
                Line2D([],[],marker="s",ls="none",color=INK,markersize=4)],
               ["Matched scorer seeds (n = 5)","Mean ± SD"],loc="lower center",bbox_to_anchor=(.5,.02),ncol=2,
               handlelength=2,columnspacing=2)
    exp.save(fig,"fig2_main_comparison",rows,{
        "question":"Do commitment-policy differences recur in a later frozen window?",
        "archetype":"quantitative grid", "panel_roles":["Exploratory comparison","Frozen-window validation"],
        "n":"Five stochastic GBT fits per policy and window; seeds 1, 2, 3, 4, 13.",
        "center":"Arithmetic mean", "uncertainty":"Sample SD (ddof=1), not SE or CI.",
        "pairing":"Lines connect the same scorer seed across policies; fixed horizontal offsets separate overlapping marks.",
        "metric":"2 TP / (TP + FP + number of gold positive pairs), from known workflow components.",
        "exclusions":"None within the four main arms. Visibility ablations are reported separately because they test a different mechanism contrast.",
        "summary":means,"pvalues":"None; fit-seed variation is not population uncertainty.",
        "font_floor_pt":8,
    })


def threshold_curve(exp, run, protocol):
    selection=read_json(run/"reports"/"calibration_selection.json")
    grid=protocol["threshold"]["grid"]
    budget=protocol["threshold"]["risk_budget"]
    min_fraction=protocol["threshold"]["min_merge_fraction"]
    min_known=protocol["threshold"]["min_adjudicable_merges"]
    rows=[]
    for window,families in (("March",("gbt","logit")),("May",("gbt",))):
        for family in families:
            for tau in grid:
                row=result(run,window,family,13,f"tau{tau:g}")
                row["volume_eligible"]=row["merges"]>=min_fraction*row["records"] and row["known_merges"]>=min_known
                row["risk_feasible"]=row["volume_eligible"] and row["proxy_fmp"] is not None and row["proxy_fmp"]<=budget
                row["selected_in_march"]=math.isclose(tau,selection["families"][family]["tau"])
                rows.append(row)
    fig,axes=plt.subplots(1,2,figsize=(WIDTH_MM/MM,96/MM),sharex=True,sharey=True)
    fig.subplots_adjust(left=.105,right=.98,bottom=.33,top=.86,wspace=.2)
    all_recall=[r["recall"] for r in rows if r["recall"] is not None]
    ymax=math.ceil((max(all_recall)+.04)/.1)*.1
    missing={}
    for ax,window,letter in zip(axes,("March","May"),"ab"):
        for family,color,marker in (("gbt",COLORS["band"],"o"),("logit",COLORS["gate5"],"^")):
            data=[r for r in rows if r["window"]==window and r["family"]==family]
            if not data:continue
            x=[r["proxy_fmp"] if r["proxy_fmp"] is not None else np.nan for r in data]
            y=[r["recall"] if r["recall"] is not None else np.nan for r in data]
            ax.plot(x,y,color=color,lw=1,zorder=1)
            for r,xx,yy in zip(data,x,y):
                if not np.isfinite(xx) or not np.isfinite(yy):continue
                ax.scatter(xx,yy,s=27,marker=marker,facecolor=color if r["volume_eligible"] else "white",
                           edgecolor=color,lw=.9,zorder=3)
                if r["selected_in_march"]:
                    ax.scatter(xx,yy,s=76,marker="s",facecolor="none",edgecolor=INK,lw=.8,zorder=4)
            missing[window+"_"+family]=sum(r["proxy_fmp"] is None for r in data)
        ax.axvline(budget,color=MUTED,ls=(0,(4,3)),lw=.8,zorder=0)
        ax.set(xlim=(-.025,1.025),ylim=(-.025,ymax),xticks=[0,.2,.4,.6,.8,1])
        ax.set_xlabel("Known-MERGE proxy FMP")
        panel(ax,letter,window+" · "+("calibration" if window=="March" else "exploratory"))
        axes_style(ax)
    axes[0].set_ylabel("Certified positive-pair recall")
    handles=[Line2D([],[],marker="o",color=COLORS["band"],lw=1,markersize=4),
             Line2D([],[],marker="^",color=COLORS["gate5"],lw=1,markersize=4),
             Line2D([],[],marker="s",color=INK,markerfacecolor="none",ls="none",markersize=6),
             Line2D([],[],marker="o",color=INK,markerfacecolor="none",ls="none",markersize=4),
             Line2D([],[],color=MUTED,ls=(0,(4,3)),lw=.8)]
    labels=["GBT","Logistic","March-selected τ","Below minimum volume","Proxy budget = 0.40"]
    fig.legend(handles,labels,loc="lower center",bbox_to_anchor=(.5,.105),ncol=3,columnspacing=1.5,handlelength=1.8)
    total_missing=sum(missing.values())
    chosen=selection["families"]
    fig.text(.5,.059,f'March choices: GBT τ = {chosen["gbt"]["tau"]:g}; logistic τ = {chosen["logit"]["tau"]:g}.',
             ha="center",fontsize=8)
    fig.text(.5,.019,f"13 thresholds per curve; {total_missing} undefined-risk point(s) omitted. Connections follow threshold order.",
             ha="center",fontsize=8)
    exp.save(fig,"fig3_risk_recall",rows,{
        "question":"What risk–recall combinations are observed across the complete threshold grid?",
        "archetype":"quantitative grid", "panel_roles":["Threshold selection with two scorers","Transfer of selected GBT threshold to May"],
        "n":"One fitted scorer per family (seed 13); 13 frozen thresholds per curve.",
        "uncertainty":"Descriptive curves on a fixed stream; no independent threshold replicates or error bars.",
        "minimum_volume":{"merge_fraction":min_fraction,"known_merges":min_known},
        "risk":"Wrong known MERGEs / all known MERGEs. Unknown MERGEs excluded from proxy denominator, not asserted correct.",
        "selection":"Square outlines identify the threshold selected using March only; they do not indicate May feasibility.",
        "transform":"No smoothing or monotone interpolation; straight connections follow ascending threshold order.",
        "undefined_risk_omissions":missing,"undefined_policy":"Zero known-MERGE denominator is undefined and never plotted at risk zero.",
        "font_floor_pt":8,
    })


def mechanism(exp, run):
    rows=[]
    arrays={}
    counts={}
    for window in ("May","July"):
        source=run/"reports"/f"mechanism_{window}"/"mechanism_analysis.json"
        data=read_json(source)
        for kind in ("positive_pair_changes","wrong_merge_changes"):
            for original in data[kind]:
                if original["comparison"] not in ("band_vs_arrival","gate5_vs_arrival"):continue
                row={"window":window,"quantity":kind,**original,"source_file":source.parent.name+"/"+source.name,
                     "source_sha256":digest(source)}
                rows.append(row)
        for kind in ("positive","wrong"):
            for arm in ("band","gate5"):
                selected=[r for r in rows if r["window"]==window and r["comparison"]==arm+"_vs_arrival"
                          and r["quantity"]==("positive_pair_changes" if kind=="positive" else "wrong_merge_changes")]
                favorable="new" if kind=="positive" else "avoided"
                adverse="lost" if kind=="positive" else "new"
                pos=[];neg=[]
                for b in BINS:
                    pos.append(sum(r["count"] for r in selected if r["category"]==favorable and
                                   r["lag_bucket" if kind=="positive" else "baseline_target_age"]==b))
                    neg.append(-sum(r["count"] for r in selected if r["category"]==adverse and
                                    r["lag_bucket" if kind=="positive" else "other_target_age"]==b))
                arrays[window,kind,arm]=(np.asarray(pos),np.asarray(neg))
                counts[window+"_"+kind+"_"+arm]={"favorable":sum(pos),"adverse":-sum(neg),
                    "common":sum(r["count"] for r in selected if r["category"]=="common")}
                expected=sum(r["count"] for r in selected if r["category"] in (favorable,adverse))
                if sum(pos)-sum(neg)!=expected:
                    raise ValueError("Unexpected missing/unknown lag bucket; do not silently omit pairs")
    fig,axes=plt.subplots(2,2,figsize=(WIDTH_MM/MM,117/MM))
    fig.subplots_adjust(left=.11,right=.98,bottom=.2,top=.9,wspace=.3,hspace=.65)
    limits={}
    for kind in ("positive","wrong"):
        vals=np.concatenate([np.r_[p,n] for (w,k,a),(p,n) in arrays.items() if k==kind])
        step=100 if max(abs(vals))<800 else 250
        limits[kind]=(math.floor((vals.min()-step*.3)/step)*step,math.ceil((vals.max()+step*.3)/step)*step)
    for r,window in enumerate(("May","July")):
        for c,kind in enumerate(("positive","wrong")):
            ax=axes[r,c]
            for arm,offset in (("band",-.18),("gate5",.18)):
                pos,neg=arrays[window,kind,arm]
                ax.bar(np.arange(4)+offset,pos,width=.31,color=COLORS[arm],edgecolor="white",lw=.4,zorder=3)
                ax.bar(np.arange(4)+offset,neg,width=.31,facecolor="white",edgecolor=COLORS[arm],lw=1,zorder=3)
            ax.axhline(0,color=INK,lw=.65,zorder=4)
            ax.set(xticks=range(4),xticklabels=BIN_LABELS,xlim=(-.55,3.55),ylim=limits[kind])
            ax.set_xlabel("Gold-pair lag (days)" if kind=="positive" else "Target age at MERGE (days)")
            ax.set_ylabel("New (+) / lost (−) pairs" if kind=="positive" else "Avoided (+) / new (−) errors")
            panel(ax,chr(97+r*2+c),window+" · "+("positive-pair changes" if kind=="positive" else "wrong-MERGE changes"))
            axes_style(ax)
    fig.legend([Patch(facecolor=COLORS["band"]),Patch(facecolor=COLORS["gate5"]),
                Patch(facecolor="white",edgecolor=MUTED,lw=1)],
               ["Band vs Arrival","Gate vs Arrival","Adverse change"],ncol=3,
               loc="lower center",bbox_to_anchor=(.5,.065),columnspacing=1.7,handlelength=1.6)
    fig.text(.5,.024,"Seed 13; descriptive set differences. Unchanged pairs are included in Source Data.",ha="center",fontsize=8)
    exp.save(fig,"fig4_mechanism",rows,{
        "question":"Which recovered positives and directed wrong operations change under deferral?",
        "archetype":"quantitative grid", "panel_roles":["Exploratory recovery shifts","Exploratory error shifts","Validation recovery shifts","Validation error shifts"],
        "n":"One GBT fit (seed 13) per fixed window; each mark is an exact set count.",
        "uncertainty":"Descriptive dependent-pair counts; no independent-pair error bars or tests.",
        "positive_definition":"Same gold component and same final predicted component; gain/loss against Arrival.",
        "wrong_definition":"Directed (source record, scoring representative) MERGE with unequal known workflow components.",
        "bin_definition":"[0,1], (1,7], (7,30], (30,infinity) elapsed 24-hour days.",
        "target_age":"Avoided operations use Arrival target age; new errors use deferral-policy target age.",
        "aggregation":"Sum over actual trigger rows. Common/neither pairs do not contribute to differences; all source rows preserved.",
        "counts":counts,"font_floor_pt":8,
    })


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--qa-tools",type=Path)
    parser.add_argument("--figures",nargs="+",choices=["workflow","main","threshold","mechanism"],
                        default=["workflow","main","threshold","mechanism"])
    args=parser.parse_args()
    protocol=read_json(args.run_dir/"experiment_protocol.json")
    exp=Exporter(args.out,args.qa_tools)
    funcs={"workflow":lambda:workflow(exp,protocol),"main":lambda:main_comparison(exp,args.run_dir,protocol),
           "threshold":lambda:threshold_curve(exp,args.run_dir,protocol),"mechanism":lambda:mechanism(exp,args.run_dir)}
    for name in args.figures:
        funcs[name]()


if __name__=="__main__":
    main()
