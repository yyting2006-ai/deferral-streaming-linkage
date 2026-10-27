"""Bounded fault injection and shared-state tests, using synthetic event logs."""
import copy, json, unittest
from pathlib import Path
from compare_prefix import compare, shared_randomness, cloned_rng

T='2024-05-01T12:00:00+00:00'
def event(hour,obj,action='NEW',target=None):
    return dict(emitted_at=f'2024-05-01T{hour:02}:00:00+00:00',
                object_id=obj,action=action,target_id=target)
state,fingerprint=shared_randomness(13)
BASE={'common_randomness':fingerprint,
      'events':[event(8,'a'),event(10,'b','MERGE','a'),event(16,'c')]}

class TestCompletePrefixes(unittest.TestCase):
    def test_extra_emission(self):
        run=copy.deepcopy(BASE);run['events'].insert(2,event(11,'d'))
        result=compare(BASE,run,T)
        self.assertFalse(result['equal']);self.assertEqual(result['b_prefix_count'],3)
    def test_missing_emission(self):
        run=copy.deepcopy(BASE);run['events'][1]['emitted_at']=event(13,'b')['emitted_at']
        self.assertFalse(compare(BASE,run,T)['equal'])
    def test_same_content_early_emission(self):
        run=copy.deepcopy(BASE);run['events'][0]['emitted_at']=event(7,'a')['emitted_at']
        self.assertFalse(compare(BASE,run,T)['equal'])
    def test_action_and_target(self):
        for key,value in [('action','NEW'),('target_id','z')]:
            run=copy.deepcopy(BASE);run['events'][1][key]=value
            self.assertFalse(compare(BASE,run,T)['equal'])
    def test_order_with_equal_timestamps(self):
        a=copy.deepcopy(BASE);a['events'][1]['emitted_at']=a['events'][0]['emitted_at']
        b=copy.deepcopy(a);b['events'][:2]=reversed(b['events'][:2])
        self.assertFalse(compare(a,b,T)['equal'])
    def test_future_only_change(self):
        run=copy.deepcopy(BASE);run['events'][2]['object_id']='future_changed'
        self.assertTrue(compare(BASE,run,T)['equal'])
    def test_shared_random_state(self):
        a,b=cloned_rng(state),cloned_rng(state)
        self.assertEqual([a.random() for _ in range(50)],[b.random() for _ in range(50)])
        run=copy.deepcopy(BASE);run['common_randomness']=shared_randomness(14)[1]
        self.assertFalse(compare(BASE,run,T)['valid_comparison'])
    def test_timezones_and_boundary(self):
        run=copy.deepcopy(BASE);run['events'][0]['emitted_at']='2024-05-01T16:00:00+08:00'
        self.assertTrue(compare(BASE,run,T)['equal'])
        run['events'].insert(2,event(12,'at_cutoff'))
        self.assertFalse(compare(BASE,run,T)['equal'])
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):compare(BASE,BASE,'2024-05-01T12:00:00')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(TestCompletePrefixes)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    Path(__file__).with_name('test_result.json').write_text(json.dumps({
        'scope':'Synthetic unit tests only; not a real-data protocol audit',
        'tests_run':result.testsRun,'failures':len(result.failures),
        'errors':len(result.errors),'passed':result.wasSuccessful()},indent=2),encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
