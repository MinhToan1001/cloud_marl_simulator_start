# Cloud MARL Simulator

Mỗi task chỉ có **một báo cáo tổng hợp**. Mở đúng task trong bảng dưới để đọc toàn bộ nội dung, kết quả, giới hạn và cách chạy lại.

| Task | Báo cáo duy nhất | Trạng thái |
|---|---|---|
| Task 1 — Thu thập dữ liệu Cluster A | [Mở báo cáo Task 1](docs/task1/REPORT.md) | Có dữ liệu REAL lịch sử; calibration chỉ low-load |
| Task 2 — Calibration và Placement V1 | [Mở báo cáo Task 2](docs/task2/REPORT.md) | A1/A2/A3/A4/A6 DONE; A5 PENDING |
| Task 3 — Simulator DAG V2 | [Mở báo cáo Task 3](docs/task3/REPORT.md) | Hoàn thành B1–B8; 236 test đạt, 192 episode SIMULATED; review độ nhạy 42/42 đạt |

Cluster A đã xác minh bằng snapshot REAL và shadow replay; A4 DONE. A5 chưa duyệt và chưa thử tải; closed-loop vẫn chưa sẵn sàng vì B/C và telemetry ứng dụng còn thiếu. Không thay SSH key, sudoers hoặc OpenStack.

## Thư mục

| Thư mục | Dùng để làm gì |
|---|---|
| `docs/task1`, `docs/task2`, `docs/task3` | Mỗi thư mục có một `REPORT.md` để đọc; JSON/CSV/script đi kèm là bằng chứng kỹ thuật |
| `results` | CSV, checkpoint, trace, log, metadata, JUnit và biểu đồ; không cần tìm báo cáo ở đây |
| `data` | Dataset gốc, collector và client records |
| `src`, `tests` | Mã nguồn và kiểm thử |
| `configs`, `deploy/k3s` | Cấu hình và manifest tham khảo; không tự áp dụng vào hạ tầng |
| `docs/archive` | Một ZIP giữ nguyên bản các báo cáo cũ và manifest hash |

Báo cáo cũ đã được gom vào [ZIP lưu trữ](docs/archive/reports_before_consolidation_20261109_01.zip), không làm mất nội dung hay số liệu. Dataset, checkpoint, source physics/schema và số liệu kết quả giữ nguyên. Trạng thái lịch sử trong ZIP không thay thế trạng thái ở đầu báo cáo hiện hành.

## Lệnh thường dùng

```powershell
# Kiểm thử toàn bộ bằng runtime đã có
.\.venv\Scripts\python.exe -m pytest -q

# Mở API đọc kết quả
.\.venv\Scripts\python.exe -m cli.serve

# Chỉ khi cần tái hiện V2, dùng output mới
.\.venv-task2\Scripts\python.exe -X utf8 -m cli.task3 --output results/task3/<run_moi> --test-python .venv/Scripts/python.exe
```

Quy tắc tài liệu từ nay: cập nhật ngay báo cáo duy nhất của task; không tạo H/report/addendum Markdown riêng hoặc bản báo cáo lặp trong results. Log/snapshot/JUnit/CSV/JSON vẫn lưu theo run để kiểm chứng. Không chạy lại training, thử tải hoặc thay đổi hạ tầng chỉ để cập nhật báo cáo.
