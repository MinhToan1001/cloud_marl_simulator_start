# Dữ liệu và bằng chứng

| Thư mục | Vai trò |
|---|---|
| `runs/<run-id>/` | Collector/load trên hạ tầng đã xác minh; raw JSONL, CSV và metadata |
| `imported/<run-id>/` | Dữ liệu chép từ VM về để xử lý trên Windows |
| `demo/` | Đầu ra offline/localhost, `source=simulated`; sinh khi chạy, có thể bỏ khi không dùng |
| `validation/` | Báo cáo tests/HTTP và `preserved-files.json` để kiểm tra SHA-256 |
| `archive/af-experiments.zip` | Các pilot, smoke, replay, demo cũ và tài liệu lịch sử đã kiểm tra từng file trước khi gom |

Chưa có dữ liệu Cluster A thật đã được xác nhận. Fixture nằm ở `tests/fixtures/`; kết quả mô phỏng chính ở `results/`.

ZIP lịch sử giữ tên đường dẫn trước khi sắp xếp. Giải nén vào thư mục riêng nếu cần đối chiếu. Không đưa kubeconfig/token/mật khẩu vào repo hoặc gói bàn giao.
