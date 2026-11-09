# Task 3 — Mô hình hóa bài toán và Simulator DAG V2

Đây là báo cáo duy nhất cần đọc cho Task 3: lý thuyết, mô hình toán, simulator, schema CTDE, feasibility, validation, kết quả và cách tái hiện. Các report lẻ trong results đã được lưu trữ.

## Kết quả và trạng thái hiện tại

- B1–B8 đã hoàn thành và được review trước Task 4. Run hiện hành `results/task3/dag_v2_20261109_06`: **236 test toàn bộ đạt** (227 trong run _06; regression A4 cuối gồm kiểm tra freshness pod metrics), **192 episode smoke SIMULATED**, 12 shared trace, 286 feature rows.
- Workflow vẫn chạy trong migration PREPARING/GRACE nếu còn endpoint Ready và dependency hợp lệ. Workflow arrivals/completed/failed/unfinished được theo dõi riêng với service invocations, terminal/SLA không đếm trùng tại fork-join.
- Tổng smoke: 13.568 workflow arrivals = 13.185 completed + 380 failed + 3 unfinished; 49.306 service invocations. Đây là các episode giả lập độc lập.
- Không huấn luyện MAPPO/IPPO, không thay đổi hạ tầng. Cluster A đã xác minh qua snapshot REAL/owner chain/metrics và shadow; A4 DONE, A5 vẫn PENDING. closed_loop_ready=false.
- B/C, WAN, capacity, queues và RAM pressure chưa xác minh thực tế. Profile chỉ low-load, CPU error 60,86%. Task 4 mới làm MAPPO CTDE/training; Task 5 làm các baseline GA/Single-Agent RL/K8s-default-like.

Tái hiện khi cần vào output mới (không phải yêu cầu chạy lại hiện tại):

```powershell
.\.venv-task2\Scripts\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi> --test-python .venv/Scripts/python.exe
```

Từ nay CLI lưu số liệu/metadata ở results; báo cáo đọc ở file này, không sinh thêm Markdown cho mỗi run. Các report thử _01–_04 và report run _05 trong phụ lục là lịch sử, không phải nhiều task cần kiểm tra riêng.

<a id="pre-task4-review"></a>
## Review trước Task 4 — audit, độ nhạy và reward

**Kết quả hiện hành: 236/236 test toàn bộ đạt** — [JUnit đóng A4](../../results/task2/placement_v1_20261109_03_closeout/a4_fresh_20261109_03/validation.xml). Run mới `dag_v2_20261109_06` có 192 episode baseline SIMULATED và JUnit 227 test tại thời điểm tạo run; sau đó 6 test inventory/thu snapshot A4 nâng bộ test tổng lên 233. Không huấn luyện MAPPO hoặc chạy tải thật.

### Audit 232 / 949 file và verifier

Manifest 949 file không phải superset của 232: có 227 file chung, thiếu 5 file `configs/integration/collector.example.json`, `configs/integration/load-safe.json`, `configs/integration/load-smoke.json`, `configs/marl.json`, `configs/simulation.json`; thêm 702 artifact Task 3, 13 artifact Task 2 và 7 source V2. Các expected hash giao nhau không xung đột. Đã tạo union 954 mục, kiểm lại đủ 232 gốc bằng cách đọc bytes và SHA-256, không tính lại expected hash để hợp thức hóa thay đổi.

Phát hiện fallback ZIP cũ có thể che file hiện tại đã sửa. Đã siết: file tồn tại mà sai hash phải FAIL; chỉ Markdown đã chuyển đi, được khai báo trong manifest và khớp bytes ZIP mới được ghi là archived_original. Không dùng archive để bỏ qua config/checkpoint/source. CLI từng chạy nhưng đã đổi cách xuất báo cáo được audit riêng như provenance lịch sử, không gọi là file hiện tại không đổi.

Không có git hoặc bản sao verify_artifacts.py trước các chỉnh sửa lịch sử nên không bịa diff cũ. Có diff chính xác trước/sau lượt review này và diff CLI từ bản ZIP. Tám negative test bảo toàn đều đạt, gồm sửa file sau lần kiểm tra đầu, archive đúng nhưng file hiện tại sai, thiếu config/source, archive không khai báo hoặc bị sửa. Verifier từ chối python -O.

Bộ union hiện có 935 file tại chỗ khớp, 17 Markdown gốc khớp trong archive và **2 source V2 thay đổi có khai báo old/new hash + lý do** (`config.py`, `env.py`). Không tuyên bố 954 file đều không đổi. Audit run _05 yêu cầu source snapshot cũ khớp mọi hash và báo current_runtime_compatible=false; loader model vẫn strict, không cho dùng artifact cũ với code mới. Không có ngoại lệ bỏ qua theo prefix: mọi delta phải đúng danh sách transition tường minh.

[Inventory và union](../../results/task3/review_20261109_01/audit/inventory.json), [diff verifier](../../results/task3/review_20261109_01/audit/verify_artifacts_review.diff), [diff fallback](../../results/task3/review_20261109_01/audit/retained_documents_review.diff), [transition source](../../results/task3/review_20261109_01/audit/declared_v2_transition.json), [audit lịch sử _05](../../results/task3/review_20261109_01/audit/historical_05_strict.json), [audit _06](../../results/task3/review_20261109_01/audit/current_06_strict.json).

### Kiểm tra độ nhạy có kiểm soát

16 tình huống × 3 seed mới 70101/70102/70103 = 48 episode mỗi phiên bản; cùng arrivals, placement và các điều kiện không bị can thiệp. Không tune policy hoặc dùng seed test 30001–30005. Bản trước sửa đạt 36/42 kiểm tra, bản sau đạt 42/42. Matrix đầu tiên có 45 episode cho thấy severe bandwidth không có transfer hoàn tất; giữ nguyên dữ liệu đó và bổ sung moderate-bandwidth để đo delay, không gán latency=0 khi mẫu hoàn tất rỗng.

| Tình huống | Workflow time trung bình tất cả arrivals (s) | Traffic (byte) | Tổng reward interval |
|---|---:|---:|---:|
| base | 0.120000 | 240000.000000 | -5.339804 |
| cpu_high_A | 1.792985 | 40000.000000 | -48.045937 |
| ram_pressure_errors | 1.673076 | 23333.333333 | -66.553072 |
| bandwidth_moderate_AB | 0.210000 | 240000.000000 | -6.767212 |
| payload_large | 0.210000 | 2400000.000000 | -6.810412 |
| migration_slow_keep | 1.781216 | 0.000000 | -48.690795 |
| migration_slow_move | 0.050000 | 0.000000 | -5.116706 |
| migration_fast_keep | 0.050000 | 0.000000 | -5.091706 |
| migration_fast_move | 1.781216 | 0.000000 | -48.715795 |

Workflow time ở bảng dùng (finish hoặc horizon − arrival), gồm cả failed/unfinished; không đánh đồng với E2E completed-only. CPU cao/RAM pressure làm queue/time và SLA xấu hơn; giảm bandwidth vừa phải hoặc tăng RTT tăng transfer; payload ×10 làm traffic/cost tăng. Migration từ A nghẽn sang B rảnh có lợi trong fixture, chiều ngược lại gây hại. Đây là các quan hệ đã kiểm trong điều kiện cố định, không phải luật reward đơn điệu cho mọi trade-off.

Run _05 và _06 giữ nguyên toàn bộ metric vật lý chung ở 192 episode heuristic; thay đổi reward không tự cải thiện hiệu năng một policy chưa học. Không đổi fit/calibration hoặc physics CPU/mạng để ép kết quả. [Đối chiếu](../../results/task3/review_20261109_01/baseline_physical_parity.json).

[Dữ liệu paired trước](../../results/task3/review_20261109_01/sensitivity_before_paired/metrics.csv), [sau](../../results/task3/review_20261109_01/sensitivity_after/metrics.csv), [42 checks](../../results/task3/review_20261109_01/sensitivity_after/checks.json), [JUnit cuối 233 test](../../results/task3/review_20261109_01/validation_final.xml), [baseline mới _06](../../results/task3/dag_v2_20261109_06/smoke/metrics.csv).

### Reward interval-v1 và giới hạn còn lại

Đã sửa hai thiếu sót thực nghiệm: legacy tiếp tục phạt latency cũ ở tick không còn workflow; và thiếu migration cost trực tiếp. Chế độ default mới `interval-v1`; `legacy-cumulative` chỉ giữ để tái hiện chẩn đoán. Code/config fingerprints thay đổi, không load contract/model cũ qua loader hiện tại.

Gọi W là tích phân số workflow unfinished theo thời gian trong khoảng quyết định, N là unfinished đầu khoảng + arrivals mới, T là độ dài khoảng; U là tích phân std CPU/RAM utilization; V là số vi phạm terminal mới; R là số workflow resolved mới. Reward chung = −[w1×(W/max(1,N))/SLA + w2×Δtraffic_cost + w3×ΔU/T + w4×V/max(1,R)] − migration_cost. Latency term là surrogate thời gian workflow tồn tại trong interval, không gọi nó là mean E2E của completed cohort. Failed/censored vẫn có holding time; terminal/SLA không bị tính lại ở tick sau.

Migration_cost = 0,02×số MOVE bắt đầu + 0,01×giây overlap service + 0,1×số MOVE thất bại trong khoảng. Các hệ số là ASSUMPTION chưa hiệu chuẩn, không tối ưu bằng các case này. Counter baseline được chụp trước action để không bỏ sót MOVE bắt đầu ngay tick đó. info trả reward_components và interval_metrics để audit. Không có workflow thì latency term bằng 0; balance nền vẫn có thể có cost hợp lệ.

Fixture cô lập latency: legacy tổng reward −6,9 và còn phạt sau 2s không có request; interval chỉ ghi −0,3 tại thời gian thực sự có workflow. Fixture không có demand và các objective weight bằng 0 để cô lập operation: KEEP = 0, MOVE = −0,025 (0,02 start + 0,01×0,5s overlap); rollback tính failure một lần. Đây là kiểm tra cô lập, không phải tune trọng số benchmark.

Reward vẫn là team reward; chưa triển khai credit assignment riêng cho từng agent, counterfactual advantage hoặc MAPPO. Surrogate chuẩn hóa theo cohort trong từng khoảng và SLA resolved không bảo đảm bất biến khi đổi decision interval hoặc tránh mọi chiến lược khai thác; cần chốt horizon/decision interval, đo reward scaling và ablation ở Task 4. Qua sensitivity không đồng nghĩa bảo đảm hội tụ. A4 đã đóng bằng snapshot chỉ đọc; A5, miền calibration và mapping DAG V2 vẫn là giới hạn thực tế.

### A4 đã hoàn tất sau review

A4 DONE; 11/11 kiểm tra snapshot/shadow đạt. [Readiness A4](../../results/task2/placement_v1_20261109_03_closeout/a4_fresh_20261109_03/analysis_verified/readiness.json). Toàn bộ 236 test đạt tại [JUnit](../../results/task2/placement_v1_20261109_03_closeout/a4_fresh_20261109_03/validation.xml); file validation_final.xml trong review trước ghi 233 test tại thời điểm đó, được giữ nguyên làm lịch sử. A5 chưa được duyệt, chưa tạo profile V2 hoặc huấn luyện MAPPO.

### Cách tái hiện lượt review

```powershell
.\.venv-task2\Scripts\python.exe -X utf8 tools/sensitivity_v2.py --output results/task3/<sensitivity_moi>
.\.venv-task2\Scripts\python.exe -X utf8 docs/task3/verify_artifacts.py dag_v2_20261109_06 --source-transition results/task3/review_20261109_01/audit/declared_v2_transition.json --output results/task3/<audit_moi>.json
```

Run _05 là bằng chứng lịch sử trước review; _06 là baseline môi trường/reward hiện hành. Các kết quả mới đều ghi stage mới, không ghi đè run/dataset/checkpoint cũ.

## Mục lục

- [Kết quả Simulator DAG V2](#results)
- [Mô hình toán học](#math-model)
- [Schema placement-v2](#schema)
- [Khung lý thuyết](#theory)
- [Khả năng đo feature trên hạ tầng thật](#real-feasibility)
- [Kiểm thử và tái hiện](#validation)
- [Trạng thái hoàn thành Task 3](#progress)

<details>
<summary>Mục lục phụ lục kỹ thuật và lịch sử</summary>

- [Run V2 _01 — lỗi lưu numpy, chưa hoàn tất](#source-results-task3-dag-v2-20261109-01-report-md)
- [Run V2 _02 — lỗi alias scenario, chưa hoàn tất](#source-results-task3-dag-v2-20261109-02-report-md)
- [Run V2 _03 — smoke đủ, regression thiếu dependency](#source-results-task3-dag-v2-20261109-03-report-md)
- [Run V2 _04 — 204 test trước bổ sung hồi quy](#source-results-task3-dag-v2-20261109-04-report-md)
- [Run V2 _05 — nghiệm thu cuối, 206 test](#source-results-task3-dag-v2-20261109-05-report-md)

</details>

Chỉ cần file này. Bản gốc trước khi gom nằm trong [ZIP lưu trữ](../archive/reports_before_consolidation_20261109_01.zip), kèm hash để đối chiếu.

<a id="results"></a>

## Kết quả Simulator DAG V2

> Nội dung nền từ lần nghiệm thu trước review. Trạng thái, reward `interval-v1`, run _06 và bộ test 236 hiện hành xem [review trước Task 4](#pre-task4-review). Các link _05 được giữ làm bằng chứng lịch sử; code/model loader không bỏ qua fingerprint.

### Báo cáo Simulator DAG V2

Run nghiệm thu: [results/task3/dag_v2_20261109_05](#source-results-task3-dag-v2-20261109-05-report-md). **206 test đạt**, gồm 156 test cũ và 50 test V2; 192 episode smoke SIMULATED. Không huấn luyện MAPPO/IPPO trong Task 3, không đổi hạ tầng. Task 2B chưa nghiệm thu đầy đủ do A4 PENDING (Cluster A đã phục hồi theo kiểm tra thủ công, còn thiếu bằng chứng); phần B hoàn thành độc lập với A4/A5.

| Heuristic | E2E mean (s) | Traffic (bytes) | CPU std | RAM std | SLA resolved |
|---|---:|---:|---:|---:|---:|
| round_robin | 0.155548 | 907517.637524 | 0.077804 | 0.043405 | 0.110382 |
| least_loaded | 0.155328 | 829204.444723 | 0.077635 | 0.048195 | 0.111551 |
| random_valid | 0.148128 | 585833.596456 | 0.077669 | 0.069636 | 0.110956 |
| traffic_aware | 0.143273 | 580864.000000 | 0.077476 | 0.061574 | 0.111641 |

Đây là trung bình 48 episode mỗi heuristic, không phải benchmark MARL hoặc Task 5. Traffic-aware giảm traffic và E2E trong bộ smoke này nhưng RAM std cao hơn RR; SLA không đồng thời tốt hơn mọi cách. Không chọn policy bằng các seed test cũ. Seed mới 51001/51002; bốn DAG × sáu scenario dùng trace chung theo scenario/seed. Latency completed-only phải đọc cùng thất bại/censored.

Tổng trên mọi episode: arrivals=13568, completed=13185, failed=380, unfinished=3, service invocations=49306. Đây là tổng qua nhiều episode độc lập, không phải duy nhất request thật. Mỗi episode có assert workflow conservation và invocation conservation; ledger liên kết bằng workflow ID. Không lấy tổng service calls làm tổng workflow arrivals.

#### Chức năng đã xây dựng

Module mới `src/placement_v2/` có DAG tối đa năm service, bốn template, initial topo placement, DAG đến giữa episode khi cụm có tải nền, PENDING/retry/REJECTED, KEEP/PLACE/MOVE, prepare/grace/drain/rollback/cooldown. Workflow admission dùng endpoint Ready và dependency, không chặn theo nhãn MIGRATING. Mỗi request tới ứng dụng không tạo deployment mới.

Event engine tính fork-join max, CPU fair sharing/queue, RAM pressure, link RTT/bandwidth contention và bytes/cost riêng. Capacity randomization có điều kiện initial feasibility; capacity fault log tường minh; overload giữ workload/raw ratios, chặn inbound, cho MOVE out. Workflow timeout/failure/cancellation và censored được báo riêng. Schema actor/critic cố định, masks/padding và loader fingerprint là giao kèo Task 4.

#### Bằng chứng và giới hạn

[Mô hình toán](#math-model), [schema](#schema), [feasibility từng feature](SCHEMA_REAL_FEASIBILITY.csv), [validation](#validation), [lý thuyết](#theory). Mỗi run có config/provenance, profile hash, code/schema fingerprints, runtime freeze, trace seed/hash, decision/event/resource/workflow/invocation ledgers và JUnit. Hai runtime được ghi riêng: .venv-task2 chạy smoke; .venv đầy đủ chạy regression API/Gymnasium. Không cài thêm hay thay package của runtime cũ.

Profile V1 chỉ low-load, CPU relative MAE 60,86%, saturation null. Các node DAG dùng cùng Sample API class proxy, không phải calibration từng microservice. Capacity/queues/RAM pressure/WAN/prices/background/readiness đều là giả định chưa xác minh miền. Một DAG/episode, không optional branch/async/retry, không state-transfer traffic của migration, không OOM-kill, không MARL training. Reward tham chiếu dùng thống kê lũy kế; actor network summary có giới hạn ghi trong schema. Hạ tầng thật chưa sẵn sàng closed loop.

Task 4: MAPPO CTDE, masked rollout/loss, reward và credit assignment, huấn luyện ngoài mẫu; nhiều DAG đồng thời nếu cần phải mở rộng entity schema. Task 5: GA, Single-Agent RL, K8s-default-like với cùng constraints và budget. A5 là lượt thử tải riêng sau phê duyệt đúng file/mức RPS.

<a id="math-model"></a>

## Mô hình toán học

> Nội dung nền từ lần nghiệm thu trước review. Trạng thái, reward `interval-v1`, run _06 và bộ test 236 hiện hành xem [review trước Task 4](#pre-task4-review). Các link _05 được giữ làm bằng chứng lịch sử; code/model loader không bỏ qua fingerprint.

### Mô hình toán học DAG V2

Simulator V2 mô hình hóa bốn mục tiêu của đề cương trong một episode có tối đa một controlled DAG và năm service. Toàn bộ episode là **SIMULATED**. Calibration V1 cung cấp tham chiếu low-load cho lớp Sample API; các service DAG, B/C và WAN là giả định. Đây là mô hình chuẩn bị Task 4, chưa phải controller đa cụm thực.

#### Hạ tầng, ứng dụng và trạng thái

Tập cụm C={A,B,C}, năng lực CPU Kᶜ (core), RAM Mᶜ (byte), ma trận RTT Lᵢⱼ (giây), bandwidth Bᵢⱼ (byte/giây), trọng số pᵢⱼ (cost/byte). `None` biểu diễn bandwidth vô hạn, 0 biểu diễn link không truyền dữ liệu. Các liên kết có hướng và chia sẻ bandwidth giữa các transfer hoạt động; jitter thay đổi từng giây theo trace đã lưu.

Workflow G=(V,E) là DAG. Service v có CPU request rᵥ, RAM request mᵥ, CPU limit ℓᵥ, work wᵥ (core-second), concurrency và queue limit. Cạnh (u,v) có dᵤᵥ byte/workflow. Mỗi service được gọi đúng một lần cho một workflow ID; mặc định gọi đồng bộ fork-join, mọi nhánh bắt buộc, lỗi là fail-fast và hủy công việc còn lại. Không retry request ngầm. DAG/cạnh/payload/concurrency/deadline cấu hình được qua dataclass; lựa chọn async/optional-branch chưa thuộc phiên bản này.

Một service có một placement logic xᵥᶜ∈{0,1}, Σᶜxᵥᶜ=1 khi READY. Trong migration, nguồn và đích cùng giữ reservation trong khoảng overlap, nhưng workflow pin endpoint theo ID và mỗi invocation chỉ chạy một lần. Trước triển khai có UNPLACED/PENDING, sau từ chối có REJECTED nên chưa có placement; không ép ràng buộc một cụm cho những trạng thái này.

Initial PLACE theo topo: service chỉ được đặt sau khi tiền nhiệm đã có endpoint được cấp phát. Node chưa đủ điều kiện tự vào PENDING, mỗi decision tick thử lại; quá số retry hoặc timeout thì REJECTED. PREPARING là chờ endpoint Ready. MOVE là PREPARING → GRACE → READY, có cooldown, giới hạn số lần và số MOVE đồng thời; rollback khi target mất khả dụng hoặc prepare timeout. Release nguồn đợi hết grace và mọi invocation/transfer đã pin nguồn được drain.

**Lifecycle không phải readiness phục vụ.** PREPARING khi migration vẫn có thể phục vụ ở nguồn; GRACE phục vụ được ở đích hoặc nguồn còn Ready. Endpoint hợp lệ cần Ready, cluster available và service health. Workflow được admit khi mọi service bắt buộc có endpoint phục vụ hợp lệ; node chỉ enqueue sau khi dependency của chính workflow đó hoàn tất. Thiếu endpoint thì queue có giới hạn/timeout; REJECTED gây lỗi workflow. Mất cluster khi job đã pin đang chạy gây fail-fast, không chuyển job sang endpoint khác hoặc đếm invocation mới ngầm. Transfer đợi link/endpoint phục hồi hoặc workflow timeout.

#### Thời gian và contention

Không tranh chấp:

`finish(v) = max_{u∈pred(v)}[finish(u)+transfer(u,v)] + queue(v) + service(v)`.

`transfer(u,v) = L[x(u),x(v)] + d_uv/B[x(u),x(v)]` khi khác cụm; cùng cụm dùng `intra_latency_s`, không tính WAN bytes. `service(v)=w_v/min(ℓ_v,CPU khả dụng)` trong trường hợp đơn lẻ. E2E = thời điểm mọi sink hoàn tất − workflow arrival. Max ở join tránh cộng hai nhánh song song. Thời gian triển khai/readiness chỉ đi vào E2E nếu request phải đợi deployment; không cộng lại latency đã tích phân.

Trong simulator, background CPU là tải ngoại sinh tuyệt đối. CPU còn lại `max(0,K-background)` được water-fill công bằng cho các invocation hoạt động, có CPU limit mỗi endpoint chia đều cho invocation cùng service. Concurrency giới hạn queue riêng theo endpoint. Giảm capacity hoặc tăng background khiến service rate giảm, queue có thể tăng. Hàm critical_path chỉ là cận không tranh chấp; event engine tính thời gian thực với queue và chia sẻ CPU.

Transfer chịu RTT một lần, sau đó mỗi flow được B/n khi có n flow cùng link có hướng đã hết RTT. Byte được ghi đúng khi truyền, có thể chỉ truyền một phần trước lỗi/timeout. Hai chiều link độc lập. CPU và mạng có thể chạy song song; không cộng thời gian transfer hai lần. Đặt chung có thể chậm hơn vì contention, tách cụm có thể chậm hơn vì WAN; không có placement luôn thắng.

#### Capacity và fault

Capacity CPU/RAM ban đầu được lấy mẫu có điều kiện trong dải cấu hình sao cho capacity ≥ reservation ban đầu. Nếu không có giá trị khả thi trong dải thì báo lỗi. Initial placement do trace ngoài cung cấp cũng phải qua kiểm tra. Khởi tạo trống trong smoke nên điều kiện này tự thỏa mãn. Capacity không giảm ngẫu nhiên giữa episode; thay đổi chỉ qua fault tường minh có log. Background CPU có thể đổi từng giây, RAM nền cố định theo trace.

Reservation tính cả endpoint đích đang chuẩn bị và nguồn chưa release. Usage CPU, RAM, reservation và tỷ lệ thô không bị ép về 1. Khi vượt capacity, gắn OVERLOADED, chặn PLACE/MOVE vào cụm, vẫn cho MOVE ra. Không xóa workload hay eviction ngầm. CPU throttling là chia sẻ phần CPU khả dụng kể trên. RAM usage = background RAM + baseline các endpoint + memory mỗi invocation queued/running. Khi RAM/capacity=q>1, progress rate chia cho `1 + ram_latency_penalty×(q−1)`; invocation mới bắt đầu có xác suất lỗi `min(.95, ram_error_coefficient×(q−1))`, dùng hash(seed, workflow ID, service) cố định thay vì RNG phụ thuộc policy. `oom_risk` là cờ q>1; không mô phỏng OOM-kill/eviction. Việc RAM lỗi/timeout gây lỗi workflow và được ghi loại lỗi. Các cơ chế này là ASSUMPTION, chưa kiểm chứng thực tế.

#### Bốn mục tiêu và conservation

- E2E mean/p95/p99 chỉ trên workflow completed; kèm số mẫu, failed và unfinished, không diễn giải 0 khi không có mẫu là latency tốt.
- Traffic bytes = tổng byte thực đã qua cạnh khác cụm; traffic cost = Σ bytesᵢⱼ×pᵢⱼ. Byte từng link được giữ riêng. Migration state transfer không nằm trong DAG traffic; chi phí đó chưa được hiệu chuẩn.
- Load balance = trung bình theo thời gian của std giữa ba cụm, riêng CPU usage/capacity và RAM usage/capacity; báo thêm reservation std, utilization, waste = trung bình phần dương reservation ratio − usage ratio từng tài nguyên/cụm/thời điểm. Usage có cả background; waste là proxy toàn cụm, không phải chi phí cấp phát thực tế.
- SLA violation resolved = (workflow completed quá deadline + workflow failed)/(completed+failed). Failure là violation ngay cả khi chưa quá deadline; unfinished báo censored riêng, không tự kết luận thành công. Khi resolved=0 trả 0 kèm denominator/censored; phải đọc cùng counts.

Reward gốc trước review (`legacy-cumulative`, không còn default): `−[w1×E2E_mean/SLA + w2×Δtraffic_cost + w3×(CPU_std+RAM_std) + w4×SLA_resolved]`. Đây là reward tham chiếu dùng thống kê lũy kế, chưa tune/train; Task 4 cần xem lại credit assignment và reward theo interval. Constraint admission dựa trên `max(reservation,usage)+request≤capacity` và cluster availability, cooldown/readiness; các đề xuất đồng thời được kiểm tra lại theo topo để không vượt headroom.

Tại mọi thời điểm: `workflow_arrivals = completed + failed + unfinished`. `service_invocations = invocation_completed + invocation_failed + invocation_cancelled + invocation_live`. Workflow ID liên kết mọi nhánh, transfer, binding và terminal; completion/failure/SLA mỗi workflow chỉ ghi một lần. Không cộng số service calls để suy tổng workflow request. Error theo loại, queue delay, migration count/latency/downtime, OVERLOADED/PENDING ticks và thời gian được báo riêng.

#### Tham số và miền hiệu lực

`smoke/parameter_provenance.json` liệt kê mọi tham số config với nguồn. Profile V1 low-load REAL đầu vào → CALIBRATED latency 5,5397 ms, RAM 32.378.368 byte; dùng latency×giả định 1 core làm work proxy 0,0055397 core-second và RAM baseline/reference request 32 MiB làm tỷ lệ proxy là ASSUMPTION khi nhân rộng DAG/B/C. Không dùng CPU OLS để suy causal demand hay capacity. CPU validation error 60,86%, saturation null, không có profile mới khi A5 chưa duyệt. Capacity budgets, queues, SLA, payload cạnh, dải randomization, giá mạng và RAM pressure đều chưa xác minh. Tất cả giá trị lấy mẫu lưu trong trace, không lấy seed test cũ để tune.

<a id="schema"></a>

## Schema placement-v2

> Nội dung nền từ lần nghiệm thu trước review. Trạng thái, reward `interval-v1`, run _06 và bộ test 236 hiện hành xem [review trước Task 4](#pre-task4-review). Các link _05 được giữ làm bằng chứng lịch sử; code/model loader không bỏ qua fingerprint.

### Schema placement-v2 cho CTDE

Actor observation có 91 feature/agent, padding thành `(5,91)`. Critic global_state có 195 feature. Dtype float32, số chiều cố định. Schema hash: `934b66378221ba50d0347189ca27a135d2139127257da8e5d4edd329f6de45dd`. Danh sách thứ tự, đơn vị và scale đầy đủ ở [schema_v2.json](schema_v2.json), bản đóng băng theo run ở [contract](../../results/task3/dag_v2_20261109_05/smoke/contract.json).

Actor nhận trạng thái của chính service, CPU/RAM/capacity/availability toàn cụm, vị trí và payload cạnh của các láng giềng trực tiếp, tóm tắt outgoing RTT/bandwidth. Không nhận chi tiết service không kề. Critic nhận mọi service, mọi cạnh DAG, tài nguyên cụm, ma trận mạng đầy đủ và giá. Global_state không tiết lộ trace tương lai; summary queue chưa đủ mô tả mọi job residual nên chưa khẳng định vector là Markov đầy đủ. Task 4 cần đánh giá lịch sử quan sát/recurrent policy nếu cần.

Service role ID là vị trí topo ổn định trong DAG, chuẩn hóa `(index+1)/5`, kèm metadata tên service trong DAG. ID không chứng minh vai trò cùng ngữ nghĩa giữa các DAG; Task 4 có thể mở rộng role embedding có version.

`agent_mask` bật khi DAG đã đến và slot là service thật. `decision_mask` chỉ bật cho UNPLACED/READY có ít nhất một action hợp lệ. Padding trước/sau MAX_SERVICES và PREPARING/MIGRATING/PENDING/REJECTED không vào inference, action_mask toàn false, không gửi action. `obs` padding là zero. Busy service thật vẫn giữ reservation theo lifecycle; padding không có workload hay reservation.

Action 0 KEEP, 1/2/3 là chọn A/B/C. UNPLACED chọn cụm được log PLACE; đã đặt chọn cụm khác được log MOVE. UNPLACED không có KEEP; cụm hiện tại không có MOVE-to-self. READY có KEEP ngay cả khi cluster đang lỗi để giữ placement logic; việc request có được phục vụ vẫn xét endpoint. Không có WAIT. Đề xuất đồng thời được kiểm tra lại theo topo; bị xung đột headroom thì log proposal_rejected, không tự thực hiện action không hợp lệ.

Feature ratio không clip ở 1: overload vẫn hiện tỷ lệ >1 và có flag. Scale là phép chia ghi trong manifest, không tự suy từ batch. Bandwidth None mã hóa 0, critic có unlimited flag; actor chỉ có bandwidth trung bình, 0 không phân biệt zero/unlimited nên adapter live phải bỏ/proxy có missing semantics trước khi huấn luyện. Đây là giới hạn được công khai của tóm tắt actor.

`save_contract/load_contract` lưu schema đầy đủ và hash từng source file V2; version/hash/code mismatch đều bị từ chối. Artifact V1 (checkpoint torch placement-v1) không được V2 nạp; loader V1 cũng từ chối contract V2. Contract chỉ là giao kèo metadata chuẩn bị Task 4, chưa có V2 trained policy. Đổi schema/code phải version lại và tạo run mới; không dùng checkpoint V1 với action/obs V2.

<a id="theory"></a>

## Khung lý thuyết

### Khung lý thuyết cho Chương 1

Đây là khung ý chính cho đồ án; **tài liệu tham khảo cần người dùng bổ sung và kiểm chứng** trước khi đưa vào luận văn. Các liên kết dưới đây là nguồn gốc để đối chiếu, không phải danh mục trích dẫn hoàn thiện.

#### Multi-Cluster và Hybrid Cloud

Multi-cluster phân bố ứng dụng trên các cụm có CPU/RAM, điều kiện mạng và miền lỗi khác nhau. Hybrid cloud phối hợp môi trường riêng và công cộng; mô hình ba cụm trong đồ án chưa chứng minh triển khai hybrid thật. Cluster A là nguồn calibration lịch sử, B/C hiện giả lập. Quyết định placement liên cụm ở tầng trên không đồng nhất với quyết định pod→node trong một cluster.

#### Microservice DAG và phương pháp lập lịch

DAG biểu diễn dependency và payload trên cạnh. Fork-join có nhánh song song nên critical path lấy max tại join; workflow ID là đơn vị SLA, khác số lần gọi service. Scheduler phải cân đối contention CPU/RAM với chi phí truyền cạnh qua WAN.

Heuristic như RR/LL dễ giải thích nhưng chưa tối ưu đồng thời mọi mục tiêu. Heuristic traffic-aware khai thác cạnh dữ liệu và mạng. GA có thể tìm placement tổ hợp với nhiều mục tiêu nhưng chịu chi phí tính và cần định nghĩa feasibility. Kubernetes scheduler thực hiện filtering rồi scoring node cho pod trong cluster; không tự coi nó là optimizer DAG đa cụm. [Kubernetes Scheduling Framework và scheduler](https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/) mô tả phạm vi này. Baseline K8s-default-like, GA và Single-Agent RL để Task 5 với cùng trace, constraints và budget.

#### RL, MDP, MARL và CTDE

MDP gồm state, action, transition, reward và horizon/discount; actor nhìn observation cục bộ nên bài toán triển khai phù hợp hơn với cooperative partially observed setting. Mỗi service là một agent; action KEEP/PLACE/MOVE chịu mask. Environment nắm global state, tải nền và fault; actor chỉ thấy service, cụm và DAG lân cận. Padding không phải agent thật, không vào inference/loss. Mask invalid action phải áp dụng cả sampling và training sau này.

CTDE cho phép critic dùng global state khi train, actor dùng observation cục bộ khi thực thi. V1 actor đã thấy toàn bộ 123 feature nên chưa thực hiện tách này. V2 tạo schema riêng; có global_state không tự chứng minh thuật toán CTDE đã được huấn luyện hoặc hội tụ.

#### MAPPO, MADDPG, QMIX và hướng chọn

MAPPO dùng PPO cho hợp tác đa tác tử, có centralized value function, phù hợp action categorical bị mask và chia sẻ tham số theo service/role ID. Đây là ứng viên Task 4, cần thí nghiệm riêng về credit assignment, reward, entropy, clipping và generalization; chưa có kết quả MAPPO trong Task 3. Nguồn đối chiếu: [The Surprising Effectiveness of PPO in Cooperative Multi-Agent Games](https://arxiv.org/abs/2103.01955).

MADDPG dùng critic có thông tin tác tử khác để giảm vấn đề môi trường không dừng; thường thuận tiện với action liên tục. Với bốn action rời rạc của đồ án cần nêu rõ biến thể/ước lượng action trước khi so sánh. Nguồn: [Multi-Agent Actor-Critic for Mixed Cooperative-Competitive Environments](https://arxiv.org/abs/1706.02275).

QMIX phân rã giá trị chung bằng mixing đơn điệu để lựa chọn action phân tán; ràng buộc đơn điệu có thể giới hạn biểu diễn tương tác placement/traffic. Nguồn: [QMIX: Monotonic Value Function Factorisation for Deep Multi-Agent Reinforcement Learning](https://arxiv.org/abs/1803.11485).

Lý do ưu tiên MAPPO là sự phù hợp sơ bộ với cooperative categorical policies và schema actor/critic tách biệt, không phải kết luận nó tốt hơn các thuật toán khác. Task 4 mới huấn luyện CTDE/reward đa mục tiêu; nhiều controlled DAG đồng thời cần mở rộng entity/ID/credit assignment. Task 5 mới nghiệm thu baseline và đánh giá ngoài mẫu.

<a id="real-feasibility"></a>

## Khả năng đo feature trên hạ tầng thật — A4 DONE

A4 đã hoàn thành bằng snapshot REAL mới và shadow replay 3 checkpoint. [Task 2 — bằng chứng đóng A4](../task2/REPORT.md#a4-fresh-completion) ghi đầy đủ clock, UID/owner chain, inventory, metrics và checklist. User stack thực thi collector, agent phân tích và xác minh file; không sửa key/sudoers/OpenStack.

[SCHEMA_REAL_FEASIBILITY.csv](SCHEMA_REAL_FEASIBILITY.csv) vẫn có 286 hàng theo đúng thứ tự/schema, thêm live_feature_status/evidence/capture UTC. A4 DONE không có nghĩa mọi feature V2 đã có thật: cluster A CPU/RAM/capacity/reservation được đo/suy ra; B/C chưa có thật; các resource/endpoint/identity API được xác minh trên calibration prototype, chưa ánh xạ agent/role của DAG V2.

Queue và workflow latency/error/invocations cần app telemetry/tracing; DAG bytes cần config/tracing; RTT/bandwidth WAN cần phép đo riêng; giá, cooldown, lifecycle và role cần config/controller. Không gán missing=0 như đã đo; phương án bỏ khỏi actor live hoặc dùng proxy/missing semantics vẫn ghi từng feature. Overload/OOM risk là proxy suy ra usage/capacity, không phải phép đo OOM-kill thực.

Shadow dùng snapshot tại capture clock và masks chỉ KEEP; executed_operations=0, application_requests_sent=0. closed_loop_ready=false vì B/C và telemetry/mapping DAG còn thiếu. Số liệu đã lưu không tự còn fresh cho quyết định thực thi sau khi quá hạn; đây là bằng chứng khả năng lấy observation và schema parity.

<a id="validation"></a>

## Kiểm thử và tái hiện

> Nội dung nền từ lần nghiệm thu trước review. Trạng thái, reward `interval-v1`, run _06 và bộ test 236 hiện hành xem [review trước Task 4](#pre-task4-review). Các link _05 được giữ làm bằng chứng lịch sử; code/model loader không bỏ qua fingerprint.

### Kiểm thử và tái hiện V2

**206/206 test đạt**, không failure/error. Bộ cũ 156 test vẫn đạt; thêm 50 test V2. [JUnit](../../results/task3/dag_v2_20261109_05/validation/junit.xml), [console](../../results/task3/dag_v2_20261109_05/validation/pytest.txt), [status/fingerprints](../../results/task3/dag_v2_20261109_05/validation/status.json). Kiểm thử cũ có fixture huấn luyện nhỏ trong tmp như trước; không huấn luyện lại checkpoint nghiên cứu.

| Nhóm nghiệm thu | Bằng chứng trong tests/test_placement_v2.py |
|---|---|
| DAG, cycle, >5, DAG thứ hai | test_dag_bounds_cycle_and_second |
| Critical path 4 template × 3 mạng | test_exact_critical_path; test_diamond_max_not_sum |
| CPU/queue monotonic, hai chiều placement | test_contention_and_both_placement_directions; test_queue_monotonic_same_trace |
| Bandwidth và contention link | test_bandwidth_delay_and_shared_link |
| Initial topo, mid-episode, masks/padding | test_mid_episode_topo_and_masks; test_all_unplaced_keep_blocked_and_masks_reject_bad_input |
| PENDING/retry/REJECTED | test_pending_reject_and_workflow_error |
| PREPARING/GRACE vẫn phục vụ | test_migration_serves_during_preparing_and_grace; test_target_serves_after_source_loss_in_grace |
| Rollback và drain nguồn | test_migration_rollback_and_source_ready; test_slow_workflow_source_held_until_drained |
| Faults, overload, giữ raw usage, MOVE out | test_fault_conservation; test_ram_capacity_fault_no_silent_eviction |
| Workflow ID, dependency, terminal/SLA một lần | test_dependency_and_completed_workflow_no_duplicate; test_join_fail_once_and_timeout_censor |
| Queue/RAM/link-zero/cluster lỗi | test_explicit_failures_or_censored |
| Trace reproducibility/ranges/capacity feasibility | test_randomization_range_seed_and_capacity_initial; test_initial_capacity_conditional_sample; test_scenario_trace |
| Actor local/critic global, V1↔V2/fingerprint rejection | test_schema_local_global_loader |
| Policy reproducibility | test_policies_reproducible_shared_trace |

Smoke 192 episode SIMULATED dùng 12 shared traces, bốn policy chỉ là heuristics. Conservation được assert sau từng event advance và cuối episode; tổng arrivals/completed/failed/unfinished và số invocation tách riêng. Không tune theo test 30001–30005. [Thông số/freeze/trace](../../results/task3/dag_v2_20261109_05/smoke/metadata.json).

Tái hiện từ workspace (thư mục output phải mới):

```powershell

.\.venv-task2\Scripts\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi> --test-python .venv/Scripts/python.exe

.\.venv-task2\Scripts\python.exe -X utf8 -m pytest tests/test_placement_v2.py -q

```

CLI `--stage smoke`/`--stage validate` tạo stage tương ứng mới và từ chối ghi đè; `all` từ chối root đã tồn tại. `.venv-task2` không có FastAPI/PyYAML/Gymnasium nên regression dùng `.venv` đã có đủ package. Run _01 dừng ở serialize numpy, _02 dừng vì alias ramp-up, _03 smoke đủ nhưng collection regression thiếu package; giữ nguyên các bằng chứng thử đó. Run dag_v2_20261109_05 là kết quả cần đọc. Nếu sửa code/schema, tạo run mới để fingerprint khớp, không sửa artifacts đã đóng băng.

Đối chiếu độc lập sau chạy: [artifact_verification.json](../../results/task3/dag_v2_20261109_05/artifact_verification.json) xác nhận conservation theo từng workflow ID, invocation không trùng (workflow,service), shared trace/hash và 286 feature rows; 232 file được bảo vệ không đổi. Bốn lệnh CLI ghi lại trong [nonoverwrite_checks.json](../../results/task3/dag_v2_20261109_05/nonoverwrite_checks.json) đều từ chối stage đã tồn tại. Run _04 đạt 204 test trước hai test hồi quy cuối; run _05 mới là bản nghiệm thu 206 test.

<a id="progress"></a>

## Trạng thái hoàn thành Task 3

> Nội dung nền từ lần nghiệm thu trước review. Trạng thái, reward `interval-v1`, run _06 và bộ test 236 hiện hành xem [review trước Task 4](#pre-task4-review). Các link _05 được giữ làm bằng chứng lịch sử; code/model loader không bỏ qua fingerprint.

### Trạng thái tiếp tục Task 3

Task 3 Simulator DAG V2 đã hoàn thành B1–B8 ở run `dag_v2_20261109_05`: 206 test (156 cũ + 50 V2), 192 episode SIMULATED. Đây là kết quả đã chạy trước cập nhật phục hồi Cluster A, không phải số liệu đo lại.

| Hạng mục | Trạng thái và bằng chứng |
|---|---|
| B1–B2 Lý thuyết/mô hình | THEORY_NOTES.md, MATH_MODEL.md |
| B3 Simulator DAG | Initial topo/KEEP/MOVE, lifecycle/readiness, fork-join workflow ID, contention và overload; module placement_v2 |
| B4 Schema CTDE | 5 slot, 91 actor feature, 195 critic feature; masks/padding, loader fingerprint |
| B5 Feasibility | 286 hàng feature; cập nhật A4 PENDING sau phục hồi thủ công, chưa coi feature đã đo sống |
| B6 Kiểm thử | 206/206 đạt ở lần nghiệm thu trước; validation/JUnit giữ nguyên |
| B7 Smoke | 4 DAG × 6 scenario × 2 seed × 4 heuristic = 192; không kết luận MARL |
| B8 Báo cáo/tái hiện | SIMULATOR_V2_REPORT.md, VALIDATION_REPORT.md, README và run REPORT |

Cập nhật mới chỉ thay tài liệu/provenance và template báo cáo. Không sửa source simulator, schema, checkpoint, dataset hay run đã đóng băng; không cần chạy lại smoke/training vì hạ tầng phục hồi không thay đổi physics giả lập. Đối chiếu artifact được lưu riêng cùng bản ghi phục hồi thủ công. [H6](../task2/REPORT.md#closeout) ghi checklist A4 còn thiếu; việc này không chặn Task 3. A5 vẫn PENDING, calibration low-load/CPU error 60,86% giữ nguyên. Task 4/5 chưa được triển khai trong lượt cập nhật này.

<a id="source-results-task3-dag-v2-20261109-01-report-md"></a>

## Run V2 _01 — lỗi lưu numpy, chưa hoàn tất

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

Run thử chưa hoàn tất: lỗi serialize numpy.bool_ khi lưu ledger. Không dùng run này làm bằng chứng kết quả. Đã sửa writer ở CLI, tiếp tục bằng run mới _02; giữ nguyên stage cũ.

</details>

<a id="source-results-task3-dag-v2-20261109-02-report-md"></a>

## Run V2 _02 — lỗi alias scenario, chưa hoàn tất

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

Run thử chưa hoàn tất: tên ramp-up chưa ánh xạ sang ramp_up của V1. Không dùng làm kết quả nghiệm thu. Đã bổ sung alias và kiểm thử sáu scenario; tiếp tục run _03, giữ nguyên stage cũ.

</details>

<a id="source-results-task3-dag-v2-20261109-03-report-md"></a>

## Run V2 _03 — smoke đủ, regression thiếu dependency

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

### Kết quả smoke Task 3 — SIMULATED

192 episode: 4 mẫu DAG × 6 scenario × 2 seed mới × 4 heuristic; không huấn luyện MARL. Một trace dùng chung mọi DAG/policy trong cùng scenario và seed.

| Heuristic | E2E mean (s) | Traffic (bytes) | CPU std | RAM std | SLA resolved |
|---|---:|---:|---:|---:|---:|
| round_robin | 0.155548 | 907517.637524 | 0.077804 | 0.043405 | 0.110382 |
| least_loaded | 0.155328 | 829204.444723 | 0.077635 | 0.048195 | 0.111551 |
| random_valid | 0.148128 | 585833.596456 | 0.077669 | 0.069636 | 0.110956 |
| traffic_aware | 0.143273 | 580864.000000 | 0.077476 | 0.061574 | 0.111641 |

Trung bình đều giữa episode; p95/p99 trong metrics.csv là phân vị workflow từng episode, không phải phân vị gộp. Completed-only latency phải đọc cùng failed/unfinished. Heuristic random-valid có thể migration nhiều hơn và chịu overlap; không kết luận thứ hạng MARL.

Calibration V1 low-load, CPU sai số 60,86%; capacity tối đa chưa biết. B/C, WAN, workload DAG, capacity và faults đều giả lập. Không dùng seed test 30001–30005 để tune.

Bằng chứng: [metrics](../../results/task3/dag_v2_20261109_03/smoke/metrics.csv), [aggregate](../../results/task3/dag_v2_20261109_03/smoke/aggregate.csv), [contract](../../results/task3/dag_v2_20261109_03/smoke/contract.json), [metadata](../../results/task3/dag_v2_20261109_03/smoke/metadata.json), [traces](../../results/task3/dag_v2_20261109_03/smoke/traces), [ledgers](../../results/task3/dag_v2_20261109_03/smoke/episodes), [JUnit](../../results/task3/dag_v2_20261109_03/validation/junit.xml).

Tái hiện: `.venv-task2\Scripts\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi>`; stage từ chối ghi đè.

</details>

<a id="source-results-task3-dag-v2-20261109-04-report-md"></a>

## Run V2 _04 — 204 test trước bổ sung hồi quy

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

### Kết quả smoke Task 3 — SIMULATED

192 episode: 4 mẫu DAG × 6 scenario × 2 seed mới × 4 heuristic; không huấn luyện MARL. Một trace dùng chung mọi DAG/policy trong cùng scenario và seed.

| Heuristic | E2E mean (s) | Traffic (bytes) | CPU std | RAM std | SLA resolved |
|---|---:|---:|---:|---:|---:|
| round_robin | 0.155548 | 907517.637524 | 0.077804 | 0.043405 | 0.110382 |
| least_loaded | 0.155328 | 829204.444723 | 0.077635 | 0.048195 | 0.111551 |
| random_valid | 0.148128 | 585833.596456 | 0.077669 | 0.069636 | 0.110956 |
| traffic_aware | 0.143273 | 580864.000000 | 0.077476 | 0.061574 | 0.111641 |

Trung bình đều giữa episode; p95/p99 trong metrics.csv là phân vị workflow từng episode, không phải phân vị gộp. Completed-only latency phải đọc cùng failed/unfinished. Heuristic random-valid có thể migration nhiều hơn và chịu overlap; không kết luận thứ hạng MARL.

Calibration V1 low-load, CPU sai số 60,86%; capacity tối đa chưa biết. B/C, WAN, workload DAG, capacity và faults đều giả lập. Không dùng seed test 30001–30005 để tune.

Bằng chứng: [metrics](../../results/task3/dag_v2_20261109_04/smoke/metrics.csv), [aggregate](../../results/task3/dag_v2_20261109_04/smoke/aggregate.csv), [contract](../../results/task3/dag_v2_20261109_04/smoke/contract.json), [metadata](../../results/task3/dag_v2_20261109_04/smoke/metadata.json), [traces](../../results/task3/dag_v2_20261109_04/smoke/traces), [ledgers](../../results/task3/dag_v2_20261109_04/smoke/episodes), [JUnit](../../results/task3/dag_v2_20261109_04/validation/junit.xml).

Tái hiện: `.venv-task2\Scripts\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi>`; stage từ chối ghi đè.

</details>

<a id="source-results-task3-dag-v2-20261109-05-report-md"></a>

## Run V2 _05 — nghiệm thu cuối, 206 test

<details>
<summary>Mở nội dung lưu theo lịch sử — trạng thái hiện tại nằm ở đầu báo cáo</summary>

### Kết quả smoke Task 3 — SIMULATED

192 episode: 4 mẫu DAG × 6 scenario × 2 seed mới × 4 heuristic; không huấn luyện MARL. Một trace dùng chung mọi DAG/policy trong cùng scenario và seed.

| Heuristic | E2E mean (s) | Traffic (bytes) | CPU std | RAM std | SLA resolved |
|---|---:|---:|---:|---:|---:|
| round_robin | 0.155548 | 907517.637524 | 0.077804 | 0.043405 | 0.110382 |
| least_loaded | 0.155328 | 829204.444723 | 0.077635 | 0.048195 | 0.111551 |
| random_valid | 0.148128 | 585833.596456 | 0.077669 | 0.069636 | 0.110956 |
| traffic_aware | 0.143273 | 580864.000000 | 0.077476 | 0.061574 | 0.111641 |

Trung bình đều giữa episode; p95/p99 trong metrics.csv là phân vị workflow từng episode, không phải phân vị gộp. Completed-only latency phải đọc cùng failed/unfinished. Heuristic random-valid có thể migration nhiều hơn và chịu overlap; không kết luận thứ hạng MARL.

Calibration V1 low-load, CPU sai số 60,86%; capacity tối đa chưa biết. B/C, WAN, workload DAG, capacity và faults đều giả lập. Không dùng seed test 30001–30005 để tune.

Bằng chứng: [metrics](../../results/task3/dag_v2_20261109_05/smoke/metrics.csv), [aggregate](../../results/task3/dag_v2_20261109_05/smoke/aggregate.csv), [contract](../../results/task3/dag_v2_20261109_05/smoke/contract.json), [metadata](../../results/task3/dag_v2_20261109_05/smoke/metadata.json), [traces](../../results/task3/dag_v2_20261109_05/smoke/traces), [ledgers](../../results/task3/dag_v2_20261109_05/smoke/episodes), [JUnit](../../results/task3/dag_v2_20261109_05/validation/junit.xml).

Tái hiện: `.venv-task2\Scripts\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi> --test-python .venv/Scripts/python.exe`; stage từ chối ghi đè.

Kiểm thử cuối: **206/206 đạt** (156 cũ + 50 V2). [Đối chiếu artifact](../../results/task3/dag_v2_20261109_05/artifact_verification.json): 192 episode, 12 trace, 286 feature rows; 232 file gốc giữ nguyên hash. Tổng workflow arrivals 13.568 = completed 13.185 + failed 380 + unfinished 3; service invocations 49.306, không cộng vào arrivals. [Kiểm tra từ chối ghi đè](../../results/task3/dag_v2_20261109_05/nonoverwrite_checks.json).

</details>

