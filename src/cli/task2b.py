"""Đóng Task 2B bằng checkpoint cố định, không huấn luyện và không gọi hạ tầng."""
import argparse
import csv
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import time

import numpy as np
import psutil
import torch

from placement.config import load_config
from placement.env import PlacementEnv
from placement.io import new_directory, read_json, write_json, write_csv, file_hash
from placement.policies import PlacementBaseline
from placement.training import load_policy, code_fingerprints

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT/'results/task2/placement_v1_20261109_03'


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'results/task2/placement_v1_20261109_03_closeout')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = new_directory(output/'offline')
    torch.set_num_threads(1)
    config = load_config(V1/'placement_config.json')
    policies = {seed: load_policy(V1/f'training/seed_{seed}/best.pt', config)[0] for seed in (7, 17, 27)}
    comparison = read_csv(V1/'evaluation/comparison.csv')
    paired = read_csv(V1/'evaluation/paired_differences.csv')
    write_csv(stage/'scenario_comparison.csv', comparison)
    write_csv(stage/'paired_differences.csv', paired)
    traces = {(t['scenario'], t['seed']): t for t in read_json(V1/'evaluation/traces.json')}
    stored = read_csv(V1/'evaluation/summary.csv')
    telemetry, resources = [], []
    for row in stored:
        scenario, seed, method = row['scenario'], int(row['workload_seed']), row['method']
        policy_seed = int(row['policy_seed']) if row['policy_seed'] else None
        env = PlacementEnv(config); obs = env.reset(trace=traces[scenario, seed])
        raw_physics = env._physics
        local = []
        prefix = {key: row[key] for key in ('scenario', 'workload_seed', 'method', 'policy_seed')}
        def instrument():
            before = env.resource_sum.copy()
            waste = env.total['waste_sum']
            raw_physics()
            values = env.resource_sum-before
            point = {**prefix, 'second': env.second-1, 'source': 'SIMULATED', 'trace_sha256': env.trace['sha256']}
            for c, cluster in enumerate('ABC'):
                for j, name in enumerate(('cpu_usage', 'ram_usage', 'cpu_reservation', 'ram_reservation')):
                    point[f'{cluster}_{name}'] = float(values[c, j])
            for j, name in enumerate(('cpu_usage', 'ram_usage', 'cpu_reservation', 'ram_reservation')):
                point[f'{name}_std'] = float(np.std(values[:, j]))
                point[f'{name}_mean'] = float(np.mean(values[:, j]))
            point['resource_waste'] = float(env.total['waste_sum']-waste)
            local.append(point)
        env._physics = instrument
        baseline = PlacementBaseline(method, config) if method != 'ippo' else None
        with torch.random.fork_rng(devices=[]):
            if policy_seed is not None:
                torch.manual_seed(policy_seed*1000003+seed*9176)
            done = False
            while not done:
                actions = baseline.act(env) if baseline else policies[policy_seed].act(obs, env.masks())[0]
                obs, _, done, _ = env.step(actions)
        for key, value in env.summary().items():
            if value is None:
                assert row[key] == ''
            else:
                assert abs(float(row[key])-float(value)) < 1e-9, (key, scenario, seed, method)
        telemetry.extend(local)
        resources.append({**prefix, **{key: statistics.mean(x[key] for x in local) for key in local[0] if key.endswith(('_std', '_mean')) or key=='resource_waste'}})
    write_csv(stage/'resource_timeseries_reconstructed.csv', telemetry)
    write_csv(stage/'resource_summary.csv', resources)
    write_json(stage/'reconstruction.json', {'source':'SIMULATED', 'runs':len(stored), 'ticks':len(telemetry),
        'original_timeseries_has_resource_columns':False, 'all_original_summaries_reproduced':True,
        'method':'Bọc _physics trên instance mới để đọc resource_sum, không sửa source V1; cùng seed/trace/policy RNG.',
        'distinction':'mean_t(std_cluster(t)) khác std_cluster(mean_t(cluster)); không suy std RAM từ utilization trung bình.'})
    inference = []
    for seed, policy in policies.items():
        env=PlacementEnv(config); obs=env.reset(seed=41001); masks=env.masks()
        for _ in range(100):
            policy.act(obs,masks,greedy=True)
        samples=[]
        for _ in range(1000):
            start=time.perf_counter_ns(); policy.act(obs,masks,greedy=True)
            samples.append((time.perf_counter_ns()-start)/1e6)
        inference.append({'policy_seed':seed, 'samples':1000, 'warmup':100, 'mean_ms':statistics.mean(samples),
            'p95_ms':float(np.quantile(samples,.95)), 'p99_ms':float(np.quantile(samples,.99)),
            'scope':'Một quyết định chung cho 3 actor; cố định obs/mask, không gồm xây state/validator/SSH.',
            'checkpoint_sha256':file_hash(V1/f'training/seed_{seed}/best.pt')})
    write_json(stage/'inference.json', {'source':'MEASURED_LOCAL_CPU','torch_threads':1,'python':platform.python_version(),
        'processor':platform.processor(),'cpu_logical':psutil.cpu_count(),'memory_bytes':psutil.virtual_memory().total,'measurements':inference})
    freeze=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,check=True)
    (stage/'runtime-freeze.txt').write_text(freeze.stdout,encoding='utf-8')
    write_json(stage/'metadata.json',{'source':'SIMULATED_REPLAY_AND_LOCAL_BENCHMARK','training_executed':False,
        'config':config.to_dict(),'code_fingerprints':code_fingerprints(),'checkpoint_seeds':[7,17,27],
        'trace_hashes':[t['sha256'] for t in traces.values()]})
    lines=['# H6 Đóng các thiếu sót Task 2B','',
        '**Task 2B chưa nghiệm thu đầy đủ: A4 PENDING bằng chứng sau phục hồi thủ công. Phần B Task 3 tiếp tục độc lập.**','',
        '| Hạng mục | Trạng thái | Kết quả |','|---|---|---|',
        '| A1 | DONE | Phân tích đủ 6 scenario, policy/workload SD và paired differences từ run gốc |',
        '| A2 | DONE | Tái tạo telemetry tài nguyên 150 run, xác minh summary khớp; không bịa trường bị thiếu |',
        '| A3 | DONE | 3 checkpoint, CPU 1 thread, warmup 100, 1000 mẫu, mean/p95/p99 |',
        '| A4 | PENDING | Cluster A phục hồi/SSH thành công theo người dùng; chưa đủ snapshot và shadow replay mới |',
        '| A5 | PENDING | Chỉ có kế hoạch yêu cầu phê duyệt bằng văn bản; không phát tải thật |',
        '| A6 | DONE | Phân biệt reward/randomization V1 với phần bổ sung V2/Task 4 |','',
        '## A1 Kết quả theo scenario','',
        'Số là mean từng run; SD policy được tính sau mean qua workload, SD workload sau mean qua policy. Không xem 15 run IPPO là 15 policy seed độc lập.','',
        '| Scenario | Method | Throughput ± SD policy / workload | P99 ms ± SD policy / workload | SLA resolved ± SD policy / workload |','|---|---|---|---|---|']
    for scenario in sorted({r['scenario'] for r in comparison}):
        for method in ('round_robin','least_loaded','ippo'):
            cells=[]
            for metric in ('throughput_rps','latency_p99_ms','slo_violation_fraction_resolved'):
                r=next(x for x in comparison if x['scenario']==scenario and x['method']==method and x['metric']==metric)
                sd=lambda key: f"{float(r[key]):.4f}" if r[key] else 'N/A'
                cells.append(f"{float(r['mean']):.4f} ± {sd('policy_seed_std')} / {sd('workload_seed_std')}")
            lines.append('| '+ ' | '.join([scenario,method,*cells])+' |')
        for baseline in ('round_robin','least_loaded'):
            part=[r for r in paired if r['scenario']==scenario and r['baseline']==baseline]
            delta={k:statistics.mean(float(r[k]) for r in part) for k in ('throughput_rps','latency_p99_ms','slo_violation_fraction_resolved')}
            lines.append(f"\n{scenario}, IPPO − {baseline}: throughput {delta['throughput_rps']:+.4f} req/s; p99 {delta['latency_p99_ms']:+.4f} ms; SLA {delta['slo_violation_fraction_resolved']:+.4f}. Throughput dương tốt hơn; latency/SLA âm tốt hơn. Không quy thành một người thắng mọi mục tiêu.\n")
    lines += ['','## A2 Cân bằng tài nguyên','',
        'File timeseries gốc chỉ có arrival/processed/errors/waiting/placements, không có CPU/RAM. Vì vậy không thể tính đúng các std yêu cầu chỉ từ file đó. Closeout replay frozen V1 để ghi `offline/resource_timeseries_reconstructed.csv`; mọi summary gốc khớp trong 1e-9. Không huấn luyện hoặc sửa file gốc.','',
        '| Method | CPU usage std | RAM usage std | CPU reservation std | RAM reservation std | CPU mean | RAM mean | Waste |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for method in ('round_robin','least_loaded','ippo'):
        part=[r for r in resources if r['method']==method]
        keys=('cpu_usage_std','ram_usage_std','cpu_reservation_std','ram_reservation_std','cpu_usage_mean','ram_usage_mean','resource_waste')
        lines.append('| '+method+' | '+' | '.join(f'{statistics.mean(r[k] for r in part):.6f}' for k in keys)+' |')
    lines += ['','## A3 Thời gian inference','', '| Policy seed | Mean ms | P95 ms | P99 ms |','|---|---:|---:|---:|']
    lines += [f"| {r['policy_seed']} | {r['mean_ms']:.4f} | {r['p95_ms']:.4f} | {r['p99_ms']:.4f} |" for r in inference]
    lines += ['','Đây là benchmark local CPU, không phải latency controller/live Kubernetes; tải máy có thể ảnh hưởng tail. Cấu hình máy và phạm vi đo ở `offline/inference.json`.','',
        '## A4 và A5','',
        'Cluster A đã phục hồi theo kiểm tra thủ công của người dùng; không xem offline. A4 PENDING: còn thiếu snapshot UTC, identity/ownerReferences, inventory requests/limits toàn cụm, raw Metrics API và shadow replay placement-v1. Báo cáo thủ công chưa phải xác minh độc lập của agent; calibration Running được báo ở lần kiểm tra trước. B/C chưa có thật nên closed_loop_ready=false. A5 PENDING, chưa thử tải. Xem H6 và manual_recovery_20261109_01/manual_observation.json để biết provenance; không thay SSH key, sudoers hay OpenStack.','',
        '## A6 Phần đã có và còn thiếu','',
        'V1 đã có success, latency/SLO, error, SLO violation, resource waste, migration/overlap cost, failed moves; randomization CPU factor, background CPU, network factor, readiness và faults. V1 actor nhìn toàn bộ 123 features, chưa phải actor local/critic global CTDE.','',
        'V2 bổ sung DAG, bốn mục tiêu gồm cân bằng CPU/RAM, traffic bytes/cost theo cạnh, ma trận RTT/bandwidth, CPU/RAM capacity randomization, bandwidth jitter, và schema tách actor/critic. Các miền randomization vẫn ASSUMPTION(SIMULATED), chưa được xác minh thực nghiệm. MAPPO/MADDPG/QMIX training, GA/Single-Agent RL/K8s-default-like benchmarks và nhiều controlled DAG đồng thời để Task 4/5.','',
        'Calibration vẫn profile V1 low-load, CPU purged relative MAE 60,86%; maximum capacity null. Không có profile mới vì A5 chưa được duyệt/chạy.','',
        '## Bằng chứng và tái hiện','',
        '[Closeout artifacts](../../results/task2/placement_v1_20261109_03_closeout/), source `src/cli/task2b.py`; `python -m cli.task2b --output <thư mục mới>` tạo stage offline mới và từ chối ghi đè. Seed/trace/config/code fingerprints, runtime freeze, JUnit được lưu cùng run.']
    report='\n'.join(lines)+'\n'
    a4_status=read_json(ROOT/'docs/task2/A4_STATUS.json').get('A4_status','PENDING')
    write_json(stage/'report_summary.json',{'canonical_report':'docs/task2/REPORT.md',
        'statuses':{'A1':'DONE','A2':'DONE','A3':'DONE','A4':a4_status,'A5':'PENDING','A6':'DONE'},
        'replay_runs':len(stored),'resource_ticks':len(telemetry),'inference':inference,
        'note':'Báo cáo duy nhất nằm trong docs/task2/REPORT.md; results chỉ lưu số liệu và bằng chứng.'})
    print(f'Task 2B: A1/A2/A3/A6 DONE; A4 {a4_status} theo A4_STATUS.json; A5 PENDING. Không huấn luyện.')


if __name__=='__main__': main()
