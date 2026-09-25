"""catalog.json 读写、版本头与归并解析器的后端行为。"""
import json
import os

import conftest  # 复用 ROOT / read_json
import pytest

app_standalone = pytest.importorskip("app_standalone")


@pytest.fixture
def catalog_file(monkeypatch, tmp_path):
    """把 CATALOG_FILE 指向 tmp，真实 catalog.json 在测试里不可达。"""
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(app_standalone, "CATALOG_FILE", str(path))
    return path


def test_load_catalog_without_file_returns_empty_skeleton(catalog_file):
    catalog = app_standalone.load_catalog()
    assert catalog["version"] == 1
    assert [c["key"] for c in catalog["categories"]][:3] == ["board", "cpu", "ram"]
    assert catalog["parts"] == []


def test_catalog_stamp_changes_when_content_changes(catalog_file):
    catalog_file.write_text('{"version": 1, "parts": []}', encoding="utf-8")
    before = app_standalone.catalog_stamp()
    assert before == "%d-%s" % (
        catalog_file.stat().st_size,
        __import__("hashlib").sha256(catalog_file.read_bytes()).hexdigest()[:16],
    )
    catalog_file.write_text('{"version": 1, "parts": [1]}', encoding="utf-8")
    assert app_standalone.catalog_stamp() != before


def test_save_catalog_rejects_stale_stamp(catalog_file):
    app_standalone.save_catalog(app_standalone.load_catalog())
    fresh = app_standalone.catalog_stamp()
    catalog_file.write_text('{"version": 1, "parts": []}', encoding="utf-8")
    with pytest.raises(app_standalone.WriteConflict):
        app_standalone.save_catalog(app_standalone.load_catalog(), fresh)


def test_save_catalog_does_not_touch_data_json(catalog_file, monkeypatch):
    """写知识库绝不能顺手把账本的 .bak 轮转掉 —— 两者是完全独立的两份文件。"""
    data_file = str(catalog_file.parent / "data.json")
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump([{"brand": "微星", "model": "B650m-b", "cost": 1}], f)
    monkeypatch.setattr(app_standalone, "DATA_FILE", data_file)
    app_standalone.save_catalog(app_standalone.load_catalog())
    assert not os.path.exists(data_file + ".bak")
