"""Small runner integrity tests; no real data or full experiment is loaded."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from experiments.e37_revision_suite import (dump_json, json_finite, make_job,
    replay_job, require_cache_identity, save_pickle)
from psid.gold import build_gold
from psid.schema import Record


class ConstantScorer:
    def __call__(self, X):
        return np.full(len(X), .6)


def fixture_record(rid, day):
    return Record(rid, "synthetic", datetime(2024, 5, 1, tzinfo=timezone.utc) + timedelta(days=day),
                  "service", "", "100 EXAMPLE ST", "", "synthetic description")


class RevisionRunnerTests(unittest.TestCase):
    def test_undefined_metric_is_null_and_finite_zero_remains_zero(self):
        value = json_finite({"nan": float("nan"), "inf": np.float64(float("inf")),
                             "zero": 0.0, "nested": [np.int64(2)]})
        self.assertEqual(value, {"nan": None, "inf": None, "zero": 0.0, "nested": [2]})
        json.dumps(value, allow_nan=False)

    def test_cache_identity_rejects_missing_or_different_provenance(self):
        require_cache_identity({"seed": 13}, {"seed": 13}, "fixture")
        for found in (None, {"seed": 1}):
            with self.assertRaises(RuntimeError):
                require_cache_identity(found, {"seed": 13}, "fixture")

    def test_zero_merge_run_serializes_trace_and_checks_replay_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "private").mkdir()
            (run / "results").mkdir()
            dump_json(run / "experiment_protocol.json", {"fixture": True})
            dump_json(run / "reports" / "preparation_manifest.json", {"fixture": True})
            records = [fixture_record("a", 0), fixture_record("b", 1)]
            gold = build_gold("synthetic", records, {"b": "a"})
            save_pickle(run / "private" / "data_May.pkl", {"records": records, "gold": gold})
            save_pickle(run / "private" / "model_gbt_13.pkl", ConstantScorer())
            job = make_job("gbt", 13, "May", "fixture", {"delta": 0}, .99, trace=True)
            summary = replay_job(str(run), job)
            self.assertIsNone(summary["f1"])
            result = json.loads((run / "results" / f"{job['run_id']}.json").read_text())
            self.assertEqual(result["decision"]["decisions"], 0)
            self.assertIsNone(result["decision"]["dec_precision"])
            self.assertIsNone(result["metrics"]["precision"])
            self.assertEqual(result["candidate_trace"]["rows"], 2)
            trace = run / "private" / f"{job['run_id']}_candidates.jsonl"
            self.assertEqual(len(trace.read_text().splitlines()), 2)
            import pickle
            with (run / "private" / f"{job['run_id']}_events.pkl").open("rb") as handle:
                events = pickle.load(handle)
            self.assertEqual(events["candidate_snapshots"], [])
            self.assertEqual(events["candidate_trace_path"], str(trace.resolve()))
            self.assertTrue(replay_job(str(run), job)["cached"])
            changed = dict(job, tau=.1)
            with self.assertRaises(RuntimeError):
                replay_job(str(run), changed)

    def test_training_candidates_do_not_depend_on_python_hash_seed(self):
        program = r'''
import hashlib,json
from datetime import datetime,timedelta,timezone
import numpy as np
from psid.schema import Record
from psid.gold import build_gold
from psid.train_cand import build_candidate_pairs
records=[Record(str(i),'synthetic',datetime(2024,1,1,tzinfo=timezone.utc)+timedelta(hours=i),
    'service','','100 EXAMPLE ST','','aa bb cc dd ee ff gg hh ii jj') for i in range(14)]
gold=build_gold('synthetic',records,{'1':'0','3':'2','5':'4'})
x,y=build_candidate_pairs(records,gold,seed=13)
assert np.allclose(x[:,13],np.log(2)/np.log(11))
print(json.dumps({'sha':hashlib.sha256(x.tobytes()+y.tobytes()).hexdigest(),'rows':len(y)}))
'''
        outputs = []
        for seed in ("1", "77"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            text = subprocess.check_output([sys.executable, "-c", program], env=env,
                                           cwd=Path(__file__).resolve().parents[1], text=True)
            outputs.append(json.loads(text))
        self.assertEqual(outputs[0], outputs[1])
        self.assertGreater(outputs[0]["rows"], 0)


if __name__ == "__main__":
    unittest.main()
