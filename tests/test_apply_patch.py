"""人工第二遍的补丁校验器。

这里最关键的是「校验器必须真能失败」：每条规则都配一个正好踩线的坏补丁，
否则校验形同装饰。所有用例都打在 tmp 目录的 catalog 副本上，不碰真库。
"""
import copy
import hashlib
import json
import os
import shutil

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


def test_rename_created_canonical_can_receive_aliases(sandbox):
    """改名新建出来的品牌必须能在同一次补丁里领到别名。

    库里现在只有错字 凯侠，正字 铠侠 和它的官方英文名 KIOXIA 是同一条补丁建的；
    校验若按改名前的 canonical 集合判，KIOXIA 会被误报"canonical 不在库里"。
    """
    catalog = copy.deepcopy(CATALOG)
    catalog["brands"].append({"canonical": "凯侠", "aliases": []})
    with open(sandbox, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False)
    p = patch(brand_renames=[{"from": "凯侠", "to": "铠侠"}],
              brand_aliases=[{"canonical": "铠侠", "aliases": ["KIOXIA"]}])
    assert errors(catalog, p) == []
    result = ap.apply_patch(p, catalog_file=sandbox)
    kioxia = next(b for b in result["brands"] if b["canonical"] == "铠侠")
    assert "KIOXIA" in kioxia["aliases"]
    assert "凯侠" in kioxia["aliases"]


def test_alias_colliding_with_rename_target_is_still_rejected():
    """放宽到改名后的集合，不能放宽到允许别名撞规范名。"""
    catalog = copy.deepcopy(CATALOG)
    catalog["brands"].append({"canonical": "凯侠", "aliases": []})
    p = patch(brand_renames=[{"from": "凯侠", "to": "铠侠"}],
              brand_aliases=[{"canonical": "英特尔", "aliases": ["铠侠"]}])
    assert any("撞上" in e for e in ap.validate(catalog, p))


def test_rejects_merge_that_crosses_category(sandbox):
    """把水冷并进主板会同时让散热少一笔、主板多一笔，利润全歪 —— 第 6 条规则。"""
    catalog = json.load(open(sandbox, encoding="utf-8"))
    catalog["parts"].append({"cat": "unknown", "brand": "微星",
                             "name": "360水冷", "aliases": []})
    p = patch(part_merges=[{"brand": "微星", "keep": "B650M-B",
                            "fold": ["360水冷"], "confidence": "high"}])
    assert any("品类" in e for e in errors(catalog, p))


def test_allows_merge_when_one_side_is_unclassifiable(sandbox):
    """只有一边判不出时不能拦 —— 这条专门防住把规则 6 放宽成"一边 None 就拒绝"。"""
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


@pytest.fixture
def real_brand_sandbox(tmp_path, monkeypatch):
    """品牌层要打在真 catalog.json 的品牌清单上，不能打在模块级 CATALOG 上。

    模块级 CATALOG 只有微星/英特尔两个品牌，真实补丁里其余 21 条 brand_aliases
    会被 validate() 判成「canonical 不在库里」，用例永远绿不了；而计划预期的红字
    是 `assert '凯侠' not in names` —— 只有真品牌清单（凯侠/INTER 都在库里）
    才报得出那条。所以拷一份真库到 tmp，两个 CATALOG_FILE 一起 patch，照样不碰真库。
    """
    path = tmp_path / "catalog.json"
    shutil.copyfile(os.path.join(ROOT, "catalog.json"), str(path))
    monkeypatch.setattr(ap, "CATALOG_FILE", str(path))
    import app_standalone as m
    monkeypatch.setattr(m, "CATALOG_FILE", str(path))
    return str(path)


def test_brand_pass_collapses_inter_and_kai侠(real_brand_sandbox):
    """跑完真实的 p3_catalog_patch.json 里品牌层，结果必须落在库上而不是只在测试里。"""
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    brand_ops = {k: real[k] for k in ('brand_renames', 'brand_aliases')}
    ap.apply_patch(brand_ops, catalog_file=real_brand_sandbox)
    catalog = json.load(open(real_brand_sandbox, encoding='utf-8'))
    names = {b['canonical'] for b in catalog['brands']}
    assert '凯侠' not in names and '铠侠' in names
    assert '英特尔' in names and 'INTER' not in names
    import app_standalone as m
    assert m.resolve_brand(catalog, '凯侠') == '铠侠'
    assert m.resolve_brand(catalog, 'INTER') == '英特尔'
    assert m.resolve_brand(catalog, 'msi') == '微星'


# Task 4：型号层第二遍。先钉住「补丁存在且非空」：
# 否则下面所有 for 循环一次都不跑，测试会空跑通过。
REQUIRED_HIGH_MERGES = {("微星", "B650m-b"), ("微星", "H610m-E"), ("微星", "H610m-s"),
                        ("微星", "B650mgaming plus wifi"), ("微星", "B850迫击炮wifi"),
                        ("华硕", "B650m-k"), ("铭瑄", "B760M 终结者D4")}


def test_patch_declares_the_expected_high_confidence_merges():
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    high = {(e['brand'], e['keep']) for e in real['part_merges']
            if e['confidence'] == 'high'}
    assert high == REQUIRED_HIGH_MERGES, high ^ REQUIRED_HIGH_MERGES
    assert any(e['confidence'] == 'medium' for e in real['part_merges']), \
        '靠品牌常识而非数据本身判断的那些必须标 medium，不能混进 high'


def test_high_confidence_merges_fold_tail_notes_into_main_parts(real_brand_sandbox):
    """每条 high 置信合并都要真把 fold 变成 keep 的别名，且老记录仍可 resolve。

    打在真 catalog.json 的副本上：模块级 CATALOG 只有 4 条 part，装不进
    H610m-E / B850迫击炮wifi 这些 keep，红会红在「keep 不存在」而不是合并本身。
    """
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    merges = [e for e in real['part_merges'] if e['confidence'] == 'high']
    ops = {'brand_renames': real['brand_renames'], 'brand_aliases': real['brand_aliases'],
           'part_merges': merges}
    catalog = ap.apply_patch(ops, catalog_file=real_brand_sandbox)
    import app_standalone as m
    checked = 0
    for e in merges:
        for fold in e['fold']:
            hit = m.resolve_part(catalog, e['brand'], fold)
            assert hit and m.norm_key(hit['name']) == m.norm_key(e['keep']), fold
            checked += 1
    assert checked >= 8, '循环只跑了 %d 次，这条测试没有覆盖任何东西' % checked


def test_bundle_rows_are_not_folded_into_other_parts():
    """§3：板U套装并进主板会虚增利润。带 (CPU) 的行绝不能当别人的别名。"""
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    folds = [f for e in real['part_merges'] for f in e['fold']]
    assert folds, 'part_merges 为空，本测试没有覆盖任何东西'
    import cat_rules
    for fold in folds:
        assert cat_rules.guess_cat('微星', fold) != 'bundle', \
            '带 CPU 括号的 %s 不该被并进别的 part' % fold
