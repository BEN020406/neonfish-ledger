# -*- coding: utf-8 -*-
"""用 cat_rules 把品类批量写进 p3_catalog_patch.json 的 part_cats。

默认 dry-run，只打印计数和分布；加 --write 才改补丁文件（不碰 catalog.json）。

为什么要有这个脚本而不是手填：合并决定一改，被合并掉的那几个名字就不能再出现在
part_cats 里（校验器会拦），重跑一遍比手改可靠。排除的是「这次真会执行的合并」，
所以 --allow-medium 要和将来 apply 时用的一致。
"""
import argparse
import collections
import json
import sys

import app_standalone as m
import apply_catalog_patch as ap
import cat_rules


def build(catalog, patch, allow_medium):
    renamed = {e['from']: e['to'] for e in patch.get('brand_renames', [])}
    folds = {(renamed.get(e['brand'], e['brand']), m.norm_key(fold))
             for e in patch.get('part_merges', [])
             if allow_medium or e.get('confidence') != 'medium'
             for fold in e['fold']}
    entries = []
    undecided = []
    for p in catalog['parts']:
        brand = renamed.get(p['brand'], p['brand'])
        if (brand, m.norm_key(p['name'])) in folds:
            continue
        cat = cat_rules.guess_cat(brand, p['name'])
        if cat is None:
            undecided.append('%s/%s' % (brand, p['name']))
            continue
        entries.append({'brand': brand, 'name': p['name'], 'cat': cat})
    entries.sort(key=lambda e: (e['brand'], m.norm_key(e['name'])))
    return entries, folds, undecided


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true', help='写回补丁文件；默认只打印')
    parser.add_argument('--allow-medium', action='store_true')
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')

    catalog = ap.load_json(ap.CATALOG_FILE)
    patch = ap.load_json(ap.PATCH_FILE)
    entries, folds, undecided = build(catalog, patch, args.allow_medium)
    dist = collections.Counter(e['cat'] for e in entries)
    print('part 总数=%d 已排除的合并目标=%d 规则未判定=%d'
          % (len(catalog['parts']), len(folds), len(undecided)))
    print('part 层分布: %s' % dict(sorted(dist.items())))
    for name in undecided:
        print('  未判定: %s' % name)
    if not args.write:
        print('\n（dry-run，未改补丁）加 --write 才写 part_cats')
        return 0
    patch['part_cats'] = entries
    with open(ap.PATCH_FILE, 'w', encoding='utf-8', newline='') as f:
        json.dump(patch, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('\n已写入 part_cats=%d 条' % len(entries))
    return 0


if __name__ == '__main__':
    sys.exit(main())
