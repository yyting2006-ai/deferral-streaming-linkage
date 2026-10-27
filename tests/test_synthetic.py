"""Synthetic smoke tests: these fixtures contain no real municipal records."""
import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from psid.data import load_san_diego
from psid.gold import build_gold
from psid.metrics import evaluate_partition
from psid.schema import Record

def record(rid,day):
    return Record(rid,'synthetic',datetime(2024,5,day),'test-service','','100 EXAMPLE ST','','synthetic fixture')

class TestSynthetic(unittest.TestCase):
    def test_loader_orders_and_excludes_posthoc_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'synthetic.csv'
            with path.open('w',newline='',encoding='utf-8') as handle:
                fields=['service_request_id','service_request_parent_id','date_requested','service_name','street_address','public_description','status']
                writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
                writer.writerow(dict(service_request_id='b',service_request_parent_id='a',date_requested='2024-05-02',service_name='test',street_address='100 EXAMPLE ST',public_description='synthetic',status='closed'))
                writer.writerow(dict(service_request_id='a',date_requested='2024-05-01',service_name='test',street_address='100 EXAMPLE ST',public_description='synthetic',status='closed'))
            records,links=load_san_diego(path=path)
            self.assertEqual([r.rid for r in records],['a','b'])
            self.assertEqual(links,{'b':'a'})
            self.assertTrue(all(r.extra=={} for r in records))
    def test_certified_domain_excludes_unlinked_records(self):
        records=[record('a',1),record('b',2),record('u',3)]
        gold=build_gold('synthetic',records,{'b':'a'})
        self.assertEqual(gold.known,{'a','b'})
        self.assertIsNone(gold.same_issue('a','u'))
        self.assertEqual(gold.n_pos_pairs,1)
    def test_perfect_partition_has_unit_f1(self):
        records=[record('a',1),record('b',2),record('c',3),record('d',4)]
        gold=build_gold('synthetic',records,{'b':'a','d':'c'})
        result=evaluate_partition({'a':'x','b':'x','c':'y','d':'y'},gold,{'a','b','c','d'})
        self.assertEqual(result['f1'],1.0)

if __name__=='__main__': unittest.main()
