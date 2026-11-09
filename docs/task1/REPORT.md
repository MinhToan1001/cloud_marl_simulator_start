# Task 1 — Dữ liệu Cluster A và hệ thống thu thập

Đây là báo cáo duy nhất cần đọc cho Task 1: phạm vi, nguồn dữ liệu, collector, schema, cách chạy, chất lượng dữ liệu và giới hạn. Các tài liệu hướng dẫn cũ đã được gom vào các phần bên dưới.

## Kết quả và trạng thái hiện tại

- Bộ dữ liệu REAL: `data/runs/cluster-a-real-003`, gồm 18 raw snapshot, 54 metrics record được tái tạo và 152 request client không lỗi. Có 8 window hợp lệ, 10 window bị loại (warm-up, thiếu latency window hoặc resource sample cache lặp).
- Miền tải quan sát 0,2733–3,2988 req/s; chưa đo saturation/capacity tối đa. Phân biệt CPU/RAM node, pod và Ubuntu host; không cộng trùng các scope.
- Collector/preprocessing/sample API/Locust và CLI nằm ở `src/integration`, `src/apps/sample_api`, `src/loadtests`, `src/cli/cluster.py`. Dữ liệu gốc không bị chỉnh sửa trong lần gom tài liệu.
- Cluster A hiện đã xác minh qua snapshot REAL mới: k3s-a Ready, có UID/ownerReferences và Metrics API; A4 DONE. Snapshot do user stack chạy collector, agent tải về và chạy shadow replay để xác minh. Chi tiết kiểm tra và checklist A4 ở [Task 2](../task2/REPORT.md#closeout).
- Profile/calibration và sai số được trình bày đầy đủ ở [Task 2](../task2/REPORT.md#placement-results). CPU purged relative MAE 60,86%, chỉ có hiệu lực low-load; không suy capacity từ các số này.

## Dữ liệu cần đối chiếu

| Nội dung | File |
|---|---|
| Raw và metadata collector | [raw.jsonl](../../data/runs/cluster-a-real-003/collector/raw.jsonl), [session.json](../../data/runs/cluster-a-real-003/collector/session.json) |
| Metrics và client requests | [metrics.csv](../../data/runs/cluster-a-real-003/collector/metrics.csv), [requests.csv](../../data/runs/cluster-a-real-003/client/requests.csv) |
| Window hợp lệ và chất lượng | [aligned.csv](../../data/runs/cluster-a-real-003/processed/aligned.csv), [quality.json](../../data/runs/cluster-a-real-003/processed/quality.json) |

Các phần bàn giao G ở cuối là lịch sử trước khi có dữ liệu thật; thông báo chưa kết nối ở phần lịch sử không phải trạng thái Cluster A hiện tại. Các lệnh deploy/apply trong hướng dẫn là tài liệu tham khảo, không phải hành động đã thực hiện ở lượt này; hạ tầng hiện mặc định chỉ đọc.

## Mục lục

- [Hướng dẫn triển khai tham khảo K3s](#source-docs-k3s-deployment-md)
- [Schema metrics, preprocessing và calibration](#source-docs-metrics-calibration-md)
- [Thu dữ liệu qua Windows → Ubuntu → K3s](#source-docs-real-cluster-integration-md)
- [Audit collector và mô hình giai đoạn G](#source-docs-reports-stage-g-audit-md)
- [Bàn giao bộ collector — lịch sử trước dữ liệu thật](#source-docs-reports-stage-g-md)
- [Cài đặt và chạy trên Windows](#source-docs-windows-setup-md)

Chỉ cần file này. Bản gốc trước khi gom nằm trong [ZIP lưu trữ](../archive/reports_before_consolidation_20261109_01.zip), kèm hash để đối chiếu.

<a id="source-docs-k3s-deployment-md"></a>

## Hướng dẫn triển khai tham khảo K3s

### Sample app K3s — khám phá, triển khai, kiểm tra và gỡ

Các lệnh trong tài liệu này dành cho **VM chạy K3s Cluster A đã xác minh**, không phải Ubuntu DevStack host nếu đó là hai máy khác nhau. Chưa chạy các lệnh deploy trên Cluster A trong phiên phát triển. Chỉ namespace/resources mới `cloud-calibration` được đề xuất; không thay đổi hoặc restart OpenStack/K3s đang hoạt động.

#### 1. Khám phá trước

```bash

hostnamectl

ip -br address

ip route

sudo ss -ltnp

sudo k3s kubectl config current-context

sudo k3s kubectl get nodes -o wide

sudo k3s kubectl get services -A -o wide

sudo k3s kubectl get namespaces

sudo k3s kubectl -n kube-system get deployment metrics-server

sudo k3s kubectl top nodes

```

Đối chiếu node với VM/flavor/IP trong `openstack server show` trên Ubuntu. Không dùng IP host DevStack thay IP guest K3s. Nếu `top` không hoạt động, lưu lỗi, chưa cài Prometheus hoặc thay flags TLS của metrics-server. [kubectl top](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_top/) có tín hiệu phục vụ autoscaling, không phải phép đo có độ chính xác như profiler.

Kiểm tra namespace `cloud-calibration` chưa có hoặc đúng là namespace thử nghiệm của bạn. Bắt đầu một pod, CPU limit 250m và RAM limit 96Mi; đây là hạn mức đề xuất cần quan sát thực tế trên VM, không bảo đảm host DevStack không bị ảnh hưởng.

#### 2. Copy source và apply thủ công

Trong thư mục project trên VM, Python 3 có sẵn là đủ. Không cần cài Docker/SDK/Prometheus trên Windows. App chạy từ ConfigMap trên image Python chính thức; không cần tự build image:

```bash

read -rp 'Đường dẫn project trong VM: ' PROJECT_DIR

cd "$PROJECT_DIR"

### Kiểm tra manifest trước, chưa ghi cluster

sudo k3s kubectl apply --dry-run=client -f deploy/k3s/namespace.yaml

sudo k3s kubectl apply --dry-run=client -f deploy/k3s/deployment.yaml

sudo k3s kubectl apply --dry-run=client -f deploy/k3s/service.yaml

sudo k3s kubectl apply -f deploy/k3s/namespace.yaml

sudo k3s kubectl -n cloud-calibration create configmap calibration-work-code --from-file=server.py=src/apps/sample_api/server.py --dry-run=client -o yaml | sudo k3s kubectl apply -f -

sudo k3s kubectl apply -f deploy/k3s/deployment.yaml

sudo k3s kubectl apply -f deploy/k3s/service.yaml

sudo k3s kubectl -n cloud-calibration rollout status deployment/calibration-work --timeout=90s

```

Image `python:3.11-slim` cần pull được trong VM. Sau khi xác minh hoạt động, có thể pin digest image đã kéo để tái lập; không bịa digest. Nếu ImagePullBackOff, không tự cài thêm registry hoặc thay network OpenStack: xem events và cấu hình registry hiện có theo môi trường của bạn.

Pod chạy non-root UID 10001, không mount service-account token, drop capabilities, read-only root filesystem, một replica và Recreate để tránh pod surge khi cập nhật. Code ConfigMap được mount read-only. `/health` dành cho readiness/liveness; failure threshold rộng để không restart vội vì tải ngắn. `/work` mặc định tiêu thụ khoảng 5ms **thread CPU time** và cấp phát 64KiB tạm; CPU throttling có thể làm wall-clock latency dài hơn. Max concurrent work=2, khi bận trả 429 thay vì queue vô hạn. `/metrics` và `/health` không tính vào workload counters.

Các mức work có giới hạn CPU 0–25ms, memory 0–1024KiB/request; cấu hình qua env `WORK_CPU_MS`, `WORK_MEMORY_KIB`, không cho URL tạo tác vụ tùy ý. Một process/pod; `/metrics` có instance UUID để phát hiện restart. Chưa có xác nhận trên ARM/image/VM thực của bạn.

#### 3. Kiểm tra service và endpoint thật

```bash

sudo k3s kubectl -n cloud-calibration get deployment,pods,service -o wide

sudo k3s kubectl -n cloud-calibration get endpointslices -l kubernetes.io/service-name=calibration-work -o wide

sudo k3s kubectl -n cloud-calibration get events --sort-by=.metadata.creationTimestamp

sudo k3s kubectl -n cloud-calibration logs deployment/calibration-work --tail=30

sudo k3s kubectl -n cloud-calibration top pods -l app=calibration-work

sudo k3s kubectl get --raw /apis/metrics.k8s.io/v1beta1/namespaces/cloud-calibration/pods

```

Service mặc định **ClusterIP**, không giả định IP routable từ Windows và không tự cấp NodePort/load balancer. Dùng port-forward loopback trong VM:

```bash

sudo k3s kubectl -n cloud-calibration port-forward --address 127.0.0.1 service/calibration-work 18080:8080

```

Terminal khác:

```bash

curl --fail http://127.0.0.1:18080/health

curl --fail http://127.0.0.1:18080/work

curl --fail http://127.0.0.1:18080/metrics

```

`/metrics` phải có namespace cloud-calibration, pod_name khớp pod thật, instance_id và counters. Windows dùng SSH `-L`/`-J` như `real-cluster-integration.md`; không mở port-forward ra `0.0.0.0` để tiện test.

#### 4. RBAC collector tùy chọn

```bash

sudo k3s kubectl apply --dry-run=client -f deploy/k3s/collector-rbac.yaml

sudo k3s kubectl apply -f deploy/k3s/collector-rbac.yaml

```

Manifest chỉ cấp get/list nodes/node metrics và pods/pod metrics trong namespace. Không cấp exec/proxy/secrets hoặc quyền ghi. Quản trị viên cung cấp kubeconfig/context của identity này bằng cơ chế hiện có; không lưu token vào source/CSV. Port-forward được mở riêng bằng context quản trị đã có, không cấp quyền đó cho collector. Config collector cần expected_node_names và context đã xác minh.

#### 5. Gỡ phần thử nghiệm

Xem tài nguyên trước, chỉ xóa những tài nguyên vừa tạo:

```bash

sudo k3s kubectl -n cloud-calibration get all,configmap

sudo k3s kubectl delete -f deploy/k3s/collector-rbac.yaml --ignore-not-found

sudo k3s kubectl delete -f deploy/k3s/service.yaml -f deploy/k3s/deployment.yaml --ignore-not-found

sudo k3s kubectl -n cloud-calibration delete configmap calibration-work-code --ignore-not-found

### Chỉ nếu namespace đúng là namespace mới dành riêng cho bài test và không có workload khác:

sudo k3s kubectl delete -f deploy/k3s/namespace.yaml --ignore-not-found

```

Ctrl+C dừng port-forward/tunnel/load/collector. Không xóa namespace hệ thống, không reboot VM hoặc restart OpenStack để gỡ app.

<a id="source-docs-metrics-calibration-md"></a>

## Schema metrics, preprocessing và calibration

### Schema, chuẩn hóa và hiệu chỉnh

#### Source và lớp quan sát

`integration.schema.MetricRecord`/CSV schema_version=1 dùng chung cho collector và bản export simulator. **source chỉ nhận `real` hoặc `simulated`**; API A–F vẫn giữ `data_source=simulation` để tương thích, không đổi dữ liệu cũ. `collection_mode` bổ sung live/offline_fixture/simulator_export/local_test. Fixture không được phép ghi source real. App/Locust trên localhost Windows được xếp simulated/local_test để không dùng nhầm làm số đo K3s A.

| layer | Ý nghĩa | cluster_id |
|---|---|---|
| ubuntu_host | /proc của Ubuntu chạy VMware/DevStack | bắt buộc trống |
| openstack_hypervisor | /proc trên OS được xác minh là hypervisor | bắt buộc trống |
| k3s_node | CPU/RAM toàn VM/node K3s, gồm OS/control-plane/pod | A |
| k3s_pod | working-set/CPU của pod đã chọn theo namespace/label | A |
| application | counters và service latency bên trong sample app | A, hoặc cluster mô phỏng khi export |
| client | response/latency/error thấy từ Locust, gồm mạng/tunnel | A nếu thật, trống nếu local smoke |

Nếu Ubuntu host cũng là hypervisor, đó là hai vai trò của **cùng** OS, không phải hai tập tài nguyên để cộng. CPU host không biến thành CPU A. Node K3s được đối chiếu VM OpenStack bằng hostname/IP/ID ở bước khám phá; collector không tự suy diễn topology từ địa chỉ backend.

#### Các trường và đơn vị

Timestamp ISO-8601 có timezone, normalized về UTC khi so sánh. `timestamp_utc` là thời điểm metric gốc/window end; `observed_at_utc` là thời điểm collector, để kiểm tra staleness. `run_id`, `workload_sha256` gắn profile; metadata_json chứa work CPU/memory, cửa sổ, denominator và provenance. Inventory chỉ lưu tên/UID/resource/status, không ghi pod env/annotations hoặc kubeconfig/token.

CPU lưu usage **cores**: 250m=0,25 core, 125000000n=0,125 core. Memory lưu bytes: Ki/Mi/Gi là nhị phân, K/M/G là thập phân. Node % dùng node.status.capacity; pod % dùng tổng container **limits** khi tất cả có limit, không dùng requests làm denominator. Thiếu limit → usage vẫn có, % là null. Không tự điền giá trị thiếu bằng zero.

Metrics API CPU là average trên `window`, memory là working set, không giống Linux MemAvailable. [Kubernetes resource metrics](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/) và [Metrics Server](https://kubernetes-sigs.github.io/metrics-server/) nêu đây là tín hiệu phục vụ autoscaling, không dành cho phép đo monitoring chính xác. Đợt G dùng addon sẵn có để thăm dò nhẹ; trước hiệu chỉnh khoa học cuối cần kiểm tra/chọn kubelet resource metrics hoặc đo app/proc/cgroup phù hợp, không âm thầm xem dữ liệu smoothed là ground truth.

#### Request và latency

App chỉ đếm `/work`; health/metrics không vào workload. Counters trong một instance bảo toàn:

```text

received_total = completed_total + errors_total + inflight

```

completed nghĩa là service work xong thành công, không bảo đảm client đã nhận response. errors gồm 429 khi hết work slot; lỗi transport/client timeout có thể chỉ thấy từ Locust. `inflight` **không phải queue length**; queue_length real để null vì không đo được TCP/worker backlog. Simulator exporter có queue_length rõ ràng theo mô hình, không gán queue thật từ chênh lệch counters một cách suy đoán.

Delta giữa hai lần poll trong cùng instance:

```text

incoming_rps  = delta(received_total) / delta(app monotonic uptime)

completed_rps = delta(completed_total) / delta(app monotonic uptime)

error_rps     = delta(errors_total) / delta(app monotonic uptime)

error_rate    = errors / (completed + errors) * 100  # response outcomes của cửa sổ

```

Đây là denominator theo outcome của app window; khác simulator drop/incoming nếu còn in-flight. Row đầu là warmup, rate/latency null. Counter reset/uptime không tăng → không tạo rate âm hoặc spike; UUID mới → warmup mới.

App giữ tối đa 4096 completion events, sequence tăng đơn điệu; collector yêu cầu events sau cursor. Mean dùng delta latency sum/count của **successful server work**; p95 nearest-rank đúng các successful events trong interval. Nếu events bị mất, mean từ counter còn có thể tính, p95 để null, quality=latency_events_lost; không lấy p95 từ mean hoặc cửa sổ rolling khác thời gian. Sample app một replica; nhiều replica cần URL/cursor riêng theo pod, không scrape service load-balancer rồi coi là một series.

Locust raw CSV có từng response, source/run/profile/phase. Stats/history CSV Locust có percentile histogram xấp xỉ; normalizer tự tính nearest-rank từ requests.csv cho successful client responses. Latency client bao gồm Windows→SSH/network→service; không trộn với server latency. Common client window cuối có thể ngắn hơn 15s, được đánh quality partial; incoming_rps không suy diễn từ completed responses. Dữ liệu này dùng kiểm tra chéo, chưa đưa vào CPU calibration.

#### Preprocessing

```powershell

.\.venv\Scripts\python.exe -m cli.cluster preprocess --input data/imported/first-a/collector/metrics.csv --output data/imported/first-a/processed

.\.venv\Scripts\python.exe -m cli.cluster normalize-locust --input data/runs/first-a/load/client/requests.csv --workload-metadata data/runs/first-a/load/client/workload.json --output data/runs/first-a/client-common.csv

```

Preprocessor chỉ ghép application với **pod cùng tên/namespace/run/source/profile**, không ghép host hoặc node CPU. Dùng resource timestamp gần nhất **không ở tương lai** so với app window; mặc định lệch tối đa 20s, age tại collector từ −2 đến 30s để phát hiện clock skew. Mỗi resource timestamp dùng một lần, tránh lặp cache metrics-server thành nhiều quan sát độc lập. Không interpolation hoặc zero fill. Cửa sổ warmup, mất latency events, thiếu resource, stale hoặc duplicate bị loại và ghi quality.json.

Cần NTP/clock đồng bộ trên Windows/Ubuntu/VM. CPU smoothed window và app interval chỉ được căn chỉnh thăm dò, chưa phải sampling đồng bộ tuyệt đối. Chưa tổng hợp nhiều pod/replica thành capacity cả cluster; mô hình G hiện phục vụ sample app một pod.

#### Calibration

```powershell

.\.venv\Scripts\python.exe -m cli.cluster calibrate --input data/imported/first-a/collector/metrics.csv --output data/imported/first-a/calibration

```

Mặc định chỉ real/live; cần ít nhất ba aligned windows, fit cần ba mức tải khác nhau. Không gộp các work CPU/memory settings hoặc pod limits khác nhau. Report JSON/Markdown lưu số mẫu/loại trừ, input SHA-256, load range, hệ số/intercept/R²:

- CPU cores theo completed req/s; nếu slope dương/R² đủ, ngoại suy theo CPU limit được ghi riêng là **ước tính**, không phải capacity đã chứng minh.

- Working-set MB (bytes/1e6) theo incoming req/s; tương quan mô tả, không suy ra RAM/request trong queue.

- Server latency mean theo incoming req/s; không dùng trực tiếp làm base_latency simulator vì simulator đã cộng service time.

- Capacity: max healthy completed RPS là **observed lower bound** nếu chưa bão hòa. Không có overload → onset null/not observed.

- Ngưỡng overload thăm dò: CPU pod ≥85% limit hoặc outcome error ≥1%; không chứng minh nguyên nhân hay steady state, không tự tăng tải để buộc chạm ngưỡng.

Profile mặc định 1→3→6→1 user, 120s, thường chỉ đủ baseline an toàn và chưa bão hòa. Muốn tăng user/thời lượng, copy profile, giám sát host/VM trước, giữ max 16 users, tổng ≤600s, spike ≤30s, spawn ≤2/s, think-time ≥0,5s. Guard dừng khi có ≥20 response và lỗi >10% hoặc latency mean >1000ms trong ba tick liên tiếp. Thời hạn subprocess kết thúc nếu Locust không tự dừng. Đây không phải cam kết chống treo host; Ctrl+C nếu host CPU/RAM/IO xấu đi.

#### Cấu hình đề xuất A — không tự áp dụng

Chỉ khi đã có ≥6 real/live windows, nhiều mức tải và overload quan sát, có thể yêu cầu tệp **mới**:

```powershell

.\.venv\Scripts\python.exe -m cli.cluster calibrate --input data/imported/first-a/collector/metrics.csv --output data/imported/first-a/calibration-reviewed --write-candidate configs/integration/simulation-A.candidate.json

```

Tool chỉ đề xuất **A.capacity** bằng floor của lower bound đã quan sát, không sửa simulator config gốc. `.provenance.json` cạnh candidate ghi toàn bộ hạn chế/giả định. Các trường CPU/RAM/base latency/queue của A chưa xác định vẫn giữ nguyên và được liệt kê; B/C giữ nguyên từng giá trị, **vẫn là giả định mô phỏng**. Nếu CPU/RAM giả định cũ sẽ chặn capacity đề xuất, tool từ chối và yêu cầu review mapping, không đoán resource mới. Không overwrite candidate đã có hoặc config gốc.

Đây là ứng viên kiểm tra thủ công cho **budget sample pod**, chưa đồng nghĩa năng lực toàn Cluster A. Không dùng ngay để tuyên bố đã calibrated thật, không train lại MARL trong G. Fixture `--allow-simulated` chỉ sinh report demo, không được xuất candidate real.

<a id="source-docs-real-cluster-integration-md"></a>

## Thu dữ liệu qua Windows → Ubuntu → K3s

### G — Thu dữ liệu Cluster A thật, chuẩn bị trên Windows

**Trạng thái bàn giao:** code và offline/localhost tests đã triển khai; chưa có kubeconfig/context/node/network thực trong phiên này nên **chưa xác nhận đã kết nối Cluster A**. Chưa train lại MARL. React và backend Ubuntu không bị sửa; logic simulator/reward/best checkpoint giữ nguyên. Bản đồ thư mục ở `project-layout.md`; kết quả A–F được bảo toàn bằng SHA-256.

#### Kiến trúc nhẹ

`kubectl get ... -o json` và GET Metrics API → collector + app `/metrics` → raw JSONL/CSV chuẩn → preprocessing → calibration report → cấu hình đề xuất A riêng nếu đủ bằng chứng. Không dùng Kubernetes Python SDK hoặc cài Prometheus. K3s thường đóng gói metrics-server, nhưng phải kiểm tra bản cài của bạn; [K3s packaged components](https://docs.k3s.io/installation/packaged-components). Metrics API chỉ có CPU/RAM cơ bản, không cung cấp request/latency; [Kubernetes resource metrics](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/).

App đếm request thật và timestamp service, collector dùng delta counters. Node metrics là toàn bộ VM/node K3s; pod metrics là tài nguyên ứng dụng theo namespace/label. Linux /proc ở Ubuntu DevStack là host/hypervisor, không phải Cluster A. Chi tiết đơn vị, latency và chất lượng ở `metrics-calibration.md`.

#### 1. Windows PowerShell — có thể chạy ngay

```powershell

Set-Location 'D:\HocTap\hk7\VMWare\cloud_marl_simulator_start'

### Dùng venv hiện tại; nếu tạo mới:

py -3.11 -m venv .venv

.\.venv\Scripts\python.exe -m pip install -r requirements/integration.txt

### Tests G không cần PyTorch; toàn bộ tests cần requirements.txt

.\.venv\Scripts\python.exe -m pytest -q tests/integration

.\.venv\Scripts\python.exe -m cli.cluster_smoke --output data/demo/my-stage-g

```

Collector/app/preprocess/calibration chỉ dùng thư viện chuẩn; Locust và YAML parser là gói tùy chọn cho load test/tests. Không cần cài kubectl trên Windows nếu collector chạy trong VM K3s. Offline pipeline dùng fixture có nhãn simulated, không tạo calibrated config thật:

```powershell

.\.venv\Scripts\python.exe -m cli.cluster collect --fixture tests/fixtures/cluster_a.json --samples 8 --output data/demo/offline-collector

.\.venv\Scripts\python.exe -m cli.cluster preprocess --input data/demo/offline-collector/metrics.csv --allow-simulated --output data/demo/offline-processed

.\.venv\Scripts\python.exe -m cli.cluster calibrate --input data/demo/offline-collector/metrics.csv --allow-simulated --output data/demo/offline-calibration

.\.venv\Scripts\python.exe -m cli.cluster export-simulation --input results/evaluation --output data/demo/simulation-common.csv

```

Output phải mới/trống. `--allow-simulated` chỉ cho phát triển/kiểm thử, report ghi fixture demonstration và không cho xuất candidate calibrated thật.

#### 2. Ubuntu OpenStack — khám phá, không thay đổi dịch vụ

Vào môi trường OpenStack CLI đã có bằng openrc/cloud config hiện tại. Không in password/token, không cài lại DevStack:

```bash

hostnamectl

ip -br address

free -h

openstack server list

openstack hypervisor list

read -rp 'VM K3s ID đã xác minh: ' K3S_VM_ID

openstack server show "$K3S_VM_ID" -f json

read -rp 'Hypervisor ID chứa VM: ' HYPERVISOR_ID

openstack hypervisor show "$HYPERVISOR_ID" -f json

```

Đối chiếu addresses/flavor/hypervisor của VM với hostname/IP/node trong VM K3s. `vcpus_used`/`memory_mb_used` OpenStack có thể là **allocation**, không phải CPU busy hoặc working-set của guest. Không biến các trường đó thành utilization Cluster A.

Trong thư mục project đã copy sang Ubuntu, dùng Python sẵn có; không cài PyTorch/Locust ở host nếu không cần:

```bash

read -rp 'Đường dẫn project trên Ubuntu: ' PROJECT_DIR

cd "$PROJECT_DIR"

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p data/runs/first-a

python3 -m cli.cluster host --layer ubuntu_host --run-id first-a --samples 14 --interval 15 --output data/runs/first-a/ubuntu-host.csv

```

Nếu hệ điều hành đó chính là hypervisor DevStack, có thể chọn `--layer openstack_hypervisor` **thay** `ubuntu_host`, ghi rõ chúng là cùng máy; không cộng hai mẫu thành hai tài nguyên độc lập. Sampler để cluster_id trống và không được đưa vào fit CPU pod.

Backend `/api/system`, `/api/openstack/overview` chưa có source/schema trong project. Có thể chụp raw để thiết kế adapter sau, không suy diễn trường:

```bash

read -rp 'Base URL backend đang chạy: ' BACKEND_URL

mkdir -p data/unmapped

curl --fail --max-time 5 "$BACKEND_URL/api/system" -o data/unmapped/ubuntu-system.json

curl --fail --max-time 5 "$BACKEND_URL/api/openstack/overview" -o data/unmapped/openstack-overview.json

```

Không restart Nova/Neutron/libvirt/K3s hoặc đổi cấu hình OpenStack trong các lệnh này.

#### 3. VM K3s Cluster A — khám phá trước khi deploy

Theo `k3s-deployment.md` để kiểm tra node/service/network và triển khai sample app. Các lệnh sau chỉ đọc:

```bash

hostnamectl

ip -br address

ip route

sudo k3s kubectl config current-context

sudo k3s kubectl get nodes -o wide

sudo k3s kubectl get services -A -o wide

sudo k3s kubectl -n kube-system get deployment metrics-server

sudo k3s kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes

```

Nếu Metrics API thiếu/quyền bị từ chối, ghi nhận lỗi; không tự bật addon hoặc bỏ TLS verification. Collector vẫn ghi inventory/status và để CPU/RAM thiếu là null. Chưa đủ dữ liệu thì calibration từ chối hoặc loại cửa sổ.

Sau khi pod Ready, terminal thứ nhất trong VM giữ port-forward loopback:

```bash

sudo k3s kubectl -n cloud-calibration port-forward --address 127.0.0.1 service/calibration-work 18080:8080

```

Terminal thứ hai, từ project:

```bash

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

export CAL_CONTEXT="$(sudo k3s kubectl config current-context)"

export CAL_NODES="$(sudo k3s kubectl get nodes -o jsonpath='{.items[*].metadata.name}')"

export CAL_RUN_ID='first-a'

python3 - <<'PY'

import json, os

from pathlib import Path

p = json.loads(Path('configs/integration/collector.example.json').read_text())

p.update(context=os.environ['CAL_CONTEXT'], expected_node_names=os.environ['CAL_NODES'].split(),

         kubectl_command=['sudo', '-n', 'k3s', 'kubectl'], run_id=os.environ['CAL_RUN_ID'])

Path('configs/integration/collector.local.json').write_text(json.dumps(p, indent=2))

PY

python3 -m cli.cluster collect --config configs/integration/collector.local.json --workload configs/integration/load-safe.json --samples 14 --interval 15 --output data/runs/first-a/collector

```

Đọc/đối chiếu node names với đúng VM trước khi dùng config. Collector yêu cầu danh sách node khớp toàn bộ, kiểm tra app pod/namespace trong inventory, không nhận nhầm host IP là node. `sudo -n` cần quyền đã có, nếu bị từ chối hãy dùng context read-only/kubeconfig phù hợp, không nới quyền tự động. RBAC tối thiểu tùy chọn có trong deploy/k3s.

#### 4. Windows tạo tải nhỏ qua SSH tunnel

Không giả định IP. Nhập địa chỉ đã tìm ở bước trên, kiểm tra SSH. Nếu Windows truy cập VM trực tiếp được, không cần jump host:

```powershell

$UbuntuAddress = Read-Host 'IP Ubuntu đã xác minh'

$UbuntuLogin = Read-Host 'SSH user Ubuntu'

$K3sAddress = Read-Host 'IP VM K3s đã xác minh'

$VmLogin = Read-Host 'SSH user VM K3s'

Test-NetConnection -ComputerName $UbuntuAddress -Port 22

### Terminal riêng, giữ tunnel mở; port-forward trong VM vẫn chạy

ssh -J "$UbuntuLogin@$UbuntuAddress" -N -L 18080:127.0.0.1:18080 "$VmLogin@$K3sAddress"

```

Terminal Windows khác:

```powershell

Invoke-RestMethod 'http://127.0.0.1:18080/health'

Invoke-RestMethod 'http://127.0.0.1:18080/metrics'

.\.venv\Scripts\python.exe -m cli.cluster load --host http://127.0.0.1:18080 --source real --run-id first-a --profile configs/integration/load-safe.json --output data/runs/first-a/load

```

Chỉ dùng source real sau khi xác minh `/metrics` trả đúng namespace/pod và context/node mapping. Nếu tunnel/route/SSH không thông, dừng ở bước khám phá; không tự sửa security groups, bridge hoặc firewall. Load mặc định 120 giây, tối đa 6 user trong spike 15 giây; think time 1–2s, spawn 1/s, app giới hạn 0,25 CPU và 96Mi. Trên host quan sát `free -h`, CPU/IO; Ctrl+C ngay nếu ảnh hưởng DevStack. Client có guard lỗi/latency và hard timeout, nhưng không thay thế giám sát sức khỏe host.

#### 5. Chép CSV và xử lý

```powershell

$RemoteProject = Read-Host 'Đường dẫn project tuyệt đối trong VM'

New-Item -ItemType Directory -Force data/imported/first-a | Out-Null

scp -r -J "$UbuntuLogin@$UbuntuAddress" "${VmLogin}@${K3sAddress}:$RemoteProject/data/runs/first-a/collector" data/imported/first-a/

.\.venv\Scripts\python.exe -m cli.cluster preprocess --input data/imported/first-a/collector/metrics.csv --output data/imported/first-a/processed

.\.venv\Scripts\python.exe -m cli.cluster calibrate --input data/imported/first-a/collector/metrics.csv --output data/imported/first-a/calibration

```

Đợt tải nhỏ có thể chưa bão hòa: report phải ghi capacity lower bound và overload not observed. Không tăng tải tự động để buộc có kết quả. Xem `metrics-calibration.md` về điều kiện candidate A, các giả định giữ nguyên và phần chờ xác minh thật.

<a id="source-docs-reports-stage-g-audit-md"></a>

## Audit collector và mô hình giai đoạn G

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

### G — Kiểm tra đầu vào và kế hoạch

> Layout updated: executable commands use `python -m cli.<command>`; earlier pilot/smoke artifacts are stored in `data/archive/af-experiments.zip`. Original A-F/G delivery reports are retained verbatim in that archive.

#### Đã kiểm tra trước triển khai

Đọc DELIVERY/README, architecture/API/MARL, cấu hình, lõi simulator, môi trường/reward, PPO/training/evaluation, CLI và tests hiện có. Source React và source backend Ubuntu `/api/system`, `/api/openstack/overview` không nằm trong workspace; không đoán schema hai endpoint đó.

- Conservation: mỗi tick và toàn run kiểm tra assigned/received = processed + dropped + waiting, FIFO không âm.

- Throughput chia completed cho thời lượng. Error hiện chỉ là overflow; không phải HTTP/application error thật.

- Latency theo timestamp FIFO, service slot và base latency mô phỏng; p95 nearest-rank theo request, chỉ gồm completed.

- Cuối episode không drain queue; waiting được ghi riêng. Dropped/waiting không có latency, nên có censoring.

- Reward là tổng có trọng số throughput/latency/error/queue/overload/imbalance/switching và terminal queue. Chọn best bằng mean step reward validation, có mốc 0; không chọn bằng testing.

- Chưa thể ánh xạ trực tiếp utilization của service batch sang CPU pod hoặc host. Batch 1 giây, RAM reserve theo service capacity, capacity cố định và lỗi overflow là hạn chế khi hiệu chỉnh.

Không sửa model, reward, optimizer, logic chọn checkpoint hoặc train lại. Hai snapshot SHA-256 trước G (`stage-g-preservation.json`, `stage-g-logic-preservation.json`) được lưu trong ZIP lịch sử. Sau khi người dùng yêu cầu dọn: baseline/legacy được di chuyển, pilot/smoke gom ZIP; checkpoint và kết quả chính giữ nguyên nội dung. Manifest đang dùng: `data/validation/preserved-files.json`.

#### Việc thực hiện trên Windows

- [x] Nhóm mã vào `src/` theo chức năng, tests theo domain; bỏ launcher trùng, cập nhật đường dẫn và lệnh theo bản đồ project.

- [x] Schema chung source real/simulated, lớp host/hypervisor/node/pod/application/client và metadata workload.

- [x] Parser Kubernetes quantities, collector read-only và fixture offline có nhãn simulated.

- [x] App stdlib nhẹ, Deployment/Service/namespace/RBAC tùy chọn; không tự apply manifest.

- [x] Locust bốn pha với giới hạn user/duration và ngắt khi lỗi/latency kéo dài.

- [x] Preprocess giữ provenance, cân chỉnh thời gian và calibration chỉ đề xuất cấu hình A ở tệp mới.

- [x] Tests offline, smoke HTTP local, bảo toàn artifacts, hướng dẫn ba nơi chạy và bàn giao G riêng.

#### Còn phụ thuộc Cluster A thật

Chưa có địa chỉ, kubeconfig/context, node identity, output Metrics API hoặc workload thật trong phiên này. Cần xác minh topology VM/OpenStack, network/port-forward, image pull, quyền read-only, metric freshness và đo baseline tải nhỏ. Không coi fixture hoặc app chạy localhost Windows là số đo Cluster A.

</details>

<a id="source-docs-reports-stage-g-md"></a>

## Bàn giao bộ collector — lịch sử trước dữ liệu thật

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

### Bàn giao G — thu thập dữ liệu K3s và calibration

> Layout updated: executable commands use `python -m cli.<command>`; earlier pilot/smoke artifacts are stored in `data/archive/af-experiments.zip`. Original A-F/G delivery reports are retained verbatim in that archive.

**Chưa xác nhận kết nối Cluster A thật.** Đã triển khai code, offline tests và Locust/HTTP smoke trên Windows localhost. Không deploy lên K3s, không gọi OpenStack, không sửa React, không retrain mô hình đã bàn giao. Fixtures/local smoke ghi source simulated; `connected_to_real_cluster=false`.

#### Điểm bắt đầu

- [Hướng dẫn Windows, Ubuntu OpenStack và VM K3s](#source-docs-real-cluster-integration-md).

- [Khám phá node/network, triển khai và gỡ sample app](#source-docs-k3s-deployment-md).

- [Schema, provenance, preprocessing, calibration và giới hạn](#source-docs-metrics-calibration-md).

- [Bản đồ thư mục và lệnh cũ–mới](../task2/REPORT.md#source-docs-project-layout-md); [mục lục tài liệu](../README.md).

#### Đã triển khai

| Thành phần | Tệp/nhóm tệp |
|---|---|
| Schema real/simulated, timestamps, layers và CSV | src/integration/schema.py |
| CPU/memory quantity, inventory và app delta parser | src/integration/quantities.py, parser.py |
| Collector kubectl GET + app GET, identity checks, raw/error/session logs | src/integration/collector.py |
| Ubuntu host/hypervisor /proc, không gán cluster_id | src/integration/host.py |
| Ghép đúng pod/run/source/profile, freshness/dedup | src/integration/preprocess.py |
| Fit CPU/RAM/latency, capacity lower bound/overload, candidate A có điều kiện | src/integration/calibration.py |
| Adapter simulator/Locust sang schema chung | src/integration/export.py |
| Sample app stdlib health/work/metrics, bounded work/events | src/apps/sample_api/server.py |
| Namespace, một Deployment, ClusterIP Service, RBAC get/list tùy chọn | deploy/k3s/ |
| Locust bốn pha, profile có giới hạn và failure/latency guard | src/loadtests/, configs/integration/ |
| CLI G và smoke offline + localhost | src/cli/cluster.py, cluster_smoke.py |
| Tests và synthetic fixtures | tests/integration/, tests/fixtures/ |

Mã Python nằm trong `src/`; các launcher trùng ở gốc đã bỏ, dùng `python -m cli.<command>`. Tests chia theo simulation/marl/api/integration. Baseline chuyển sang `results/baseline/`; training/evaluation chính giữ đường dẫn. Legacy chuyển sang `legacy/`. Nội dung kết quả chính và source simulator/MARL không đổi; pilot/smoke được lưu trong ZIP lịch sử. Xem [báo cáo dọn thư mục](../task2/REPORT.md#source-docs-reports-cleanup-md).

#### Kết quả kiểm chứng

Trước lần dọn thư mục: **128/128 test đạt (20,38 giây): 81 A–F + 47 G.** Kết quả sau sắp xếp được ghi riêng trong [tests.xml](../../data/validation/tests.xml); xem [báo cáo dọn thư mục](../task2/REPORT.md#source-docs-reports-cleanup-md).

- Tests G bao phủ schema/parser/collector/calibration, HTTP app, profile/guard, YAML/RBAC và CLI. JUnit gốc G: [stage-g-tests.xml](../../data/validation/stage-g-tests.xml).

- [Smoke cuối](../../data/archive/af-experiments.zip): 24 record fixture, 7 aligned windows, 5.400 record simulation được export, **6 request localhost thành công**, Locust tự kết thúc sau profile ngắn.

- Negative smoke xác nhận `--source real` bị từ chối khi target chỉ là app Windows local; không có request workload bị gửi trong lần bị từ chối.

- Calibration fixture ghi `fixture_demonstration_not_real_calibration`, không cho xuất candidate calibrated thật. P95 thiếu events để null, không bịa giá trị.

- API simulation A–F vẫn trả bốn endpoint và OpenAPI hợp lệ qua HTTP thật sau sắp xếp CLI: [API compatibility](../../data/validation/api-af-compatibility.json).

- [Preservation trước khi dọn](../../data/validation/preservation.json) ghi kiểm tra G ban đầu. [Manifest hiện tại](../../data/validation/preserved-files.json) bảo vệ kết quả chính, legacy, source simulator/MARL, config chính và ZIP lịch sử sau di chuyển; không có checkpoint hoặc kết quả chính bị thay nội dung.

Các lần smoke dùng dữ liệu riêng trong `data/demo`, không phải phép đo capacity/latency của Cluster A. Không diễn giải hệ số fixture thành năng lực thật.

#### Lệnh chạy lại trên Windows

```powershell

Set-Location 'D:\HocTap\hk7\VMWare\cloud_marl_simulator_start'

.\.venv\Scripts\python.exe -m pip install -r requirements/integration.txt

.\.venv\Scripts\python.exe -m pytest -q tests/integration

.\.venv\Scripts\python.exe -m cli.cluster_smoke --output data/demo/my-next-smoke

.\.venv\Scripts\python.exe -m cli.package --output dist/cloud_marl.zip

```

Collector/preprocess/calibration/app dùng standard library. Locust 2.47.0 đã được cài và chạy trong `.venv`; phiên này vẫn dùng system site packages, nên không coi đó là venv cô lập sạch. Full suite A–F cần các dependency trong requirements.txt. Không cần cài Kubernetes/Docker/Prometheus/SDK trên Windows.

#### Các lệnh phải chạy trong VM K3s để xác minh thật

Khám phá **trước**, không giả định IP/context:

```bash

hostnamectl

ip -br address

sudo k3s kubectl config current-context

sudo k3s kubectl get nodes -o wide

sudo k3s kubectl get services -A -o wide

sudo k3s kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes

```

Sau khi đối chiếu đúng VM A và đọc hướng dẫn deploy:

```bash

sudo k3s kubectl apply -f deploy/k3s/namespace.yaml

sudo k3s kubectl -n cloud-calibration create configmap calibration-work-code --from-file=server.py=src/apps/sample_api/server.py --dry-run=client -o yaml | sudo k3s kubectl apply -f -

sudo k3s kubectl apply -f deploy/k3s/deployment.yaml -f deploy/k3s/service.yaml

sudo k3s kubectl -n cloud-calibration rollout status deployment/calibration-work --timeout=90s

sudo k3s kubectl -n cloud-calibration port-forward --address 127.0.0.1 service/calibration-work 18080:8080

```

Tạo collector.local.json từ context/node thực theo tài liệu, rồi terminal khác:

```bash

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m cli.cluster collect --config configs/integration/collector.local.json --samples 14 --interval 15 --output data/runs/first-a/collector

```

Ubuntu DevStack chỉ cần lệnh khám phá OpenStack và `/proc` sampler riêng, không restart dịch vụ. Windows tạo tải qua SSH tunnel đã kiểm tra, với cùng run ID/profile; xem tài liệu để có đủ lệnh và cách chép CSV.

#### Chờ kiểm chứng trên Cluster A

1. Context/node/VM identity và đường SSH/route thực.

2. Image pull, pod Ready, Service/EndpointSlice và port-forward từ Windows.

3. RBAC/quyền Metrics API, độ tươi timestamp/window và đồng bộ đồng hồ.

4. CPU/RAM/Pod status và request counters/latency khi có tải thật.

5. Ảnh hưởng lên Ubuntu/OpenStack; capacity chỉ là lower bound nếu chưa quan sát overload.

6. Fit chất lượng đủ, mapping budget pod sang simulator A; B/C vẫn giả định mô phỏng.

Candidate nếu đủ điều kiện chỉ ghi **A.capacity vào file mới**, kèm provenance. Không sửa config gốc hoặc tự gán CPU/RAM/latency/queue chưa đo. Mô hình batch 1s và service latency hiện tại có giới hạn khi so với HTTP thật, đã ghi trong [audit](#source-docs-reports-stage-g-audit-md); logic training không đổi trong G.

</details>

<a id="source-docs-windows-setup-md"></a>

## Cài đặt và chạy trên Windows

### Windows PowerShell

Chạy các lệnh từ thư mục gốc project, Python 3.11 trở lên. Bộ kết quả hiện có dùng PyTorch CPU; không cần bật Ubuntu/VMware để xem hoặc chạy tests offline.

#### Cài đặt

```powershell
Set-Location 'D:\HocTap\hk7\VMWare\cloud_marl_simulator_start'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` cài dependency toàn project và đăng ký editable package từ `src/`. Chỉ cần G thì thay lệnh cuối bằng `pip install -r requirements/integration.txt`; file này gồm Locust, pytest, YAML và editable package, không cài PyTorch.

Với `.venv` đã có đầy đủ dependency trước khi sắp xếp, cập nhật đường dẫn một lần, không tải dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
```

Venv hiện tại dùng `system-site-packages`; chưa kiểm chứng cài một venv cô lập mới qua mạng. Không cần activate hoặc thay ExecutionPolicy để dùng các lệnh dưới đây.

#### Tests, API và offline G

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m cli.verify_api
.\.venv\Scripts\python.exe -m cli.cluster_smoke --output data/demo/my-stage-g
.\.venv\Scripts\python.exe -m cli.serve --results results/evaluation --port 8000
```

Giữ terminal API, mở `http://127.0.0.1:8000/docs`. Terminal khác:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/simulation/status'
Invoke-RestMethod 'http://127.0.0.1:8000/api/simulation/comparison?scenario=mixed'
Get-Content results/evaluation/report.md -Encoding UTF8
```

Ctrl+C dừng API. `verify_api` tự khởi động và dừng server kiểm tra. Không có kết nối K3s thật trong offline smoke; đầu ra mang `source=simulated`. Đích smoke phải mới/trống.

Nếu plugin pytest cài toàn máy gây xung đột, tạm dùng `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'` trước khi chạy tests; bỏ bằng `Remove-Item Env:\PYTEST_DISABLE_PLUGIN_AUTOLOAD` sau đó.

#### Khi chủ động chạy thí nghiệm mới

Các lệnh sau tạo kết quả mới và có training; không cần chạy để xem bản bàn giao hoặc thu dữ liệu G:

```powershell
.\.venv\Scripts\python.exe -m cli.baselines --seeds 42 43 44 45 46 --output results/local-baseline
.\.venv\Scripts\python.exe -m cli.train --smoke --output results/local-smoke
.\.venv\Scripts\python.exe -m cli.train --output results/local-training
### Resume đến TỔNG 500 episode
.\.venv\Scripts\python.exe -m cli.train --resume results/local-training/checkpoints/last.pt --episodes 500 --output results/local-training
.\.venv\Scripts\python.exe -m cli.evaluate --checkpoint results/local-training/checkpoints/best.pt --output results/local-evaluation
.\.venv\Scripts\python.exe -m cli.plot --input results/local-evaluation/csv --output results/local-evaluation/figures --scenario mixed
.\.venv\Scripts\python.exe -m cli.package --output dist/cloud_marl.zip
```

Baseline/evaluation/plot có thể thay đầu ra nếu dùng lại cùng đích. Training từ chối ghi đè run đã có nếu không resume; thay hyperparameter cần run mới. Các lệnh CLI cũ ở gốc đã bỏ; xem [bản đồ project](../task2/REPORT.md#source-docs-project-layout-md).

