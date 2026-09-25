"""人工第二遍的补丁校验器。

这里最关键的是「校验器必须真能失败」：每条规则都配一个正好踩线的坏补丁，
否则校验形同装饰。所有用例都打在 tmp 目录的 catalog 副本上，不碰真库。
"""
import copy
import hashlib
import json
import os

import pytest

import apply_catalog_patch as ap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CATALOG = {
    "version": 1,
    "categories": [{"key": "board", "name": "主板"}, {"key": "bundle", "name": "板U套装"}],
    "brands": [{"canonical": "微星", "aliases": []}, {"canonical": "英特尔", "aliases": []}],
    "parts": [
        {"cat": "unknown", "brand": "微星", "name": "B650M-B", "aliases": []},
        {"cat": "unknown", "brand": "微星", "name": "PRO B650M-B 主板", "aliases": []},
        {"cat": "unknown", "brand": "微星", "name": "B650m-b(7500F)", "aliases": []},
        {"cat": "unknown", "brand": "英特尔", "name": "12400F", "aliases": []},
    ],
}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ap, "CATALOG_FILE", str(path))
    # apply_patch 最终走 app_standalone.save_catalog，它读的是那个模块自己的 CATALOG_FILE。
    # 只 patch 上面那一行 = 测试会把真 catalog.json 写坏。
    import app_standalone as m
    monkeypatch.setattr(m, "CATALOG_FILE", str(path))
    return str(path)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def patch(**kw):
    base = {"brand_renames": [], "brand_aliases": [], "part_merges": [], "part_cats": []}
    base.update(kw)
    return base


def errors(catalog, p):
    return ap.validate(catalog, p)


def test_ok_patch_has_no_errors(sandbox):
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(part_merges=[{"brand": "微星", "keep": "B650M-B", "fold": ["PRO B650M-B 主板"],
                            "confidence": "high"}])
    assert errors(catalog, p) == []


def test_rejects_merge_target_that_does_not_exist(sandbox):
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(part_merges=[{"brand": "微星", "keep": "不存在的板", "fold": ["B650M-B"],
                            "confidence": "high"}])
    assert any("keep" in e for e in errors(catalog, p))


def test_rejects_fold_listed_twice(sandbox):
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(part_merges=[
        {"brand": "微星", "keep": "B650M-B", "fold": ["PRO B650M-B 主板"], "confidence": "high"},
        {"brand": "微星", "keep": "B650m-b(7500F)", "fold": ["PRO B650M-B 主板"], "confidence": "high"},
    ])
    assert any("重复" in e for e in errors(catalog, p))


def test_rejects_unknown_cat_key(sandbox):
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(part_cats=[{"brand": "微星", "name": "B650M-B", "cat": "mainboard"}])
    assert any("cat" in e for e in errors(catalog, p))


def test_rejects_brand_alias_equal_to_another_canonical(sandbox):
    """别名撞上别的规范名会让两个品牌被 resolve 成同一个，必须拒。"""
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(brand_aliases=[{"canonical": "微星", "aliases": ["英特尔"]}])
    assert any("撞上" in e for e in errors(catalog, p))


def test_rejects_missing_confidence(sandbox):
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(part_merges=[{"brand": "微星", "keep": "B650M-B", "fold": ["B650M-B"]}])
    assert any("confidence" in e for e in errors(catalog, p))


def test_medium_entries_are_held_back_without_flag(sandbox):
    catalog = json.load(open(sandbox, encoding="utf-8"))
    p = patch(part_merges=[{"brand": "微星", "keep": "B650M-B",
                            "fold": ["PRO B650M-B 主板"], "confidence": "medium"}])
    assert any("medium" in e for e in ap.validate(catalog, p, allow_medium=False))
    assert ap.validate(copy.deepcopy(catalog), p, allow_medium=True) == []


def test_rejects_merge_that_crosses_category(sandbox):
    """把水冷并进主板会同时让散热少一笔、主板多一笔，利润全歪 —— 第 6 条规则。"""
    catalog = json.load(open(sandbox, encoding="utf-8"))
    catalog["parts"].append({"cat": "unknown", "brand": "微星",
                             "name": "360水冷", "aliases": []})
    p = patch(part_merges=[{"brand": "微星", "keep": "B650M-B",
                            "fold": ["360水冷"], "confidence": "high"}])
    assert any("品类" in e for e in errors(catalog, p))


def test_allows_merge_when_rules_cannot_classify_either_side(sandbox):
    """两边都认不出时不能拦 —— 那正是人工要处理的那批，拦死就没法合并了。"""
    catalog = json.load(open(sandbox, encoding="utf-8"))
    catalog["parts"].append({"cat": "unknown", "brand": "微星",
                             "name": "维修返场一次", "aliases": []})
    p = patch(part_merges=[{"brand": "微星", "keep": "h610 坏板",
                            "fold": ["维修返场一次"], "confidence": "high"}])
    catalog["parts"].append({"cat": "unknown", "brand": "微星",
                             "name": "h610 坏板", "aliases": []})
    assert errors(catalog, p) == []


def test_validation_failure_writes_nothing(sandbox):
    before = sha(sandbox)
    p = patch(part_cats=[{"brand": "微星", "name": "B650M-B", "cat": "nope"}])
    with pytest.raises(ap.PatchRejected):
        ap.apply_patch(p, allow_medium=True)
    assert sha(sandbox) == before


def test_apply_merges_and_keeps_history_resolvable(sandbox):
    p = patch(part_merges=[{"brand": "微星", "keep": "B650M-B",
                            "fold": ["PRO B650M-B 主板"], "confidence": "high"}])
    ap.apply_patch(p, allow_medium=False)
    catalog = json.load(open(sandbox, encoding="utf-8"))
    names = [x["name"] for x in catalog["parts"] if x["brand"] == "微星"]
    assert "PRO B650M-B 主板" not in names
    keep = next(x for x in catalog["parts"] if x["name"] == "B650M-B")
    # 被并掉的旧名必须留下当别名，否则历史记录归并不上
    assert "PRO B650M-B 主板" in keep["aliases"]
    import app_standalone as m
    hit = m.resolve_part(catalog, "微星", "PRO B650M-B 主板")
    assert hit and hit["name"] == "B650M-B"
