# 配件知识库 P3：人工第二遍 + 品类判定 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P1 机器播种出来的 `catalog.json` 骨架过成"人认过的库"（品牌归一、同物异名合并、每条 part 定品类），再据此给 255 条存量记录补 `cat`，并在账本里让"品类待确认"可发现、可改判。

**Architecture:** 三件事各自独立、单向依赖。① 人工第二遍写成**声明式补丁文件** `p3_catalog_patch.json`（品牌改名/品牌别名/型号合并/品类覆盖），由 `apply_catalog_patch.py` 先整表校验再落盘 —— 校验不过一条都不写；② 品类判定拆成纯函数 `cat_rules.guess_cat()`（关键字规则）+ 补丁里的 `part_cats` 覆盖，规则的验收标准是**复现规格 §3 的 8 个计数**；③ `migrate_add_cat.py` 默认 dry-run 出 255 行报告，他复核后才 `--apply` 动 `data.json`。

**Tech Stack:** Python 3.13 标准库（无新依赖）、pytest（现有 `tests/` 沙盒）、原生 JS 单文件前端 `index.html`。

---

## 0. 执行前必须知道的事实（别重新推导）

**路径与命令**
- 仓库根 `G:\claude code`（含空格）。所有命令先 `cd "G:/claude code"`；git 用 `git -C "G:/claude code"`。
- 跑测试：`python -X utf8 -m pytest "G:/claude code/tests" -q`。**`-X utf8` 不是可选的**：中文断言在非 UTF-8 控制台会假失败。
- 禁止 `git add -A` / `git add .`（仓库里有一堆无关未跟踪文件）。只按文件名暂存。
- 禁止 `git push`（纯本地仓库）。
- 杀进程用 `powershell -NoProfile -Command "Stop-Process -Id N -Force"`；`taskkill //PID` 会被 Git Bash 判成 UNC 路径而失败。
- Windows 版 Python **读不到 Git Bash 的 `/tmp/xxx`**。临时文件写到仓库目录里，或用 `urllib` 直读 HTTP。

**当前基线（本计划写下时实测）**
- `data.json`：255 条，sha256 前缀 `fd50d857c17f9f0f`。测试沙盒在 teardown 会断言它没变。
- `catalog.json`：`categories` 8 项 / `brands` 24 项 / `parts` 122 项；**`brands[].aliases` 全是 0 条**；**所有 part 的 `cat` 都是 `unknown`**。
- 全量测试现状：`212 passed`。新增用例要在此基础上叠加，任何一个变红都算失败。
- `resolve_part()` 命中 **254/255**；未命中的那条是 `brand=""` 且 `model=""`。
- 型号行数 127 组 → 122 组（P1 机械归一只并掉 5 组）；品牌行数 **24 → 24，一条都没并**。

**写计划时已实测过的两件事（别推翻，也别重蹈）**
- 品类规则在 255 条真实记录上**已跑通到与 §3 完全一致**：`board=166, cooler=25, ram=25, ssd=13, cpu=12, bundle=10, gpu=2, unknown=2`；bundle 命中的正好是那 10 条，unknown 正好是空记录与 `CPU针接触不良返场维修一次` 那 2 条。第一版规则有两个 bug，已经修进下面的代码块：
  1. `CPU_IN_PARENS` 用 `\d{4}` → 5 位数的 `12400/12100/14600/14700/12600` 全吃不进，bundle 只剩 4 条。必须是 `\d{4,5}`。
  2. 芯片组用带负向断言的正则 `[BHXZ]\d{3}` → `PRIMEB760M`、`MAGB650M`、`BATTLEAXB760M` 这类**粘连前缀**被断言判死。改成显式芯片组清单做子串匹配。
  ⚠️ 更要紧的教训：**第一版 board 恰好等于 166 是两拨错误互相抵消的假绿**。只核对 8 个总数不够，必须同时核对 bundle 与 unknown 的具体成员（Task 5 的测试就是这么写的）。
- 下面 Task 4 的合并清单，`keep` 与 `fold` **全部在现库 122 条 parts 里存在**，且 `fold` 的品类与 `keep` 一致（7 条 high + 3 条 medium 都验过）。

**已存在的可复用设施**（`app_standalone.py`，行号是写计划时的）
- `norm_key(text)` :151 — 大写 + 去所有空白 + 全角括号转半角。**规则：`N('（9600X）') == N('(9600X)')`**，写正则时要利用这点。
- `resolve_brand(catalog, brand)` :160 / `resolve_part(catalog, brand, model)` :172 / `_part_keys(part)` :200。
- `CATALOG_CATEGORIES` :95-104，8 个 key：`board/cpu/ram/ssd/cooler/gpu/bundle/unknown`。
- `catalog_stamp()` / `load_catalog()` / `save_catalog(catalog, stamp=None)` :106-149 区间 —— 补丁脚本必须走 `save_catalog`，别自己 `json.dump`。
- `file_stamp()` :55 / `save_data(data, stamp)` :73（含 `WriteConflict`）/ `reject_if_client_stale` / `handle_write_conflict`。
- 记录写入三处都按固定键表拷贝：`handle_add_record` :585-598、`handle_update_record` :629-642、拆单 `context` :710 —— 三处都只认 `ORDER_CONTEXT_KEYS`（:46）。
- 测试夹具：`tests/conftest.py` 的 `api` fixture yield `(call, read_ledger, data_path)`；`call(method, path, payload=None, raw_body=None, with_headers=False, if_match=None)`。catalog 专用夹具是 `tests/test_catalog.py` 里的 `api_catalog`。**默认只返回 `(status, json)`，要看响应头必须传 `with_headers=True`。**

**规格与本轮已定的偏离**
- 规格 §4 的示例把 `B650m-b(7500F)` 列进 `B650M-B` 的 aliases，这与 §3「`bundle` 必须独立，并进主板会虚增利润」直接矛盾。**本计划采用 §3：带 `(CPU型号)` 的记录单独成 part、定 `cat=bundle`，绝不并进裸型号。** 执行 Task 5 时顺手把 §4 示例改掉（见 Task 5 Step 5）。
- 规格 §4 明写"空 `model` 的 1 条记录不生成 part"，P1 的播种脚本违反了这条，产出了 `canonical=""` 的品牌和 1 条空名 part。Task 1 修。
- 规格 §2 不做成色/状态，所以 `B850迫击炮wifi(工包)`、`针脚坏`、`坏板` 这些尾巴**不剥离文本**，只决定"这条并进哪个 part"。

---

## 1. 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `cat_rules.py` | 纯函数：`(brand, name) → cat key`，只含关键字规则，不认识 catalog | Create |
| `apply_catalog_patch.py` | 读 `p3_catalog_patch.json` → 整表校验 → 应用到 catalog（走 `save_catalog`）；默认 dry-run | Create |
| `p3_catalog_patch.json` | 人工第二遍的**唯一载体**：品牌改名、品牌别名、型号合并、品类覆盖 | Create |
| `migrate_add_cat.py` | 给 `data.json` 补 `cat`：dry-run 报告 / `--apply` + 三重断言 | Create |
| `seed_catalog.py` | 播种：按 §4 排除空 brand/model | Modify |
| `app_standalone.py` | 让 `cat` 在新增/编辑/拆单三条写路径上不丢 | Modify |
| `index.html` | 品类维度：记录按 part 继承 cat、「品类待确认 N」chip + 筛选 + 行内下拉 | Modify |
| `tests/test_cat_rules.py` | 规则表 + §3 计数回归 | Create |
| `tests/test_apply_patch.py` | 校验器必须能失败 + 应用语义 | Create |
| `tests/test_catalog.py` | 空品牌脏键回归 | Modify |
| `tests/test_ledger_api.py` | `cat` 透传回归 | Modify |

**为什么补丁是声明式文件而不是"直接改 catalog.json"**：直接改无法校验（改错了没人拦），也无法让他复核 —— 复核的产物应该是几十行"把 A 并进 B"，而不是 122 条 part 的大文件。

---

## 2. 目标终态（验收口径）

1. `catalog.json`：`brands` 从 24 → **22**（`凯侠→铠侠` 改名、`INTER` 并进 `英特尔`、空品牌删除），每个品牌至少 1 条中英别名；`parts` 从 122 → **≈110**，每条 `cat` 都定下来，只剩他认可的残余是 `unknown`。
2. 记录级品类计数**必须等于规格 §3 的表**：`board=166, cooler=25, ram=25, ssd=13, cpu=12, bundle=10, gpu=2, unknown=2`。
3. `data.json` 每条有 `cat`；改判后再编辑保存，`cat` 不丢。
4. 账本顶部能点出「品类待确认 N」，行内能改判。
5. `python -X utf8 -m pytest tests -q` 全绿，且真实 `data.json` 在他复核前 sha 不变。

---

### Task 1: 播种脚本守住"空 brand/model 不入库"

**Files:**
- Modify: `seed_catalog.py`（`build_catalog()`）
- Modify: `catalog.json`（删掉那条脏键）
- Test: `tests/test_catalog.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_catalog.py` 末尾：

```python
def test_seed_skips_pairs_with_empty_brand_or_model():
    """规格 §4：空 model 的记录不生成 part，空 brand 不生成品牌条目。

    P1 的播种违反了这条，库里留下 canonical="" 的品牌和一条空名 part，
    它在统计里是一行看不见的脏数据。
    """
    from seed_catalog import build_catalog

    pairs = [("微星", "B650M-B"), ("", "某条维修"), ("光威", ""), ("", "")]
    catalog = build_catalog(pairs)
    assert [b["canonical"] for b in catalog["brands"]] == ["微星"]
    assert [p["name"] for p in catalog["parts"]] == ["B650M-B"]


def test_catalog_has_no_blank_brand_or_name():
    catalog = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    assert all(b["canonical"].strip() for b in catalog["brands"]), "brands 里有空 canonical"
    assert all(p["brand"].strip() and p["name"].strip() for p in catalog["parts"]), "parts 里有空 brand/name"
```

- [ ] **Step 2: 跑到红**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q -k "blank or empty_brand"`
Expected: 两条 FAIL。第一条报 `build_catalog` 产出了空 canonical；第二条断言消息 `brands 里有空 canonical`。

- [ ] **Step 3: 改 `build_catalog`**

在 `seed_catalog.py` 的 `build_catalog(pairs)` 里，分组循环那一处开头加过滤（放在 `groups` 累加之前，不是在写盘之前 —— 空值一旦进组，"取最高频写法"就会选出一个空名）：

```python
def build_catalog(pairs):
    groups = collections.defaultdict(collections.Counter)
    for brand, model in pairs:
        brand = (brand or '').strip()
        model = (model or '').strip()
        # 规格 §4：空 brand 或空 model 的组合不入库。维修、空行这类记录
        # 没有"型号"可言，硬塞进库会变成一行看不见也选不中的脏键。
        if not brand or not model:
            continue
        groups[(brand, model)][(brand, model)] += 1
    # ...（以下沿用现有实现）
```

- [ ] **Step 4: 重跑播种并落盘**

Run: `python -X utf8 -c "import json; d=json.load(open('data.json',encoding='utf-8')); print(len(d))"` → `255`
Run: `python -X utf8 seed_catalog.py --write`
Expected stdout 类似 `组合 255 对 -> part 121 条，品牌 23 个`（比原来各少 1：空品牌条目和它那条空名 part 被排除）。

- [ ] **Step 5: 确认 `data.json` 没被动**

Run: `sha256sum data.json`
Expected: 仍是 `fd50d857c17f9f0f6052670f97f546f555cdd6c1b61ffc31db21a8332f649595`。播种脚本只读 `data.json`，若 sha 变了就是写错了对象，停手排查。

- [ ] **Step 6: 跑到绿**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: 全绿（原有用例数 + 2）。注意 `test_catalog.py` 里若存在 `assert len(catalog["parts"]) == 122` 的播种断言，改成 121 —— 改的时候确认理由是本任务排除脏键，不是"测试挡路"。

- [ ] **Step 7: 提交**

```bash
git -C "G:/claude code" add seed_catalog.py catalog.json tests/test_catalog.py
git -C "G:/claude code" commit -m "fix(catalog): 播种排除空品牌与空型号（规格 §4）"
```

---

### Task 2: 补丁校验器 —— 先让它有能力失败

**Files:**
- Create: `cat_rules.py`（Task 4/5 共用；本任务因第 6 条校验规则就要用它）
- Create: `apply_catalog_patch.py`
- Create: `p3_catalog_patch.json`（本任务先放空骨架，Task 3/4 填内容）
- Test: `tests/test_apply_patch.py`

**校验规则（一条不过，整体拒绝，且 `catalog.json` 的 sha 不变）**
1. `brand_renames`：`from` 必须存在、`to` 若存在则视作并入、`from != to`。
2. `brand_aliases`：`canonical` 必须在 brands 里；每条 alias 归一后不能等于**任何**已存在的 canonical（否则 `resolve_brand` 会把两个品牌认成同一个）。
3. `part_merges`：`keep` 与每个 `fold` 都必须是同品牌下存在的 part；`fold` 不能出现在两条 merge 里（传递性未定义）；`fold == keep` 拒绝。
4. `part_cats`：`cat` 必须 ∈ `CATALOG_CATEGORIES` 的 key；`(brand, name)` 必须在合并后的 parts 里存在。
5. 全部 `confidence` ∈ `{"high","medium"}`，默认只允许 high；`medium` 条目必须 `--allow-medium` 才应用。
6. **合并不能跨品类**：`cat_rules.guess_cat(brand, fold)` 与 `guess_cat(brand, keep)` 都有值且不相等时拒绝（把 `360水冷` 并进某块主板，会同时让散热少一笔、主板多一笔，利润全歪）。两边都是 `None` 或有一边 `None` 时放行 —— 规则本来就认不出的，正是人工要处理的那批。

- [ ] **Step 1: 写失败测试**

Create `tests/test_apply_patch.py`：

```python
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
```

- [ ] **Step 2: 跑到红**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_apply_patch.py" -q`
Expected: 收集期就失败 —— `ModuleNotFoundError: No module named 'apply_catalog_patch'`。

- [ ] **Step 3: 写实现（两个文件；`cat_rules.py` 是被第 6 条校验规则引用的叶子模块，必须先建）**

先 Create `cat_rules.py`（这份实现已在 255 条真实记录上验到与 §3 完全一致；改任何一条规则都要重跑 `tests/test_cat_rules.py`）：

```python
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
```

再 Create `apply_catalog_patch.py`：

```python
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

    for entry in patch.get('part_cats', []):
        brand, name, cat = entry.get('brand'), entry.get('name'), entry.get('cat')
        if cat not in KNOWN_CATS:
            errors.append('cat 非法: %s/%s -> %s（合法: %s）'
                          % (brand, name, cat, sorted(KNOWN_CATS)))
        elif _find_part(catalog, brand, name) is None:
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
        _find_part(catalog, e['brand'], e['name'])['cat'] = e['cat']

    catalog['parts'].sort(key=lambda p: (p['brand'], m.norm_key(p['name'])))
    m.save_catalog(catalog, stamp)
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
```

- [ ] **Step 4: 写空骨架补丁**

Create `p3_catalog_patch.json`：

```json
{
  "brand_renames": [],
  "brand_aliases": [],
  "part_merges": [],
  "part_cats": []
}
```

- [ ] **Step 5: 跑到绿**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_apply_patch.py" -q`
Expected: 11 passed。若 `test_validation_failure_writes_nothing` 因为 `save_catalog` 用模块级 `CATALOG_FILE` 而写到真库 —— **立刻停手**，说明 `app_standalone.save_catalog` 不接受路径参数；改法是 monkeypatch `app_standalone.CATALOG_FILE`（在 `sandbox` fixture 里一并 patch），不要用真库跑测试。

Run: `python -X utf8 apply_catalog_patch.py`
Expected: 空计划 + `（dry-run，未写盘）`，exit 0。

- [ ] **Step 6: 变异测试（校验器的单测）**

手动确认校验器不是装饰：把 `p3_catalog_patch.json` 的 `part_cats` 临时改成
`[{"brand": "微星", "name": "B650M-B", "cat": "mainboard"}]`，跑
`python -X utf8 apply_catalog_patch.py` → 必须打印"校验未通过 1 项"且 exit 1、`catalog.json` sha 不变。改回空数组。

- [ ] **Step 7: 提交**

```bash
git -C "G:/claude code" add cat_rules.py apply_catalog_patch.py p3_catalog_patch.json tests/test_apply_patch.py
git -C "G:/claude code" commit -m "feat(catalog): 人工第二遍的声明式补丁与整表校验器"
```

---

### Task 3: 品牌层第二遍（24 → 22）

**Files:**
- Modify: `p3_catalog_patch.json`
- Test: `tests/test_apply_patch.py`

**依据**：`INTER` 与 `英特尔` 是两个品牌条目但同一家；`凯侠` 是 `铠侠` 的错字（规格 §4 明写这两个归一）。别名只加**真实存在的中英对照官方名**，不发明库里没有的写法 —— 别名唯一的作用是以后按英文也能选中，加错品牌名比不加更糟。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_apply_patch.py`：

```python
def test_brand_pass_collapses_inter_and_kai侠(sandbox):
    """跑完真实的 p3_catalog_patch.json 里品牌层，结果必须落在库上而不是只在测试里。"""
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    brand_ops = {k: real[k] for k in ('brand_renames', 'brand_aliases')}
    ap.apply_patch(brand_ops, catalog_file=sandbox)
    catalog = json.load(open(sandbox, encoding='utf-8'))
    names = {b['canonical'] for b in catalog['brands']}
    assert '凯侠' not in names and '铠侠' in names
    assert '英特尔' in names and 'INTER' not in names
    import app_standalone as m
    assert m.resolve_brand(catalog, '凯侠') == '铠侠'
    assert m.resolve_brand(catalog, 'INTER') == '英特尔'
    assert m.resolve_brand(catalog, 'msi') == '微星'
```

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_apply_patch.py" -q -k brand_pass`
Expected: FAIL —— `assert '凯侠' not in names`（补丁还是空的）。

- [ ] **Step 2: 填 `brand_renames`**

```json
  "brand_renames": [
    {"from": "凯侠", "to": "铠侠"},
    {"from": "INTER", "to": "英特尔"}
  ],
```

- [ ] **Step 3: 填 `brand_aliases`**

`norm_key` 已经忽略大小写与空格，所以同一写法的大小写变体只列一次。`AMD`、`OLOY`、`精粤`、`玖合` 无可靠中英对照，留空数组。

```json
  "brand_aliases": [
    {"canonical": "微星",   "aliases": ["MSI"]},
    {"canonical": "华硕",   "aliases": ["ASUS"]},
    {"canonical": "技嘉",   "aliases": ["GIGABYTE"]},
    {"canonical": "七彩虹", "aliases": ["COLORFUL"]},
    {"canonical": "铭瑄",   "aliases": ["MAXSUN"]},
    {"canonical": "英特尔", "aliases": ["INTEL"]},
    {"canonical": "铠侠",   "aliases": ["KIOXIA"]},
    {"canonical": "英睿达", "aliases": ["CRUCIAL"]},
    {"canonical": "光威",   "aliases": ["GLOWAY"]},
    {"canonical": "金士顿", "aliases": ["KINGSTON"]},
    {"canonical": "金百达", "aliases": ["KINGBANK"]},
    {"canonical": "芝奇",   "aliases": ["GSKILL"]},
    {"canonical": "阿斯加特", "aliases": ["ASGARD"]},
    {"canonical": "宏碁",   "aliases": ["ACER"]},
    {"canonical": "影驰",   "aliases": ["GEFORCE", "GALAX"]},
    {"canonical": "海盗船", "aliases": ["CORSAIR"]},
    {"canonical": "佰维",   "aliases": ["BIWIN"]},
    {"canonical": "西数",   "aliases": ["WD", "西部数据"]},
    {"canonical": "玖合",   "aliases": ["JUHOR"]},
    {"canonical": "OLOY",   "aliases": []},
    {"canonical": "AMD",    "aliases": []},
    {"canonical": "精粤",   "aliases": []}
  ],
```

> 校验器会拒掉撞上规范名的别名。`GEFORCE` 撞不上任何 canonical，保留；若执行时校验报"撞上"，说明库里已有该条目，删掉这一项而不是放宽校验。

- [ ] **Step 4: 跑到绿**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_apply_patch.py" -q`
Expected: 全绿。

- [ ] **Step 5: dry-run 真库**

Run: `python -X utf8 apply_catalog_patch.py`
Expected: 只列品牌层的 22 行，`（dry-run，未写盘）`，exit 0。
Run: `sha256sum catalog.json` → 与执行前相同。

- [ ] **Step 6: 提交**

```bash
git -C "G:/claude code" add p3_catalog_patch.json tests/test_apply_patch.py
git -C "G:/claude code" commit -m "feat(catalog): 品牌层第二遍（INTER/凯侠归一 + 中英别名）"
```

---

### Task 4: 型号层第二遍（同物异名合并）

**Files:**
- Modify: `p3_catalog_patch.json`
- Test: `tests/test_apply_patch.py`

**取舍口径**
- **并**：词序不同、多了 `主板/拆机/带挡板/工包/时好时坏/针脚坏` 这类噪声尾巴、系列前缀 `PRO/PRIME/MAG` 有无差异。
- **不并**：`wifi` 与不带 `wifi` 的，一律视为两块不同的板（这是 SKU 差异不是写法差异）；带 `(CPU型号)` 的单独成 part 走 `bundle`（见 §0 偏离说明）。
- 中置信的（靠品牌常识而非数据本身判断的）标 `medium`，他不点头就不落盘。

- [ ] **Step 1: 写失败测试**

```python
# 先钉住「补丁存在且非空」：否则下面所有 for 循环一次都不跑，测试会空跑通过。
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


def test_high_confidence_merges_fold_tail_notes_into_main_parts(sandbox):
    """每条 high 置信合并都要真把 fold 变成 keep 的别名，且老记录仍可 resolve。"""
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    merges = [e for e in real['part_merges'] if e['confidence'] == 'high']
    ops = {'brand_renames': real['brand_renames'], 'brand_aliases': real['brand_aliases'],
           'part_merges': merges}
    catalog = ap.apply_patch(ops, catalog_file=sandbox)
    import app_standalone as m
    checked = 0
    for e in merges:
        for fold in e['fold']:
            hit = m.resolve_part(catalog, e['brand'], fold)
            assert hit and m.norm_key(hit['name']) == m.norm_key(e['keep']), fold
            checked += 1
    assert checked >= 8, '循环只跑了 %d 次，这条测试没有覆盖任何东西' % checked


def test_bundle_rows_are_not_folded_into_other_parts(sandbox):
    """§3：板U套装并进主板会虚增利润。带 (CPU) 的行绝不能当别人的别名。"""
    real = json.load(open(os.path.join(ROOT, 'p3_catalog_patch.json'), encoding='utf-8'))
    folds = [f for e in real['part_merges'] for f in e['fold']]
    assert folds, 'part_merges 为空，本测试没有覆盖任何东西'
    import cat_rules
    for fold in folds:
        assert cat_rules.guess_cat('微星', fold) != 'bundle', \
            '带 CPU 括号的 %s 不该被并进别的 part' % fold
```

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_apply_patch.py" -q -k "expected_high or merges or bundle_rows"`
Expected: 三条 FAIL，且**必须红在断言而不是 import**（`cat_rules.py` 在 Task 2 已建好）：第一条报空集合差集，第二条报 `循环只跑了 0 次`，第三条报 `part_merges 为空`。

- [ ] **Step 2: 填 `part_merges`（high）**

```json
  "part_merges": [
    {"brand": "微星", "keep": "B650m-b",
     "fold": ["PRO B650M-B 主板", "b650m-b 爆破弹 带挡板"], "confidence": "high"},
    {"brand": "微星", "keep": "H610m-E",
     "fold": ["PRO H610M-E DDR4主板"], "confidence": "high"},
    {"brand": "微星", "keep": "H610m-s",
     "fold": ["pro h610m-s wifi ddr4拆机换下来的时"], "confidence": "high"},
    {"brand": "微星", "keep": "B650mgaming plus wifi",
     "fold": ["b650m gamingplus Wi-Fi主板 时好时"], "confidence": "high"},
    {"brand": "微星", "keep": "B850迫击炮wifi",
     "fold": ["B850迫击炮wifi(工包)"], "confidence": "high"},
    {"brand": "华硕", "keep": "B650m-k",
     "fold": ["PRIME B650M-K主板"], "confidence": "high"},
    {"brand": "铭瑄", "keep": "B760M 终结者D4",
     "fold": ["B760M-D4终结者主板"], "confidence": "high"}
  ],
```

- [ ] **Step 3: 追加 medium（要他点头才生效）**

```json
    {"brand": "微星", "keep": "B650m迫击炮wifi",
     "fold": ["mag b650m mortar 针脚坏"], "confidence": "medium"},
    {"brand": "微星", "keep": "B650mgaming wifi",
     "fold": ["B650 gaming wifi"], "confidence": "medium"},
    {"brand": "微星", "keep": "liquid R360",
     "fold": ["360水冷"], "confidence": "medium"},
    {"brand": "微星", "keep": "B650m迫击炮",
     "fold": ["B650m迫击炮wifi"], "confidence": "medium"}
```

> 最后一条是"迫击炮 与 迫击炮wifi 其实同一块"的反向假设，和它上面那条**互斥**，两条都只能由他选一条。他都没确认时，两条 medium 都不应用（默认行为就是拒绝 medium）。
> 注意：`B650m迫击炮` 那条若他确认成立，必须挪到 `mag b650m mortar 针脚坏` 之前执行，否则 `fold` 校验会看到已被并走的对象；校验器按顺序应用，`_find_part` 返回 `None` 会报 "fold 不存在" —— 这是预期行为，不要为此放宽校验。

- [ ] **Step 4: 确认 `cat_rules.py` 已在 Task 2 Step 3 建好，本任务不新建文件**

Task 2 的第 6 条校验规则已经依赖它，本任务只是往 `part_merges` 里加条目并靠第 3 步那两个测试守着。
跑一次冒烟，确认模块能 import 且 bundle 分支可用：

```bash
python -X utf8 -c "import cat_rules; print(cat_rules.guess_cat('微星','B650m-b(7500F)'))"
```

Expected: 打印 `bundle`。

- [ ] **Step 5: 跑到绿**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_apply_patch.py" -q`
Expected: 全绿（`guess_cat` 已存在，bundle 行不在 fold 列表里）。

- [ ] **Step 6: 提交**

```bash
git -C "G:/claude code" add p3_catalog_patch.json tests/test_apply_patch.py
git -C "G:/claude code" commit -m "feat(catalog): 型号层第二遍（尾注/词序/系列前缀合并）+ 品类规则骨架"
```

---

### Task 5: 把 121 条 part 的 `cat` 定下来（验收 = 复现规格 §3 计数）

**Files:**
- Modify: `p3_catalog_patch.json`
- Modify: `docs/superpowers/specs/2026-09-25-ledger-parts-catalog-design.md`（改掉 §4 示例的矛盾）
- Test: `tests/test_cat_rules.py`

- [ ] **Step 1: 写失败测试（计数即验收）**

Create `tests/test_cat_rules.py`：

```python
# -*- coding: utf-8 -*-
"""品类规则的验收标准是复现规格 §3 的记录数分布。

规则本身很容易"看起来对"：只要有一条归类错了，账本里那一类的利润就是错的。
所以断言打在 255 条真实记录上的分布，不是打在挑选过的样例上。
"""
import collections
import json
import os

import cat_rules
import app_standalone as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 规格 §3 的表，八个数字加起来就是 255
EXPECTED = {"board": 166, "cooler": 25, "ram": 25, "ssd": 13,
            "cpu": 12, "bundle": 10, "gpu": 2, "unknown": 2}


def records():
    return json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8"))


def test_every_record_classifies_to_expected_distribution():
    counts = collections.Counter()
    for r in records():
        counts[cat_rules.guess_cat(r.get("brand"), r.get("model")) or "unknown"] += 1
    assert dict(counts) == EXPECTED, counts


def test_bundle_set_is_exactly_the_ten_cpu_in_parens_rows():
    got = sorted(r["model"] for r in records()
                 if cat_rules.guess_cat(r.get("brand"), r.get("model")) == "bundle")
    assert len(got) == 10, got
    assert "B650m-b(7500F)" in got
    assert "X670小雕（9600X）" in got      # 全角括号也要认
    assert "I512490F盒装搭配微星PRO H610M" in got
    assert "610h 12400f 板u套装" in got
    assert "B850迫击炮wifi(工包)" not in got  # 工包是包装不是 CPU，不能算套装


def test_gpu_beats_board_for_rtx_rows():
    assert cat_rules.guess_cat("微星", "Rtx5070 魔龙") == "gpu"
    assert cat_rules.guess_cat("七彩虹", "Rtx5070 战斧") == "gpu"


def test_asrock_like_board_is_not_mistaken_for_ssd():
    """PRIME B650M-K / AS806 这类名字里都有一段字母+数字，ssd 必须排在 board 前且不误吃。"""
    assert cat_rules.guess_cat("华硕", "AS806 512g") == "ssd"
    assert cat_rules.guess_cat("华硕", "PRIME B650M-K主板") == "board"
    assert cat_rules.guess_cat("微星", "liquid R360") == "cooler"
```

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_cat_rules.py" -q`
Expected: FAIL，并打印出实际分布（`assert ... counts`）。**先把实际分布贴进提交信息或任务备注**，再决定改规则还是改预期 —— 不要为了让测试绿而直接改 `EXPECTED`，那 8 个数字是从真实数据数出来并经他确认过的。

- [ ] **Step 2: 按失败输出修规则**

常见偏差与处置：
- `cpu` 少算：某条 CPU 记录的 `model` 不是纯号码（例如带"盒装"）→ 它应当落在 `bundle`，把 `CPU_EXACT` 补齐而不是把规则放宽。
- `board` 多算：`ssd/ram` 的 token 没覆盖到（例如 `长江存储` 类写法）→ 加 token，并把该条原文补进测试。
- 修完后分布必须精确等于 `EXPECTED`。

- [ ] **Step 3: 核对 part 层覆盖，确认不需要任何 `part_cats` 覆盖**

`cat` 存在 part 上，所以要看的是"121 条 part 里有几条规则判不出"。**写计划时已实测：Task 1 删掉空品牌脏键后，121 条 part 全部判得出品类，判不出的是 0 条**，分布 `board=70, ram=18, bundle=10, ssd=10, cpu=9, gpu=2, cooler=2`。所以 `p3_catalog_patch.json` 的 `part_cats` 保持 `[]` —— 品类完全由规则给出，人只复核规则。

执行时必须复跑同一条核对，数字变了就说明规则被动过：

```bash
python -X utf8 -c "
import json, collections, cat_rules
c=json.load(open('catalog.json',encoding='utf-8'))
rows=[(p['brand'],p['name'],cat_rules.guess_cat(p['brand'],p['name'])) for p in c['parts'] if p['brand']]
print('parts=%d 判不出=%d'%(len(rows), sum(1 for r in rows if r[2] is None)))
print(dict(collections.Counter(r[2] or 'unknown' for r in rows)))
for r in rows:
    if r[2] is None: print('  待人工:', *r)
"
```

Expected: `parts=121 判不出=0` 且分布与上面一致。若出现"判不出"，**不要往 `part_cats` 里塞一条覆盖把它盖掉** —— 先判断是规则缺 token（改 `cat_rules` + 加测试样例）还是这条真该留 `unknown`（那就是 §3 里 unknown 的合理残余，记下来交他判）。

> 记录层与 part 层的分布数字不同是正常的：part 层按"型号条数"数，记录层按"笔数"数。验收以 §3 的**记录层** 8 个数字为准（Step 1 的测试）。

- [ ] **Step 4: dry-run 全补丁并确认校验通过**

Run: `python -X utf8 apply_catalog_patch.py`
Expected: 打印品牌层 + 7 条 high 合并 + 品类覆盖；4 条 medium 报"未放行"，exit 1 —— 这是**预期**，因为默认不放开 medium。加 `--allow-medium` 应变成 exit 0 且 0 错误。

- [ ] **Step 5: 改掉规格里那条自相矛盾的示例**

`docs/superpowers/specs/2026-09-25-ledger-parts-catalog-design.md` §4 的示例 aliases 改为不含 CPU 套装行：

```jsonc
      "aliases": ["B650m-b", "b650m-b", "PRO B650M-B 主板", "b650m-b 爆破弹 带挡板"]
```

并在 §4 规则列表末尾加一句：

```markdown
- **带 `(CPU型号)` 的记录不并进裸型号 part**：它是板U套装，成本含两块硬件。§3 与 §4 在此处曾有冲突（§4 示例把 `B650m-b(7500F)` 列为 B650M-B 的别名），以 §3 为准。
```

- [ ] **Step 6: 提交**

```bash
git -C "G:/claude code" add tests/test_cat_rules.py p3_catalog_patch.json docs/superpowers/specs/2026-09-25-ledger-parts-catalog-design.md docs/superpowers/specs/2026-09-25-ledger-parts-catalog-design.md
git -C "G:/claude code" commit -m "test(catalog): 品类规则以规格 §3 的 8 个计数为验收"
```

---

### Task 6: `cat` 在三条写路径上不丢

**Files:**
- Modify: `app_standalone.py:46`（键表）、`:596`、`:638`、`:710`
- Test: `tests/test_ledger_api.py`

**为什么是 P3 而不是 P2**：迁移把 `cat` 写进记录之后，任何一次普通编辑/新增/拆单都会决定这个字段活不活。现状是 `handle_add_record` 用固定字段表重建 record（`cat` 一定丢）、`handle_update_record` 逐键赋值（盘上已有的 `cat` 侥幸保住）。行为不一致本身就是要修的 bug。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ledger_api.py`：

```python
def test_cat_survives_update_and_split(api):
    """迁移写进记录的 cat 不能被一次普通编辑或一次拆单抹掉。

    抹掉的后果不是报错，是「品类待确认」的数量在下一次保存后莫名变少。
    """
    call, read_ledger, _ = api

    # 先给第 0 条打上 cat
    data = read_ledger()
    data[0]['cat'] = 'board'
    status, body = call('POST', '/api/data', {'items': data})
    assert status == 200, body

    # 编辑同一条的售价：cat 必须还在
    status, body = call('PUT', '/api/record/0', {'brand': '微星', 'model': 'B650M GAMING WIFI',
                                                 'cost': '600', 'sell': '950'})
    assert status == 200, body
    assert read_ledger()[0]['cat'] == 'board'

    # 拆单：新行继承 cat
    status, body = call('POST', '/api/split_record',
                        {'index': 0, 'parts': [{'brand': '微星', 'model': '拆出来的',
                                                'cost': '300', 'sell': ''}]})
    assert status == 200, body
    assert any(r.get('cat') == 'board' for r in read_ledger())


def test_add_record_accepts_cat_when_client_sends_it(api):
    call, read_ledger, _ = api
    status, body = call('POST', '/api/record',
                        {'brand': '光威', 'model': '神策 16G', 'cost': '200', 'sell': '',
                         'cat': 'ram'})
    assert status == 200, body
    assert read_ledger()[-1]['cat'] == 'ram'


def test_add_record_rejects_unknown_cat(api):
    call, _, _ = api
    status, body = call('POST', '/api/record',
                        {'brand': '光威', 'model': 'X', 'cost': '1', 'sell': '', 'cat': '主板'})
    assert status == 400, body
```

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_ledger_api.py" -q -k "cat_"`
Expected: 三条 FAIL（`cat` 被丢弃 / 非法值被接受）。

- [ ] **Step 2: 改键表**

`app_standalone.py:46` 之后：

```python
ORDER_CONTEXT_KEYS = ('source_order_id', 'order_date', 'item_title', 'order_paid')
# cat 是迁移写进记录的品类快照。它必须和订单上下文一样在新增/编辑/拆单三条路上
# 原样透传，否则「品类待确认」会在用户下一次随手保存时凭空变少 —— 那种丢失没有报错。
CAT_KEY = 'cat'
VALID_CAT_KEYS = frozenset(c['key'] for c in CATALOG_CATEGORIES)
```

> 注意：`CATALOG_CATEGORIES` 定义在 :95，比这里晚。若模块级引用报 `NameError`，把 `VALID_CAT_KEYS` 的下移到了 `CATALOG_CATEGORIES` 之后（不要为此在 :46 提前定义类别表）。

- [ ] **Step 3: 三处循环与拆单一起改**

`handle_add_record`（:596）与 `handle_update_record`（:638）：

```python
        for key in ORDER_CONTEXT_KEYS + (CAT_KEY,):
            if key in body:
                record[key] = body[key]
```

拆单 `context`（:710）：

```python
        context = {key: record[key] for key in ORDER_CONTEXT_KEYS + (CAT_KEY,) if key in record}
```

`handle_add_record` 在 `reject_bad_images` 之后加校验：

```python
        if self.reject_bad_cat(body):
            return
```

并在 handler 类里新增（与 `reject_bad_money` 同级、同样早于写盘）：

```python
    def reject_bad_cat(self, body):
        """cat 只认 §3 的 8 个 key；空串与缺失同等对待（落「品类待确认」）。"""
        if 'cat' not in body:
            return False
        value = body['cat']
        if value in ('', None) or value in VALID_CAT_KEYS:
            return False
        self.send_json({'ok': False, 'error': 'bad cat',
                        'cats': sorted(VALID_CAT_KEYS)}, 400)
        return True
```

- [ ] **Step 4: 跑到绿**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_ledger_api.py" -q`
Expected: 全绿。再跑全量 `python -X utf8 -m pytest "G:/claude code/tests" -q`，必须仍全绿（此时应为 `212 + 新增` 条，逐条数一遍，不接受"应该没影响"）。

- [ ] **Step 5: 提交**

```bash
git -C "G:/claude code" add app_standalone.py tests/test_ledger_api.py
git -C "G:/claude code" commit -m "fix(ledger): cat 在新增/编辑/拆单路径上原样透传"
```

---

### Task 7: `migrate_add_cat.py` —— 只 dry-run，报告交他复核

**Files:**
- Create: `migrate_add_cat.py`
- Test: `tests/test_migrate_cat.py`

**本任务的交付物是报告，不是写盘。** `--apply` 由他点头后单独跑（Task 7 Step 6），执行者不得自行 apply。

- [ ] **Step 1: 写失败测试**

Create `tests/test_migrate_cat.py`：

```python
# -*- coding: utf-8 -*-
"""迁移脚本的安全性质。

dry-run 绝不动盘；apply 之后除 cat 外逐条相等、条数不变。
断言写在脚本自己的输出上，不接受"我看过 diff 了"。
"""
import hashlib
import json
import os

import migrate_add_cat as mg


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def test_dry_run_report_covers_every_row():
    rows = json.load(open(mg.DATA_FILE, encoding='utf-8'))
    report = mg.build_report(rows, mg.load_catalog_json())
    assert len(report) == 255
    assert all(r['cat'] for r in report)


def test_report_flags_rows_that_fell_back_to_unknown():
    report = mg.build_report(
        [{'brand': '', 'model': 'CPU针接触不良返场维修一次'},
         {'brand': '微星', 'model': 'B650M-B'}],
        {'parts': [{'brand': '微星', 'name': 'B650M-B', 'cat': 'board', 'aliases': []}],
         'brands': []},
    )
    assert report[0]['cat'] == 'unknown' and report[0]['source'] == 'fallback'
    assert report[1]['cat'] == 'board' and report[1]['source'] == 'part'


def test_apply_changes_only_cat(tmp_path):
    data_file = tmp_path / 'data.json'
    original = [{'brand': '微星', 'model': 'B650M-B', 'cost': 600, 'images': []}]
    data_file.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    catalog = {'brands': [], 'parts': [{'brand': '微星', 'name': 'B650M-B',
                                        'cat': 'board', 'aliases': []}]}
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
    catalog = {'brands': [], 'parts': []}
    # 人为制造条数不一致：断言必须炸，不能静默写坏
    import pytest
    with pytest.raises(AssertionError):
        mg.run_apply(str(data_file), catalog, expect_count=999)
```

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_migrate_cat.py" -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'migrate_add_cat'`。

- [ ] **Step 2: 写实现**

Create `migrate_add_cat.py`：

```python
# -*- coding: utf-8 -*-
"""给 data.json 每条记录补 cat。默认 dry-run。

    python -X utf8 migrate_add_cat.py            # 打印 255 行报告，不写
    python -X utf8 migrate_add_cat.py --apply    # 他复核过之后才跑

品类来源只有两个：命中的 part 自带的 cat；没命中就 unknown。
不在这里做任何"从名字猜类别"的临场判断 —— 那属于 cat_rules，规则改了要重跑测试。
"""
import argparse
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expect-count', type=int, default=255)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')

    rows = json.load(open(DATA_FILE, encoding='utf-8'))
    catalog = load_catalog_json()
    before_sha = sha256(DATA_FILE)
    report = build_report(rows, catalog)

    import collections
    counts = collections.Counter(e['cat'] for e in report)
    print('品类分布:', dict(sorted(counts.items(), key=lambda kv: -kv[1])))
    print('来源分布:', dict(collections.Counter(e['source'] for e in report)))
    print()
    print('%-5s %-8s %-40s %-8s %s' % ('下标', '品牌', '型号', 'cat', '命中 part / 来源'))
    for e in report:
        print('%-5d %-8s %-40s %-8s %s'
              % (e['index'], e['brand'], e['model'][:40], e['cat'], e['part'] or e['source']))

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
```

- [ ] **Step 3: 应用 catalog 补丁**

前面 Task 3/4 只改了补丁文件，还没写进 `catalog.json`。这一步是真落盘：

Run: `sha256sum catalog.json` （记录执行前值）
Run: `python -X utf8 apply_catalog_patch.py --apply`
Expected: `已写盘：brands=22 parts=114`（parts 具体数字以 high 合并生效为准；medium 未放行时不减）。若报校验错误，**逐条读错误信息**，不要靠删补丁绕过。
Run: `python -X utf8 -m pytest "G:/claude code/tests" -q`
Expected: 全绿 —— 特别是 13 条 resolve 契约用例和 `test_cat_rules` 的 §3 计数。

- [ ] **Step 4: 确认 catalog 里每条 part 都有 cat**

```bash
python -X utf8 -c "
import json;c=json.load(open('catalog.json',encoding='utf-8'))
bad=[p for p in c['parts'] if not p.get('cat') or p['cat']=='unknown']
print('parts=%d 其中 unknown/缺=%d'%(len(c['parts']),len(bad)))
for p in bad: print(' ', p['brand'],'|',p['name'])
"
```

Expected: `unknown` 只剩他明确允许留着的（目标 ≤ 2）。

- [ ] **Step 5: 出 dry-run 报告并交他复核（阻塞点）**

Run: `python -X utf8 migrate_add_cat.py > p3_migration_report.txt`
Run: `sha256sum data.json`
Expected: sha 仍是 `fd50d857...`；文件首两行是品类分布与来源分布。
**把报告交给他，明确说：`--apply` 我等你一句话才跑。** 未获同意不得执行 Step 6。

- [ ] **Step 6: 他同意之后才做**

```bash
git -C "G:/claude code" add data.json
git -C "G:/claude code" commit -m "checkpoint: 迁移写盘前的 data.json"
python -X utf8 migrate_add_cat.py --apply
sha256sum data.json
```
Expected: 脚本打印三重断言通过。回滚手段：`git -C "G:/claude code" checkout HEAD~1 -- data.json`。

- [ ] **Step 7: 提交脚本与测试**

```bash
git -C "G:/claude code" add migrate_add_cat.py tests/test_migrate_cat.py p3_migration_report.txt
git -C "G:/claude code" commit -m "feat(ledger): cat 存量迁移脚本（默认 dry-run + 三重断言）"
```

---

### Task 8: 前端 —— 品类维度与「品类待确认」收尾

**Files:**
- Modify: `index.html`（`parseItems`、筛选区、行渲染、编辑表单）
- Test: `tests/test_frontend_contract.py`（已存在的静态契约测试文件；若名字不同，用现有那个）

- [ ] **Step 1: 写失败静态契约测试**

```python
def test_cat_is_read_and_the_pending_filter_exists():
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    assert 'cat: r.cat' in html or 'cat: (r.cat' in html, "parseItems 没带出 cat，品类筛选读不到值"
    assert '品类待确认' in html, "没有「品类待确认」入口"
    assert "cat: 'unknown'" in html or 'cat === \'unknown\'' in html or 'UNKNOWN_CAT' in html
```

Run: `python -X utf8 -m pytest "G:/claude code/tests" -q -k cat_is_read`
Expected: FAIL —— `parseItems 没带出 cat`。

- [ ] **Step 2: `parseItems` 带出 cat**

在 `parseItems` 的 map 输出里（紧跟已有的 `cat`/`canonicalModel` 附近）确保：

```javascript
    cat: (r.cat || '').trim(),
```

> 已有实现是 `cat: r.cat || ''`；若已在，本步只需确认没有别处覆盖掉它，然后直接进 Step 3，并在提交信息里说明"该步为确认而非新增"。别为了改而改。

- [ ] **Step 3: 「品类待确认」chip + 筛选**

复用任务 #11 的"未补售价"可发现机制（同一个位置、同一个样式类）。在既有 `noSell` 筛选旁边加：

```javascript
// 品类待确认 = 记录自己没有 cat，且按知识库也归不出品类。
// 归不出品类的行必须留在视野里，否则 §3 里的「unknown 是兜底但不是垃圾桶」落不了地。
function pendingCat(i) {
  if (i.cat) return false;
  const part = resolvePart(i.brand, i.model);
  return !part || !part.cat || part.cat === 'unknown';
}
```

把 chip 文案与计数接进筛选状态（与"未补售价"同一套 state 开关），点击后把列表筛成 `pendingCat(i)` 为真的行。

- [ ] **Step 4: 行内改判下拉**

行内渲染一个 `<select>`，8 个品类，值来自 `CATALOG.categories`（已在 `loadCatalog()` 里取回）；change 时：

```javascript
  const payload = { cat: sel.value };
  // 走既有 update 通道，带 If-Match；409 交给 handleConflict，不要在这里吞掉
  await saveRowPatch(index, payload);
```

若 `saveRowPatch` 这个名字在本仓库不存在，就用既有的那条"行内改售价"的函数（Task 8 执行时先在 `index.html` 里 grep 出真正的函数名，把名字对上，**不要新造一套写盘逻辑**）。

- [ ] **Step 5: 端到端验证（自己起服务、自己点）**

```bash
python -X utf8 -c "
import app_standalone as m, threading, json, tempfile, os
d=json.load(open('data.json',encoding='utf-8'))
t=tempfile.mkdtemp(); open(os.path.join(t,'data.json'),'w',encoding='utf-8').write(json.dumps(d[:5],ensure_ascii=False))
m.DATA_FILE=os.path.join(t,'data.json'); m.IMAGES_DIR=os.path.join(t,'images'); m.PORT=8792
s=m.LedgerServer((m.HOST,m.PORT), m.APIHandler); threading.Thread(target=s.serve_forever,daemon=True).start()
print('fixture on 8792, rows', len(json.load(open(m.DATA_FILE,encoding='utf-8'))))
import time; time.sleep(600)
"
```

用 chrome-devtools MCP 打开 `http://127.0.0.1:8792`，实测：chip 出现且计数正确 → 点开 → 行内下拉改判 → 保存 → 刷新页面值还在 → 该条的 `cat` 落到磁盘。截图给他。**如果 `take_screenshot` 在这个场景失败，改用 `evaluate_script` 取 DOM 断言并把结果原文贴给他，不要只说"验证通过"。**

- [ ] **Step 6: 跑全量测试 + 提交**

Run: `python -X utf8 -m pytest "G:/claude code/tests" -q` → 全绿
Run: `sha256sum data.json` → 若 Task 7 Step 6 已 apply，sha 是新值；若还没 apply，必须仍是 `fd50d857...`

```bash
git -C "G:/claude code" add index.html tests/test_frontend_contract.py
git -C "G:/claude code" commit -m "feat(ledger): 品类维度接入统计与「品类待确认」可发现/可改判"
```

---

### Task 9: 重启窗口 + 交付验收

- [ ] **Step 1: 重启前检查**

```bash
git -C "G:/claude code" status --porcelain -- . | grep -v '^??' ; echo "(上面空=跟踪区干净)"
sha256sum data.json
powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess"
```

- [ ] **Step 2: 征求他同意后重启**

```bash
powershell -NoProfile -Command "Stop-Process -Id <PID> -Force"
cd "G:/claude code" && python app_standalone.py   # run_in_background
```

- [ ] **Step 3: 核对（把实际输出贴给他，别总结成"正常"）**

```bash
curl -s -D - -o NUL http://127.0.0.1:8765/api/catalog | head -6
python -X utf8 -c "
import json,collections,urllib.request
c=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/catalog'))
print('brands=%d parts=%d 有别名品牌=%d'%(len(c['brands']),len(c['parts']),
  sum(1 for b in c['brands'] if b['aliases'])))
d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/data'))
items=d['items'] if isinstance(d,dict) and 'items' in d else d
print('records=%d 有cat=%d'%(len(items),sum(1 for i in items if i.get('cat'))))
print('cat 分布', dict(collections.Counter(i.get('cat') or 'none' for i in items)))
"
```

Expected: `brands=22`、品牌别名数 > 0、`records=255`、`cat` 分布等于 §3 的 8 个数字（apply 之后）。

- [ ] **Step 4: 关掉本轮临时产物**

`p3_draft_parts.txt`、`p3_migration_report.txt` 若已提交则留着，未提交的临时文件删掉自己生成的那份。停止 8792 夹具进程（`Get-NetTCPConnection -LocalPort 8792` 找 PID 再 `Stop-Process`），并确认它监听数归 0。

---

## 自检结论（写完后回看规格）

- **§4 第二遍**（品牌归一、同物异名、定 cat、空 model 不生成 part）→ Task 1/3/4/5。
- **§9 六步**：检查点→dry-run→判定顺序→人工复核→三重断言→unknown 收尾，逐条落在 Task 7 Step 5/6 与 Task 8。
- **§3 计数**：钉成 `test_every_record_classifies_to_expected_distribution`。
- **§12 P3 验收**（255 条都有 cat、unknown 为他认可的残余）→ Task 7 Step 4/6 + Task 9 Step 3。
- **§13 风险表**：迁移写坏账本（git 检查点 + 三重断言）；`新增字段漏传`（Task 6，本计划新发现的 `handle_add_record` 固定字段表风险）。
- **不在 P3**：录入搜索框（P2）、配件库管理页与 `/api/catalog/delete`（P4）、闲鱼导入带 cat（§10，属 P2/P4 之后的独立一段）。
- 规格 §11 里"删除被引用的 part → 400"依赖 delete 端点，属 P4，本计划未包含。
