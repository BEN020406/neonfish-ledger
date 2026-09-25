# -*- coding: utf-8 -*-
"""按 (brand, name) 猜品类。纯函数、不读 catalog、不写盘 —— 规则要能单独判对错。

判定顺序即优先级，先命中先得。规格 §9 第 3 步把 bundle 排第一是有原因的：
`B650m-b(7500F)` 既是 B650M-B 又不是 B650M-B，成本是两块硬件合并的，
归到 board 会虚增主板利润、归到 cpu 会丢掉板子。
"""
import re

# 括号里是 CPU 型号：(12400) / （9600X） / (14600KF)。
# 必须是 \d{4,5}：只有 \d{4} 会吃掉 1240/1460 却剩下尾数 0 撑不起 [A-Z]{0,3}，
# 结果五条 5 位数套装全漏 —— 实测漏成 bundle=4。
CPU_IN_PARENS = re.compile(r'[（(]\s*\d{4,5}[A-Z]{0,3}\s*[）)]')
BUNDLE_WORDS = ('套装', '搭配')

# 显式芯片组清单而不是 [BHXZ]\d{3} 正则：带负向断言的正则会把粘连前缀
# PRIMEB760M / MAGB650M / BATTLEAXB760M 全判死（前一个字符是字母）。
# 清单是子串匹配，认不出的落「待确认」让人看，不猜。
CHIPSETS = ('X870E', 'X870', 'X670E', 'X670', 'X470', 'X370', 'X99',
            'Z790', 'Z890', 'Z690',
            'B860', 'B850', 'B760', 'B660', 'B650', 'B550', 'B450',
            'A620', 'A520', 'H610', 'H810')
CPU_EXACT = {'2700X', '3700X', '5600X', '7500F', '9700X',
             '12100F', '12400F', '14600KF', '8600K'}
# AS806 / SN5000 里都含字母+数字段，所以 ssd 必须排在 board 之前。
SSD_TOKENS = ('SD10', 'SE10', 'VD10', 'RC20', 'GM7000', 'GM7', 'SN5000',
              'NV2', 'AS806', '512G', '1T', '2T')
RAM_TOKENS = ('海力士', '镁光', '长鑫', '三星', 'ADIE', 'MDIE', 'CJR')
RAM_CAPACITY = re.compile(r'\d+G\*\d+')
COOLER_TOKENS = ('水冷', 'LIQUID', '风冷')
GPU_TOKENS = ('RTX', 'GTX', '显卡')


def _norm(text):
    """本地做大写+去空白，避免 import app_standalone 把 http.server 一起拖进来。"""
    s = (text or '').upper()
    s = s.replace('（', '(').replace('）', ')').replace('　', '')
    return ''.join(s.split())


def guess_cat(brand, name):
    n = _norm(name)
    b = _norm(brand)
    if CPU_IN_PARENS.search(n) or any(w in name for w in BUNDLE_WORDS):
        return 'bundle'
    if any(t in n for t in COOLER_TOKENS):
        return 'cooler'
    if any(t in n for t in GPU_TOKENS):
        return 'gpu'
    if any(t in n for t in SSD_TOKENS):
        return 'ssd'
    if any(t in n for t in RAM_TOKENS) or RAM_CAPACITY.search(n):
        return 'ram'
    if n in CPU_EXACT or b == 'AMD':
        return 'cpu'
    if any(c in n for c in CHIPSETS) or '主板' in name:
        return 'board'
    return None


def is_bundle(brand, name):
    return guess_cat(brand, name) == 'bundle'
