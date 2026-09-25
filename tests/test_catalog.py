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


@pytest.fixture
def api_catalog(monkeypatch, api, tmp_path):
    """复用 conftest.api 起的服务，只把 CATALOG_FILE 换进同一次往返的临时目录。"""
    call, read_ledger, data_path = api
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"version": 1,
                                "categories": [dict(c) for c in app_standalone.CATALOG_CATEGORIES],
                                # brands 必须带上：凯侠→铠侠 这类归一靠的就是这张表，
                                # 播种成空列表等于把被测契约抹掉了。
                                "brands": [dict(b) for b in FIXTURE["brands"]],
                                "parts": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app_standalone, "CATALOG_FILE", str(path))
    return call, path


def test_get_catalog_returns_body_and_stamp_header(api_catalog):
    call, path = api_catalog
    status, body, headers = call("GET", "/api/catalog", with_headers=True)
    assert status == 200
    assert [c["key"] for c in body["categories"]][:2] == ["board", "cpu"]
    assert headers["X-Catalog-Stamp"] == "%d-%s" % (
        path.stat().st_size,
        __import__("hashlib").sha256(path.read_bytes()).hexdigest()[:16],
    )


def test_get_catalog_without_file_is_200_and_empty_stamp(monkeypatch, api, tmp_path):
    call, _, _ = api
    monkeypatch.setattr(app_standalone, "CATALOG_FILE", str(tmp_path / "nope.json"))
    status, body, headers = call("GET", "/api/catalog", with_headers=True)
    assert status == 200
    assert body["parts"] == []
    assert headers["X-Catalog-Stamp"] == ""


def _upsert(call, payload, if_match=None):
    # with_headers 是必须的：下面几条测试按 (status, body, headers) 三元组解包
    return call("POST", "/api/catalog/upsert", payload, if_match=if_match, with_headers=True)


def test_upsert_creates_part_and_brand(api_catalog):
    call, path = api_catalog
    status, body, _ = _upsert(call, {"brand": "微星", "name": "B850M GAMING PLUS", "cat": "board"})
    assert status == 200 and body["ok"] is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [p["name"] for p in saved["parts"]] == ["B850M GAMING PLUS"]
    assert saved["parts"][0]["brand"] == "微星"
    assert {b["canonical"] for b in saved["brands"]} >= {"微星"}


def test_upsert_applies_brand_alias_and_appends_new_aliases(api_catalog):
    call, path = api_catalog
    _upsert(call, {"brand": "凯侠", "name": "VD10 1TB", "cat": "ssd", "aliases": ["vd10 1t"]})
    saved = json.loads(path.read_text(encoding="utf-8"))
    part = saved["parts"][0]
    assert part["brand"] == "铠侠"            # 走 brands 别名归一后落库
    assert part["aliases"] == ["vd10 1t"]


def test_upsert_rename_pushes_old_name_into_aliases(api_catalog):
    call, path = api_catalog
    _upsert(call, {"brand": "微星", "name": "B650M-B", "cat": "board"})
    status, _, _ = _upsert(call, {"brand": "微星", "old_name": "B650M-B", "name": "B650M BOMBER", "cat": "board"})
    assert status == 200
    part = json.loads(path.read_text(encoding="utf-8"))["parts"][0]
    assert part["name"] == "B650M BOMBER"
    assert "B650M-B" in part["aliases"]      # 历史记录还能归并过来


def test_upsert_rejects_unknown_cat(api_catalog):
    call, path = api_catalog
    status, body, _ = _upsert(call, {"brand": "微星", "name": "X", "cat": "sound"})
    assert status == 400 and "cats" in body
    assert json.loads(path.read_text(encoding="utf-8"))["parts"] == []   # 一条都不写


def test_upsert_requires_brand_and_name(api_catalog):
    call, _ = api_catalog
    assert _upsert(call, {"brand": "", "name": "B", "cat": "board"})[0] == 400
    assert _upsert(call, {"brand": "微星", "name": "  ", "cat": "board"})[0] == 400


def test_upsert_honours_if_match_and_allows_absent(api_catalog):
    call, path = api_catalog
    _upsert(call, {"brand": "微星", "name": "A", "cat": "board"})
    stale = "0-deadbeef"
    status, body, _ = _upsert(call, {"brand": "微星", "name": "B", "cat": "board"}, if_match=stale)
    assert status == 409 and body["ok"] is False
    fresh = app_standalone.catalog_stamp()
    status, _, _ = _upsert(call, {"brand": "微星", "name": "B", "cat": "board"}, if_match=fresh)
    assert status == 200                      # If-Match 缺席放行（导入页与 agent 不知道版本）


def test_upsert_accepts_bad_json_as_400(api_catalog):
    call, _ = api_catalog
    status, _, _ = call("POST", "/api/catalog/upsert", raw_body=b"{not json", with_headers=True)
    assert status == 400
