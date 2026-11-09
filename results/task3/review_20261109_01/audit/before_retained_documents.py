"""Đối chiếu hash lịch sử khi bản Markdown gốc đã được lưu trong ZIP."""
import hashlib
from pathlib import Path
from zipfile import ZipFile


def retained_location(root,name,expected):
    root=Path(root); path=root/name
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected: return 'current_file'
    # Chỉ báo cáo cũ và CLI đổi cách xuất báo cáo được phép đối chiếu từ archive.
    if Path(name).suffix.lower()!='.md' and name!='src/cli/task3.py': return None
    archive=root/'docs/archive/reports_before_consolidation_20261109_01.zip'
    if not archive.exists(): return None
    with ZipFile(archive) as z:
        try: content=z.read(name)
        except KeyError: return None
    return 'archived_original' if hashlib.sha256(content).hexdigest()==expected else None
