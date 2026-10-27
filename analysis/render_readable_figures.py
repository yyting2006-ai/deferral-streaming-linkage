"""Readable, vector empirical figures from the frozen corrected replay outputs.

Run as python -m analysis.render_readable_figures --run-dir RUN --out OUT
--qa-tools PATH/TO/nature-figure/scripts. All observed values remain traceable.
"""
from __future__ import annotations
import argparse,csv,math
from pathlib import Path
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter
from .readable_plot_common import (plt,MM,WIDTH_MM,INK,MUTED,BLUE,ORANGE,COLORS,
    DISPLAY,ARMS,BINS,BIN_LABELS,Exporter,read_json,write_csv,result,digest,panel,style)


def main_comparison(exp,run,protocol):
    seeds=protocol['core']['seeds']
    assert sorted(seeds)==[1,2,3,4,13]
    rows=[result(run,w,'gbt',s,a) for w in ('May','July') for s in seeds for a in ARMS]
    assert len(rows)==40 and all(r['f1'] is not None for r in rows)
    contrasts=list(csv.DictReader((run/'reports/paired_contrasts.csv').open(encoding='utf-8')))
    fig,axes=plt.subplots(2,2,figsize=(WIDTH_MM/MM,108/MM),gridspec_kw={'height_ratios':[1.45,1]})
    fig.subplots_adjust(left=.14,right=.975,top=.895,bottom=.13,wspace=.42,hspace=.91)
    means=[];differences=[];summaries=[]
    for col,window in enumerate(('May','July')):
        ax=axes[0,col]
        matrix=np.array([[next(r['f1'] for r in rows if r['window']==window and r['seed']==s and r['arm']==a)
                          for a in ARMS] for s in seeds])
        offsets=np.linspace(-.34,.22,5)
        ax.axhspan(1.60,2.43,facecolor='#FBF0E9',zorder=0,lw=0)
        for vals,off in zip(matrix,offsets):
            ax.plot(vals,np.arange(4)+off,color='#C7CED4',lw=.7,zorder=1)
            for j,arm in enumerate(ARMS):
                ax.scatter(vals[j],j+off,s=18,color=COLORS[arm],edgecolor='white',lw=.45,zorder=3)
        mu=matrix.mean(axis=0);sd=matrix.std(axis=0,ddof=1)
        for j,arm in enumerate(ARMS):
            ax.errorbar(mu[j],j+.40,xerr=sd[j],fmt='D',color=INK,markersize=4.5,
                        capsize=2.5,elinewidth=1.2,zorder=4)
            ax.text(.542,j+.03,f'{mu[j]:.3f}',ha='right',va='center',fontsize=10,color=COLORS[arm],fontweight='bold')
            means.append({'window':window,'arm':arm,'n':5,'mean_f1':float(mu[j]),'sd_f1':float(sd[j])})
        ax.set(xlim=(.398,.547),ylim=(3.55,-.55),yticks=range(4),yticklabels=[DISPLAY[a] for a in ARMS],
               xticks=[.40,.45,.50],xlabel='Workflow-pair F1')
        panel(ax,'ab'[col],window+' · '+('exploratory' if window=='May' else 'frozen validation'))
        style(ax)
        ax.spines['left'].set_visible(False);ax.tick_params(axis='y',length=0,pad=5)
        for label,arm in zip(ax.get_yticklabels(),ARMS):label.set_color(COLORS[arm])
        bx=axes[1,col]
        for j,arm in enumerate(('band','gate5')):
            d=matrix[:,ARMS.index(arm)]-matrix[:,ARMS.index('cutoff10')]
            record=next(r for r in contrasts if r['window']==window and r['family']=='gbt' and r['arm_a']==arm and r['arm_b']=='cutoff10' and r['metric']=='f1')
            mean=float(record['mean_difference']);lo=float(record['ci95_low']);hi=float(record['ci95_high'])
            assert math.isclose(d.mean(),mean,rel_tol=1e-12,abs_tol=1e-12)
            assert int(record['n_valid_pairs'])==5
            for seed,value,off in zip(seeds,d,np.linspace(-.22,.02,5)):
                bx.scatter(100*value,j+off,s=23,facecolor=COLORS[arm],edgecolor='white',lw=.45,zorder=4)
                differences.append({'window':window,'seed':seed,'arm':arm,'reference':'cutoff10','f1_difference':float(value)})
            bx.errorbar(mean*100,j+.23,xerr=[[100*(mean-lo)],[100*(hi-mean)]],fmt='D',
                        color=COLORS[arm],markersize=5,capsize=3,elinewidth=1.6,zorder=4)
            bx.text(6.0,j+.03,f'{100*mean:+.2f}',ha='right',va='center',color=COLORS[arm],fontweight='bold',fontsize=10)
            summaries.append({'window':window,'arm':arm,'mean':mean,'ci95_low':lo,'ci95_high':hi,'df':4})
        bx.axvline(0,color='#8F9AA3',lw=.9,ls=(0,(3,3)),zorder=0)
        bx.set(xlim=(-2,6.2),ylim=(1.6,-.55),yticks=[0,1],yticklabels=['Band','Gate'],
               xticks=[-2,0,2,4],xlabel='ΔF1 vs Cutoff (percentage points)')
        bx.xaxis.set_major_formatter(FuncFormatter(lambda x,p:f'{x:+.0f}' if x else '0'))
        panel(bx,'cd'[col],'Paired gain · 95% CI')
        style(bx);bx.spines['left'].set_visible(False);bx.tick_params(axis='y',length=0,pad=5)
        for label,arm in zip(bx.get_yticklabels(),('band','gate5')):label.set_color(COLORS[arm])
    write_csv(exp.out/'fig2_main_comparison.paired_differences.csv',differences)
    exp.save(fig,'fig2_main_comparison',rows,{
      'question':'Does the Gate improvement beyond the cutoff recur across matched fits and the later frozen window?',
      'archetype':'quantitative grid','panel_roles':['Exploratory levels','Frozen-window levels','Exploratory paired contrasts','Frozen paired contrasts'],
      'raw_n':40,'paired_differences_n':20,'n':'Five GBT scorer fits per policy/window; seeds1,2,3,4,13',
      'center':'Arithmetic mean; direct values label means only',
      'uncertainty':'Upper row sample SD; lower row paired pointwise95% t interval,df4,unadjusted; fit-seed variability only',
      'pairing':'Thin upper profiles join the same seed; lower contrasts subtract the same-seed Cutoff.',
      'transform':'Only lower x-axis is multiplied by100 to show F1 percentage points; no data exclusion or smoothing',
      'exclusions':'None among4 primary arms; visibility controls remain separately reported in the paper',
      'summary':means,'paired_summary':summaries,'source_contrasts_sha256':digest(run/'reports/paired_contrasts.csv')})


def mechanism(exp,run):
    rows=[];arrays={};counts={}
    for window in ('May','July'):
        source=run/'reports'/f'mechanism_{window}'/'mechanism_analysis.json'
        data=read_json(source)
        for kind in ('positive_pair_changes','wrong_merge_changes'):
            for original in data[kind]:
                if original['comparison'] not in ('band_vs_arrival','gate5_vs_arrival'):continue
                rows.append({'window':window,'quantity':kind,**original,'source_file':source.parent.name+'/'+source.name,'source_sha256':digest(source)})
        for kind in ('positive','wrong'):
            for arm in ('band','gate5'):
                selected=[r for r in rows if r['window']==window and r['comparison']==arm+'_vs_arrival' and r['quantity']==('positive_pair_changes' if kind=='positive' else 'wrong_merge_changes')]
                favorable='new' if kind=='positive' else 'avoided';adverse='lost' if kind=='positive' else 'new'
                pos=[sum(r['count'] for r in selected if r['category']==favorable and r['lag_bucket' if kind=='positive' else 'baseline_target_age']==b) for b in BINS]
                neg=[-sum(r['count'] for r in selected if r['category']==adverse and r['lag_bucket' if kind=='positive' else 'other_target_age']==b) for b in BINS]
                assert sum(pos)-sum(neg)==sum(r['count'] for r in selected if r['category'] in (favorable,adverse))
                arrays[window,kind,arm]=(np.array(pos),np.array(neg))
                counts[window+'_'+kind+'_'+arm]={'favorable':sum(pos),'adverse':-sum(neg),'common':sum(r['count'] for r in selected if r['category']=='common')}
    fig,axes=plt.subplots(2,2,figsize=(WIDTH_MM/MM,112/MM))
    fig.subplots_adjust(left=.115,right=.98,top=.835,bottom=.14,wspace=.38,hspace=.7)
    for row,window in enumerate(('May','July')):
        for col,kind in enumerate(('positive','wrong')):
            ax=axes[row,col]
            for arm,off in [('band',-.21),('gate5',.21)]:
                pos,neg=arrays[window,kind,arm]
                yy=np.arange(4)+off
                ax.barh(yy,pos,height=.32,color=COLORS[arm],edgecolor=COLORS[arm],lw=.6,zorder=3)
                ax.barh(yy,neg,height=.32,facecolor='#EBF0F3' if arm=='band' else '#F8EAE3',edgecolor=COLORS[arm],lw=.9,zorder=3)
                if kind=='wrong':
                    # Explicitly annotate tiny >30d counts; true bar lengths stay unchanged.
                    text_y=3+(-.29 if arm=='band' else .29)
                    ax.text(-70,text_y,str(abs(int(neg[-1]))),color=COLORS[arm],ha='right',va='center',fontsize=9.5)
                    ax.text(70,text_y,str(int(pos[-1])),color=COLORS[arm],ha='left',va='center',fontsize=9.5)
            ax.axvline(0,color=INK,lw=.8,zorder=4)
            lim=(-560,400) if kind=='positive' else (-300,600)
            ticks=[-400,-200,0,200,400] if kind=='positive' else [-200,0,200,400,600]
            ax.set(xlim=lim,ylim=(3.6,-.65),yticks=range(4),yticklabels=BIN_LABELS,xticks=ticks,
                   xlabel='Positive pairs' if kind=='positive' else 'Wrong MERGE operations')
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x,p:str(abs(int(x)))))
            ax.set_ylabel('Pair lag (days)' if kind=='positive' else 'Target age (days)',labelpad=5)
            if row==0:ax.set_xlabel('')
            panel(ax,chr(97+2*row+col),window+' · '+('positive pairs' if kind=='positive' else 'wrong merges'),pad=21)
            # Panel letters and titles use a common physical anchor; direction labels sit below title.
            for t in ax.texts:
                if t.get_text()==chr(97+2*row+col):
                    from matplotlib.transforms import ScaledTranslation
                    t.set_transform(ax.transAxes+ScaledTranslation(-16/72,18/72,fig.dpi_scale_trans))
            ax.text(.22,1.015,'← Lost' if kind=='positive' else '← Introduced',transform=ax.transAxes,ha='center',va='bottom',color=MUTED,fontsize=9.5)
            ax.text(.77,1.015,'Recovered →' if kind=='positive' else 'Avoided →',transform=ax.transAxes,ha='center',va='bottom',color=MUTED,fontsize=9.5)
            style(ax);ax.spines['left'].set_visible(False);ax.tick_params(axis='y',length=0)
    fig.legend([Patch(facecolor=BLUE),Patch(facecolor=ORANGE)],['Band vs Arrival','Gate vs Arrival'],
               loc='upper center',bbox_to_anchor=(.53,1.01),ncol=2,handlelength=1.3,columnspacing=2.5)
    exp.save(fig,'fig4_mechanism',rows,{
      'question':'Which positive pairs and directed wrong operations are gained or lost under deferral?',
      'archetype':'quantitative grid','panel_roles':['May positive-pair tradeoff','May wrong-operation tradeoff','July positive-pair tradeoff','July wrong-operation tradeoff'],
      'n':'One GBT seed13 replay per fixed stream; exact dependent set counts; no independent-pair uncertainty.',
      'uncertainty':'None: descriptive dependent counts, not independent replicates',
      'direction':'Left=lostpositivepairs or introducedwrongoperations; right=recoveredpositivepairs or avoidedwrongoperations. Axislabels give count magnitudes.',
      'bin_definition':'[0,1],(1,7],(7,30],(30,infinity) elapsed24-hour days',
      'target_age':'Avoided operations use Arrival target age; introduced operations use deferral-policy target age',
      'aggregation':'Sum all original trigger rows; common/unchanged rows retained in Source Data',
      'exclusions':'No source rows excluded among Band/Gate vs Arrival. Both >30d positive groups are zero; tinywrongcounts labeled directly.',
      'counts':counts,'original_source_rows':len(rows)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--qa-tools',type=Path)
    parser.add_argument('--figures',nargs='+',choices=['main','threshold','mechanism'],default=['main','threshold','mechanism'])
    args=parser.parse_args();protocol=read_json(args.run_dir/'experiment_protocol.json')
    exp=Exporter(args.out,args.qa_tools)
    if 'main' in args.figures:main_comparison(exp,args.run_dir,protocol)
    if 'threshold' in args.figures:
        from .readable_threshold import threshold_curve
        threshold_curve(exp,args.run_dir,protocol)
    if 'mechanism' in args.figures:mechanism(exp,args.run_dir)

if __name__=='__main__':main()
