# -*- coding: utf-8 -*-
"""给 data.json 每条记录补 cat。默认 dry-run。

    python -X utf8 migrate_add_cat.py                              # 报告打在线上库上（现在全 unknown）
    python -X utf8 migrate_add_cat.py --catalog 副本.json           # 报告打在打过补丁的副本上
    python -X utf8 migrate_add_cat.py --apply                       # 他复核过之后才跑

品类来源只有两个：命中的 part 自带的 cat；没命中就 unknown。
不在这里做任何"从名字猜类别"的临场判断 —— 那属于 cat_rules，规则改了要重跑测试。

--catalog 是给一次性脚本留的口子：p3_catalog_patch.json 里的品类还没落进 catalog.json
（刻意等他点头），直接拿线上库出报告每行都是 unknown。正确姿势是把库拷到临时副本、
用 apply_catalog_patch.apply_patch(ops, catalog_file=副本) 打上只含 high 合并的补丁，
再让本脚本 --catalog 指那份副本。读哪份判哪份，本脚本从不写知识库。
"""
import argparse
import collections
import hashlib
import json
import os
import sys

import app_standalone as m
import cat_rules

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, 'data.json')
CATALOG_FILE = os.path.join(APP_DIR, 'catalog.json')


def load_catalog_json(path=None):
    with open(path or CATALOG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_report(rows, catalog, index_from=0):
    """每行一条：下标 | 原 brand | 原 model | cat | 命中 part | 来源。"""
    report = []
    for i, r in enumerate(rows):
        part = m.resolve_part(catalog, r.get('brand'), r.get('model'))
        if part and part.get('cat'):
            cat, source, hit = part['cat'], 'part', '%s/%s' % (part['brand'], part['name'])
        else:
            guessed = cat_rules.guess_cat(r.get('brand'), r.get('model'))
            cat, source, hit = guessed or 'unknown', ('rule' if guessed else 'fallback'), ''
        report.append({'index': index_from + i, 'brand': (r.get('brand') or '').strip(),
                       'model': (r.get('model') or '').strip(), 'cat': cat,
                       'part': hit, 'source': source})
    return report


def run_apply(data_file, catalog, expect_count=None):
    """写盘 + 三重断言。任何一条不满足都 AssertionError 抛出，且已写坏的文件要靠 git 回退。"""
    with open(data_file, 'r', encoding='utf-8') as f:
        rows = json.load(f)
    before = [dict(r) for r in rows]
    if expect_count is not None and len(rows) != expect_count:
        raise AssertionError('条数与预期不符：%d != %d，拒绝写盘' % (len(rows), expect_count))
    report = build_report(rows, catalog)
    for row, entry in zip(rows, report):
        row['cat'] = entry['cat']
    assert len(rows) == len(before), '记录条数变了'
    assert all('cat' in r for r in rows), '有记录没拿到 cat'
    for old, new in zip(before, rows):
        assert {k: v for k, v in new.items() if k != 'cat'} == old, \
            '除 cat 外有字段被改动: index=%s' % (old,)
    tmp = data_file + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    os.replace(tmp, data_file)
    return rows


def _utf8_stdout():
    """一次性脚本要能在中文控制台下打印；capsys 之类的替身没有 reconfigure 就跳过。"""
    reconfigure = getattr(sys.stdout, 'reconfigure', None)
    if reconfigure is not None:
        reconfigure(encoding='utf-8')


def _catalog_note(catalog):
    """报告抬头自证一句：判定用的是哪份库、里面有多少 part 其实还没品类。

    线上库现在 121 条 part 的 cat 全是 unknown，这行会直接把 121 打出来；
    打过补丁的副本会打 0。没有这行，一份全 unknown 的报告看起来和正常报告一样。
    """
    parts = catalog.get('parts', [])
    undecided = sum(1 for p in parts if not p.get('cat') or p['cat'] == 'unknown')
    return 'brands=%d parts=%d 其中 cat 缺失或 unknown 的 part=%d' % (
        len(catalog.get('brands', [])), len(parts), undecided)


def print_report(rows, catalog, catalog_file):
    report = build_report(rows, catalog)
    counts = collections.Counter(e['cat'] for e in report)
    sources = collections.Counter(e['source'] for e in report)
    print('账本: %s (%d 条)' % (DATA_FILE, len(rows)))
    print('知识库: %s %s' % (catalog_file, _catalog_note(catalog)))
    print('品类分布:', dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))))
    print('来源分布:', dict(sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))))
    print()
    print('%-5s %-8s %-40s %-8s %s' % ('下标', '品牌', '型号', 'cat', '命中 part / 来源'))
    for e in report:
        print('%-5d %-8s %-40s %-8s %s'
              % (e['index'], e['brand'], e['model'][:40], e['cat'], e['part'] or e['source']))
    unknown = [e for e in report if e['cat'] == 'unknown']
    print('\n待确认(unknown) %d 条：' % len(unknown))
    for e in unknown:
        print('  %-5d brand=%r model=%r 来源=%s' % (e['index'], e['brand'], e['model'], e['source']))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--catalog', default=CATALOG_FILE,
                        help='判定用的知识库；一次性试算指向打过补丁的临时副本')
    parser.add_argument('--expect-count', type=int, default=255)
    args = parser.parse_args(argv)
    _utf8_stdout()

    rows = json.load(open(DATA_FILE, encoding='utf-8'))
    catalog = load_catalog_json(args.catalog)
    before_sha = sha256(DATA_FILE)
    print_report(rows, catalog, args.catalog)

    if not args.apply:
        print('\n（dry-run）data.json sha %s -> %s'
              % (before_sha[:16], sha256(DATA_FILE)[:16]))
        assert before_sha == sha256(DATA_FILE), 'dry-run 动了盘，停手排查'
        return 0

    print('\n提醒：执行期间账本窗口不要停在编辑表单上。')
    run_apply(DATA_FILE, catalog, expect_count=args.expect_count)
    print('已写盘；三重断言通过（条数 / 每条含 cat / 其他字段逐条相等）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
