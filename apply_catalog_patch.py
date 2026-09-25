# -*- coding: utf-8 -*-
"""把 p3_catalog_patch.json 描述的人工第二遍应用到 catalog.json。

用法：
    python -X utf8 apply_catalog_patch.py            # 只看校验和计划，不写
    python -X utf8 apply_catalog_patch.py --apply    # 校验通过才写盘
    python -X utf8 apply_catalog_patch.py --apply --allow-medium

设计约束（都在 validate() 里兑现）：
- 整表校验，一条不过就一条不写。半应用会把库停在"某个名字既不是 part 也不是别名"的缝里。
- 合并是「把 fold 变成 keep 的别名后删掉 fold」，不是删掉 fold —— 历史记录的 model
  原文就是 fold 的名字，删了它这些记录会从统计里消失。
"""
import argparse
import json
import os
import sys

import app_standalone as m
import cat_rules

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_FILE = os.path.join(APP_DIR, 'catalog.json')
PATCH_FILE = os.path.join(APP_DIR, 'p3_catalog_patch.json')

KNOWN_CATS = {c['key'] for c in m.CATALOG_CATEGORIES}
CONFIDENCES = {'high', 'medium'}


class PatchRejected(Exception):
    """补丁有校验问题。message 里带全部错误，调用方不要吞。"""


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _find_part(catalog, brand, name):
    for p in catalog['parts']:
        if p.get('brand') == brand and m.norm_key(p.get('name')) == m.norm_key(name):
            return p
    return None


def _part_resolver(catalog, patch, allow_medium=False):
    """返回 resolve(brand, name) -> (part 或 None, 被并入的 keep 或 None)。

    算的是 apply_patch 真正落盘时的那份 part 集合：品牌改名先跑，型号合并再跑，
    part_cats 最后跑，那时被改名的 part 已经换了品牌、被合并的已经不存在。
    拿合并前的库校验 part_cats 会放行「给一条即将消失的 part 定品类」，到写盘才崩。
    品牌写改名前或改名后的写法都认——补丁是人手写的，两种都会出现。
    allow_medium 必须和这次执行用的一致：没放行的 medium 合并不会跑，
    它的 fold 就还活着，判成「会被合并掉」是假红。
    """
    renamed = {e['from']: e['to'] for e in patch.get('brand_renames', [])
               if e.get('from') and e.get('to')}

    def final_brand(brand):
        return renamed.get(brand, brand)

    merged_away = {}
    for entry in patch.get('part_merges', []):
        if entry.get('confidence') == 'medium' and not allow_medium:
            continue
        for fold in entry.get('fold', []):
            merged_away[(final_brand(entry.get('brand')), m.norm_key(fold))] = entry.get('keep')

    surviving = {}
    for p in catalog['parts']:
        key = (final_brand(p.get('brand')), m.norm_key(p.get('name')))
        if key not in merged_away:
            surviving[key] = p

    def resolve(brand, name):
        key = (final_brand(brand), m.norm_key(name))
        return surviving.get(key), merged_away.get(key)

    return resolve


def validate(catalog, patch, allow_medium=False):
    """返回错误列表；空列表 == 可应用。不修改 catalog。"""
    errors = []
    canonicals = {b['canonical'] for b in catalog['brands']}

    for entry in patch.get('brand_renames', []):
        src, dst = entry.get('from'), entry.get('to')
        if not src or not dst:
            errors.append('brand_renames 缺 from/to: %r' % (entry,))
            continue
        if src == dst:
            errors.append('brand_renames from==to: %s' % src)
        if src not in canonicals:
            errors.append('brand_renames 的 from 不在库里: %s' % src)
        # 目标名要现在就进集合：后面的别名校验得认得改名新建出来的规范名
        # （库里只有错字 凯侠，正字 铠侠 是这条补丁自己造的，不给就领不到 KIOXIA）
        canonicals.add(dst)

    for entry in patch.get('brand_aliases', []):
        canonical = entry.get('canonical')
        if canonical not in canonicals:
            errors.append('brand_aliases 的 canonical 不在库里: %s' % canonical)
        for alias in entry.get('aliases', []):
            n = m.norm_key(alias)
            if any(n == m.norm_key(c) for c in canonicals):
                errors.append('别名 %s 撞上已有规范名，会让两个品牌被认成同一个' % alias)

    seen_fold = {}
    for entry in patch.get('part_merges', []):
        brand, keep, folds = entry.get('brand'), entry.get('keep'), entry.get('fold', [])
        conf = entry.get('confidence')
        if conf not in CONFIDENCES:
            errors.append('part_merges 缺/非法 confidence（必须 high|medium）: %s/%s' % (brand, keep))
        elif conf == 'medium' and not allow_medium:
            errors.append('medium 置信度未放行（加 --allow-medium）: %s/%s <- %s' % (brand, keep, folds))
        if _find_part(catalog, brand, keep) is None:
            errors.append('part_merges 的 keep 不存在: %s/%s' % (brand, keep))
        for fold in folds:
            if m.norm_key(fold) == m.norm_key(keep):
                errors.append('fold 不能就是 keep 自己: %s/%s' % (brand, keep))
            if _find_part(catalog, brand, fold) is None:
                errors.append('part_merges 的 fold 不存在: %s/%s' % (brand, fold))
            keep_cat = cat_rules.guess_cat(brand, keep)
            fold_cat = cat_rules.guess_cat(brand, fold)
            if keep_cat and fold_cat and keep_cat != fold_cat:
                # 跨品类合并会把两类的笔数同时算错；两边都认不出的才交给人判断
                errors.append('合并跨品类: %s/%s(%s) <- %s(%s)'
                              % (brand, keep, keep_cat, fold, fold_cat))
            key = (brand, m.norm_key(fold))
            if key in seen_fold:
                errors.append('fold 在两条合并里重复: %s/%s（已在 %s 里）'
                              % (brand, fold, seen_fold[key]))
            else:
                seen_fold[key] = keep

    resolve = _part_resolver(catalog, patch, allow_medium=allow_medium)
    for entry in patch.get('part_cats', []):
        brand, name, cat = entry.get('brand'), entry.get('name'), entry.get('cat')
        if cat not in KNOWN_CATS:
            errors.append('cat 非法: %s/%s -> %s（合法: %s）'
                          % (brand, name, cat, sorted(KNOWN_CATS)))
            continue
        part, merged_into = resolve(brand, name)
        if merged_into is not None:
            errors.append('part_cats 写给了会被合并掉的名字: %s/%s（合并进 %s，品类请写给 %s）'
                          % (brand, name, merged_into, merged_into))
        elif part is None:
            errors.append('part_cats 指向不存在的 part: %s/%s' % (brand, name))
    return errors


def plan(catalog, patch):
    """人读的一行一句改动清单，dry-run 就靠它复核。"""
    lines = []
    for e in patch.get('brand_renames', []):
        lines.append('品牌改名/并入: %s -> %s（旧名进别名）' % (e['from'], e['to']))
    for e in patch.get('brand_aliases', []):
        if e['aliases']:
            lines.append('品牌别名: %s += %s' % (e['canonical'], '、'.join(e['aliases'])))
    for e in patch.get('part_merges', []):
        lines.append('[%s] %s/%s <- %s'
                     % (e.get('confidence'), e['brand'], e['keep'], '、'.join(e['fold'])))
    for e in patch.get('part_cats', []):
        lines.append('品类覆盖: %s/%s -> %s' % (e['brand'], e['name'], e['cat']))
    return lines


def apply_patch(patch, allow_medium=False, catalog_file=None, stamp=None):
    """校验 → 应用 → save_catalog。任何校验问题都抛 PatchRejected，且不写盘。"""
    path = catalog_file or CATALOG_FILE
    catalog = load_json(path)
    errors = validate(catalog, patch, allow_medium=allow_medium)
    if errors:
        raise PatchRejected('\n'.join(errors))

    # 必须在动 catalog 之前建索引：改名和合并都是原地改那些 part 字典，
    # 索引里存的引用到 part_cats 这一步依然指向同一个对象。
    resolve = _part_resolver(catalog, patch, allow_medium=allow_medium)
    by_name = {b['canonical']: b for b in catalog['brands']}

    for e in patch.get('brand_renames', []):
        src, dst = e['from'], e['to']
        if dst in by_name:
            target = by_name[dst]
        else:
            target = {'canonical': dst, 'aliases': []}
            catalog['brands'].append(target)
            by_name[dst] = target
        for extra in [src] + by_name[src]['aliases']:
            if extra not in target['aliases']:
                target['aliases'].append(extra)
        for p in catalog['parts']:
            if p['brand'] == src:
                p['brand'] = dst
        catalog['parts'] = [p for p in catalog['parts'] if p['brand'] != src or p['name'] != '']
        catalog['brands'].remove(by_name[src])
        del by_name[src]

    for e in patch.get('brand_aliases', []):
        by_name[e['canonical']]['aliases'] = sorted(
            set(by_name[e['canonical']]['aliases']) | set(e['aliases']))

    for e in patch.get('part_merges', []):
        keep = _find_part(catalog, e['brand'], e['keep'])
        for fold in e['fold']:
            victim = _find_part(catalog, e['brand'], fold)
            for extra in [victim['name']] + victim.get('aliases', []):
                if extra != keep['name'] and extra not in keep['aliases']:
                    keep['aliases'].append(extra)
            catalog['parts'].remove(victim)
        keep['aliases'] = sorted(set(keep['aliases']))

    for e in patch.get('part_cats', []):
        part, merged_into = resolve(e['brand'], e['name'])
        if part is None:
            raise PatchRejected('校验漏了: part_cats 指向不存在的 part %s/%s（合并进 %s）'
                                % (e['brand'], e['name'], merged_into))
        part['cat'] = e['cat']

    catalog['parts'].sort(key=lambda p: (p['brand'], m.norm_key(p['name'])))
    m.save_catalog(catalog, stamp, path=path)
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='真的写盘；默认只打印计划')
    parser.add_argument('--allow-medium', action='store_true')
    parser.add_argument('--patch', default=PATCH_FILE)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')

    patch = load_json(args.patch)
    catalog = load_json(CATALOG_FILE)
    for line in plan(catalog, patch):
        print(line)
    errors = validate(catalog, patch, allow_medium=args.allow_medium)
    if errors:
        print('\n校验未通过 %d 项：\n%s' % (len(errors), '\n'.join('  - ' + e for e in errors)))
        return 1
    if not args.apply:
        print('\n（dry-run，未写盘）加 --apply 才生效')
        return 0
    result = apply_patch(patch, allow_medium=args.allow_medium)
    print('已写盘：brands=%d parts=%d' % (len(result['brands']), len(result['parts'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
