# Buoi 1 - Mo phong dieu phoi 3 cluster (khong can AWS)

## Chay tren Windows + VS Code

1. Giai nen thu muc.
2. Mo thu muc trong VS Code.
3. Mo Terminal va chay:

```powershell
py -3 simulate.py
```

Neu may dung lenh `python`, hay chay `python simulate.py`.

Khong can cai package, khong can bat VMware, khong can AWS.

## 3 file ket qua trong `output/`

- `workload.csv`: so request vao he thong moi giay.
- `cluster_metrics.csv`: request duoc phan bo, xu ly, bi roi, hang doi, chi so CPU/RAM/latency *gia lap* theo giay va cluster.
- `summary.csv`: so sanh Round Robin va Least Loaded tren **cung** luong request (seed=42).

## Quy uoc

- A/B/C deu la simulator Python khi chay script, **khong** thay the K3s A da tao tren OpenStack.
- Nang luc gia lap A=60, B=90, C=120 request/s, phai hieu chinh bang du lieu that o giai doan sau.
- CPU, RAM, `latency_proxy_ms` chi la cong thuc mo phong; khong phai so do thuc te, khong phai p95.
- Spike tao luong request lon hon tong nang luc -> co queue va drop.
- Day la baseline, chua co MARL. Sau khi xac nhan simulator dung, moi thiet ke moi truong MARL.

## Buoi tiep theo

1. Them cau hinh va cac kieu workload.
2. Thu metrics tu K3s Cluster A that de hieu chinh mo hinh.
3. Xay `MARL Env` voi cung giao dien `step/reset`.
4. So sanh MARL, Round Robin, Least Loaded voi cung traffic test chua dung de train.
