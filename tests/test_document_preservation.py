"""Negative tests bảo toàn: ZIP không được che file hiện tại bị sửa."""
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile
import pytest

spec=importlib.util.spec_from_file_location('retained_documents_test',Path(__file__).parents[1]/'tools/retained_documents.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def fixture(root,name='docs/old.md',declared=True,payload=b'original'):
    sha=hashlib.sha256(b'original').hexdigest(); archive=root/'docs/archive'; archive.mkdir(parents=True)
    (archive/'consolidation_manifest.json').write_text(json.dumps({'archived_hashes':{name:sha} if declared else {}}))
    with ZipFile(archive/'reports_before_consolidation_20261109_01.zip','w') as z:z.writestr(name,payload)
    return sha


def test_modified_existing_document_must_fail_even_with_good_archive(tmp_path):
    sha=fixture(tmp_path); p=tmp_path/'docs/old.md'; p.write_bytes(b'changed')
    assert module.retained_location(tmp_path,'docs/old.md',sha) is None


def test_missing_declared_document_is_archived_provenance(tmp_path):
    sha=fixture(tmp_path)
    assert module.retained_location(tmp_path,'docs/old.md',sha)=='archived_original'


@pytest.mark.parametrize('name',['configs/simulation.json','src/placement/env.py','src/cli/task3.py'])
def test_non_document_never_gets_preservation_fallback(tmp_path,name):
    sha=fixture(tmp_path,name)
    assert module.retained_location(tmp_path,name,sha) is None


@pytest.mark.parametrize('declared,payload',[(False,b'original'),(True,b'tampered')])
def test_undeclared_or_corrupt_archive_rejected(tmp_path,declared,payload):
    sha=fixture(tmp_path,declared=declared,payload=payload)
    assert module.retained_location(tmp_path,'docs/old.md',sha) is None


def test_existing_original_is_rehashed(tmp_path):
    sha=fixture(tmp_path); p=tmp_path/'docs/old.md'; p.write_bytes(b'original')
    assert module.retained_location(tmp_path,'docs/old.md',sha)=='current_file'
    p.write_bytes(b'edited after first check')
    assert module.retained_location(tmp_path,'docs/old.md',sha) is None
