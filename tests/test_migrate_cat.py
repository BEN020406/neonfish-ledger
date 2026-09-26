# -*- coding: utf-8 -*-
"""cat 存量迁移脚本的安全性质。

dry-run 绝不动盘；apply 之后除 cat 外逐条相等、条数不变。
断言写在脚本自己的输出上，不接受"我看过 diff 了"。

**报告的起点是"播种态快照 + 已应用补丁"，不是线上库直接加补丁**：补丁 2026-09-26
已真落库，线上 catalog.json 里 凯侠/INTER 都降级成了别名，再从它出发跑一遍品牌改名会
在 validate() 第一步就报「from 不在库里」。所以本文件从
tests/fixtures/catalog_pre_p3.json 起步现拷现打，判定结果与当初的报告同源。
REAL_CATALOG_FILE 仍指线上 catalog.json —— 「dry-run 不许动盘」那两条盯的就是它。
所有用例都打在 tmp 目录的副本上，线上 data.json / catalog.json 一个字节都不动。
"""
import collections
import hashlib
import json
import os
import shutil

import pytest

import apply_catalog_patch as ap
import migrate_add_cat as mg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DATA_FILE = os.path.join(ROOT, 'data.json')
REAL_CATALOG_FILE = os.path.join(ROOT, 'catalog.json')
# 打补丁之前的起点。线上库已打过补丁，拿它再跑一遍品牌改名会在第一步就报
# 「from 不在库里」，红在起点而不是红在被测行为上。
PRE_PATCH_CATALOG = os.path.join(ROOT, 'tests', 'fixtures', 'catalog_pre_p3.json')
PATCH_FILE = os.path.join(ROOT, 'p3_catalog_patch.json')

# 规格 §3 认可的那份记录级分布，与 tests/test_apply_patch.py 的
# SPEC3_RECORD_COUNTS 同源。它是一次性迁移的验收闸门，账本每记一单就会漂，
# 红了先重新核对规格 §3 再改数字，不许改数字去凑。
SPEC3_RECORD_COUNTS = {'board': 166, 'cooler': 25, 'ram': 25, 'ssd': 13,
                       'cpu': 12, 'bundle': 10, 'gpu': 2, 'unknown': 2}
LEGAL_CATS = set(SPEC3_RECORD_COUNTS)
# unknown 允许剩的两条：一条品牌型号都空，一条 model 是维修备注不是型号。
KNOWN_UNRESOLVABLE = [
    {'index': 166, 'brand': '', 'model': ''},
    {'index': 243, 'brand': '', 'model': 'CPU针接触不良返场维修一次'},
]


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def catalog_with(parts):
    return {'brands': [], 'parts': parts}


@pytest.fixture
def patched_catalog(tmp_path, monkeypatch):
    """播种态快照的副本 + 真补丁里 high 那部分（3 条 medium 他还没批）。

    两个 CATALOG_FILE 一起 monkeypatch：apply_patch 最后走 app_standalone.save_catalog，
    少了这一手，校验器一旦退回「读副本写线上」就会真改坏知识库。
    """
    path = tmp_path / 'catalog_patched.json'
    shutil.copyfile(PRE_PATCH_CATALOG, str(path))
    import app_standalone as m
    monkeypatch.setattr(ap, 'CATALOG_FILE', str(path))
    monkeypatch.setattr(m, 'CATALOG_FILE', str(path))
    real = json.load(open(PATCH_FILE, encoding='utf-8'))
    ops = {
        'brand_renames': real['brand_renames'],
        'brand_aliases': real['brand_aliases'],
        'part_merges': [e for e in real['part_merges'] if e['confidence'] == 'high'],
        'part_cats': real['part_cats'],
    }
    ap.apply_patch(ops, catalog_file=str(path))
    return str(path)


def test_dry_run_report_covers_every_row(patched_catalog):
    """255 行全覆盖，且每行的 cat 是真判出来的不是空壳。

    这里刻意不写 all(r['cat'])：线上库 cat 全 unknown 时那句照样真，挡不住任何东西。
    换成三层硬断言：分布等于规格 §3、unknown 只许是那两条已知残行、命中层必须是 part。
    """
    rows = json.load(open(REAL_DATA_FILE, encoding='utf-8'))
    report = mg.build_report(rows, mg.load_catalog_json(patched_catalog))
    assert len(rows) == 255 == sum(SPEC3_RECORD_COUNTS.values()), \
        '账本已不是 255 条（现在 %d 条），先重新核对规格 §3 再改数字' % len(rows)
    assert len(report) == 255
    assert [r['index'] for r in report] == list(range(255)), '下标必须与 data.json 逐条对齐'
    assert all(set(r) == {'index', 'brand', 'model', 'cat', 'part', 'source'}
               for r in report), '报告行字段不齐，复核的人没有依据可判'
    got = collections.Counter(r['cat'] for r in report)
    assert dict(got) == SPEC3_RECORD_COUNTS, \
        '差异: %s' % {k: (got.get(k, 0), v) for k, v in SPEC3_RECORD_COUNTS.items()
                      if got.get(k, 0) != v}
    assert set(got) <= LEGAL_CATS, sorted(set(got) - LEGAL_CATS)
    # 落 unknown 的只许那两条已知的残行，多一条就是判定层漏了东西。
    left = [{'index': r['index'], 'brand': r['brand'], 'model': r['model']}
            for r in report if r['cat'] == 'unknown']
    assert left == KNOWN_UNRESOLVABLE, left
    # 253 条走 part 层（补丁后每条 part 都有 cat），只有那两条残行走 fallback。
    sources = collections.Counter(r['source'] for r in report)
    assert sources['part'] == 253 and sources['fallback'] == 2, dict(sources)
    assert all(r['part'] for r in report if r['source'] == 'part'), \
        '命中 part 的行必须记下命中的是谁，否则复核时看不出归并到没归并对'


def test_report_records_which_layer_decided_each_cat():
    """三层来源各钉一条：part 说了算，其次规则，都不认得的才 unknown。"""
    report = mg.build_report(
        [{'brand': '', 'model': 'CPU针接触不良返场维修一次'},
         {'brand': '微星', 'model': 'B650M-B'},
         {'brand': '微星', 'model': 'MAG B760M MORTAR WIFI'},
         {'brand': '微星', 'model': '某块没名分的板子'}],
        catalog_with([{'brand': '微星', 'name': 'B650M-B', 'cat': 'board', 'aliases': []}]),
    )
    assert report[0]['cat'] == 'unknown' and report[0]['source'] == 'fallback'
    assert report[1]['cat'] == 'board' and report[1]['source'] == 'part'
    assert report[1]['part'] == '微星/B650M-B', '命中 part 要写下 brand/name 供人复核'
    assert report[2]['cat'] == 'board' and report[2]['source'] == 'rule'
    assert report[3]['cat'] == 'unknown' and report[3]['source'] == 'fallback'
    # 来源写成 rule/fallback 之外的东西就是脚本自己发明了一层，规则要能单独判对错。
    assert all(r['source'] in ('part', 'rule', 'fallback') for r in report)


def test_apply_changes_only_cat(tmp_path):
    data_file = tmp_path / 'data.json'
    original = [{'brand': '微星', 'model': 'B650M-B', 'cost': 600, 'images': []}]
    data_file.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    catalog = catalog_with([{'brand': '微星', 'name': 'B650M-B',
                             'cat': 'board', 'aliases': []}])
    mg.run_apply(str(data_file), catalog)
    after = json.load(open(str(data_file), encoding='utf-8'))
    assert len(after) == 1
    assert after[0]['cat'] == 'board'
    stripped = {k: v for k, v in after[0].items() if k != 'cat'}
    assert stripped == original[0]


def test_apply_asserts_when_count_changes(tmp_path):
    data_file = tmp_path / 'data.json'
    data_file.write_text(json.dumps([{'brand': 'x', 'model': 'y'}], ensure_ascii=False),
                         encoding='utf-8')
    # 人为制造条数不一致：断言必须炸，不能静默写坏
    with pytest.raises(AssertionError):
        mg.run_apply(str(data_file), catalog_with([]), expect_count=999)


def test_dry_run_touches_neither_production_file(tmp_path, monkeypatch, capsys):
    """dry-run 的交付物是 stdout，两份生产文件必须一个字节都不动。"""
    data_file = tmp_path / 'data.json'
    data_file.write_text(json.dumps([{'brand': '微星', 'model': 'B650M-B'}],
                                    ensure_ascii=False), encoding='utf-8')
    catalog_copy = tmp_path / 'catalog.json'
    shutil.copyfile(REAL_CATALOG_FILE, str(catalog_copy))
    monkeypatch.setattr(mg, 'DATA_FILE', str(data_file))
    before = (sha(str(data_file)), sha(str(catalog_copy)),
              sha(REAL_DATA_FILE), sha(REAL_CATALOG_FILE))
    assert mg.main(['--catalog', str(catalog_copy)]) == 0
    assert (sha(str(data_file)), sha(str(catalog_copy)),
            sha(REAL_DATA_FILE), sha(REAL_CATALOG_FILE)) == before, 'dry-run 动了盘'
    assert not list(tmp_path.glob('*.tmp')), '留下 .tmp 说明走了写盘那条路'
    out = capsys.readouterr().out
    assert '品类分布' in out and '来源分布' in out, '报告首两行就是分布，缺了没法复核'
    assert 'dry-run' in out


def test_catalog_argument_is_the_file_the_report_is_built_from(tmp_path, monkeypatch, capsys):
    """--catalog 必须真换掉判定用的那份库；默认仍是仓库里的 catalog.json。"""
    assert mg.CATALOG_FILE == REAL_CATALOG_FILE, '默认值不能离开线上那份知识库'
    data_file = tmp_path / 'data.json'
    data_file.write_text(json.dumps([{'brand': '微星', 'model': 'B650M-B'}],
                                    ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(mg, 'DATA_FILE', str(data_file))
    for cat in ('board', 'cooler'):
        other = tmp_path / ('catalog_%s.json' % cat)
        other.write_text(json.dumps(catalog_with([{'brand': '微星', 'name': 'B650M-B',
                                                    'cat': cat, 'aliases': []}]),
                                    ensure_ascii=False), encoding='utf-8')
        assert mg.main(['--catalog', str(other)]) == 0
        out = capsys.readouterr().out
        # 只认分布那一行：报告抬头会打印副本的文件名，而文件名里就带着 board/cooler，
        # 拿整段 stdout 做子串匹配会假绿。
        assert "品类分布: {'%s': 1}" % cat in out, \
            '--catalog 没生效，报告仍在读另一份库\n%s' % out


def test_production_catalog_already_carries_the_patched_state():
    """线上 catalog.json 必须已经处在补丁之后的状态。

    上面那些用例全打在快照上，就算 catalog.json 被 checkout 回退掉也照样绿。
    这里盯的就是真文件本身：错误拼写品牌只能当别名存在，品类不能还是空壳。
    阈值刻意写下限不写死数：medium 三条以后批了、新配件继续入库，都不该让这条变红。
    """
    import app_standalone as m
    catalog = mg.load_catalog_json()
    names = {b['canonical'] for b in catalog['brands']}
    assert '凯侠' not in names and 'INTER' not in names, '品牌归一没落库'
    assert m.resolve_brand(catalog, '凯侠') == '铠侠'
    assert m.resolve_brand(catalog, 'INTER') == '英特尔'
    legal = {c['key'] for c in m.CATALOG_CATEGORIES}
    missing = [p for p in catalog['parts'] if p.get('cat') not in legal]
    assert missing == [], '这些 part 没有合法 cat: %s' % (missing[:5],)
    classified = sum(1 for p in catalog['parts'] if p['cat'] != 'unknown')
    assert classified >= 100, \
        '全库 %d 条 part 只有 %d 条判出了品类，品类层没落库' % (len(catalog['parts']), classified)
