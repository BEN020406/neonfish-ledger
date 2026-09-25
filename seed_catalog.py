"""把 data.json 里出现过的 (brand, model) 播种成 catalog.json 初稿。

只归一「同一种写法的大小写/空格差异」，判品类和合并同物异名都留给人工第二遍
（规格 §4 实测：127 组机械归一只能并到 122 组，剩下必须人工判）。
"""
import argparse
import collections
import json
import os
import sys

import app_standalone as m


def load_pairs(data_file):
    with open(data_file, 'r', encoding='utf-8') as f:
        records = json.load(f)
    return [((r.get('brand') or '').strip(), (r.get('model') or '').strip()) for r in records]


def build_catalog(pairs):
    """N() 相同的组合并成一个 part；name 取该组里出现次数最多的原写法。"""
    counter = collections.Counter(pairs)
    groups = collections.defaultdict(list)
    for (brand, model), hits in counter.items():
        if not model:
            continue
        groups[(m.norm_key(brand), m.norm_key(model))].append((brand, model, hits))

    catalog = m._empty_catalog()
    brand_names = collections.defaultdict(int)
    for key, members in groups.items():
        members.sort(key=lambda it: (-it[2], it[1]))
        brand, name, _ = members[0]
        brand_names[brand] += 1
        catalog['parts'].append({
            'cat': 'unknown',
            'brand': brand,
            'name': name,
            'aliases': sorted({mem[1] for mem in members[1:]}),
        })
    catalog['parts'].sort(key=lambda p: (p['brand'], p['name']))
    catalog['brands'] = [
        {'canonical': b, 'aliases': []}
        for b in sorted(brand_names, key=lambda x: (-brand_names[x], x))
    ]
    return catalog


def resolve_pair(catalog, brand, model):
    """与后端同一个解析器，播种自检直接复用它。"""
    return m.resolve_part(catalog, brand, model)


def main():
    parser = argparse.ArgumentParser(description='从 data.json 播种 catalog.json')
    parser.add_argument('--data', default=m.DATA_FILE)
    parser.add_argument('--out', default=m.CATALOG_FILE)
    parser.add_argument('--write', action='store_true', help='真的落盘；默认只打印统计')
    args = parser.parse_args()

    pairs = load_pairs(args.data)
    catalog = build_catalog(pairs)
    print('组合 %d 对 -> part %d 条，品牌 %d 个'
          % (len(pairs), len(catalog['parts']), len(catalog['brands'])))
    if args.write:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(json.dumps(catalog, ensure_ascii=False, indent=2))
        print('written:', args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
