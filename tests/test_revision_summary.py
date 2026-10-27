"""Mathematical regression checks without opening any real evaluation result."""
import json,sys,tempfile,unittest,pickle
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'analysis'))
from summarize_revision import paired,normalized_row,descriptive,add_event_timing,summarize

class SummaryChecks(unittest.TestCase):
    def test_paired_interval_uses_differences(self):
        seeds=[1,2,3,4,13]
        a={s:{'f1':.1*i+.01*(i+1)} for i,s in enumerate(seeds)}
        b={s:{'f1':.1*i} for i,s in enumerate(seeds)}
        r=paired(a,b,'f1',seeds)
        self.assertAlmostEqual(r['mean_difference'],.03)
        self.assertAlmostEqual(r['sd_difference'],.0158113883008419)
        self.assertEqual(r['df'],4)
        self.assertAlmostEqual(r['ci95_low'],.03-2.7764451051977987*.0158113883008419/(5**.5))
    def test_no_subset_pairing(self):
        with self.assertRaises(AssertionError):paired({1:{'f1':.4}},{2:{'f1':.5}},'f1',[1])
    def test_no_missing_metric_deletion(self):
        seeds=[1,2,3,4,13]
        a={s:{'f1':.4} for s in seeds};b={s:{'f1':.5} for s in seeds};a[13]['f1']=None
        r=paired(a,b,'f1',seeds)
        self.assertIsNone(r['mean_difference']);self.assertEqual(r['n_valid_pairs'],4)
    def test_single_seed_has_no_interval(self):
        r=paired({13:{'f1':.5}},{13:{'f1':.4}},'f1',[13])
        self.assertIsNone(r['ci95_low']);self.assertIsNone(r['sd_difference'])
    def test_zero_denominator_is_null(self):
        d={'n':0,'mean':0,'median':0,'p95':0,'max':0}
        r={'job':dict(run_id='fixture',window='May',family='gbt',seed=13,arm='arrival',tau=.99),
           'metrics':dict(tp=0,fp=0,n_pos=10,n_records=5,n_known=5,f1=None,fmp=0),
           'decision':dict(dec_correct=0,dec_wrong=0,decisions=0,dec_unverifiable=0,dec_fmp=0,dec_coverage=0),
           'protocol_summary':dict(n_records=5,complete=True,unique_finalizations=True,n_pending=0,
             action_counts={'NEW':5},n_events=5,n_deferred=0,peak_pending=0,trigger_counts={'arrival':5},
             deadline_resolutions=0,claim_finalizations=0,monthend_finalizations=0,
             all_record_latency_days=dict(n=5,mean=0,median=0,p95=0,max=0),held_record_latency_days=d)}
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'fixture.json';p.write_text(json.dumps(r))
            row=normalized_row(r,p)
        self.assertIsNone(row['decision_fmp']);self.assertIsNone(row['decision_coverage'])
        self.assertIsNone(row['held_latency_mean']);self.assertIsNone(row['precision'])
        self.assertEqual(row['f1'],0)
        self.assertIn('zero-denominator',row['raw_encoding_overrides'])
    def test_pending_area_and_observation_horizon(self):
        events=[{'object_id':str(i),'emitted_at':f'2024-01-{c:02}T00:00:00Z',
                 'payload':{'arrival_at':f'2024-01-{a:02}T00:00:00Z','held':h}}
                for i,(a,c,h) in enumerate(((1,2,True),(2,4,True),(3,3,False)))]
        row={'run_id':'fixture','records':3,'held':2,'held_latency_mean':1.5}
        with tempfile.TemporaryDirectory() as t:
            run=Path(t);(run/'private').mkdir()
            with (run/'private/fixture_events.pkl').open('wb') as f:pickle.dump({'events':events},f)
            add_event_timing(row,run)
        self.assertEqual(row['pending_area_record_days'],3)
        self.assertEqual(row['replay_horizon_days'],3)
        self.assertEqual(row['mean_pending_over_replay_horizon'],1)
    def test_complete_suite_outputs_and_incomplete_seed_rejection(self):
        arms=['arrival','band','gate5','cutoff10','invisible_band','invisible_gate5'];seeds=[1,2,3,4,13]
        protocol={'core':{'windows':['May','July'],'arms':arms,'seeds':seeds},'secondary':{'logit':{'windows':[],'seed':13}}}
        with tempfile.TemporaryDirectory() as t:
            run=Path(t);(run/'results').mkdir();(run/'experiment_protocol.json').write_text(json.dumps(protocol))
            for window in ('May','July'):
                for arm in arms:
                    for seed in seeds:
                        name=f'{window}_gbt_s{seed}_{arm}'
                        dist=dict(n=50,mean=0,median=0,p95=0,max=0)
                        r={'job':dict(run_id=name,window=window,family='gbt',seed=seed,arm=arm,tau=.1,policy={}),
                           'metrics':dict(tp=10,fp=10,n_pos=20,n_records=50,n_known=30,f1=.123),
                           'decision':dict(dec_correct=8,dec_wrong=2,decisions=15,dec_unverifiable=5,dec_fmp=.2,dec_coverage=2/3),
                           'protocol_summary':dict(n_records=50,complete=True,unique_finalizations=True,n_pending=0,
                              action_counts={'NEW':35,'MERGE':15},n_events=50,n_deferred=0,peak_pending=0,
                              trigger_counts={'arrival':50},deadline_resolutions=0,claim_finalizations=0,monthend_finalizations=0,
                              all_record_latency_days=dist,held_record_latency_days=dict(n=0,mean=0,median=0,p95=0,max=0))}
                        (run/'results'/(name+'.json')).write_text(json.dumps(r))
            result=summarize(run)
            self.assertTrue(result['complete_core']);self.assertEqual(len(result['rows']),60)
            self.assertTrue(all(r['f1']==.5 for r in result['rows']))
            self.assertEqual(len(result['paired_contrasts']),96)
            for file in ('results_summary.json','summary_stats.csv','paired_contrasts.csv','event_timing_summary.csv','result_manifest.csv'):
                self.assertTrue((run/'reports'/file).exists())
            (run/'results/July_gbt_s13_arrival.json').unlink()
            with self.assertRaisesRegex(ValueError,'Core suite incomplete'):summarize(run)

if __name__=='__main__':unittest.main(verbosity=2)
