"""Gom tài liệu một lần; chỉ gỡ bản Markdown cũ sau khi ZIP đã khớp hash."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'docs/archive/reports_before_consolidation_20261109_01.zip'


def digest(data): return hashlib.sha256(data).hexdigest()


def main():
    if ARCHIVE.exists(): raise FileExistsError(ARCHIVE)
    old=sorted({*list((ROOT/'docs').rglob('*.md')),*list((ROOT/'results').rglob('*.md'))})
    old=[p for p in old if p!=ROOT/'docs/README.md']
    scripts=[ROOT/p for p in ('docs/task2/build_reports.py','docs/task3/build_reports.py')]
    backups=old+scripts+[ROOT/'src/cli/task3.py',ROOT/'src/cli/task2b.py',ROOT/'README.md',ROOT/'docs/README.md']
    originals={p.relative_to(ROOT).as_posix():p.read_bytes() for p in backups}
    protected={p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for base in ('data','results','src/placement','src/placement_v2')
               for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix.lower()!='.md' and '__pycache__' not in p.parts}
    targets={i:ROOT/f'docs/task{i}/REPORT.md' for i in (1,2,3)}
    assert all(not p.exists() for p in targets.values())
    # Đọc mọi đầu vào trước khi thay đổi đường dẫn; tuyệt đối không đưa credential vào ZIP.
    ARCHIVE.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(ARCHIVE,'x',ZIP_DEFLATED) as z:
        for name,content in originals.items(): z.writestr(name,content)
        z.writestr('MANIFEST.json',json.dumps({'utc':datetime.now(timezone.utc).isoformat(),
                    'files':{n:digest(b) for n,b in originals.items()}},indent=2))
    with ZipFile(ARCHIVE) as z:
        assert z.testzip() is None
        assert all(digest(z.read(n))==digest(b) for n,b in originals.items())

    groups={1:[],2:[],3:[]}
    special={
        'docs/task2/H6_CLOSEOUT_REPORT.md':(2,'closeout','H6 — Closeout và trạng thái A4 hiện hành'),
        'docs/task2/A5_LOAD_TEST_APPROVAL_REQUEST.md':(2,'a5-load-plan','Kế hoạch A5 — PENDING, chưa phát tải'),
        'results/task2/placement_v1_20261109_03/REPORT.md':(2,'placement-results','Kết quả Placement V1'),
        'docs/task3/SIMULATOR_V2_REPORT.md':(3,'results','Kết quả Simulator DAG V2'),
        'docs/task3/MATH_MODEL.md':(3,'math-model','Mô hình toán học'),
        'docs/task3/SCHEMA_V2.md':(3,'schema','Schema placement-v2'),
        'docs/task3/THEORY_NOTES.md':(3,'theory','Khung lý thuyết'),
        'docs/task3/SCHEMA_REAL_FEASIBILITY.md':(3,'real-feasibility','Khả năng đo feature trên hạ tầng thật'),
        'docs/task3/VALIDATION_REPORT.md':(3,'validation','Kiểm thử và tái hiện'),
        'docs/task3/PROGRESS_UPDATE.md':(3,'progress','Trạng thái hoàn thành Task 3')}
    task1_names={'docs/windows-setup.md','docs/real-cluster-integration.md','docs/k3s-deployment.md',
                 'docs/metrics-calibration.md','docs/reports/stage-g.md','docs/reports/stage-g-audit.md'}
    routes={}
    for p in old:
        name=p.relative_to(ROOT).as_posix()
        if name in special: task,anchor,title=special[name]
        else:
            task=1 if name in task1_names else 3 if '/task3/' in name else 2
            anchor='source-'+re.sub('[^a-zA-Z0-9]+','-',name).strip('-').lower()
            title=p.stem.replace('_',' ')
        routes[p.resolve()]=(task,anchor)
        groups[task].append((p,anchor,title))
    for task in groups:
        priority={'closeout':0,'placement-results':1,'a5-load-plan':2,'results':0,'math-model':1,
                  'schema':2,'theory':3,'real-feasibility':4,'validation':5,'progress':6}
        groups[task].sort(key=lambda row:(priority.get(row[1],20),row[0].as_posix()))

    def rel(path,task): return os.path.relpath(path,targets[task].parent).replace('\\','/')

    def links(text,source,task):
        def replace(match):
            label,target=match.group(1),match.group(2)
            if '://' in target or target.startswith(('#','mailto:')): return match.group(0)
            base,sep,fragment=target.partition('#')
            destination=(source.parent/base).resolve()
            if destination in routes:
                other,anchor=routes[destination]
                return f'[{label}]({"" if task==other else rel(targets[other],task)}#{anchor})'
            if destination in scripts:
                return f'[{label}]({rel(ARCHIVE,task)})'
            return f'[{label}]({rel(destination,task)}'+(f'#{fragment}' if sep else '')+')'
        return re.sub(r'\[([^\]]*)\]\(([^)]+)\)',replace,text)

    intro={1:'''# Task 1 — Dữ liệu Cluster A và hệ thống thu thập

Đây là báo cáo duy nhất cần đọc cho Task 1: phạm vi, nguồn dữ liệu, collector, schema, cách chạy, chất lượng dữ liệu và giới hạn. Các tài liệu hướng dẫn cũ đã được gom vào các phần bên dưới.

## Kết quả và trạng thái hiện tại

- Bộ dữ liệu REAL: `data/runs/cluster-a-real-003`, gồm 18 raw snapshot, 54 metrics record được tái tạo và 152 request client không lỗi. Có 8 window hợp lệ, 10 window bị loại (warm-up, thiếu latency window hoặc resource sample cache lặp).
- Miền tải quan sát 0,2733–3,2988 req/s; chưa đo saturation/capacity tối đa. Phân biệt CPU/RAM node, pod và Ubuntu host; không cộng trùng các scope.
- Collector/preprocessing/sample API/Locust và CLI nằm ở `src/integration`, `src/apps/sample_api`, `src/loadtests`, `src/cli/cluster.py`. Dữ liệu gốc không bị chỉnh sửa trong lần gom tài liệu.
- Cluster A hiện đã phục hồi theo kiểm tra thủ công của người dùng: k3s-a Ready; SSH từ Ubuntu user stack thành công. Đây là báo cáo thủ công, không thay thế snapshot mới có UTC/UID/ownerReferences. Chi tiết kiểm tra và checklist A4 ở [Task 2](../task2/REPORT.md#closeout).
- Profile/calibration và sai số được trình bày đầy đủ ở [Task 2](../task2/REPORT.md#placement-results). CPU purged relative MAE 60,86%, chỉ có hiệu lực low-load; không suy capacity từ các số này.

## Dữ liệu cần đối chiếu

| Nội dung | File |
|---|---|
| Raw và metadata collector | [raw.jsonl](../../data/runs/cluster-a-real-003/collector/raw.jsonl), [session.json](../../data/runs/cluster-a-real-003/collector/session.json) |
| Metrics và client requests | [metrics.csv](../../data/runs/cluster-a-real-003/collector/metrics.csv), [requests.csv](../../data/runs/cluster-a-real-003/client/requests.csv) |
| Window hợp lệ và chất lượng | [aligned.csv](../../data/runs/cluster-a-real-003/processed/aligned.csv), [quality.json](../../data/runs/cluster-a-real-003/processed/quality.json) |

Các phần bàn giao G ở cuối là lịch sử trước khi có dữ liệu thật; thông báo chưa kết nối ở phần lịch sử không phải trạng thái Cluster A hiện tại. Các lệnh deploy/apply trong hướng dẫn là tài liệu tham khảo, không phải hành động đã thực hiện ở lượt này; hạ tầng hiện mặc định chỉ đọc.
''',2:'''# Task 2 — Calibration, Placement V1 và closeout

Đây là báo cáo duy nhất cần đọc cho Task 2, gồm kết quả H1–H6, calibration, training/evaluation, shadow, kế hoạch thử tải và các phụ lục kỹ thuật. Không cần mở thêm báo cáo trong results.

## Kết quả và trạng thái hiện tại

- Run chính: `results/task2/placement_v1_20261109_03`; closeout: `results/task2/placement_v1_20261109_03_closeout`. Dữ liệu/checkpoint/CSV ở nguyên vị trí.
- Đã huấn luyện 3 policy seed × 300 episode và đánh giá 150 run ở giai đoạn Task 2 trước đây; không huấn luyện lại khi gom tài liệu. IPPO không vượt Least Loaded tổng thể.
- Calibration V1 chỉ low-load; CPU purged relative MAE 60,86%, saturation/capacity tối đa chưa xác định.
- **A1/A2/A3/A6 DONE; A4 PENDING; A5 PENDING.** Cluster A đã phục hồi theo kiểm tra thủ công của người dùng, không xem offline. A4 còn thiếu snapshot UTC, identity/ownerReferences, inventory toàn cụm, raw Metrics API và shadow replay mới. `closed_loop_ready=false`; B/C vẫn giả lập.
- 156 test cũ của Task 2 đã đạt ở lần nghiệm thu V1; bộ tổng hiện có 206 test đạt (156 cũ + 50 V2), xem [Task 3](../task3/REPORT.md#validation). Đây là kết quả đã chạy trước, không phải lần test mới ở lượt gom tài liệu.

Phần closeout bên dưới là trạng thái hiện hành. Các phụ lục audit, proposal và báo cáo run cũ được đánh dấu lịch sử; thông báo SSH timeout/BLOCKED trong đó không thay thế cập nhật phục hồi mới. A5 chưa được duyệt và chưa chạy tải.
''',3:'''# Task 3 — Mô hình hóa bài toán và Simulator DAG V2

Đây là báo cáo duy nhất cần đọc cho Task 3: lý thuyết, mô hình toán, simulator, schema CTDE, feasibility, validation, kết quả và cách tái hiện. Các report lẻ trong results đã được lưu trữ.

## Kết quả và trạng thái hiện tại

- B1–B8 đã hoàn thành. Run nghiệm thu `results/task3/dag_v2_20261109_05`: **206 test đạt** (156 cũ + 50 V2), **192 episode smoke SIMULATED**, 12 shared trace, 286 feature rows.
- Workflow vẫn chạy trong migration PREPARING/GRACE nếu còn endpoint Ready và dependency hợp lệ. Workflow arrivals/completed/failed/unfinished được theo dõi riêng với service invocations, terminal/SLA không đếm trùng tại fork-join.
- Tổng smoke: 13.568 workflow arrivals = 13.185 completed + 380 failed + 3 unfinished; 49.306 service invocations. Đây là các episode giả lập độc lập.
- Không huấn luyện MAPPO/IPPO, không thay đổi hạ tầng. Cluster A đã phục hồi theo báo cáo thủ công, A4 còn PENDING bằng chứng; việc đó không chặn Task 3.
- B/C, WAN, capacity, queues và RAM pressure chưa xác minh thực tế. Profile chỉ low-load, CPU error 60,86%. Task 4 mới làm MAPPO CTDE/training; Task 5 làm các baseline GA/Single-Agent RL/K8s-default-like.

Tái hiện khi cần vào output mới (không phải yêu cầu chạy lại hiện tại):

```powershell
.\\.venv-task2\\Scripts\\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi> --test-python .venv/Scripts/python.exe
```

Từ nay CLI lưu số liệu/metadata ở results; báo cáo đọc ở file này, không sinh thêm Markdown cho mỗi run. Các report thử _01–_04 và report run _05 trong phụ lục là lịch sử, không phải nhiều task cần kiểm tra riêng.
'''}
    for task in (1,2,3):
        chunks=[intro[task],'## Mục lục\n']
        for source,anchor,title in groups[task]: chunks.append(f'- [{title}](#{anchor})')
        chunks.append('\nChỉ cần file này. Bản gốc trước khi gom nằm trong [ZIP lưu trữ](../archive/reports_before_consolidation_20261109_01.zip), kèm hash để đối chiếu.\n')
        for source,anchor,title in groups[task]:
            original=originals[source.relative_to(ROOT).as_posix()].decode('utf-8-sig')
            if anchor=='placement-results':
                original=original.replace('SSH Ubuntu timeout; chưa có live verification của A và B/C chưa triển khai.',
                    'Cập nhật hiện hành: Cluster A đã phục hồi theo kiểm tra thủ công; A4 PENDING bằng chứng và B/C chưa triển khai. Xem phần closeout trong báo cáo này.')
            if anchor=='a5-load-plan':
                original=original.replace('Phê duyệt phải nêu rõ file này','Phê duyệt phải nêu rõ `docs/task2/REPORT.md`, mục **Kế hoạch A5** trong file này')
            original=links(original,source,task)
            original=re.sub(r'^(#{1,5}) ',lambda m:'#'*(len(m[1])+2)+' ',original,flags=re.M)
            historical=anchor.startswith('source-') and not (task==1 and source.name in ('windows-setup.md','real-cluster-integration.md','k3s-deployment.md','metrics-calibration.md'))
            chunks.extend([f'\n<a id="{anchor}"></a>\n\n## {title}\n'])
            if historical:
                chunks.append('<details>\n<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>\n')
            chunks.append(original)
            if historical: chunks.append('\n</details>\n')
        target=targets[task]; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('\n'.join(chunks)+'\n',encoding='utf-8')
    # Chỉ gỡ file đúng danh sách sau khi đã có cả ZIP và ba báo cáo mới.
    for p in old+scripts:
        resolved=p.resolve()
        assert resolved.is_relative_to(ROOT) and p.is_file()
        assert resolved.is_relative_to(ROOT/'docs') or resolved.is_relative_to(ROOT/'results')
        with ZipFile(ARCHIVE) as z: assert z.read(p.relative_to(ROOT).as_posix())==p.read_bytes()
        p.unlink()
    state={'utc':datetime.now(timezone.utc).isoformat(),'archive':ARCHIVE.relative_to(ROOT).as_posix(),
           'archived_hashes':{n:digest(b) for n,b in originals.items()},'retained_artifact_hashes':protected,
           'reports':[p.relative_to(ROOT).as_posix() for p in targets.values()],
           'removed_markdown_files':len(old),'archived_legacy_generators':2,
           'note':'Chỉ tổ chức lại tài liệu theo yêu cầu người dùng; raw dataset, checkpoints, physics/schema và số liệu run không đổi.'}
    (ARCHIVE.parent/'consolidation_manifest.json').write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'reports':state['reports'],'archived_markdown_files':len(old),'protected_artifacts':len(protected)},ensure_ascii=False))


if __name__=='__main__': main()
