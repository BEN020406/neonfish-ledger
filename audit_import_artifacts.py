# -*- coding: utf-8 -*-
"""只读审计：找 data.json 里可能是「导入合计行」的记录，并核对其覆盖区间。

为什么要有它：data.json 下标 166 那条 brand/model 全空、cost 51554 / sell 88368，
恰好等于下标 0..163 这 164 条的 cost 与 sell 之和。它现在只是被 index.html 的
`if (!b && !m) return false;` 顺手挡掉了才没进统计 —— 也就是说账面正确性靠的是一个
没有任何注释、也没有测试的副作用。要判断它到底该删、该补全、还是该显式排除，
得先把证据落成一份能对表格的文件，而不是凭一句「看起来像合计行」。

这个脚本一个字都不写：只读 data.json，打印审计结论。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, 'data.json')
BRAND_KEY = 'brand'
MODEL_KEY = 'model'
COST_KEY = 'cost'
SELL_KEY = 'sell'
# 判「是同一批导入」用的字段：只有表格导入才会填这几列
BATCH_FIELDS = ('sn', 'accessory', 'accessory_price', 'extra_price', 'images',
                'source_order_id')
# 一条记录要等于多少个连续区间的和才算可疑：区间越短越容易巧合（两行同价很常见），
# 所以门槛放在跨度上，而不是「只要两个数对上就算」。
MIN_SUSPECT_SPAN = 10


def as_cents(value):
    """金额一律按整数分比较。

    用浮点容差判「精确对上」是假严格：把售价改成 88368.01，
    88368.01 - 88368.00 在二进制浮点下是 0.00999…，仍然 < 0.01，
    于是这条判定对一分钱的变化完全不敏感 —— 一个不会失败的校验器等于没有校验。
    账本最小单位就是分，转成整数比大小既严格又不需要容差。
    """
    return int(round(as_number(value) * 100))


def as_number(value):
    """按前端 safeFloat 的口径取值：数字直接用，字符串去掉千分位再转，转不动算 0。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(',', '')
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def load(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit('%s 不是数组，审计口径全部按裸数组写' % path)
    return data


def is_blank_identity(row):
    return not str(row.get(BRAND_KEY) or '').strip() and not str(row.get(MODEL_KEY) or '').strip()


def totals(rows):
    return (sum(as_number(r.get(COST_KEY)) for r in rows),
            sum(as_number(r.get(SELL_KEY)) for r in rows))


def find_span_matches(data, target_idx, min_span):
    """找所有「连续区间（跳过 target 自己）之和 == target 的 cost 与 sell」的区间。

    两个数都以整数分同时对上才算，单看 cost 会有海量巧合。
    提前退出要求金额非负 —— 全表已核过没有负数，也都没有小于分的零头。
    """
    tc, ts = (as_cents(data[target_idx].get(COST_KEY)),
              as_cents(data[target_idx].get(SELL_KEY)))
    hits = []
    n = len(data)
    for start in range(n):
        acc_c = acc_s = 0
        for end in range(start, n):
            if end == target_idx:
                continue
            acc_c += as_cents(data[end].get(COST_KEY))
            acc_s += as_cents(data[end].get(SELL_KEY))
            if acc_c > tc and acc_s > ts:
                break
            if end - start + 1 >= min_span and acc_c == tc and acc_s == ts:
                hits.append((start, end))
                break
    return hits


def scan_all(data, min_span):
    """全表扫一遍，返回 {记录号: 命中区间列表}。只报跨度够长的，短跨度是巧合不是证据。"""
    found = {}
    for idx in range(len(data)):
        hits = find_span_matches(data, idx, min_span)
        if hits:
            found[idx] = hits
    return found


def describe(data, idx):
    row = data[idx]
    cost = as_number(row.get(COST_KEY))
    sell = as_number(row.get(SELL_KEY))
    return '下标 %d | brand=%r model=%r | 成本 %.2f 售价 %.2f 利润 %.2f | cat=%r' % (
        idx, row.get(BRAND_KEY), row.get(MODEL_KEY), cost, sell, sell - cost, row.get('cat'))


def batch_profile(data, lo, hi):
    """这一段像不像同一批导入：看键集合有几种、以及只有导入才会填的列有多少非空。"""
    rows = data[lo:hi]
    shapes = {tuple(sorted(r.keys())) for r in rows}
    filled = {f: sum(1 for r in rows if str(r.get(f) or '').strip() or r.get(f))
              for f in BATCH_FIELDS}
    return len(rows), len(shapes), filled


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=DATA_FILE)
    parser.add_argument('--min-span', type=int, default=MIN_SUSPECT_SPAN)
    parser.add_argument('--target', type=int, default=None,
                        help='只审某一条；默认审所有 品牌型号全空 的记录')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    data = load(args.data)
    cost, sell = totals(data)
    blank = [i for i, r in enumerate(data) if is_blank_identity(r)]
    print('=' * 72)
    print('data.json 导入痕迹审计（只读）')
    print('文件 %s' % args.data)
    print('记录数 %d | 全表成本 %.2f | 全表售价 %.2f | 全表利润 %.2f'
          % (len(data), cost, sell, sell - cost))
    print('品牌与型号同时为空、因此前端 parseItems 会整条丢掉的记录：%s'
          % (blank or '无'))
    print('=' * 72)

    targets = [args.target] if args.target is not None else blank
    verdicts = []
    for t in targets:
        print('\n【审 %s】' % describe(data, t))
        hits = find_span_matches(data, t, min_span=2)
        long_hits = [h for h in hits if h[1] - h[0] + 1 >= args.min_span]
        print('  两个数同时对上的连续区间共 %d 个；其中跨度 >= %d 的 %d 个'
              % (len(hits), args.min_span, len(long_hits)))
        for start, end in hits[:5]:
            span = end - start + 1
            seg_c, seg_s = totals([r for k, r in enumerate(data[start:end + 1]) if k + start != t])
            print('    区间 %d..%d（%d 条）: 成本 %.2f 售价 %.2f%s'
                  % (start, end, span, seg_c, seg_s,
                     '   <== 长跨度，基本只能是合计行' if span >= args.min_span else ''))
        if len(hits) > 5:
            print('    …另 %d 个短跨度命中（同价巧合，不足为凭）' % (len(hits) - 5))
        if not long_hits:
            print('  结论：没有长跨度证据，这条不像合计行。')
        else:
            start, end = long_hits[0]
            print('  结论：下标 %d..%d 这 %d 条的成本与售价被本条一次性对上，'
                  '本条是汇总行而非独立交易。' % (start, end, end - start + 1))
            kept = [r for i, r in enumerate(data) if i != t]
            kc, ks = totals(kept)
            print('  影响：把本条当独立交易计入，会虚增成本 %.2f、虚增利润 %.2f（占剔除后利润 %.1f%%）'
                  % (as_number(data[t].get(COST_KEY)),
                     as_number(data[t].get(SELL_KEY)) - as_number(data[t].get(COST_KEY)),
                     (ks - kc) and (as_number(data[t].get(SELL_KEY)) - as_number(data[t].get(COST_KEY))) / (ks - kc) * 100))
            verdicts.append((t, start, end))

    print('\n【全表复查：还有没有别的长跨度汇总行】')
    others = {i: h for i, h in scan_all(data, args.min_span).items() if i not in targets}
    if not others:
        print('  无：跨度 >= %d 且两个数同时对上的，只有上面审过的那些。' % args.min_span)
    for i, hits in sorted(others.items()):
        print('  可疑 %s -> 区间 %s' % (describe(data, i),
                                        ['%d..%d' % h for h in hits]))

    print('\n【分段画像：判断哪些记录属于同一批导入】')
    bounds = {0, len(data)}
    for t, start, end in verdicts:
        # 既在命中区间两端切，也在被审记录本身上下切：
        # 合计行前后往往是不同批次的录入，并成一段就看不出批次边界了。
        bounds.update((start, end + 1, t, t + 1))
    ordered = sorted(b for b in bounds if 0 <= b <= len(data))
    for lo, hi in zip(ordered, ordered[1:]):
        if hi <= lo:
            continue
        n, shapes, filled = batch_profile(data, lo, hi)
        print('  下标 %d..%d：%d 条，键集合 %d 种；'
              % (lo, hi - 1, n, shapes)
              + '  '.join('%s=%d' % (k, v) for k, v in filled.items() if v)
              + ('（全空）' if not any(filled.values()) else ''))

    print('\n【口径】')
    print('  金额取值按前端 safeFloat：字符串去千分位后转数，转不动记 0。')
    print('  判汇总行要求 cost 与 sell 都以整数分严格相等，'
          '且跨度 >= %d 才作为结论。' % args.min_span)
    print('  本脚本不写任何文件。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
