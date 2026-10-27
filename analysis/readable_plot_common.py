"""Shared physical-size styling and auditable exports for empirical figures."""
from pathlib import Path
import json,os,re,subprocess,sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.transforms import ScaledTranslation
from .render_revision_figures import read_json,write_json,write_csv,digest,result

MM=25.4
WIDTH_MM=180
INK='#1D2935'
MUTED='#687785'
BLUE='#286F93'
ORANGE='#BC623D'
COLORS={'arrival':'#77838D','band':BLUE,'gate5':ORANGE,'cutoff10':'#837A92'}
DISPLAY={'arrival':'Arrival','band':'Band','gate5':'Gate','cutoff10':'Cutoff'}
ARMS=tuple(DISPLAY)
BINS=['0-1d','1-7d','7-30d','>30d']
BIN_LABELS=['0–1','>1–7','>7–30','>30']
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','Helvetica','DejaVu Sans'],
 'font.size':10,'axes.labelsize':10,'axes.titlesize':11,'xtick.labelsize':9.5,'ytick.labelsize':10,
 'legend.fontsize':9.5,'svg.fonttype':'none','svg.hashsalt':'deferral-readable-v1','pdf.fonttype':42,'axes.linewidth':.7,
 'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':INK,'text.color':INK,
 'axes.labelcolor':INK,'xtick.color':INK,'ytick.color':INK,'legend.frameon':False,
 'savefig.facecolor':'white','figure.facecolor':'white'})

def panel(ax,letter,title,pad=9):
    trans=ax.transAxes+ScaledTranslation(-16/72,6/72,ax.figure.dpi_scale_trans)
    ax.text(0,1,letter,transform=trans,fontsize=11,fontweight='bold',va='bottom',ha='left')
    ax.set_title(title,loc='left',pad=pad,fontweight='bold',fontsize=11)

def style(ax,axis='x'):
    ax.tick_params(length=3,width=.7,pad=3)
    ax.set_axisbelow(True)
    ax.grid(axis=axis,color='#E4E9ED',lw=.6)

class Exporter:
    def __init__(self,out,qa_tools):
        self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
        self.qa=self.out/'qa';self.qa.mkdir(exist_ok=True)
        self.qa_tools=Path(qa_tools) if qa_tools else None
        self.records=[]
    def save(self,fig,name,rows,notes):
        fig.canvas.draw()
        if self.qa_tools:
            sys.path.insert(0,str(self.qa_tools))
            from audit_panel_alignment import require_matplotlib_panel_alignment
            require_matplotlib_panel_alignment(fig,json_out=self.qa/f'{name}.alignment.json',
              overlay_svg=self.qa/f'{name}.alignment.svg',tolerance_pt=1.5,gutter_tolerance_pt=1.5,
              require_panel_labels=True,strict=True)
        else:
            write_json(self.qa/f'{name}.alignment.json',{'verdict':'NOT AUDITABLE','reason':'Supply --qa-tools'})
        prefix=self.out/name
        fig.savefig(prefix.with_suffix('.pdf'),metadata={'CreationDate':None,'ModDate':None})
        fig.savefig(prefix.with_suffix('.svg'),metadata={'Date':None})
        svg=prefix.with_suffix('.svg');svg.write_text(re.sub(r'<!DOCTYPE[^>]*>','',svg.read_text(encoding='utf-8')),encoding='utf-8')
        fig.savefig(prefix.with_suffix('.png'),dpi=450)
        fig.savefig(self.out/f'{name}.preview.png',dpi=300)
        plt.close(fig)
        write_csv(self.out/f'{name}.source_data.csv',rows)
        notes.update({'width_mm':WIDTH_MM,'font_floor_pt':9.5,'backend':'Python/Matplotlib','smoothing':'none'})
        write_json(self.qa/f'{name}.notes.json',notes)
        record={'figure':name,'pdf_sha256':digest(prefix.with_suffix('.pdf')),'qa':{}}
        if self.qa_tools:
            commands={'text':['audit_pdf_text.py',str(prefix.with_suffix('.pdf')),'--min-pt','9.5','--json'],
              'collision':['audit_figure_collisions.py',str(prefix.with_suffix('.pdf')),'--json-out',str(self.qa/f'{name}.collision.json'),
                          '--overlay-pdf',str(self.qa/f'{name}.collision.pdf')]}
            for kind,args in commands.items():
                proc=subprocess.run([sys.executable,str(self.qa_tools/args[0]),*args[1:]],capture_output=True,text=True,encoding='utf-8',env={**os.environ,'PYTHONIOENCODING':'utf-8'})
                (self.qa/f'{name}.{kind}.txt').write_text(proc.stdout+proc.stderr,encoding='utf-8')
                record['qa'][kind]=proc.returncode
        self.records.append(record)
        write_json(self.qa/'export_manifest.json',self.records)
        print(json.dumps(record),flush=True)
        if any(record['qa'].values()):
            raise RuntimeError(f'{name}: rendered QA failed; see {self.qa}')
