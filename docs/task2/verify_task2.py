"""Final read-only validation of measured outputs, old-file hashes and manifests."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
import site
import sys
import xml.etree.ElementTree as ET

import torch

from placement.config import load_config
from placement.env import quantile
from placement.io import file_hash, fingerprint, read_json, write_json
from placement.schema import manifest
from placement.training import code_fingerprints, load_policy, rollout

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from retained_documents import retained_location


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    config = load_config(run/'placement_config.json')
    torch.set_num_threads(1)
    baseline = read_json(ROOT/'docs/task2/evidence/h1_20261109_01/protected_before.json')
    changes = {key: {'expected': value['sha256'], 'actual': file_hash(ROOT/key) if (ROOT/key).is_file() else None}
               for key, value in baseline.items() if retained_location(ROOT,key,value['sha256']) is None}
    assert not changes, changes
    assert read_json(run/'runtime.json')['code_fingerprints'] == code_fingerprints()
    assert config.profile_sha256 == file_hash(run/'calibration/profile.json')
    assert read_json(run/'schema.json') == manifest(config)
    checkpoint_checks = []
    for seed in (7, 17, 27):
        for kind in ('best', 'last'):
            path = run/f'training/seed_{seed}/{kind}.pt'
            policy, saved = load_policy(path, config)
            assert saved['policy_seed'] == seed
            if kind == 'last':
                assert saved['episode'] == 300 and len(saved['history']) == 300
            checkpoint_checks.append({'seed': seed, 'kind': kind, 'episode': saved['episode'], 'sha256': file_hash(path)})
    profile = read_json(run/'calibration/profile.json')
    assert profile['maximum_capacity_rps'] is None
    rows = csv_rows(run/'evaluation/summary.csv')
    traces = read_json(run/'evaluation/traces.json')
    assert len(rows) == 150 and len(traces) == 30
    trace_index = {(t['scenario'], int(t['seed'])): t for t in traces}
    for t in traces:
        assert t['sha256'] == fingerprint({k:v for k,v in t.items() if k != 'sha256'})
    histogram = defaultdict(Counter)
    for row in csv_rows(run/'evaluation/latency_histogram.csv'):
        key = (row['scenario'], row['workload_seed'], row['method'], row['policy_seed'])
        histogram[key][float(row['latency_ms'])] += int(row['count'])
    for row in rows:
        assert int(row['received']) == int(row['processed'])+int(row['errors'])+int(row['unfinished'])
        assert int(row['accepted_moves']) == int(row['completed_moves'])+int(row['failed_moves'])+int(row['pending_moves'])
        assert 0 <= float(row['slo_violation_fraction_resolved']) <= 1
        trace = trace_index[(row['scenario'], int(row['workload_seed']))]
        assert row['trace_sha256'] == trace['sha256']
        assert int(row['received']) == sum(sum(d) for d in trace['demand'])
        hist = histogram[(row['scenario'], row['workload_seed'], row['method'], row['policy_seed'])]
        assert sum(hist.values()) == int(row['processed'])
        for field, fraction in (('latency_p95_ms', .95), ('latency_p99_ms', .99)):
            assert abs(float(row[field])-quantile(hist, fraction)) < 1e-9
    # Independently replay a real saved testing trace with the final policy.
    policy, _ = load_policy(run/'training/seed_7/best.pt', config)
    trace = traces[0]
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(7*1000003+trace['seed']*9176)
        env, _, _ = rollout(policy, config, trace['seed'], trace['scenario'], trace=trace)
    stored = next(r for r in rows if r['method']=='ippo' and r['policy_seed']=='7' and r['scenario']==trace['scenario'] and int(r['workload_seed'])==trace['seed'])
    for field, value in env.summary().items():
        if value is None:
            assert stored[field] == ''
        else:
            assert abs(float(stored[field])-float(value)) < 1e-10, field
    shadow = read_json(run/'shadow-matrix.json')
    assert shadow['workload_proposals'] == 162 and shadow['executed_operations'] == 0
    assert not shadow['closed_loop_ready']
    for report in shadow['reports']:
        assert report['legacy_routing_checkpoint_rejected'] and report['synthetic_plan_has_six_operations']
    for legacy in (ROOT/'results/training/checkpoints/best.pt', ROOT/'results/task2/placement_v1_20261109_02/training/seed_7/best.pt'):
        try:
            load_policy(legacy, config)
        except ValueError:
            pass
        else:
            raise AssertionError('Legacy/incompatible implementation checkpoint accepted')
    suites = list(ET.parse(ROOT/'docs/task2/evidence/task2-tests-final.xml').getroot().iter('testsuite'))
    assert sum(int(s.attrib['tests']) for s in suites) == 156
    assert all(int(s.attrib['errors']) == 0 and int(s.attrib['failures']) == 0 for s in suites)
    assert site.ENABLE_USER_SITE is False
    assert 'include-system-site-packages = false' in (Path(sys.prefix)/'pyvenv.cfg').read_text()
    # Self-reference in REPORT.md can now resolve before link validation.
    result = {'original_files_checked': len(baseline), 'original_files_unchanged': not changes, 'original_changes': changes,
              'final_checkpoint_checks': checkpoint_checks, 'evaluation_runs_checked': len(rows), 'exogenous_traces_checked': len(traces),
              'histograms_p95_p99_and_accounting_checked': True, 'one_final_policy_trace_reproduced': True,
              'tests_passed': 156, 'shadow_workload_proposals': 162, 'executed_infrastructure_operations': 0,
              'live_cluster_verified': False, 'closed_loop_ready': False, 'runtime': sys.executable,
              'source_code_fingerprints': code_fingerprints(), 'documentation_link_errors': []}
    write_json(run/'verification.json', result)
    problems = []
    for path in [ROOT/'docs/task2/REPORT.md']:
        content = path.read_text(encoding='utf-8')
        assert '\ufffd' not in content
        for target in re.findall(r'\]\(([^)]+)\)', content):
            if '://' in target:
                continue
            if not (path.parent/target.split('#')[0]).exists():
                problems.append(f'{path.name}: {target}')
    result['documentation_link_errors'] = problems
    write_json(run/'verification.json', result)
    assert not problems, problems
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_code_fingerprints','final_checkpoint_checks')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
