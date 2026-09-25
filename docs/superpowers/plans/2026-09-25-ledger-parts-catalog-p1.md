# 配件知识库 P1（catalog + 归并）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建出 `catalog.json` 知识库与「按规范名归并」的解析器，让品牌总览 / 型号总览不再被大小写和别名拆开（`H610m-E` 9 笔 + `H610m-e` 6 笔 → 15 笔一行）。

**Architecture:** 后端新增一份与 `data.json` 平行的 `catalog.json`，走同一套 `file_stamp` + `If-Match` 版本协议；归并规则（精确命中 → 最长前缀命中）在 Python 里实现一份、在 `index.html` 里镜像一份，两边共用一个用例表 `tests/fixtures/resolve_cases.json` 钉死行为。本阶段不动任何表单、不新增记录字段、不改写 `model` 的历史值。

**Tech Stack:** Python 3 标准库（`http.server`、`json`、`hashlib`）、pytest、原生 JS（单文件 `index.html`）、无构建工具、无新依赖。

**规格来源：** `docs/superpowers/specs/2026-09-25-ledger-parts-catalog-design.md` §3–§6、§12 P1。

**后续计划：** P2（录入选择器）、P3（存量补 `cat` + 复核）、P4（配件库管理页）各自单开一份计划。P3 需要他复核 dry-run 报告，不能和本阶段合并提交。

**与规格 §12 的偏差（有意为之）：** 规格把 P1 的后端写成「catalog 只读接口」，本计划多做了一个 `POST /api/catalog/upsert`（Task 4）。理由：Task 5 播种后的人工第二遍要合并同物异名，有 upsert 才能用脚本改而不是手改 JSON。规格 §6 的第三个端点 `/api/catalog/delete` **不在本阶段做**，它属于 P4 管理页。

**两个解析器签名不同是有意的：** Python 侧 `resolve_part(catalog, brand, model)` 显式传库，JS 侧 `resolvePart(brand, model)` 读模块级 `CATALOG`。行为一致性由 `tests/fixtures/resolve_cases.json` 单一用例表钉住，不要在任一侧偷偷加规则。

---

## 既有事实（写代码前必须知道的）

| 位置 | 事实 |
|---|---|
| `app_standalone.py:26-46` | `PORT`、`APP_DIR`、`DATA_FILE`、`ORDER_CONTEXT_KEYS` 都在模块级；`os.chdir(BUNDLE_DIR)` |
| `app_standalone.py:55-65` | `file_stamp()` 无参，返回 `'%d-%s' % (len, sha256[:16])`，文件不存在返回 `''` |
| `app_standalone.py:73-88` | `save_data(data, stamp=None)`：stamp 不符抛 `WriteConflict`；写盘走 `.tmp` + `os.replace` |
| `app_standalone.py:345-379` | `do_GET` 是 `if/elif` 链；`do_POST` 同上，未匹配的路径回 404 |
| `app_standalone.py:784-799` | `reject_if_client_stale(stamp=None)`：`If-Match` 缺席一律放行；不等则回 409 返回 True |
| `app_standalone.py:832-843` | `send_json(data, status=200, extra_headers=None)`：只加头不改体形状 |
| `tests/conftest.py:52-70` | session 级 `ledger_sandbox` 把 `DATA_FILE` 指向临时目录，teardown 断言真实 `data.json` 的 sha 没变 |
| `tests/conftest.py:73-130` | `api` fixture 返回 `(call, read_ledger, data_path)`；`call(method, path, payload=None, raw_body=None, with_headers=False, if_match=None)` |
| `tests/conftest.py:30-37` | `SEED` 两条记录：微星 B650M GAMING WIFI、光威 神策 16G |
| `index.html:1177-1187` | `parseItems` 的 filter 只留 brand/model 非全空的行；`.map` 里逐字段显式取值 |
| `index.html:1390-1397` | `buildBrands()` 用 `map[i.brand]` 和 `models[i.model]` 直接当键 |
| `index.html:1447` | `brandIcon(brand)` 查 `BRAND_STYLES[brand.toLowerCase()]`，查不到掉灰色兜底 |
| `index.html:1611-1612` | 型号总览键是 `i.brand + '|||' + i.model` |

**跑测试的命令（中文断言必须带 `-X utf8`）：**

```bash
python -X utf8 -m pytest "G:/claude code/tests/test_ledger_api.py" -q
```

当前基线：**176 passed in 83.55s**（2026-09-25 实跑）。每个任务结束时这个数只增不减。

---

### Task 1: catalog 文件读写与 stamp

**Files:**
- Create: `catalog.json`（本任务先写一个只含品类与两个品牌的最小版）
- Modify: `app_standalone.py`（在 `save_data` 之后插入 catalog 段）
- Test: `tests/test_catalog.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_catalog.py`：

```python
"""catalog.json 读写、版本头与归并解析器的后端行为。"""
import json
import os

import conftest  # 复用 ROOT / read_json
import pytest

app_standalone = pytest.importorskip("app_standalone")


@pytest.fixture
def catalog_file(monkeypatch, tmp_path):
    """把 CATALOG_FILE 指向 tmp，真实 catalog.json 在测试里不可达。"""
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(app_standalone, "CATALOG_FILE", str(path))
    return path


def test_load_catalog_without_file_returns_empty_skeleton(catalog_file):
    catalog = app_standalone.load_catalog()
    assert catalog["version"] == 1
    assert [c["key"] for c in catalog["categories"]][:3] == ["board", "cpu", "ram"]
    assert catalog["parts"] == []


def test_catalog_stamp_changes_when_content_changes(catalog_file):
    catalog_file.write_text('{"version": 1, "parts": []}', encoding="utf-8")
    before = app_standalone.catalog_stamp()
    assert before == "%d-%s" % (
        catalog_file.stat().st_size,
        __import__("hashlib").sha256(catalog_file.read_bytes()).hexdigest()[:16],
    )
    catalog_file.write_text('{"version": 1, "parts": [1]}', encoding="utf-8")
    assert app_standalone.catalog_stamp() != before


def test_save_catalog_rejects_stale_stamp(catalog_file):
    app_standalone.save_catalog(app_standalone.load_catalog())
    fresh = app_standalone.catalog_stamp()
    catalog_file.write_text('{"version": 1, "parts": []}', encoding="utf-8")
    with pytest.raises(app_standalone.WriteConflict):
        app_standalone.save_catalog(app_standalone.load_catalog(), fresh)


def test_save_catalog_does_not_touch_data_json(catalog_file, monkeypatch):
    """写知识库绝不能顺手把账本的 .bak 轮转掉 —— 两者是完全独立的两份文件。"""
    data_file = str(catalog_file.parent / "data.json")
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump([{"brand": "微星", "model": "B650m-b", "cost": 1}], f)
    monkeypatch.setattr(app_standalone, "DATA_FILE", data_file)
    app_standalone.save_catalog(app_standalone.load_catalog())
    assert not os.path.exists(data_file + ".bak")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `AttributeError: module 'app_standalone' has no attribute 'CATALOG_FILE'`（4 条全 error）

- [ ] **Step 3: 写最小实现**

`app_standalone.py`，在 `save_data` 之后（第 89 行空行处）插入：

```python
# ─── Parts catalog（品类/品牌/型号知识库）───
# 与 data.json 完全平行的一份文件：账本只存文本，规范名和别名都放这里，
# 所以同一块板子的 5 种写法不需要改写历史数据也能并成一条统计。

CATALOG_CATEGORIES = [
    {'key': 'board', 'name': '主板'},
    {'key': 'cpu', 'name': 'CPU'},
    {'key': 'ram', 'name': '内存'},
    {'key': 'ssd', 'name': '固态硬盘'},
    {'key': 'cooler', 'name': '散热'},
    {'key': 'gpu', 'name': '显卡'},
    {'key': 'bundle', 'name': '板U套装'},
    {'key': 'unknown', 'name': '待确认'},
]

CATALOG_FILE = os.path.join(APP_DIR, 'catalog.json')


def _empty_catalog():
    """每次新建，不给调用方共享可变的默认值。"""
    return {
        'version': 1,
        'categories': [dict(c) for c in CATALOG_CATEGORIES],
        'brands': [],
        'parts': [],
    }


def catalog_stamp():
    """catalog.json 的版本 token，算法与 file_stamp 同源但对象不同一份文件。"""
    if not os.path.exists(CATALOG_FILE):
        return ''
    with open(CATALOG_FILE, 'rb') as f:
        blob = f.read()
    return '%d-%s' % (len(blob), hashlib.sha256(blob).hexdigest()[:16])


def load_catalog():
    """读知识库；文件还没建时返回空骨架，让首次启动不至于 500。"""
    if not os.path.exists(CATALOG_FILE):
        return _empty_catalog()
    with open(CATALOG_FILE, 'r', encoding='utf-8') as f:
        catalog = json.load(f)
    if not isinstance(catalog, dict) or not isinstance(catalog.get('parts'), list):
        raise ValueError('catalog.json must be an object with a parts list')
    return catalog


def save_catalog(catalog, stamp=None):
    """整份写知识库。不轮转任何 .bak：知识库的误改从 git 回滚，账本的 .bak 只有一份、别乱占。"""
    payload = json.dumps(catalog, ensure_ascii=False, indent=2)
    if stamp is not None and catalog_stamp() != stamp:
        raise WriteConflict('catalog.json changed since it was read')
    tmp = CATALOG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(payload)
    os.replace(tmp, CATALOG_FILE)
```

再建最小版 `catalog.json`（Task 2 会把它填满）：

```bash
cd "G:/claude code" && python -X utf8 -c "
import json, app_standalone as m
json.dump(m._empty_catalog(), open('catalog.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)
print('seeded empty catalog')
"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `4 passed`

- [ ] **Step 5: 提交**

```bash
cd "G:/claude code" && git add app_standalone.py catalog.json tests/test_catalog.py && git commit -m "feat(ledger): catalog.json 知识库读写与独立版本 token"
```

---

### Task 2: 归并解析器（精确 + 最长前缀）

**Files:**
- Modify: `app_standalone.py`（Task 1 那段之后）
- Create: `tests/fixtures/resolve_cases.json`
- Test: `tests/test_catalog.py`

- [ ] **Step 1: 先写用例表（它就是前后端共用的契约）**

新建 `tests/fixtures/resolve_cases.json`。`model` 全部取自 `data.json` 的真实值：

```json
{
  "brands": [
    { "canonical": "微星", "aliases": ["MSI"] },
    { "canonical": "英特尔", "aliases": ["INTER", "Intel"] },
    { "canonical": "铠侠", "aliases": ["凯侠"] }
  ],
  "parts": [
    { "cat": "board", "brand": "微星", "name": "B650M-B", "aliases": ["B650m-b", "b650m-b", "PRO B650M-B 主板"] },
    { "cat": "board", "brand": "微星", "name": "B650M-B PRO", "aliases": [] },
    { "cat": "board", "brand": "微星", "name": "H610M-E DDR4", "aliases": ["H610m-E", "H610m-e", "PRO H610M-E DDR4主板"] },
    { "cat": "board", "brand": "微星", "name": "B650M 迫击炮 WIFI", "aliases": ["B650m迫击炮wifi", "MAG B650M MORTAR WIFI"] },
    { "cat": "board", "brand": "铭瑄", "name": "B760M 终结者 D4 WIFI", "aliases": ["B760m 终结者D4 wifi", "B760M-D4终结者主板"] },
    { "cat": "ssd", "brand": "铠侠", "name": "SD10 2TB", "aliases": ["sd10 2t"] },
    { "cat": "board", "brand": "英特尔", "name": "B660", "aliases": [] }
  ],
  "cases": [
    { "brand": "微星", "model": "B650m-b", "name": "B650M-B", "cat": "board" },
    { "brand": "微星", "model": "H610m-e", "name": "H610M-E DDR4", "cat": "board" },
    { "brand": "微星", "model": "H610m-E", "name": "H610M-E DDR4", "cat": "board" },
    { "brand": "微星", "model": "PRO H610M-E DDR4主板", "name": "H610M-E DDR4", "cat": "board" },
    { "brand": "微星", "model": "b650m-b 爆破弹 带挡板", "name": "B650M-B", "cat": "board" },
    { "brand": "微星", "model": "b650m-b pro", "name": "B650M-B PRO", "cat": "board" },
    { "brand": "微星", "model": "mag b650m mortar 针脚坏", "name": "B650M 迫击炮 WIFI", "cat": "board" },
    { "brand": "铭瑄", "model": "B760M-D4终结者主板", "name": "B760M 终结者 D4 WIFI", "cat": "board" },
    { "brand": "凯侠", "model": "sd10 2t", "name": "SD10 2TB", "cat": "ssd" },
    { "brand": "INTER", "model": "B660", "name": "B660", "cat": "board" },
    { "brand": "微星", "model": "X870E CARBON WIFI", "name": null, "cat": null },
    { "brand": "微星", "model": "", "name": null, "cat": null }
  ]
}
```

`b650m-b pro` 这条就是最长前缀优先的证据：它同时以 `B650M-B` 开头，但必须落到 `B650M-B PRO`。

- [ ] **Step 2: 写失败测试**

`tests/test_catalog.py` 追加：

```python
FIXTURE = json.loads(
    (os.path.join(conftest.ROOT, "tests", "fixtures", "resolve_cases.json"))
    and open(os.path.join(conftest.ROOT, "tests", "fixtures", "resolve_cases.json"), encoding="utf-8").read()
)


def _fixture_catalog():
    return {
        "version": 1,
        "categories": [dict(c) for c in app_standalone.CATALOG_CATEGORIES],
        "brands": [dict(b) for b in FIXTURE["brands"]],
        "parts": [dict(p) for p in FIXTURE["parts"]],
    }


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["model"] or "empty" for c in FIXTURE["cases"]])
def test_resolve_part_matches_shared_cases(case):
    got = app_standalone.resolve_part(_fixture_catalog(), case["brand"], case["model"])
    if case["name"] is None:
        assert got is None, "未命中的必须返回 None，不能猜一个最近的"
    else:
        assert got is not None
        assert got["name"] == case["name"]
        assert got["cat"] == case["cat"]
        assert got["brand"] == app_standalone.resolve_brand(_fixture_catalog(), case["brand"])


def test_resolve_brand_falls_back_to_given_text():
    catalog = _fixture_catalog()
    assert app_standalone.resolve_brand(catalog, "inter") == "英特尔"
    assert app_standalone.resolve_brand(catalog, "  ") == ""
    assert app_standalone.resolve_brand(catalog, "朗孜") == "朗孜"


def test_norm_key_ignores_case_spaces_and_fullwidth_parens():
    assert app_standalone.norm_key("H610M-E") == app_standalone.norm_key("h610m-e ")
    assert app_standalone.norm_key("B650M（迫击炮）") == app_standalone.norm_key("b650m(迫击炮)")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `AttributeError: module 'app_standalone' has no attribute 'norm_key'`

- [ ] **Step 4: 写实现**

`app_standalone.py`，接在 Task 1 那段之后：

```python
def norm_key(text):
    """归一化比较键：大写、去所有空白、全角括号转半角。
    大小写与空格是账本里最主要的重复来源（H610m-E / H610m-e 就是两种写法）。"""
    s = (text or '').upper()
    s = s.replace('（', '(').replace('）', ')').replace('　', '')
    return ''.join(s.split())


def resolve_brand(catalog, brand):
    """把 brand 折成 canonical 写法；库里没有就原样返回，绝不猜。"""
    needle = norm_key(brand)
    if not needle:
        return (brand or '').strip()
    for entry in catalog.get('brands', []):
        if norm_key(entry.get('canonical')) == needle:
            return entry['canonical']
        for alias in entry.get('aliases', []):
            if norm_key(alias) == needle:
                return entry['canonical']
    return (brand or '').strip()


def resolve_part(catalog, brand, model):
    """model → 库里的 part；命中不了返回 None。

    两趟，且必须在同一趟品牌候选里比：
    1) 精确：N(model) 等于 N(name) 或某个 N(alias)；
    2) 最长前缀：N(model) 以某个 N(name)/N(alias) 开头，取匹配串最长的那个 ——
       这样 `b650m-b pro` 落到 B650M-B PRO 而不是被 B650M-B 截走，
       带尾注的 `mag b650m mortar 针脚坏` 也能并回规范名。
    """
    canonical = resolve_brand(catalog, brand)
    needle = norm_key(model)
    if not needle:
        return None
    candidates = []
    for part in catalog.get('parts', []):
        if part.get('brand') != canonical:
            continue
        for key, source in _part_keys(part):
            if key == needle:
                return part
            if key and needle.startswith(key):
                candidates.append((len(key), part))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return None


def _part_keys(part):
    """part 自己参与的匹配键：规范名 + 全部别名。"""
    yield norm_key(part.get('name')), 'name'
    for alias in part.get('aliases', []):
        yield norm_key(alias), 'alias'
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `18 passed`（4 + 12 条参数化 + 2）

- [ ] **Step 6: 提交**

```bash
cd "G:/claude code" && git add app_standalone.py tests/test_catalog.py tests/fixtures/resolve_cases.json && git commit -m "feat(ledger): 知识库归并解析器（精确 + 最长前缀），行为钉在共用用例表"
```

---

### Task 3: `GET /api/catalog`

**Files:**
- Modify: `app_standalone.py:349-362`（`do_GET` 的 if/elif 链）
- Test: `tests/test_catalog.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.fixture
def api_catalog(monkeypatch, api, tmp_path):
    """复用 conftest.api 起的服务，只把 CATALOG_FILE 换进同一次往返的临时目录。"""
    call, read_ledger, data_path = api
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"version": 1,
                                "categories": [dict(c) for c in app_standalone.CATALOG_CATEGORIES],
                                "brands": [], "parts": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app_standalone, "CATALOG_FILE", str(path))
    return call, path


def test_get_catalog_returns_body_and_stamp_header(api_catalog):
    call, path = api_catalog
    status, body, headers = call("GET", "/api/catalog", with_headers=True)
    assert status == 200
    assert [c["key"] for c in body["categories"]][:2] == ["board", "cpu"]
    assert headers["X-Catalog-Stamp"] == "%d-%s" % (
        path.stat().st_size,
        __import__("hashlib").sha256(path.read_bytes()).hexdigest()[:16],
    )


def test_get_catalog_without_file_is_200_and_empty_stamp(monkeypatch, api, tmp_path):
    call, _, _ = api
    monkeypatch.setattr(app_standalone, "CATALOG_FILE", str(tmp_path / "nope.json"))
    status, body, headers = call("GET", "/api/catalog", with_headers=True)
    assert status == 200
    assert body["parts"] == []
    assert headers["X-Catalog-Stamp"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py::test_get_catalog_returns_body_and_stamp_header" -q`
Expected: 404（`do_POST`/`do_GET` 里还没有这条路径），断言 `status == 200` 失败

- [ ] **Step 3: 写实现**

`do_GET` 里，紧跟 `/api/data` 那一支之后、`send_html` 那支之前插入：

```python
        elif path == '/api/catalog':
            # 与 /api/data 同样的取舍：先取 stamp 再读文件，宁可虚警不可漏判。
            stamp = catalog_stamp()
            self.send_json(load_catalog(), extra_headers={'X-Catalog-Stamp': stamp})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `20 passed`

- [ ] **Step 5: 提交**

```bash
cd "G:/claude code" && git add app_standalone.py tests/test_catalog.py && git commit -m "feat(ledger): GET /api/catalog 带 X-Catalog-Stamp"
```

---

### Task 4: `POST /api/catalog/upsert`

**Files:**
- Modify: `app_standalone.py:364-379`（`do_POST`）与 `APIHandler` 内新增 handler
- Test: `tests/test_catalog.py`

- [ ] **Step 1: 写失败测试**

```python
def _upsert(call, payload, if_match=None):
    return call("POST", "/api/catalog/upsert", payload, if_match=if_match)


def test_upsert_creates_part_and_brand(api_catalog):
    call, path = api_catalog
    status, body, _ = _upsert(call, {"brand": "微星", "name": "B850M GAMING PLUS", "cat": "board"})
    assert status == 200 and body["ok"] is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [p["name"] for p in saved["parts"]] == ["B850M GAMING PLUS"]
    assert saved["parts"][0]["brand"] == "微星"
    assert {b["canonical"] for b in saved["brands"]} >= {"微星"}


def test_upsert_applies_brand_alias_and_appends_new_aliases(api_catalog):
    call, path = api_catalog
    _upsert(call, {"brand": "凯侠", "name": "VD10 1TB", "cat": "ssd", "aliases": ["vd10 1t"]})
    saved = json.loads(path.read_text(encoding="utf-8"))
    part = saved["parts"][0]
    assert part["brand"] == "铠侠"            # 走 brands 别名归一后落库
    assert part["aliases"] == ["vd10 1t"]


def test_upsert_rename_pushes_old_name_into_aliases(api_catalog):
    call, path = api_catalog
    _upsert(call, {"brand": "微星", "name": "B650M-B", "cat": "board"})
    status, _, _ = _upsert(call, {"brand": "微星", "old_name": "B650M-B", "name": "B650M BOMBER", "cat": "board"})
    assert status == 200
    part = json.loads(path.read_text(encoding="utf-8"))["parts"][0]
    assert part["name"] == "B650M BOMBER"
    assert "B650M-B" in part["aliases"]      # 历史记录还能归并过来


def test_upsert_rejects_unknown_cat(api_catalog):
    call, path = api_catalog
    status, body, _ = _upsert(call, {"brand": "微星", "name": "X", "cat": "sound"})
    assert status == 400 and "cats" in body
    assert json.loads(path.read_text(encoding="utf-8"))["parts"] == []   # 一条都不写


def test_upsert_requires_brand_and_name(api_catalog):
    call, _ = api_catalog
    assert _upsert(call, {"brand": "", "name": "B", "cat": "board"})[0] == 400
    assert _upsert(call, {"brand": "微星", "name": "  ", "cat": "board"})[0] == 400


def test_upsert_honours_if_match_and_allows_absent(api_catalog):
    call, path = api_catalog
    _upsert(call, {"brand": "微星", "name": "A", "cat": "board"})
    stale = "0-deadbeef"
    status, body, _ = _upsert(call, {"brand": "微星", "name": "B", "cat": "board"}, if_match=stale)
    assert status == 409 and body["ok"] is False
    fresh = app_standalone.catalog_stamp()
    status, _, _ = _upsert(call, {"brand": "微星", "name": "B", "cat": "board"}, if_match=fresh)
    assert status == 200                      # If-Match 缺席放行（导入页与 agent 不知道版本）


def test_upsert_accepts_bad_json_as_400(api_catalog):
    call, _ = api_catalog
    status, _, _ = call("POST", "/api/catalog/upsert", raw_body=b"{not json")
    assert status == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q -k upsert`
Expected: 全部 404（路由还没有）→ 断言 200/400 处失败

- [ ] **Step 3: 写实现**

`do_POST` 里 `/api/data` 那支之后插入：

```python
        elif path == '/api/catalog/upsert':
            self.handle_catalog_upsert()
```

`APIHandler` 内，紧跟 `handle_split_record` 之后新增：

```python
    def handle_catalog_upsert(self):
        """新增或就地改一条 part。整批一次写盘：任何校验不过都一条都不写。

        old_name 是改名的痕迹 —— 旧规范名自动进 aliases，否则历史记录瞬间归并不上，
        品牌总览会凭空多出一行。
        """
        body = self.read_body()
        if body is None:
            self.send_json({'ok': False, 'error': 'invalid json body'}, 400)
            return
        brand = (body.get('brand') or '').strip()
        name = (body.get('name') or '').strip()
        cat = (body.get('cat') or '').strip()
        if not brand or not name:
            self.send_json({'ok': False, 'error': 'brand and name are required'}, 400)
            return
        known = [c['key'] for c in CATALOG_CATEGORIES]
        if cat not in known:
            self.send_json({'ok': False, 'error': 'unknown cat %r' % cat, 'cats': known}, 400)
            return

        stamp = catalog_stamp()
        if self.reject_if_client_stale(stamp):
            return
        catalog = load_catalog()
        canonical = resolve_brand(catalog, brand)

        if canonical not in [b.get('canonical') for b in catalog['brands']]:
            catalog['brands'].append({'canonical': canonical, 'aliases': []})

        part = next(
            (p for p in catalog['parts']
             if p.get('brand') == canonical and norm_key(p.get('name')) == norm_key(name)),
            None,
        )
        if part is None:
            part = {'cat': cat, 'brand': canonical, 'name': name, 'aliases': []}
            catalog['parts'].append(part)

        keep = [(body.get('old_name') or '').strip()] + [
            (a or '').strip() for a in (body.get('aliases') or [])
        ]
        for extra in keep:
            if extra and norm_key(extra) != norm_key(part['name']) and extra not in part['aliases']:
                part['aliases'].append(extra)
        part['cat'] = cat

        try:
            save_catalog(catalog, stamp)
        except WriteConflict:
            self.handle_write_conflict()
            return
        self.send_json({'ok': True, 'part': part})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `28 passed`

- [ ] **Step 5: 提交**

```bash
cd "G:/claude code" && git add app_standalone.py tests/test_catalog.py && git commit -m "feat(ledger): catalog upsert（改名留别名、cat 白名单、If-Match 版本闸）"
```

---

### Task 5: 播种脚本（127 组 → 初稿库）

**Files:**
- Create: `seed_catalog.py`
- Test: `tests/test_catalog.py`

- [ ] **Step 1: 写失败测试**

```python
def test_seed_groups_variant_writings_under_their_own_norm_key(tmp_path):
    """同一种写法归一后只剩一个 part，且组内最高频的原写法当 name。"""
    seed = tmp_path / "data.json"
    seed.write_text(json.dumps([
        {"brand": "微星", "model": "H610m-E", "cost": 1},
        {"brand": "微星", "model": "H610m-e", "cost": 1},
        {"brand": "微星", "model": "H610m-e", "cost": 1},
    ], ensure_ascii=False), encoding="utf-8")
    catalog = seed_catalog.build_catalog(seed_catalog.load_pairs(str(seed)))
    parts = [p for p in catalog["parts"] if p["brand"] == "微星"]
    assert len(parts) == 1
    assert parts[0]["name"] == "H610m-e"        # 出现 2 次的那写法胜出
    assert parts[0]["aliases"] == ["H610m-E"]


def test_seed_marks_unknown_cat_instead_of_guessing(tmp_path):
    """播种阶段不许判品类：判类是 P3 迁移的活，那里要他复核 dry-run。"""
    seed = tmp_path / "data.json"
    seed.write_text(json.dumps([{"brand": "微星", "model": "X99 carbon", "cost": 1}]), encoding="utf-8")
    catalog = seed_catalog.build_catalog(seed_catalog.load_pairs(str(seed)))
    assert catalog["parts"][0]["cat"] == "unknown"


def test_seed_is_deterministic(tmp_path):
    seed = tmp_path / "data.json"
    seed.write_text(json.dumps([
        {"brand": "微星", "model": "B650m-b", "cost": 1},
        {"brand": "铭瑄", "model": "B760挑战者", "cost": 1},
    ], ensure_ascii=False), encoding="utf-8")
    pairs = seed_catalog.load_pairs(str(seed))
    a = json.dumps(seed_catalog.build_catalog(pairs), ensure_ascii=False, sort_keys=True)
    b = json.dumps(seed_catalog.build_catalog(list(reversed(pairs))), ensure_ascii=False, sort_keys=True)
    assert a == b                                # 顺序不影响输出，便于 git diff 复核


def test_seed_produces_no_duplicate_canonical_keys(tmp_path):
    """播种的产物若还有两 part 在归一后同键，说明有一组写法漏并了 —— 这才是会出事的地方。
    （反过来，"用同一份 data.json 播的库当然命中自己每条 model" 是套套逻辑，不测。）"""
    pairs = seed_catalog.load_pairs(conftest.REAL_DATA_FILE)
    catalog = seed_catalog.build_catalog(pairs)
    keys = [(p["brand"], app_standalone.norm_key(p["name"])) for p in catalog["parts"]]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert dupes == set()
    assert len(catalog["parts"]) == 122        # 规格 §4 实测值，漂了要停下来查
```

`tests/test_catalog.py` 顶部还要加两行导入：

```python
import seed_catalog
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q -k seed`
Expected: `ModuleNotFoundError: No module named 'seed_catalog'`

- [ ] **Step 3: 写实现**

新建 `seed_catalog.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_catalog.py" -q`
Expected: `32 passed`

- [ ] **Step 5: 真跑一遍 dry-run 看数字对不对**

```bash
cd "G:/claude code" && python -X utf8 seed_catalog.py
```
Expected: `组合 255 对 -> part 122 条，品牌 24 个`（part 数若偏离 122，说明 `norm_key` 和规格实测不一致，停下来查，不要继续）

- [ ] **Step 6: 提交**

```bash
cd "G:/claude code" && git add seed_catalog.py tests/test_catalog.py && git commit -m "feat(ledger): catalog 播种脚本（127 组 -> 122 part，品类一律留待人工）"
```

---

### Task 6: 前端接上归并（统计不再被拆开）

**Files:**
- Modify: `index.html`（`parseItems` 之后新增 resolver；`buildBrands` `:1390-1397`、型号总览 `:1611`、`BRAND_STYLES` `:1447` 改用规范名）
- Test: `tests/test_ledger_api.py`（静态契约段 `:1169-1270` 之后追加）

- [ ] **Step 1: 写失败测试**

`tests/test_ledger_api.py` 追加：

```python
def test_frontend_ships_a_catalog_resolver():
    """JS 侧必须有与 Python 同一个两趟规则，否则统计还是按脏值算。"""
    src = _read_index_html()
    assert "function normKey(" in src
    assert "function resolvePart(" in src
    assert "startsWith(key)" in src          # 最长前缀那一趟
    assert "candidates" in src


def test_frontend_aggregates_statistics_by_canonical_names():
    src = _read_index_html()
    assert "map[i.canonicalBrand]" in src            # buildBrands 用规范品牌当键
    assert "i.canonicalModel" in src                 # 型号明细/型号总览用规范型号
    assert "i.brand + '|||' + i.model" not in src    # 老的脏键必须消失


def test_resolve_cases_fixture_matches_js_contract_shape():
    """用例表是前后端唯一的契约，字段名一改两边都会瞎。"""
    cases = _read_resolve_cases()["cases"]
    assert cases, "用例表不能为空"
    for case in cases:
        assert set(case) == {"brand", "model", "name", "cat"}


def test_brand_styles_cover_every_seeded_canonical_brand():
    """品牌归一到中文后，BRAND_STYLES 缺键会让图标掉成灰色兜底。"""
    src = _read_index_html()
    catalog = _read_catalog_json()
    block = src.split("const BRAND_STYLES", 1)[1].split("};", 1)[0]
    missing = [b["canonical"] for b in catalog["brands"]
               if b["canonical"].lower() not in block.lower()]
    assert missing == []


def _read_catalog_json():
    import json, os
    with open(os.path.join(os.path.dirname(__file__), "..", "catalog.json"), encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_ledger_api.py" -q -k "resolver or canonical or resolve_cases or brand_styles"`
Expected: 4 failed（`_read_index_html` / `_read_resolve_cases` 若不存在，一并补上：见 Step 3 第一小步）

- [ ] **Step 3: 写实现**

3a. `tests/test_ledger_api.py` 里若还没有这两个读取器，加在文件末尾：

```python
def _read_index_html():
    import os
    with open(os.path.join(os.path.dirname(__file__), "..", "index.html"), encoding="utf-8") as f:
        return f.read()


def _read_resolve_cases():
    import json, os
    path = os.path.join(os.path.dirname(__file__), "fixtures", "resolve_cases.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
```

3b. `index.html`：在 `parseItems` 定义之前（`:1177` 上方）插入知识库载入与解析器：

```javascript
// ─── Parts catalog：规范名归并 ───
// 规则与 app_standalone.resolve_part 一字不差地对齐，两边共用
// tests/fixtures/resolve_cases.json 钉行为（pytest 跑 Python 侧，浏览器端到端跑这里）。
let CATALOG = { categories: [], brands: [], parts: [] };
const CATALOG_INDEX = new Map();   // normKey(canonicalBrand)+'\u0000'+normKey(key) -> part

function normKey(text) {
  return String(text || '').toUpperCase().replace(/（/g, '(').replace(/）/g, ')').replace(/\s+/g, '');
}

function buildCatalogIndex(catalog) {
  CATALOG_INDEX.clear();
  for (const brand of catalog.brands || []) {
    for (const key of [brand.canonical, ...(brand.aliases || [])]) {
      CATALOG_INDEX.set(normKey(brand.canonical) + '\u0000' + normKey(key), { kind: 'brand', canonical: brand.canonical });
    }
  }
  for (const part of catalog.parts || []) {
    for (const key of [part.name, ...(part.aliases || [])]) {
      CATALOG_INDEX.set(normKey(part.brand) + '\u0000' + normKey(key), { kind: 'part', part });
    }
  }
}

function resolveBrand(brand) {
  const needle = normKey(brand);
  if (!needle) return String(brand || '').trim();
  const hit = CATALOG_INDEX.get(needle + '\u0000' + needle);
  return hit && hit.kind === 'brand' ? hit.canonical : String(brand || '').trim();
}

function resolvePart(brand, model) {
  const canonical = resolveBrand(brand);
  const needle = normKey(model);
  if (!needle) return null;
  const exact = CATALOG_INDEX.get(normKey(canonical) + '\u0000' + needle);
  if (exact && exact.kind === 'part') return exact.part;
  // 第二趟：前缀命中取最长，带尾注的原值才能并回规范名
  const candidates = [];
  const prefix = normKey(canonical) + '\u0000';
  for (const [key, entry] of CATALOG_INDEX) {
    if (entry.kind !== 'part' || entry.part.brand !== canonical || !key.startsWith(prefix)) continue;
    const k = key.slice(prefix.length);
    if (k && needle.startsWith(k)) candidates.push([k.length, entry.part]);
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => b[0] - a[0]);
  return candidates[0][1];
}

async function loadCatalog() {
  const res = await fetch('/api/catalog');
  CATALOG = await res.json();
  buildCatalogIndex(CATALOG);
}
```

3c. `parseItems` 的 `.map` 输出里加规范名两字段（`brand:`/`model:` 两行之后）：

```javascript
    brand: (r.brand || '').trim(),
    model: (r.model || '').trim(),
    cat: r.cat || '',
    // 归并用的规范名：命不中就退回原值，绝不为空白名单丢记录
    canonicalBrand: resolveBrand(r.brand) || (r.brand || '').trim(),
    canonicalModel: (resolvePart(r.brand, r.model) || {}).name || (r.model || '').trim(),
```

3d. `buildBrands()`（`:1390-1397`）把 `i.brand` / `i.model` 换成规范名：

```javascript
    if (!map[i.canonicalBrand]) map[i.canonicalBrand] = { brand: i.canonicalBrand, count: 0, cost: 0, sell: 0, models: {} };
    map[i.canonicalBrand].count++;
    map[i.canonicalBrand].cost += i.cost;
    map[i.canonicalBrand].sell += i.sell;
    if (!map[i.canonicalBrand].models[i.canonicalModel]) map[i.canonicalBrand].models[i.canonicalModel] = { model: i.canonicalModel, count: 0, cost: 0, sell: 0 };
    const detail = map[i.canonicalBrand].models[i.canonicalModel];
    detail.count++;
    detail.cost += i.cost;
    detail.sell += i.sell;
```

3e. 型号总览（`:1611-1612`）同样换键：

```javascript
            const k = i.canonicalBrand + '|||' + i.canonicalModel;
            if (!m[k]) m[k] = { brand: i.canonicalBrand, model: i.canonicalModel, count: 0, profit: 0, sellTotal: 0 };
```

3f. 页面启动处调用一次 `loadCatalog()`，放在首次 `loadPayload()` 之前（同一处 `init` 里），保证 `parseItems` 跑起来时索引已就绪；失败时保持空库、不阻塞记账：

```javascript
  try { await loadCatalog(); } catch (e) { console.warn('知识库没加载上，先按原值统计', e); }
```

3g. `BRAND_STYLES`（`:1447`）补上 `catalog.json` 里每个 canonical 品牌的小写键。先用脚本列出缺哪些，再按真实厂商色补，不确定的先用中性色、`ch` 取首字：

```bash
cd "G:/claude code" && python -X utf8 -c "
import json,re
src=open('index.html',encoding='utf-8').read()
block=src.split('const BRAND_STYLES',1)[1].split('};',1)[0].lower()
for b in json.load(open('catalog.json',encoding='utf-8'))['brands']:
    if b['canonical'].lower() not in block: print('缺:', b['canonical'])
"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest "G:/claude code/tests/test_ledger_api.py" "G:/claude code/tests/test_catalog.py" -q`
Expected: `212 passed`（176 旧 + 4 新静态契约 + 32 catalog）；若数字不同，以「只增不减」为准并说明差异。

- [ ] **Step 5: 浏览器端到端看真账本**

```bash
cd "G:/claude code" && python app_standalone.py > /tmp/p1-run.log 2>&1 &
sleep 3 && curl -s -D - -o /dev/null http://127.0.0.1:8765/api/catalog | grep -i x-catalog-stamp
```

然后打开品牌总览，逐项核对：`H610M-E DDR4` 一行 **15 笔**（原来 9+6 两行）、铭瑄 `B760M 终结者 D4 WIFI` 一行 **6 笔**（原来 5 种写法各行）、`英特尔` 与 `INTER` 合成一行 **7 笔**。这三处是本阶段的验收线，少一个都不算过。

- [ ] **Step 6: 提交**

```bash
cd "G:/claude code" && git add index.html tests/test_ledger_api.py && git commit -m "feat(ledger): 前端按知识库规范名归并统计（品牌总览与型号总览不再被大小写拆开）"
```

---

## 验收（P1 完成的定义）

- `python -X utf8 -m pytest` 两个测试文件全绿，且旧 176 项一项未减。
- `git status` 里 `data.json` 无改动（P1 完全不碰账本），真实 `data.json` sha 仍为 `fd50d857c17f9f0f…`。
- 品牌总览三处归并肉眼核对通过（15 / 6 / 7）。
- `catalog.json` 已提交进 git，品类 8 项、`parts` 全部 `cat: "unknown"`（人工判类是 P3 的活）。
