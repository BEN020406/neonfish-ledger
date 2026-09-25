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


FIXTURE = json.loads(
    (os.path.join(conftest.ROOT, "tests", "fixtures", "resolve_cases.json"))
    and open(os.path.join(conftest.ROOT, "tests", "fixtures", "resolve_cases.json"), encoding="utf-8").read()
)


def _fixture_catalog():
    return {
        "version": 1,
        "categories": [dict(c) for c in app_standalone.CATALOG_CATEGORIES],
        "brands": [dict(b) for b in FIXTURE["brands"]],
        "parts": [dict(p) for p in FIXTURE["parts"]],
    }


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["model"] or "empty" for c in FIXTURE["cases"]])
def test_resolve_part_matches_shared_cases(case):
    got = app_standalone.resolve_part(_fixture_catalog(), case["brand"], case["model"])
    if case["name"] is None:
        assert got is None, "未命中的必须返回 None，不能猜一个最近的"
    else:
        assert got is not None
        assert got["name"] == case["name"]
        assert got["cat"] == case["cat"]
        assert got["brand"] == app_standalone.resolve_brand(_fixture_catalog(), case["brand"])


def test_resolve_brand_falls_back_to_given_text():
    catalog = _fixture_catalog()
    assert app_standalone.resolve_brand(catalog, "inter") == "英特尔"
    assert app_standalone.resolve_brand(catalog, "  ") == ""
    assert app_standalone.resolve_brand(catalog, "朗孜") == "朗孜"


def test_norm_key_ignores_case_spaces_and_fullwidth_parens():
    assert app_standalone.norm_key("H610M-E") == app_standalone.norm_key("h610m-e ")
    assert app_standalone.norm_key("B650M（迫击炮）") == app_standalone.norm_key("b650m(迫击炮)")
