"""Đối chiếu hash lịch sử khi bản Markdown gốc đã được lưu trong ZIP."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def retained_location(root,name,expected):
    root=Path(root); path=root/name
    if not path.resolve().is_relative_to(root.resolve()): return None
    # File còn tồn tại nhưng sai hash phải fail; không được che bằng bản ZIP cũ.
    if path.exists():
        return 'current_file' if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected else None
    if Path(name).suffix.lower()!='.md': return None
    return 'archived_original' if archived_content_matches(root,name,expected) else None


def archived_content_matches(root,name,expected):
    """Kiểm tra provenance lưu trữ, KHÔNG chứng minh file hiện tại không đổi."""
    root=Path(root)
    manifest=root/'docs/archive/consolidation_manifest.json'
    if not manifest.exists(): return False
    declared=json.loads(manifest.read_text(encoding='utf-8'))['archived_hashes']
    if declared.get(name)!=expected: return False
    archive=root/'docs/archive/reports_before_consolidation_20261109_01.zip'
    if not archive.exists(): return False
    with ZipFile(archive) as z:
        try: content=z.read(name)
        except KeyError: return False
    return hashlib.sha256(content).hexdigest()==expected
