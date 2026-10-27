"""Bounded synthetic invariants for the actual mechanism-analysis implementation."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'analysis'))
from revision_mechanism import analyze_bundle

def event(rid,rep,time,trigger='arrival'):
    return {'object_id':rid,'action':'MERGE' if rep else 'NEW','target_id':rep,
            'emitted_at':f'2024-01-{time:02}T00:00:00Z','trigger':trigger,'sequence':time,
            'payload':{'representative_id':rep,'arrival_at':f'2024-01-{time:02}T00:00:00Z','held':trigger!='arrival'}}

class Analyses(unittest.TestCase):
    def setUp(self):
        self.records=[dict(rid=x,when=f'2024-01-{t:02}T00:00:00Z',service_key='A') for x,t in [('a',1),('b',2),('c',3),('d',4),('e',5)]]
        self.gold={'a':'G1','b':'G1','c':'G2','d':'G2'}
        # Arrival recovers (a,b), but makes wrong c->a; band recovers (c,d),
        # makes wrong b->c and keeps unknown e->d. Pair metrics remain distinct
        # from directed scoring decisions.
        self.arms={'arrival':{'events':[event('a',None,1),event('b','a',2),event('c','a',3),event('d',None,4),event('e','d',5)],
                              'partition':{'a':'I1','b':'I1','c':'I1','d':'I2','e':'I2'}},
                   'band':{'events':[event('a',None,1),event('b','c',3,'claim'),event('c',None,3,'claim'),event('d','c',4),event('e','d',5)],
                           'partition':{'a':'I1','b':'I2','c':'I2','d':'I2','e':'I2'}}}
    def test_counts_and_set_identity(self):
        r=analyze_bundle(self.records,self.gold,self.arms)
        for x in r['arm_denominators']:
            self.assertEqual(x['records'],5);self.assertEqual(x['new'],2);self.assertEqual(x['merge'],3)
            self.assertEqual(x['known_merge'],2);self.assertEqual(x['unknown_merge'],1)
            self.assertEqual(x['pending'],0);self.assertEqual(x['fmp'],.5)
        p={x['category']:x['count'] for x in r['positive_pair_changes']}
        self.assertEqual(p,{'new':1,'lost':1})
        wrong={x['category']:x['count'] for x in r['wrong_merge_changes']}
        self.assertEqual(wrong,{'new':1,'avoided':1})
        common=r['common_support']
        self.assertEqual(common[0]['known_merge'],1)
        self.assertEqual(common[0]['fmp'],0);self.assertEqual(common[1]['fmp'],1)
    def test_duplicate_finalization_rejected(self):
        self.arms['band']['events'].append(self.arms['band']['events'][-1])
        with self.assertRaisesRegex(ValueError,'duplicate final'):analyze_bundle(self.records,self.gold,self.arms)
    def test_partition_event_mismatch_rejected(self):
        del self.arms['band']['partition']['a']
        with self.assertRaisesRegex(ValueError,'partition/final-event'):analyze_bundle(self.records,self.gold,self.arms)
    def test_missing_rep_rejected(self):
        del self.arms['band']['events'][1]['payload']['representative_id']
        with self.assertRaisesRegex(ValueError,'representative'):analyze_bundle(self.records,self.gold,self.arms)
    def test_unknown_gold_excluded_not_negative(self):
        r=analyze_bundle(self.records,{},self.arms)
        for a in r['arm_denominators']:
            self.assertEqual(a['known_merge'],0);self.assertIsNone(a['fmp']);self.assertEqual(a['coverage'],0)
        self.assertEqual(r['positive_pair_changes'],[])
    def test_claim_operation_is_distinct_from_raw_arrival_trigger(self):
        records=[dict(rid=x,when=f'2024-01-{d:02}T00:00:00Z',service_key='A') for x,d in [('a',1),('b',3)]]
        baseline=[event('a',None,1),event('b',None,3)]
        target=event('a',None,3,'claim');target['sequence']=0;target['payload']['issue_id']='I1'
        claimant=event('b','a',3,'arrival');claimant['sequence']=1;claimant['target_id']='I1';claimant['payload']['issue_id']='I1'
        r=analyze_bundle(records,{'a':'G1','b':'G1'},
                         {'arrival':{'events':baseline,'partition':{'a':'I1','b':'I2'}},
                          'band':{'events':[target,claimant],'partition':{'a':'I1','b':'I1'}}})
        row=r['positive_pair_changes'][0]
        self.assertEqual(row['other_later_final_trigger'],'arrival')
        self.assertEqual(row['other_later_operation'],'claim_on_arrival')
        self.assertEqual(r['merge_operation_breakdown'][0]['operation'],'claim_on_arrival')

if __name__=='__main__':unittest.main(verbosity=2)
