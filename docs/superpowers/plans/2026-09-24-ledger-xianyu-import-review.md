# 闲鱼订单可视化与就地修正 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 NeonFish 账本（`app_standalone.py` + `index.html`）能显示"这次进来了哪些闲鱼订单"，并在账本里就地改品牌/型号/买价、把一笔订单拆成多条记录、从已有品牌中选取品牌。

**Architecture:** 订单上下文（`order_date` / `item_title` / `order_paid`）在导入时直接写进 `data.json`，账本从此不依赖 MySQL；新增 `POST /api/split_record` 单端点单次写盘完成拆单；前端新增第 5 个 tab「闲鱼订单」按 `source_order_id` 分组；品牌字段改成"输入框 + 霓虹建议面板"，保留 `#fBrand` 为唯一真值源。规格见 `docs/superpowers/specs/2026-09-24-ledger-xianyu-import-review-design.md`（commit `80971e8`）。

**Tech Stack:** Python 3.13 标准库（`http.server` + `ThreadingHTTPServer`）、pywebview、原生 JavaScript（无框架、无构建步骤）、pytest（本机已装，`find_spec('pytest')` 为真）、MySQL 仅由 `xianyu_review.py` 与本计划的回填脚本访问。

---

## 执行前必读：本机硬约束

这几条不遵守会**静默产出假证据**，不是风格问题：

1. **绝对不要往 `G:\claude code\data.json` 写测试数据。** 后端测试一律 `monkeypatch` 模块级 `DATA_FILE` 到 `tmp_path`；前端测试一律连 `tests/ui_fixture_server.py` 起的 8790 端口 fixture 实例（Task 6）。
2. **改 `app_standalone.py` 后必须真正重启。** `main()`（`app_standalone.py:590-594`）检测到 8765 有进程就只新开窗口、沿用旧后端 —— 点启动器按钮看起来重启了，跑的还是旧代码。重启步骤见 Task 12 的 runbook。
3. **改 `index.html` 后已开着的窗口仍跑旧 JS。** `send_html`（`app_standalone.py:559-562`）每请求重读文件，所以要在新标签页/重新导航后才能看到变化。
4. **中文断言只信字节和 `grep -c`，不要看屏幕。** Bash 工具捕获到的 Python stdout 是 cp936 字节，屏幕上的中文必然乱码；"没看到报错"不等于"检查通过"。所有由 agent 跑的 Python 命令一律 `python -X utf8 ...`。
5. **`Write` 工具写含中文的文件后要核验编码。** 本机历史上出现过按 GBK 落盘。每写完一个含中文的工件就跑：
   ```bash
   python -X utf8 -c "b=open('<path>','rb').read(); b.decode('utf-8'); print('bytes=%d crlf=%d'%(len(b), b.count(b'\r\n')))"
   ```
   期望：能 decode、`crlf=0`（`.gitattributes` 已把 `*.md` / `*.py` / `*.html` 钉成 `text eol=lf`）。
6. **Bash 里别在双引号内写 PowerShell 的 `$` 变量**，会被 shell 吞成空值并抛出 PowerShell 解析错（错误文本还是乱码，极易误判成"PowerShell 不支持"）。需要进程/端口信息就用 `python -X utf8 -c`。
7. **提交身份**：仓库级已配 `No_object <no_object@local>`，不要动 `--global`。**本计划不 push**（仓库无 remote）。
8. **不要删除或覆盖 `data.json.bak`**；回填只允许通过 `data.json.pre_backfill` 兜底（Task 10）。

## 文件结构

| 文件 | 职责 | 本计划里的动作 |
| --- | --- | --- |
| `app_standalone.py` | 账本 HTTP 后端 + 记录读写 | 扩字段白名单、`_to_float` 修 `sell` 崩溃、`save_data` 冲突检测、新增 `split_record` 端点、`add_record` 回传索引 |
| `index.html` | 账本全部前端（2169 行原生 JS） | `parseItems` 透传新键、第 5 个 tab、`renderOrders()`、徽章跳转、拆单弹窗、品牌选择器 |
| `xianyu_review.py` | 导入页后端 | `import_orders` 补 3 个键（**不进 git**，含明文口令） |
| `xianyu_review.html` | 导入页前端 | POST 载荷补 `order_date`/`item_title`/`paid`（**不进 git**） |
| `xianyu_backfill.py` | 一次性回填 18 条 | 新建：`build_updates()` 纯函数 + MySQL 适配层，默认 dry-run |
| `tests/test_ledger_api.py` | 后端黑盒测试 | 新建：fixture 起临时实例 |
| `tests/test_backfill.py` | 回填纯函数测试 | 新建 |
| `tests/test_review_import.py` | 导入写盘测试 | 新建 |
| `tests/ui_fixture_server.py` | 前端测试用的隔离实例（8790） | 新建 |
| `tests/fixtures/ledger_with_orders.json` | 3 笔订单 + 1 条手动 + 1 条被过滤记录 | 新建 |
| `tests/fixtures/ledger_empty_orders.json` | 只有手动记录（验空态） | 新建 |

前端没有测试框架，所以 UI 断言一律通过 Chrome DevTools MCP 的 `evaluate_script` 在 fixture 实例上执行，脚本和期望输出都写在各任务的步骤里 —— 不写"手工看一下"。

---

## Task 1: 后端测试地基 + 订单字段落盘

**Files:**
- Create: `tests/test_ledger_api.py`
- Modify: `app_standalone.py:365-383`（`handle_add_record`）
- Modify: `app_standalone.py:386-409`（`handle_update_record`）

- [ ] **Step 1: 写 fixture 与第一个失败测试**

创建 `tests/test_ledger_api.py`：

```python
import http.server
import json
import os
import socket
import threading
import urllib.error
import urllib.request

import pytest


SEED = [
    {"brand": "微星", "model": "B650M GAMING WIFI", "cost": 600, "sell": 900,
     "sn": "", "accessory": "原盒", "accessory_price": "", "extra_price": "", "images": []},
    {"brand": "光威", "model": "神策 16G", "cost": 200, "sell": "",
     "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": [],
     "source_order_id": "20250921001", "order_date": "2025-09-21 14:32:05",
     "item_title": "光威神策16G，成色新", "order_paid": 800},
]

ORDER_KEYS = ("source_order_id", "order_date", "item_title", "order_paid")


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def api(monkeypatch, tmp_path):
    """账本后端的隔离实例：DATA_FILE 指向 tmp，真实 data.json 绝不参与。"""
    import app_standalone as m

    data_file = tmp_path / "data.json"
    data_file.write_text(json.dumps(SEED, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(m, "DATA_FILE", str(data_file))
    monkeypatch.setattr(m, "IMAGES_DIR", str(tmp_path / "images"))

    server = m.LedgerServer(("127.0.0.1", 0), m.APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    def call(method, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + path, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionResetError) as exc:
            raise AssertionError(
                "backend aborted the request (likely an unhandled exception): %r" % (exc,)
            )

    yield call, lambda: read_json(str(data_file)), str(data_file)
    server.shutdown()
    server.server_close()


def test_add_record_persists_order_context(api):
    call, load, _ = api
    status, resp = call("POST", "/api/data", {
        "brand": "七彩虹", "model": "B760M", "cost": 700, "sell": "",
        "source_order_id": "20250922007", "order_date": "2025-09-22 09:10:00",
        "item_title": "七彩虹b760M主板", "order_paid": 700,
    })
    assert status == 200, resp
    records = load()
    saved = records[resp["index"]]
    assert saved["brand"] == "七彩虹"
    assert [saved.get(k) for k in ORDER_KEYS] == [
        "20250922007", "2025-09-22 09:10:00", "七彩虹b760M主板", 700,
    ]


def test_manual_add_has_no_order_keys(api):
    call, load, _ = api
    status, resp = call("POST", "/api/data", {"brand": "AMD", "model": "7800X3D", "cost": 2200, "sell": 2500})
    assert status == 200, resp
    assert not any(k in load()[resp["index"]] for k in ORDER_KEYS), \
        "手动新增被塞了订单键，会污染闲鱼分组判据"
```

> 注：`LedgerServer` 定义在 `app_standalone.py:570-576`（`allow_reuse_address = False` 必须在子类里覆盖，构造后赋值无效）。端口传 0 由 OS 分配，测试之间不互相抢 8765。

- [ ] **Step 2: 跑测试，确认失败信息是"键不存在"而不是崩溃**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -v`
Expected: **2 failed**（不是 1 failed）：

- `test_add_record_persists_order_context` 失败原因是 `backend aborted the request ... RemoteDisconnected` —— 旧的 `handle_add_record` 也在 `'sell': float(body.get('sell', 0))` 上踩 `float('')`，说明新增路径同样带这个崩溃，Step 3 的 `_to_float` 顺手修掉了它。
- `test_manual_add_has_no_order_keys` 失败原因是 `KeyError: 'index'`。它也解引用 `resp["index"]`，所以在 Step 3 之前不可能通过 —— 别把这条预期写成"应当 PASS"。

- [ ] **Step 3: 实现 `handle_add_record` 的白名单与索引回传**

把 `app_standalone.py:365-383` 的 `handle_add_record` 整体替换：

```python
    def handle_add_record(self):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        data = load_data()
        record = {
            'brand': body.get('brand', ''),
            'model': body.get('model', ''),
            'cost': _to_float(body.get('cost', 0)),
            'sell': _to_float(body.get('sell', 0)),
            'sn': body.get('sn', ''),
            'accessory': body.get('accessory', ''),
            'accessory_price': _norm_price(body.get('accessory_price', '')),
            'extra_price': _norm_price(body.get('extra_price', '')),
            'images': body.get('images', []),
        }
        for key in ('source_order_id', 'order_date', 'item_title', 'order_paid'):
            if key in body:
                record[key] = body[key]
        data.append(record)
        save_data(data)
        self.send_json({'ok': True, 'record': record, 'index': len(data) - 1})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -v`
Expected: 2 passed

- [ ] **Step 5: 写"空 sell 会被 PUT 打崩"的失败测试**

追加到 `tests/test_ledger_api.py`：

```python
def test_update_partial_body_keeps_order_context_and_survives_empty_sell(api):
    """seed[1] 的 sell 是空串：省略 sell 的 PUT 过去会走 float('') 抛 ValueError，
    整次写入不落盘且连接被掐断。这条必须先失败，才能证明修复有效。"""
    call, load, _ = api
    status, resp = call("PUT", "/api/data/1", {"cost": 260})
    assert status == 200, resp
    saved = load()[1]
    assert saved["cost"] == 260
    assert [saved.get(k) for k in ORDER_KEYS] == [
        "20250921001", "2025-09-21 14:32:05", "光威神策16G，成色新", 800,
    ]
    assert saved["sell"] == ""
```

- [ ] **Step 6: 跑测试确认它失败**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py::test_update_partial_body_keeps_order_context_and_survives_empty_sell -v`
Expected: FAIL，报错文本含 `backend aborted the request (likely an unhandled exception)`；服务控制台出现 `ValueError: could not convert string to float: ''`（`app_standalone.py:397`）。

- [ ] **Step 7: 改写 `handle_update_record`（省略即保留，显式才转数值）**

`_to_float` 已存在于 `app_standalone.py:60-66`（对 `''`/`None` 返回默认值，不会崩）。但**不能**写成 `_to_float(body.get('sell', r.get('sell', 0)))` —— 那样会把"请求没带 sell"也拿去强制转换，将库里的空串 `''` 变成 `0.0`。空串是"这条还没定价"的唯一标记：实测 255 条里 `sell` 为空的恰好就是那 18 条 `source_order_id` 记录，且没有任何记录使用数值 `0`，一旦塌成 `0.0` 就无法区分回来，金色「待补售价」胶囊会立刻失去目标。所以只有请求显式带键时才转换：

```python
    def handle_update_record(self, idx):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        data = load_data()
        if not (0 <= idx < len(data)):
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)
            return
        r = data[idx]
        r['brand'] = body.get('brand', r.get('brand', ''))
        r['model'] = body.get('model', r.get('model', ''))
        r['cost'] = _to_float(body['cost']) if 'cost' in body else r.get('cost', 0)
        r['sell'] = _to_float(body['sell']) if 'sell' in body else r.get('sell', 0)
        r['sn'] = body.get('sn', r.get('sn', ''))
        r['accessory'] = body.get('accessory', r.get('accessory', ''))
        r['accessory_price'] = _norm_price(body.get('accessory_price', r.get('accessory_price', '')))
        r['extra_price'] = _norm_price(body.get('extra_price', r.get('extra_price', '')))
        for key in ('source_order_id', 'order_date', 'item_title', 'order_paid'):
            if key in body:
                r[key] = body[key]
        if 'images' in body:
            r['images'] = body['images']
        save_data(data)
        self.send_json({'ok': True, 'record': r})
```

行为对照（实现后实测）：省略 `sell` → 保持 `''`；显式 `sell: 1200` → `1200.0`；显式 `sell: ""` → `0.0`（前端提交时 `parseFloat(x) || 0` 就是 0，符合直觉）。

- [ ] **Step 8: 跑全套确认绿灯**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -v`
Expected: 3 passed

- [ ] **Step 9: 提交**

```bash
git add app_standalone.py tests/test_ledger_api.py
git commit -m "feat(ledger): persist xianyu order context on write paths

Records imported from Xianyu now keep order_date / item_title / order_paid through
add and update. Update also stops crashing on an empty sell price: float('') used to
abort the whole PUT, so editing an imported record without touching sell silently
saved nothing."
```

---

## Task 2: 写盘冲突检测（两个窗口不再互相抹掉）

**Files:**
- Modify: `app_standalone.py:50-57`（`save_data`）、`:45-47`（`load_data` 附近新增 `file_stamp`）
- Modify: `app_standalone.py` 内全部 `save_data(` 调用点：`handle_add_record`、`handle_update_record`、`handle_delete_record`（`:411`）、`handle_split_record`（Task 3 新增）、`_agent_add_record`（`:116`）、图片相关写盘
- Test: `tests/test_ledger_api.py`

背景：账本窗口（8765）和导入页（8766）都是"整读 → 改 → 整写"，`ThreadingHTTPServer` 无锁，后写的会把先写的那条记录整条抹掉；而 `.bak` 只有一代，下一次写盘就把唯一的救命备份销毁。规格 §12 风险 2 要求写前校验，不做完整文件锁。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ledger_api.py`：

```python
def test_save_data_refuses_to_clobber_external_writer(api):
    """载入后又出现第三方写入时，save_data(stamp) 必须拒绝，而不是覆盖。"""
    import app_standalone as m

    call, load, path = api
    data = m.load_data()
    stamp = m.file_stamp()

    # 模拟导入页在这之后写了一整份新内容（多出一条）
    outside = load() + [{"brand": "外部", "model": "写入", "cost": 1, "sell": 1}]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(outside, f, ensure_ascii=False)

    with pytest.raises(m.WriteConflict):
        m.save_data(data, stamp)
    assert len(load()) == 3, "冲突时不得写盘"

    m.save_data(data)  # 不带 stamp 的写盘保持旧语义（回填脚本需要）
    assert len(load()) == 2


def test_update_returns_409_on_conflict(api):
    import app_standalone as m

    call, load, path = api
    # 让下一次 PUT 看到的文件与它自己读到的不一致
    original_save = m.save_data
    state = {}

    def fake_save(data, stamp=None):
        state["stamp"] = stamp
        raise m.WriteConflict("stale")

    m.save_data = fake_save
    try:
        status, resp = call("PUT", "/api/data/0", {"cost": 601})
    finally:
        m.save_data = original_save
    assert status == 409, resp
    assert state["stamp"] is not None, "handler 没把 stamp 传给 save_data"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -k "conflict" -v`
Expected: 2 个 FAIL，报 `AttributeError: module 'app_standalone' has no attribute 'file_stamp'`（`WriteConflict` 同样不存在）。

- [ ] **Step 3: 实现 stamp 与冲突异常**

在 `app_standalone.py:43` 的 `# ─── Data helpers ───` 之后、`load_data` 之前插入：

```python
class WriteConflict(Exception):
    """文件在本进程读取之后被别处改过，拒绝覆盖。"""


def file_stamp():
    st = os.stat(DATA_FILE) if os.path.exists(DATA_FILE) else None
    if st is None:
        return None
    return (st.st_mtime_ns, st.st_size)
```

把 `save_data`（`app_standalone.py:50-57`）替换为：

```python
def save_data(data, stamp=None):
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if stamp is not None and file_stamp() != stamp:
        raise WriteConflict('data.json changed since it was read')
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, DATA_FILE + '.bak')
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(payload)
    os.replace(tmp, DATA_FILE)
```

- [ ] **Step 4: 各 handler 带上 stamp**

`handle_add_record` / `handle_update_record` / `handle_delete_record` 里，把 `data = load_data()` 之后各加一行、并把 `save_data(data)` 改成 `save_data(data, stamp)`：

```python
        data = load_data()
        stamp = file_stamp()
```

并在 `APIHandler` 内新增统一兜底（放在 `read_body` 之前，`app_standalone.py:544` 附近）：

```python
    def handle_write_conflict(self):
        self.send_json({'ok': False, 'error': 'data.json changed elsewhere, reloaded'}, 409)
```

把三个写 handler 的函数体用 `try/except WriteConflict` 包住 —— 以 `handle_update_record` 为例，最外层结构变成：

```python
    def handle_update_record(self, idx):
        try:
            ...（Task 1 Step 7 的整段实现）...
        except WriteConflict:
            self.handle_write_conflict()
            return
```

`_agent_add_record`（`app_standalone.py:102-117`）保持不带 stamp 的写盘：Agent 每次工具调用都重新 `load_data()`，且它在同一次 LLM 回合里连续调用，加锁会把正常录入打断。这一点写进注释，别当遗漏：

```python
    data.append(record)
    # 不带 stamp：Agent 单次回合内连续写盘是正常路径，冲突保护留给 UI
    save_data(data)
```

- [ ] **Step 5: 跑全套确认绿灯**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
git add app_standalone.py tests/test_ledger_api.py
git commit -m "fix(ledger): detect concurrent data.json writes instead of clobbering"
```

## Task 3: `POST /api/split_record` —— 一次写盘完成拆单

**Files:**
- Modify: `app_standalone.py:318-331`（`do_POST` 路由）
- Modify: `app_standalone.py`（在 `handle_delete_record` 之前插入 `handle_split_record`）
- Test: `tests/test_ledger_api.py`

为什么必须单端点：前端循环调 `add_record` 会触发 N 次 `save_data`，每次 `shutil.copy2` 都把上一代 `.bak` 冲掉（`app_standalone.py:50-64`），拆单中途失败时既没有可信备份也不是原子结果。

- [ ] **Step 1: 写 5 个失败测试**

追加到 `tests/test_ledger_api.py`：

```python
import hashlib


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_split_record_appends_and_inherits(api):
    call, load, path = api
    before = sha256(path)
    status, resp = call("POST", "/api/split_record", {
        "idx": 1,
        "parts": [
            {"brand": "光威", "model": "神策 16G×2", "cost": 500},
            {"brand": "十铨", "model": "Delta 16G", "cost": 300},
        ],
    })
    assert status == 200, resp
    records = load()
    assert len(records) == 3
    assert resp["indices"] == [1, 2], resp["indices"]
    a, b = records[1], records[2]
    for key in ("source_order_id", "order_date", "item_title", "order_paid"):
        assert a[key] == b[key] == SEED[1][key], key
    assert a["model"] == "神策 16G×2" and a["cost"] == 500
    assert b["cost"] == 300
    assert b["sell"] == "" and b["images"] == []
    # 备份必须是写入前那一刻的 data.json（不要用 mtime：copy2 会继承源时间戳）
    assert sha256(path + ".bak") == before


def test_split_record_twice_keeps_paid_baseline(api):
    call, load, _ = api
    call("POST", "/api/split_record", {"idx": 1, "parts": [
        {"brand": "光威", "model": "A", "cost": 500},
        {"brand": "光威", "model": "B", "cost": 300},
    ]})
    call("POST", "/api/split_record", {"idx": 2, "parts": [
        {"brand": "十铨", "model": "C", "cost": 150},
        {"brand": "十铨", "model": "D", "cost": 150},
    ]})
    assert [r.get("order_paid") for r in load()[1:]] == [800, 800, 800], \
        "二次拆单把实付基线改写了，组头会冒出假差额"


def test_split_record_rejects_manual_record(api):
    call, load, path = api
    before = sha256(path)
    status, resp = call("POST", "/api/split_record", {"idx": 0, "parts": [
        {"brand": "微星", "model": "X", "cost": 1}]})
    assert status == 400, resp
    assert sha256(path) == before


def test_split_record_rejects_bad_input(api):
    call, load, path = api
    before = sha256(path)
    cases = [
        (404, {"idx": 99, "parts": [{"brand": "a", "model": "b", "cost": 1}]}),
        (400, {"idx": 1, "parts": []}),
        (400, {"idx": 1, "parts": [{"brand": "  ", "model": "b", "cost": 1}]}),
        (400, {"idx": "x", "parts": [{"brand": "a", "model": "b", "cost": 1}]}),
    ]
    for expected, payload in cases:
        status, resp = call("POST", "/api/split_record", payload)
        assert status == expected, (payload, resp)
    assert sha256(path) == before, "四种非法输入都不该写盘"


def test_split_record_conflict_leaves_file_intact(api):
    import app_standalone as m

    call, load, path = api
    before = sha256(path)
    original = m.save_data
    m.save_data = lambda data, stamp=None: (_ for _ in ()).throw(m.WriteConflict("stale"))
    try:
        status, resp = call("POST", "/api/split_record", {"idx": 1, "parts": [
            {"brand": "光威", "model": "A", "cost": 500},
            {"brand": "光威", "model": "B", "cost": 300}]})
    finally:
        m.save_data = original
    assert status == 409, resp
    assert sha256(path) == before
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -k split -v`
Expected: 5 个 FAIL，`status == 404`（`do_POST` 里没有 `/api/split_record` 分支，落到 `else: not found`）。

- [ ] **Step 3: 实现端点**

在 `app_standalone.py` 的 `handle_delete_record`（`:411`）之前插入：

```python
    ORDER_CONTEXT_KEYS = ('source_order_id', 'order_date', 'item_title', 'order_paid')

    def handle_split_record(self):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        idx = body.get('idx')
        parts = body.get('parts')
        if not isinstance(idx, int):
            self.send_json({'ok': False, 'error': 'idx must be an int'}, 400)
            return
        if not isinstance(parts, list) or not parts:
            self.send_json({'ok': False, 'error': 'parts required'}, 400)
            return
        for part in parts:
            if not str(part.get('brand', '')).strip() or not str(part.get('model', '')).strip():
                self.send_json({'ok': False, 'error': 'brand and model required'}, 400)
                return

        data = load_data()
        stamp = file_stamp()
        if not (0 <= idx < len(data)):
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)
            return
        target = data[idx]
        if not target.get('source_order_id'):
            self.send_json({'ok': False, 'error': 'not an imported record'}, 400)
            return

        first = parts[0]
        target['brand'] = str(first['brand']).strip()
        target['model'] = str(first['model']).strip()
        target['cost'] = _to_float(first.get('cost', target.get('cost', 0)))

        indices = [idx]
        for part in parts[1:]:
            record = {
                'brand': str(part['brand']).strip(),
                'model': str(part['model']).strip(),
                'cost': _to_float(part.get('cost', 0)),
                'sell': '', 'sn': '', 'accessory': '', 'accessory_price': '',
                'extra_price': '', 'images': [],
            }
            for key in self.ORDER_CONTEXT_KEYS:
                if key in target:
                    record[key] = target[key]
            data.append(record)
            indices.append(len(data) - 1)

        try:
            save_data(data, stamp)
        except WriteConflict:
            self.handle_write_conflict()
            return
        self.send_json({'ok': True, 'indices': indices, 'order_paid': target.get('order_paid', '')})
```

`do_POST`（`app_standalone.py:318-331`）里，在 `/api/upload` 分支前加：

```python
        elif path == '/api/split_record':
            self.handle_split_record()
```

- [ ] **Step 4: 跑全套确认绿灯**

Run: `python -X utf8 -m pytest tests/test_ledger_api.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add app_standalone.py tests/test_ledger_api.py
git commit -m "feat(ledger): split one xianyu order into multiple records atomically

One endpoint, one write. Looping add_record would rotate data.json.bak once per
part and leave the ledger half-split if a later request failed."
```

---

## Task 4: 导入侧真的把订单上下文带进账本

**Files:**
- Modify: `xianyu_review.py:258-291`（`import_orders`）
- Modify: `xianyu_review.html:863-867`（POST 载荷）
- Create: `tests/test_review_import.py`

实测事实：核对页手里有 `order_date` / `item_title` / `price`（`xianyu_review.html:590`、`:596` 在渲染它们），但 POST 只发 `order_id / brand / model / cost`（`:863-866`）。所以改的是载荷，不是后端拼装。

> **Task 1 遗留给本任务的决定**：`handle_add_record` 现在把显式提交的 `sell: ""` 落成 `0.0`，而 `handle_update_record` 是"省略即保留 `''`"。今天无害 —— 导入器直接写文件、不经过 HTTP，且 `0.0` 与 `''` 在前端 `!r.sell` 判断里都算待补。但如果将来把导入改成走 `POST /api/data`，"待补售价"的标记语义会在入库那一刻分叉。届时要么让 add 也保留 `''`，要么把"未定价"统一改成一个显式的 `null` 约定，别同时留两套。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_import.py`：

```python
import json

import pytest


SEED_LEDGER = [
    {"brand": "微星", "model": "B650M", "cost": 600, "sell": 900,
     "source_order_id": "OLD1"},
]


@pytest.fixture
def review(monkeypatch, tmp_path):
    import xianyu_review as m

    data_file = tmp_path / "data.json"
    data_file.write_text(json.dumps(SEED_LEDGER, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "DATA_FILE", str(data_file))
    return m, lambda: json.loads(data_file.read_text(encoding="utf-8"))


def test_import_writes_order_context(review):
    m, load = review
    added, skipped, total = m.import_orders([{
        "order_id": "NEW1",
        "brand": "光威",
        "model": "神策 16G",
        "cost": 800,
        "order_date": "2025-09-21 14:32:05",
        "item_title": "光威神策16G，成色新",
        "paid": 800,
    }])
    assert added == ["NEW1"] and skipped == [] and total == 2
    record = load()[1]
    assert record["source_order_id"] == "NEW1"
    assert record["order_date"] == "2025-09-21 14:32:05"
    assert record["item_title"] == "光威神策16G，成色新"
    assert record["order_paid"] == 800
    assert record["sell"] == "", "买入单不该带卖出价"


def test_import_skips_existing_order_id(review):
    m, load = review
    added, skipped, total = m.import_orders([
        {"order_id": "OLD1", "brand": "微星", "model": "B650M", "cost": 600}])
    assert added == [] and skipped == ["OLD1"] and total == 1


def test_import_without_context_still_works(review):
    """手动缺字段时不写空键，避免污染闲鱼分组判据。"""
    m, load = review
    m.import_orders([{"order_id": "NEW2", "brand": "AMD", "model": "7800X3D", "cost": 2200}])
    record = load()[1]
    assert "order_date" not in record or record["order_date"] == ""
    assert "item_title" not in record or record["item_title"] == ""
    assert "order_paid" not in record or record["order_paid"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest tests/test_review_import.py -v`
Expected: `test_import_writes_order_context` FAIL —— `KeyError: 'order_date'`。后两条 PASS（防回归）。

- [ ] **Step 3: 改 `import_orders` 的 record 构造**

把 `xianyu_review.py:258-291` 里的 `record = {...}` 替换：

```python
        record = {
            "brand": str(it.get("brand") or "").strip(),
            "model": str(it.get("model") or "").strip(),
            "cost": _to_float(it.get("cost")),
            # 买入订单没有卖出价，留空由用户之后自己补
            "sell": "",
            "sn": "",
            "accessory": "",
            "accessory_price": "",
            "extra_price": "",
            "images": [],
            "source_order_id": oid,
            "order_date": str(it.get("order_date") or ""),
            "item_title": str(it.get("item_title") or ""),
            "order_paid": _to_float(it.get("paid")),
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest tests/test_review_import.py -v`
Expected: 3 passed

- [ ] **Step 5: 补核对页 POST 载荷**

`xianyu_review.html:863-867` 当前是：

```javascript
      order_id: o.order_id,
      brand: (o._brand || "").trim(),
      model: (o._model || "").trim(),
      cost: o._cost === "" ? "" : o._cost
```

改成（新增 3 行，注意 `cost` 行尾要补逗号）：

```javascript
      order_id: o.order_id,
      brand: (o._brand || "").trim(),
      model: (o._model || "").trim(),
      cost: o._cost === "" ? "" : o._cost,
      order_date: o.order_date || "",
      item_title: o.item_title || "",
      paid: o.price === undefined ? "" : o.price
```

- [ ] **Step 6: 重启导入页并在浏览器里验载荷**

Run（PowerShell 的 `$` 会被 Bash 吞，进程操作用 Python）：

```bash
python -X utf8 -c "import ctypes,os,subprocess,sys,time; \
net=subprocess.run(['netstat','-ano','-p','TCP'],capture_output=True,text=True,errors='replace').stdout; \
pid=[int(l.split()[-1]) for l in net.splitlines() if ':8766' in l and 'LISTENING' in l][0]; \
subprocess.run(['taskkill','/PID',str(pid),'/F'],capture_output=True); time.sleep(1.2); \
subprocess.Popen([sys.executable,'-X','utf8',r'G:\claude code\xianyu_review.py'],cwd=r'G:\claude code'); \
time.sleep(5); print('8766 up', os.system('curl -s -o NUL -w %{http_code} http://127.0.0.1:8766/api/orders')==0)"
```

然后在连到 8766 的标签页里执行 `evaluate_script`，**把 fetch 换掉，所以不会真的写 `data.json`**：

```javascript
async () => {
  const captured = [];
  const real = window.fetch;
  window.fetch = (url, opts) => { captured.push({url, body: opts && opts.body}); return Promise.resolve(new Response('{"ok":true,"added":[],"skipped":[],"total":0}', {status: 200, headers: {'Content-Type':'application/json'}})); };
  document.querySelectorAll('.rowCk').forEach(ck => { if (!ck.checked) ck.click(); });
  const btn = document.getElementById('importBtn') || [...document.querySelectorAll('button')].find(b => /填入账本|录入账本/.test(b.textContent));
  btn.click();
  await new Promise(r => setTimeout(r, 600));
  window.fetch = real;
  const sent = captured.filter(c => /import/.test(c.url));
  const first = sent.length ? JSON.parse(sent[0].body) : null;
  const item = Array.isArray(first) ? first[0] : (first && (first.items || first.orders || [])[0]);
  return { sentCount: sent.length, keys: item ? Object.keys(item).sort() : null,
           hasDate: !!(item && item.order_date), hasTitle: !!(item && item.item_title), hasPaid: item && typeof item.paid !== 'undefined' };
}
```

Expected: `hasDate=true`、`hasTitle=true`、`hasPaid=true`。若 `sentCount=0`，说明勾选/按钮选择器与当前 DOM 不符 —— 先 `take_snapshot` 找到真实按钮再改脚本，不要跳过这步。

- [ ] **Step 7: 核验导入页没被这次验证写坏**

Run: `python -X utf8 -c "import json;d=json.load(open('data.json',encoding='utf-8'));print('records=%d xianyu=%d'%(len(d),sum(1 for r in d if r.get('source_order_id'))))"`
Expected: `records=255 xianyu=18`（数字变了说明 fetch 桩没拦住，立刻回滚并复盘）

- [ ] **Step 8: 提交**

`xianyu_review.py` / `xianyu_review.html` 在 `.gitignore` 里（明文口令），只提交测试：

```bash
git add tests/test_review_import.py
git commit -m "test(ledger): pin xianyu import to carry order context into data.json

The review-page scripts stay untracked (hardcoded MySQL credentials), so the test
is the committed contract for their record shape."
```

---

## Task 5: 前端测试夹具服务（后续所有 UI 任务的前提）

**Files:**
- Create: `tests/ui_fixture_server.py`
- Create: `tests/fixtures/ledger_with_orders.json`
- Create: `tests/fixtures/ledger_empty_orders.json`

账本前端没有测试框架，而且真实 `data.json` 不能碰。方案：用同一个 `app_standalone` 后端在 8790 端口跑一份 fixture 数据，浏览器在它上面点真实交互，随它写。

- [ ] **Step 1: 建 fixture 数据**

`tests/fixtures/ledger_with_orders.json`（注意第二条 brand/model 全空 —— 它会被 `parseItems` 过滤掉，用来验证下标口径仍然正确）：

```json
[
  {"brand": "微星", "model": "B650M GAMING WIFI", "cost": 600, "sell": 900, "sn": "", "accessory": "原盒", "accessory_price": "", "extra_price": "", "images": [], "source_order_id": "A1", "order_date": "2025-09-21 14:32:05", "item_title": "微星b650M迫击炮，箱说全", "order_paid": 600},
  {"brand": "", "model": "", "cost": 51554, "sell": "", "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": []},
  {"brand": "光威", "model": "神策 16G", "cost": 400, "sell": "", "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": [], "source_order_id": "A1", "order_date": "2025-09-21 14:32:05", "item_title": "微星b650M迫击炮，箱说全", "order_paid": 600},
  {"brand": "AMD", "model": "7800X3D", "cost": 2200, "sell": 2500, "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": [], "source_order_id": "B2", "order_date": "2025-09-22 09:10:00", "item_title": "AMD 7800X3D，散片", "order_paid": 2200},
  {"brand": "芝奇", "model": "幻锋戟 16G×2", "cost": 300, "sell": "", "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": [], "source_order_id": "C3", "order_date": "2025-09-22 11:05:00", "item_title": "", "order_paid": ""},
  {"brand": "致态", "model": "TiPlus7100 1T", "cost": 400, "sell": 450, "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": []}
]
```

`tests/fixtures/ledger_empty_orders.json`：

```json
[
  {"brand": "微星", "model": "B650M GAMING WIFI", "cost": 600, "sell": 900, "sn": "", "accessory": "原盒", "accessory_price": "", "extra_price": "", "images": []}
]
```

- [ ] **Step 2: 建夹具服务**

创建 `tests/ui_fixture_server.py`：

```python
"""账本前端的隔离实例：8790 端口 + fixture 数据，真实 data.json 完全不参与。

    python -X utf8 tests/ui_fixture_server.py              # 3 笔订单
    LEDGER_FIXTURE=empty python -X utf8 tests/ui_fixture_server.py
"""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WHICH = os.environ.get('LEDGER_FIXTURE', 'orders')
FIXTURE = os.path.join(ROOT, 'tests', 'fixtures',
                       'ledger_empty_orders.json' if WHICH == 'empty' else 'ledger_with_orders.json')
PORT = int(os.environ.get('LEDGER_FIXTURE_PORT', '8790'))

import app_standalone as m  # noqa: E402


def main():
    workdir = tempfile.mkdtemp(prefix='ledger-fixture-')
    data_file = os.path.join(workdir, 'data.json')
    shutil.copyfile(FIXTURE, data_file)
    m.DATA_FILE = data_file
    m.IMAGES_DIR = os.path.join(workdir, 'images')
    os.makedirs(m.IMAGES_DIR, exist_ok=True)

    server = m.LedgerServer(('127.0.0.1', PORT), m.APIHandler)
    print('fixture backend on http://127.0.0.1:%d  data=%s' % (PORT, data_file), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: 启动并验活**

```bash
python -X utf8 tests/ui_fixture_server.py   # 后台跑，另开一条命令验证
curl -s http://127.0.0.1:8790/api/data | python -X utf8 -c "import json,sys;d=json.load(sys.stdin);print('records=%d xianyu=%d orders=%d'%(len(d),sum(1 for r in d if r.get('source_order_id')),len({r['source_order_id'] for r in d if r.get('source_order_id')})))"
```

Expected: `records=6 xianyu=4 orders=3`（3 笔订单：A1 两条、B2、C3）。这条数字是后续所有 UI 断言的基线。

- [ ] **Step 4: 提交**

```bash
git add tests/ui_fixture_server.py tests/fixtures/
git commit -m "test(ledger): add isolated fixture backend for UI verification

The ledger front-end has no test runner; this serves a fixture data.json on 8790
so browser assertions can click for real without touching the live ledger."
```

---

## Task 6: `parseItems` 透传 + 第 5 个 tab「闲鱼订单」

**Files:**
- Modify: `index.html:1135-1151`（`parseItems` 的 map）
- Modify: `index.html:940-943`（tab 条）
- Modify: `index.html:1566-1572`（`render()` 分派）
- Modify: `index.html`（在 `renderRanking` 之后新增 `renderOrders()`）
- Modify: `index.html`（CSS，放在 `.badge-source` 附近 `:681`）

**这是整个改动里最容易做坏的一处**：`parseItems` 用 `.map()` 显式重建每条记录，后端加了字段而这里不透传，页面就是空白而不是报错。

- [ ] **Step 1: 透传新键**

在 `parseItems` 的 map 里（`index.html:1136-1140` 附近，`source_order_id` 那一行之后）插入：

```javascript
    order_date: r.order_date || '',
    item_title: r.item_title || '',
    order_paid: (r.order_paid === '' || r.order_paid == null) ? null : safeFloat(r.order_paid),
```

- [ ] **Step 2: 注册 tab 与分派**

`index.html:940-943` 的 tab 组里，在 `data-view="ranking"` 那个按钮**之前**插入：

```html
        <button class="tab" data-view="orders">闲鱼订单</button>
```

`render()`（`index.html:1566-1572`）改成分派到 `renderOrders()`：

```javascript
function render() {
  updateStats();
  if (currentView === 'brands') renderBrands();
  else if (currentView === 'models') renderModels();
  else if (currentView === 'chipsets') renderChipsets();
  else if (currentView === 'orders') renderOrders();
  else if (currentView === 'ranking') renderRanking();
}
```

- [ ] **Step 3: 写 `buildOrders()` 与 `renderOrders()`**

放在 `renderRanking` 函数结束之后。分组键是 `source_order_id`，`order_date` 只用于排序和显示：

```javascript
function buildOrders() {
  const groups = new Map();
  for (const r of items) {
    const oid = r.source_order_id;
    if (!oid) continue;
    if (!groups.has(oid)) {
      groups.set(oid, { oid, records: [], order_date: '', item_title: '', order_paid: null });
    }
    const g = groups.get(oid);
    g.records.push(r);
    if (r.order_date && r.order_date > g.order_date) g.order_date = r.order_date;
    if (!g.item_title && r.item_title) g.item_title = r.item_title;
    if (g.order_paid === null && r.order_paid !== null) g.order_paid = r.order_paid;
  }
  return [...groups.values()].map(g => {
    g.paidTotal = g.records.reduce((s, r) => s + r.cost, 0);
    g.unfilled = g.records.filter(r => !r.sell).length;
    return g;
  }).sort((a, b) => (b.order_date || '').localeCompare(a.order_date || ''));
}

function renderOrders() {
  const head = document.getElementById('tableHead');
  const body = document.getElementById('tableBody');
  let groups = buildOrders();
  if (searchTerm) {
    // 搜索框在这个 tab 里退化为按订单号 / 原标题过滤分组
    const q = searchTerm.toLowerCase();
    groups = groups.filter(g => g.oid.toLowerCase().includes(q) || (g.item_title || '').toLowerCase().includes(q));
  }

  head.innerHTML = `<tr><th>订单</th><th>入账</th><th>品牌 / 型号</th><th class="num">买价</th><th class="num">售价</th><th class="num">利润</th><th></th></tr>`;

  if (!groups.length) {
    body.innerHTML = `
      <tr><td colspan="7">
        <div class="orders-empty">
          <div class="orders-empty-title">还没有导入任何闲鱼订单</div>
          <div class="orders-empty-desc">在启动器点「闲鱼数据填入」打开核对页（127.0.0.1:8766），勾选后填入账本，这些记录就会按订单出现在这里。</div>
        </div>
      </td></tr>`;
    return;
  }

  body.innerHTML = groups.map(g => {
    const paidCell = g.order_paid === null
      ? '<span class="orders-muted">实付未知</span>'
      : `实付 ¥${fmt(g.order_paid)}`;
    const diff = g.order_paid === null ? null : g.order_paid - g.paidTotal;
    const diffHtml = diff === null ? '' :
      (Math.abs(diff) < 0.5
        ? '<span class="orders-ok">已对齐</span>'
        : `<span class="orders-diff">差额 ¥${fmt(diff)}</span>`);
    const title = g.item_title
      ? `<span class="orders-title" title="${esc(g.item_title)}">${esc(g.item_title)}</span>`
      : `<span class="orders-muted">订单 ${esc(String(g.oid).slice(-6))}</span>`;
    const rows = g.records.map(r => {
      const pClass = r.profit >= 0 ? 'profit-positive' : 'profit-negative';
      return `
        <tr class="order-row">
          <td class="order-row-oid">${esc(String(r.source_order_id).slice(-6))}</td>
          <td><span class="badge-source">闲鱼</span></td>
          <td><span class="order-row-brand">${esc(r.brand)}</span> <span class="order-row-model">${esc(r.model)}</span></td>
          <td class="num">¥${fmt(r.cost)}</td>
          <td class="num ${r.sell ? '' : 'orders-muted'}">${r.sell ? '¥' + fmt(r.sell) : '待补'}</td>
          <td class="num ${pClass}">${r.sell ? (r.profit >= 0 ? '+' : '') + '¥' + fmt(r.profit) : '—'}</td>
          <td class="num"><button class="icon-btn" title="编辑" onclick="event.stopPropagation(); openEditModal(${r.id})" aria-label="编辑">✎</button></td>
        </tr>`;
    }).join('');

    return `
      <tr class="order-group">
        <td colspan="7">
          <table class="order-inner">
            <tr class="order-head">
              <td>
                <span class="order-date">${esc((g.order_date || '').slice(0, 16) || '日期未知')}</span>
                <span class="order-count">${g.records.length} 条</span>
                ${g.unfilled ? `<span class="orders-diff">${g.unfilled} 条待补售价</span>` : ''}
              </td>
              <td colspan="2">${title}</td>
              <td class="num">${paidCell}</td>
              <td class="num">入账 ¥${fmt(g.paidTotal)}</td>
              <td class="num">${diffHtml}</td>
              <td class="num"><button class="order-split-btn" onclick="event.stopPropagation(); openSplitModal('${esc(g.oid)}')">拆成多条</button></td>
            </tr>
            ${rows}
          </table>
        </td>
      </tr>`;
  }).join('');
}
```

- [ ] **Step 4: 加 CSS（沿用既有 token，不新增色值）**

放在 `index.html:681` 的 `.badge-source` 规则之后：

```css
.order-inner { width: 100%; border-collapse: collapse; }
.order-group > td { padding: 0; border: 1px solid rgba(255,215,64,0.18); background: rgba(255,215,64,0.03); }
.order-head > td { padding: 9px 12px; font-size: 12px; color: var(--text); }
.order-date { font-weight: 700; color: var(--gold); text-shadow: 0 0 8px rgba(255,215,64,0.28); margin-right: 8px; }
.order-count { color: var(--muted); margin-right: 8px; }
.orders-title { display: inline-block; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
.orders-muted { color: var(--muted); }
.orders-ok { color: var(--green); }
.orders-diff { color: var(--gold); }
.order-row > td { padding: 6px 12px; font-size: 12px; border-top: 1px solid rgba(255,255,255,0.04); }
.order-row-brand { font-weight: 600; }
.order-row-model { color: var(--muted); }
.order-split-btn { background: transparent; border: 1px solid rgba(255,215,64,0.35); color: var(--gold); border-radius: 6px; padding: 3px 8px; font-size: 11px; cursor: pointer; }
.order-split-btn:hover { box-shadow: 0 0 12px rgba(255,215,64,0.25); }
.orders-empty { text-align: center; padding: 40px 12px; }
.orders-empty-title { font-weight: 700; color: var(--gold); margin-bottom: 6px; }
.orders-empty-desc { color: var(--muted); font-size: 12px; }
```

- [ ] **Step 4b: tab 上的「有待补」圆点（规格 §6.2）**

不新增筛选胶囊（`togglePending` 会强制切 view、tab 切换会清 `pendingOnly`，跨 tab 交集语义在现有状态机里不成立）。改成 tab 标签上的一枚圆点：

在 `renderOrders()` 之后加：

```javascript
function refreshOrdersTabDot() {
  const tab = document.querySelector('.tab[data-view="orders"]');
  if (!tab) return;
  const pending = items.some(r => r.source_order_id && !r.sell);
  tab.classList.toggle('has-pending', pending);
}
```

并在 `render()` 末尾调用（`updateStats()` 之后任意位置）：

```javascript
  refreshOrdersTabDot();
```

CSS：

```css
.tab { position: relative; }
.tab.has-pending::after { content: ''; position: absolute; top: 6px; right: 8px; width: 6px; height: 6px;
  border-radius: 50%; background: var(--gold); box-shadow: 0 0 8px rgba(255,215,64,0.8); }
```

验证（夹具页，fixture 里有 `sell` 为空的导入记录）：

```javascript
() => { currentView = 'brands'; render();
  const tab = document.querySelector('.tab[data-view="orders"]');
  return { dot: tab.classList.contains('has-pending'),
           pendingCount: items.filter(r => r.source_order_id && !r.sell).length }; }
```

Expected: `dot=true`、`pendingCount=2`（A1 的光威行 + C3 的芝奇行）。

- [ ] **Step 5: 在夹具上验证**

夹具服务已在 Task 5 起好。浏览器导航到 `http://127.0.0.1:8790/` 后执行 `evaluate_script`：

```javascript
() => {
  currentView = 'orders';
  render();
  const groups = [...document.querySelectorAll('.order-group')];
  const headText = groups.map(g => {
    const h = g.querySelector('.order-head');
    return h ? h.textContent.replace(/\s+/g, ' ').trim() : null;
  });
  const rows = document.querySelectorAll('.order-row').length;
  const badge = document.querySelectorAll('.order-row .badge-source').length;
  const expectedGroups = new Set(items.filter(i => i.source_order_id).map(i => i.source_order_id)).size;
  return { expectedGroups, domGroups: groups.length, rows, badge,
           headText, sameDayMerged: groups.length === expectedGroups };
}
```

Expected:
```json
{"expectedGroups": 3, "domGroups": 3, "rows": 4, "badge": 4,
 "headText": ["2025-09-22 11:05 1 条 1 条待补售价 订单 C3 实付未知 入账 ¥300 拆成多条", "...B2...", "...A1..."],
 "sameDayMerged": true}
```
`sameDayMerged` 必须为 `true`：fixture 里 B2 与 C3 同一天，若分组数变成 2 说明还是按日期分了 —— 这是本任务要防的核心错误。

- [ ] **Step 6: 验空态**

另起一个空夹具实例：

```bash
LEDGER_FIXTURE=empty LEDGER_FIXTURE_PORT=8791 python -X utf8 tests/ui_fixture_server.py
```

导航到 `http://127.0.0.1:8791/`，执行 Step 5 的脚本。
Expected: `domGroups=1`（空态那一行）且 `document.querySelector('.orders-empty') !== null`；`expectedGroups=0`。

- [ ] **Step 7: 验既有 tab 未受影响**

在同一夹具页上执行：

```javascript
() => {
  const out = {};
  for (const v of ['brands', 'models', 'chipsets', 'ranking']) {
    currentView = v; render();
    out[v] = { rows: document.querySelectorAll('#tableBody tr').length, head: document.getElementById('tableHead').textContent.trim().slice(0, 24) };
  }
  return out;
}
```

Expected: 4 个视图都 `rows > 0`，无 console 报错（用 `list_console_messages` 复查，必须为空）。

- [ ] **Step 8: 提交**

```bash
git add index.html
git commit -m "feat(ledger): orders tab grouped by xianyu order id

Order context was invisible to the front end because parseItems rebuilds each
record from a field whitelist; pass order_date / item_title / order_paid through
so the new tab can group by order id rather than date, which would merge two
orders placed the same day."
```

## Task 7: 「型号明细」的闲鱼徽章点击跳到订单分组

**Files:**
- Modify: `index.html`（`renderModels()` 内渲染 `.badge-source` 的那一行，约 `:1398`）
- Modify: `index.html`（`renderOrders()` 附近新增 `locateOrder(oid)`）

- [ ] **Step 1: 写失败断言（夹具上跑）**

导航到 `http://127.0.0.1:8790/`，执行：

```javascript
() => {
  currentView = 'models'; render();
  const badge = document.querySelector('#tableBody .badge-source');
  return { badgeFound: !!badge,
           clickable: !!(badge && badge.getAttribute('onclick')),
           cursor: badge ? getComputedStyle(badge).cursor : null,
           title: badge ? badge.getAttribute('title') : null };
}
```

Expected（改前）: `badgeFound=true`、`clickable=false`。

- [ ] **Step 2: 让徽章可点**

把 `renderModels()` 里渲染徽章的片段从：

```javascript
<span class="badge-source" title="订单号 ${esc(i.source_order_id)}">闲鱼</span>
```

改成：

```javascript
<span class="badge-source badge-source-link" title="订单号 ${esc(i.source_order_id)} · 点击看这一单" onclick="event.stopPropagation(); locateOrder('${esc(i.source_order_id)}')">闲鱼</span>
```

CSS 追加（Task 6 Step 4 那块之后）：

```css
.badge-source-link { cursor: pointer; }
.badge-source-link:hover { box-shadow: 0 0 10px rgba(255,0,255,0.45); }
```

- [ ] **Step 3: 写跳转函数**

在 `renderOrders()` 之后加。它要同时更新 tab 高亮，不能只改 `currentView`：

```javascript
function locateOrder(oid) {
  currentView = 'orders';
  window._orderFocus = oid;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === 'orders'));
  render();
  const target = [...document.querySelectorAll('.order-group')]
    .find(g => g.querySelector('[data-oid]') && g.querySelector('[data-oid]').dataset.oid === oid);
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.classList.add('order-group-focus');
    setTimeout(() => target.classList.remove('order-group-focus'), 1600);
  }
}
```

在 `renderOrders()` 的 `<tr class="order-group">` 上补 `data-oid`（否则上面的查找找不到锚点）：

```javascript
      <tr class="order-group" data-oid="${esc(g.oid)}">
```

CSS：

```css
.order-group-focus > td { animation: orderFlash 1.6s ease-out; }
@keyframes orderFlash {
  0%, 60% { background: rgba(255,0,255,0.12); box-shadow: inset 0 0 0 1px rgba(255,0,255,0.5); }
  100% { background: rgba(255,215,64,0.03); box-shadow: none; }
}
```

- [ ] **Step 4: 验证**

夹具页执行：

```javascript
() => {
  currentView = 'models'; render();
  const badge = document.querySelector('#tableBody .badge-source');
  badge.click();
  const activeTab = document.querySelector('.tab.active');
  const focused = document.querySelector('.order-group-focus, .order-group');
  return { view: currentView, tab: activeTab ? activeTab.dataset.view : null,
           focusOid: focused ? focused.getAttribute('data-oid') : null,
           groupsShown: document.querySelectorAll('.order-group').length };
}
```

Expected: `view='orders'`、`tab='orders'`（tab 高亮真的跟着切了）、`focusOid` 等于被点徽章所在记录的 `source_order_id`、`groupsShown=3`。

- [ ] **Step 5: 提交**

```bash
git add index.html
git commit -m "feat(ledger): jump from model badge to its xianyu order group"
```

---

## Task 8: 拆单弹窗

**Files:**
- Modify: `index.html`（`#modal` 结构之后新增 `#splitModal`；HTML 约 `:1010` 附近）
- Modify: `index.html`（JS 新增 `openSplitModal` / `submitSplit`）

- [ ] **Step 1: 弹窗骨架**

在现有 `</div><!-- /modal -->` 之后插入（沿用 `.modal` / `.modal-card` 类，不新造视觉）：

```html
<div class="modal" id="splitModal">
  <div class="modal-card">
    <h3>拆成多条</h3>
    <div class="split-hint" id="splitHint"></div>
    <div id="splitRows"></div>
    <div class="split-foot">
      <button class="btn ghost" id="splitAddRow">＋ 加一行</button>
      <span id="splitSummary"></span>
      <button class="btn ghost" id="splitCancel">取消</button>
      <button class="btn primary" id="splitSubmit">保存拆分</button>
    </div>
  </div>
</div>
```

CSS：

```css
.split-hint { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
.split-row { display: grid; grid-template-columns: 1fr 1.6fr 90px 32px; gap: 8px; margin-bottom: 8px; }
.split-row input { width: 100%; }
.split-del { background: transparent; border: none; color: var(--magenta); cursor: pointer; font-size: 15px; }
.split-foot { display: flex; align-items: center; gap: 10px; margin-top: 12px; font-size: 12px; }
.split-foot #splitSummary { margin-left: auto; color: var(--muted); }
.split-diff { color: var(--gold); }
```

- [ ] **Step 2: JS 逻辑**

放在 `locateOrder` 之后：

```javascript
let _splitRows = [], _splitOid = null, _splitPaid = null;

function orderGroupsIndex() {
  const map = new Map();
  for (const r of items) {
    if (!r.source_order_id) continue;
    if (!map.has(r.source_order_id)) map.set(r.source_order_id, []);
    map.get(r.source_order_id).push(r);
  }
  return map;
}

function openSplitModal(oid) {
  const recs = orderGroupsIndex().get(oid) || [];
  if (!recs.length) { showToast('找不到这笔订单的记录'); return; }
  _splitRows = recs.map(r => ({ idx: r.id, brand: r.brand, model: r.model, cost: r.cost }));
  _splitOid = oid;
  const withPaid = recs.find(r => r.order_paid !== null);
  _splitPaid = withPaid ? withPaid.order_paid : null;
  document.getElementById('splitModal').classList.add('active');
  renderSplitRows();
}

function renderSplitRows() {
  const brandOptions = [...new Set(items.map(i => i.brand).filter(b => b))];
  document.getElementById('splitRows').innerHTML = _splitRows.map((row, n) => `
    <div class="split-row" data-row="${n}">
      <input list="brandOptions" placeholder="品牌" value="${esc(row.brand)}" oninput="onSplitCell(${n},'brand',this.value)">
      <input placeholder="型号" value="${esc(row.model)}" oninput="onSplitCell(${n},'model',this.value)">
      <input type="number" step="1" placeholder="买价" value="${row.cost}" oninput="onSplitCell(${n},'cost',this.value)">
      <button class="split-del" title="删掉这一行" onclick="removeSplitRow(${n})" ${_splitRows.length <= 1 ? 'disabled' : ''}>×</button>
    </div>`).join('') +
    `<datalist id="brandOptions">${brandOptions.map(b => `<option value="${esc(b)}"></option>`).join('')}</datalist>`;
  renderSplitSummary();
}

function onSplitCell(n, key, value) {
  _splitRows[n][key] = key === 'cost' ? safeFloat(value) : value;
  renderSplitSummary();
}

function addSplitRow() { _splitRows.push({ idx: -1, brand: '', model: '', cost: 0 }); renderSplitRows(); }

function removeSplitRow(n) {
  if (_splitRows.length <= 1) return;
  _splitRows.splice(n, 1);
  renderSplitRows();
}

function renderSplitSummary() {
  const total = _splitRows.reduce((s, r) => s + safeFloat(r.cost), 0);
  const parts = [`合计 ¥${fmt(total)}`];
  if (_splitPaid !== null && _splitPaid !== undefined) {
    const diff = _splitPaid - total;
    parts.push(`订单实付 ¥${fmt(_splitPaid)}`);
    parts.push(Math.abs(diff) < 0.5
      ? '<span class="split-diff">已对齐</span>'
      : `<span class="split-diff">差额 ¥${fmt(diff)}</span>`);
  }
  parts.push(`${_splitRows.length} 条`);
  document.getElementById('splitSummary').innerHTML = parts.join(' · ');
}

async function submitSplit() {
  const parts = _splitRows.map(r => ({ brand: String(r.brand).trim(), model: String(r.model).trim(), cost: safeFloat(r.cost) }));
  const bad = parts.findIndex(p => !p.brand || !p.model);
  if (bad >= 0) { showToast(`第 ${bad + 1} 行的品牌和型号不能为空`); return; }
  if (parts.length < 2) { showToast('拆分至少要两行；只改一条请用行内编辑'); return; }
  const resp = await fetch('/api/split_record', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idx: _splitRows[0].idx, parts }),
  });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok || !json.ok) {
    showToast(json.error ? `拆分失败：${json.error}` : `拆分失败：HTTP ${resp.status}`);
    return;  // 保留弹窗与已填内容，不重拉列表
  }
  closeSplitModal();
  await fetchItems();
  showToast(`已拆成 ${parts.length} 条`);
  currentView = 'orders';
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === 'orders'));
  render();
}

function closeSplitModal() { document.getElementById('splitModal').classList.remove('active'); }
```

- [ ] **Step 3: 绑定按钮与遮罩关闭**

在既有事件绑定区（`modalSubmit` 监听附近，约 `index.html:1699`）加：

```javascript
document.getElementById('splitAddRow').addEventListener('click', addSplitRow);
document.getElementById('splitSubmit').addEventListener('click', submitSplit);
document.getElementById('splitCancel').addEventListener('click', closeSplitModal);
document.getElementById('splitModal').addEventListener('click', e => {
  if (e.target === document.getElementById('splitModal')) closeSplitModal();
});
```

`#splitHint` 的静态文案直接写在 HTML 里：

```html
<div class="split-hint" id="splitHint">一笔订单里其实是几件硬件时，把它们拆成多行，每行各填自己的买价。</div>
```

- [ ] **Step 4: 检查与既有全局监听的冲突**

`index.html:1731-1736` 有个"弹窗打开时回车=点提交"的全局 keydown，它只判断 `#modal`，不判断 `#splitModal`。执行下列断言确认没有互相干扰：

```javascript
() => {
  currentView = 'orders'; render();
  document.querySelector('.order-split-btn').click();
  const splitOpen = document.getElementById('splitModal').classList.contains('active');
  const modalOpen = document.getElementById('modal').classList.contains('active');
  document.querySelector('.split-del').click();
  return { splitOpen, modalOpen, rows: document.querySelectorAll('.split-row').length,
           summary: document.getElementById('splitSummary').textContent };
}
```

Expected: `splitOpen=true`、`modalOpen=false`（拆单弹窗不能顺手把编辑弹窗也打开）、`rows=1`（A1 两组两行，删一行后剩 1）、`summary` 含"合计"。

- [ ] **Step 5: 端到端验证（写的是夹具临时文件，允许）**

```javascript
async () => {
  currentView = 'orders'; render();
  const a1 = [...document.querySelectorAll('.order-group')].find(g => g.dataset.oid === 'A1');
  a1.querySelector('.order-split-btn').click();
  const rows = document.querySelectorAll('.split-row');
  rows[1].children[2].value = 200;
  rows[1].children[2].dispatchEvent(new Event('input', { bubbles: true }));
  document.getElementById('splitSubmit').click();
  await new Promise(r => setTimeout(r, 900));
  const raw = await (await fetch('/api/data')).json();
  const mine = raw.filter(r => r.source_order_id === 'A1');
  return { count: mine.length, paid: [...new Set(mine.map(r => r.order_paid))],
           costs: mine.map(r => r.cost), groups: document.querySelectorAll('.order-group').length };
}
```

Expected: `count=2`、`paid=[600]`（**基线没变**）、`costs=[600,200]`、`groups=3`。

- [ ] **Step 6: 提交**

```bash
git add index.html
git commit -m "feat(ledger): split-order modal with live total vs paid diff"
```

---

## Task 9: 品牌选择器（选择为主 + 新品牌确认）

**Files:**
- Modify: `index.html`（`#fBrand` 输入框处，约 `:995`）
- Modify: `index.html`（JS 新增品牌面板逻辑 + 改全局回车监听 `:1731-1736`）

必须处理的既有事实：`#fBrand` 有 4 个写入方（`fTemplate` change `:1743`、智能解析 `:1923`、聊天面板 `fieldMap` `:2001`、`openEditModal` `:1663`），保留输入框当唯一真值源，四条链路才不用改。

- [ ] **Step 1: 建议面板 DOM**

把 `#fBrand` 那一行从：

```html
        <input id="fBrand" placeholder="微星 / 华硕 / 技嘉...">
```

改成（外层套一个 relative 容器，面板插在输入框下方）：

```html
        <div class="field-pop" id="brandWrap">
          <input id="fBrand" placeholder="从列表选，或输入新品牌" autocomplete="off">
          <div class="brand-pop" id="brandPop" role="listbox" aria-label="已有品牌"></div>
        </div>
```

CSS：

```css
.field-pop { position: relative; }
.brand-pop { display: none; position: absolute; z-index: 30; top: calc(100% + 4px); left: 0; right: 0;
  max-height: 208px; overflow-y: auto; background: rgba(10,14,22,0.97);
  border: 1px solid rgba(0,229,255,0.22); border-radius: 8px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.55); }
.brand-pop.active { display: block; }
.brand-item { display: flex; justify-content: space-between; gap: 10px; padding: 6px 10px; font-size: 12px; cursor: pointer; }
.brand-item:hover, .brand-item.sel { background: rgba(0,229,255,0.08); color: var(--cyan); }
.brand-item em { font-style: normal; color: var(--muted); }
.brand-new { border-top: 1px solid rgba(255,215,64,0.22); color: var(--gold); }
.brand-current { box-shadow: inset 2px 0 0 var(--magenta); }
```

- [ ] **Step 2: 面板状态机**

放在 `fetchPriceHint` 附近：

```javascript
let _brandItems = [], _brandSel = 0, _brandOpen = false, _brandPendingNew = false;

function existingBrands() {
  const counts = new Map();
  for (const r of items) {
    const b = (r.brand || '').trim();
    if (!b) continue;
    counts.set(b, (counts.get(b) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function openBrandPop() {
  _brandOpen = true;
  renderBrandPop();
  document.getElementById('brandPop').classList.add('active');
}

function closeBrandPop() {
  _brandOpen = false;
  document.getElementById('brandPop').classList.remove('active');
}

function renderBrandPop() {
  const input = document.getElementById('fBrand');
  const q = input.value.trim().toLowerCase();
  const all = existingBrands();
  _brandItems = q ? all.filter(([b]) => b.toLowerCase().includes(q)) : all;
  const exact = all.some(([b]) => b.toLowerCase() === q && q);
  _brandSel = Math.min(_brandSel, Math.max(0, _brandItems.length - 1));
  const rows = _brandItems.map(([b, n], i) => `
    <div class="brand-item${i === _brandSel ? ' sel' : ''}${b === input.value.trim() ? ' brand-current' : ''}"
         role="option" data-i="${i}" onmousedown="pickBrand(${i}); return false;"><span>${esc(b)}</span><em>${n} 条</em></div>`).join('');
  const create = (!q || !exact) && input.value.trim()
    ? `<div class="brand-item brand-new" role="option" onmousedown="confirmNewBrand(); return false;">＋ 以新品牌 “${esc(input.value.trim())}” 创建</div>`
    : '';
  document.getElementById('brandPop').innerHTML = rows + (create || (!rows ? '<div class="brand-item"><em>没有匹配品牌，直接输入新名字</em></div>' : ''));
}

function pickBrand(i) {
  const entry = _brandItems[i];
  if (!entry) return;
  const input = document.getElementById('fBrand');
  input.value = entry[0];
  _brandPendingNew = false;
  closeBrandPop();
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function confirmNewBrand() {
  _brandPendingNew = true;
  closeBrandPop();
  trySubmitModal();
}
```

- [ ] **Step 3: 拆掉回车自动提交的冲突**

当前 `index.html:1731-1736`：

```javascript
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
  if (e.key === 'Enter' && document.getElementById('modal').classList.contains('active')) {
    document.getElementById('modalSubmit').click();
  }
});
```

替换为（面板开着时回车只做选择，绝不提交；Esc 只关面板）：

```javascript
function trySubmitModal() { document.getElementById('modalSubmit').click(); }

document.addEventListener('keydown', e => {
  const modalOpen = document.getElementById('modal').classList.contains('active');
  if (!modalOpen) return;
  const onBrand = document.activeElement === document.getElementById('fBrand');
  if (e.key === 'Escape') {
    if (_brandOpen) { e.stopPropagation(); closeBrandPop(); return; }
    closeModal();
    return;
  }
  if (e.key !== 'Enter') return;
  if (_brandOpen && onBrand) {
    e.preventDefault();
    e.stopPropagation();
    if (_brandItems[_brandSel]) pickBrand(_brandSel);
    return;
  }
  e.preventDefault();
  trySubmitModal();
}, true);   // capture：抢在既有冒泡监听之前
```

> 说明：必须用捕获阶段。旧监听是文档级冒泡，若在冒泡里 `preventDefault`，提交仍会被旧代码触发一次（`modalSubmit.click()`）。同时**不要删掉旧的 `Escape` 分支**之外的那段——上面这段整体替换它，避免两个 Enter 处理器并存。

`#fBrand` 上加焦点/输入绑定（放在 `renderBrandPop` 之后）：

```javascript
document.getElementById('fBrand').addEventListener('focus', () => { _brandSel = 0; openBrandPop(); });
document.getElementById('fBrand').addEventListener('input', () => { _brandSel = 0; if (_brandOpen) renderBrandPop(); });
document.getElementById('fBrand').addEventListener('blur', () => setTimeout(closeBrandPop, 120));
document.getElementById('fBrand').addEventListener('keydown', e => {
  if (!document.getElementById('brandPop').classList.contains('active')) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const n = _brandItems.length || 1;
    _brandSel = (e.key === 'ArrowDown' ? (_brandSel + 1) % n : (_brandSel - 1 + n) % n);
    renderBrandPop();
  }
});
```

- [ ] **Step 4: 新品牌确认条**

`modalSubmit` 处理器（约 `index.html:1699-1712`）开头插入确认逻辑：

```javascript
  const brandInput = document.getElementById('fBrand');
  const brand = brandInput.value.trim();
  if (brand && _brandPendingNew) {
    if (!confirm(`账本里还没有「${brand}」这个品牌，确认按新品牌保存？`)) { _brandPendingNew = false; return; }
  }
  _brandPendingNew = false;
```

（确认用 `confirm()` 而不是新造 UI：桌面 webview 里它已被 `confirmDelete` 用过，风格一致且零新状态。金色内联确认条留给以后统一做 toast 组件时替换。）

- [ ] **Step 5: 验证三件事**

夹具页执行。注意两点：`openEditModal` 会预填已有品牌，而面板按输入值过滤，所以要看全量列表必须先清空输入框；脚本里不要再用 `items` 这个名字，它会遮蔽全局 `items` 数组。

```javascript
() => {
  currentView = 'brands'; render();
  openEditModal(0);
  const input = document.getElementById('fBrand');
  input.focus();
  input.value = '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  const pop = document.getElementById('brandPop');
  const rows = [...pop.querySelectorAll('.brand-item:not(.brand-new)')];
  return { open: pop.classList.contains('active'),
           shown: rows.length,
           expected: [...new Set(items.map(i => (i.brand || '').trim()).filter(b => b))].length,
           ordered: rows.map(r => r.textContent.trim()).slice(0, 3) };
}
```

Expected: `open=true`、`shown === expected`（fixture 上应为 5，两侧数字都要打印）、`ordered[0]` 以"微星"开头并带"条"计数（它 1 条…按条数降序时并列则按拼音）。

聚焦即展开面板是用户选定的"选择为主"行为，评审提过"开弹窗就被面板挡住"的担忧 —— 这里按用户决定保留，不改成输入后才展开。

再验回车只选不提交：

```javascript
async () => {
  openEditModal(0);
  const input = document.getElementById('fBrand');
  const before = (await (await fetch('/api/data')).json()).map(r => r.brand + '|' + r.model);
  input.value = '致态';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await new Promise(r => setTimeout(r, 400));
  const after = (await (await fetch('/api/data')).json()).map(r => r.brand + '|' + r.model);
  return { value: input.value, popClosed: !document.getElementById('brandPop').classList.contains('active'),
           untouched: JSON.stringify(before) === JSON.stringify(after), modalOpen: document.getElementById('modal').classList.contains('active') };
}
```

Expected: `value='致态'`、`popClosed=true`、**`untouched=true`**（回车没写盘）、`modalOpen=true`。这一条就是规格 §9-14b 的实现前必须失败项。

再验"新品牌要确认"：把 `input.value` 设成 `不存在的牌子`、回车 → 断言面板出现且只出现一行 `.brand-new`、`confirm` 被调用（用 `window.confirm = () => false` 桩住，断言 `data.json` 未变）。

- [ ] **Step 6: 提交**

```bash
git add index.html
git commit -m "feat(ledger): brand picker over #fBrand, Enter selects instead of submitting

#fBrand keeps its four writers (template, smart parse, chat, edit modal), so the
picker augments the existing input instead of replacing it. The global Enter
handler had to move to capture phase or selecting a brand would save the record."
```

---

## Task 10: 回填 18 条已有闲鱼记录

**Files:**
- Create: `xianyu_backfill.py`
- Create: `tests/test_backfill.py`

- [ ] **Step 1: 写失败测试（纯函数，不碰 MySQL）**

创建 `tests/test_backfill.py`：

```python
import xianyu_backfill as bf


def test_backfill_fills_only_missing_fields():
    ledger = [
        {"brand": "微星", "model": "B650M", "source_order_id": "A1"},                 # 缺全部
        {"brand": "光威", "model": "16G", "source_order_id": "B2", "order_date": "旧"},  # 已有日期不动
        {"brand": "AMD", "model": "7800X3D"},                                          # 手动记录，完全跳过
    ]
    meta = {"A1": {"order_date": "2025-09-21 14:32:05", "item_title": "迫击炮", "price": 600},
            "B2": {"order_date": "2025-09-22 10:00:00", "item_title": "神策", "price": 800}}
    plan, missing = bf.build_updates(ledger, meta)
    assert plan == {0: {"order_date": "2025-09-21 14:32:05", "item_title": "迫击炮", "order_paid": 600}}
    assert missing == []


def test_backfill_reports_misses_instead_of_blanking():
    ledger = [{"brand": "X", "model": "Y", "source_order_id": "Z9"}]
    plan, missing = bf.build_updates(ledger, {})
    assert plan == {}
    assert missing == ["Z9"], "MySQL 查不到必须报出来，不能写空串掩盖"


def test_backfill_datetime_is_stringified():
    import datetime

    ledger = [{"brand": "微星", "model": "B", "source_order_id": "A1"}]
    meta = {"A1": {"order_date": datetime.datetime(2025, 9, 21, 14, 32, 5),
                   "item_title": "板", "price": 600}}
    plan, _ = bf.build_updates(ledger, meta)
    assert plan[0]["order_date"] == "2025-09-21 14:32:05"
    import json
    json.dumps(plan)  # 序列化不炸，回填才不会留下半截文件
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -X utf8 -m pytest tests/test_backfill.py -v`
Expected: `ModuleNotFoundError: No module named 'xianyu_backfill'`

- [ ] **Step 3: 实现脚本**

创建 `xianyu_backfill.py`：

```python
"""回填 data.json 里缺失的闲鱼订单上下文：order_date / item_title / order_paid。

默认 dry-run，只打印计划，不写盘。--apply 才写，并先留 data.json.pre_backfill。
    python -X utf8 xianyu_backfill.py
    python -X utf8 xianyu_backfill.py --apply
"""
import argparse
import datetime
import json
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
FIELDS = ("source_order_id", "order_date", "item_title", "order_paid")


def build_updates(ledger, meta_by_id):
    """返回 (行号 -> 将写入的字段, 查不到元信息的 order_id 列表)。只补缺，不覆盖已有值。"""
    plan = {}
    missing = []
    for i, record in enumerate(ledger):
        oid = str(record.get("source_order_id") or "")
        if not oid:
            continue
        needs = [k for k in ("order_date", "item_title", "order_paid") if not record.get(k)]
        if not needs:
            continue
        meta = meta_by_id.get(oid)
        if not meta:
            missing.append(oid)
            continue
        writes = {}
        for key, source in (("order_date", "order_date"), ("item_title", "item_title"), ("order_paid", "price")):
            if key not in needs:
                continue
            value = meta.get(source)
            if isinstance(value, (datetime.datetime, datetime.date)):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            writes[key] = value
        if writes:
            plan[i] = writes
    return plan, missing


def load_meta(order_ids):
    """从 MySQL 取订单元信息。凭据复用 xianyu_review.DB_CONF，本文件不落新口令。"""
    import pymysql
    sys.path.insert(0, BASE_DIR)
    from xianyu_review import DB_CONF

    ids = sorted({str(i) for i in order_ids if i})
    if not ids:
        return {}
    conn = pymysql.connect(**DB_CONF)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            marks = ",".join(["%s"] * len(ids))
            cur.execute(
                "SELECT order_id, order_date, item_title, price FROM xianyu_orders "
                "WHERE order_id IN (%s)" % marks, ids)
            rows = cur.fetchall()
    finally:
        conn.close()
    return {str(r["order_id"]): r for r in rows}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真的写盘（默认只打印计划）")
    args = parser.parse_args(argv)

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        ledger = json.load(f)
    targets = [r.get("source_order_id") for r in ledger if r.get("source_order_id")]
    print("xianyu records: %d" % len(targets))
    meta = load_meta(targets)
    print("mysql hits: %d / %d distinct ids" % (len(meta), len(set(targets))))
    plan, missing = build_updates(ledger, meta)
    for i, writes in sorted(plan.items()):
        title = str(writes.get("item_title") or "")[:20]
        print("  #%d %s  %s  %r" % (i, writes.get("order_date", ""), writes.get("order_paid", ""), title))
    print("would update: %d records" % len(plan))
    if missing:
        print("NOT FOUND in mysql: %d -> %s" % (len(missing), ", ".join(missing[:10])))
    if not args.apply:
        print("dry-run only; pass --apply to write")
        return 0
    if not plan:
        print("nothing to write")
        return 0

    shutil.copy2(DATA_FILE, DATA_FILE + ".pre_backfill")
    for i, writes in plan.items():
        ledger[i].update(writes)
    payload = json.dumps(ledger, ensure_ascii=False, indent=2)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, DATA_FILE)
    print("wrote %d records; backup at data.json.pre_backfill" % len(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -X utf8 -m pytest tests/test_backfill.py -v`
Expected: 3 passed

- [ ] **Step 5: 对真实数据跑 dry-run（只读）**

```bash
python -X utf8 -c "import json;b=open('data.json','rb').read();import hashlib;print('sha', hashlib.sha256(b).hexdigest()[:16])" && python -X utf8 xianyu_backfill.py && python -X utf8 -c "import hashlib;print('sha', hashlib.sha256(open('data.json','rb').read()).hexdigest()[:16])"
```

Expected: 两条 `sha` **必须相同**（dry-run 没写盘），脚本输出含 `xianyu records: 18`、`would update: 18`、以及 18 行计划。若 `NOT FOUND in mysql` 非空，把那些 order_id 原样报告给用户，不要填空值糊过去。

- [ ] **Step 6: 取得用户明确同意后再 `--apply`**

这一步会改他的真实账本，必须先把 dry-run 的 18 行计划贴给他并得到"执行"的回复。执行后核验：

```bash
python -X utf8 -c "import json;d=json.load(open('data.json',encoding='utf-8'));print('records=%d xianyu=%d dated=%d titles=%d paid=%d'%(len(d),sum(1 for r in d if r.get('source_order_id')),sum(1 for r in d if r.get('order_date')),sum(1 for r in d if r.get('item_title')),sum(1 for r in d if r.get('order_paid'))))"
```

Expected: `records=255 xianyu=18 dated=18 titles=18 paid=18`（MySQL 有未命中时按实际数字报告），且 `data.json.pre_backfill` 存在。

- [ ] **Step 7: 提交**

```bash
git add xianyu_backfill.py tests/test_backfill.py
git commit -m "feat(ledger): backfill order context for already-imported records

Dry-run by default, leaves data.json.pre_backfill before writing, and reports
order ids MySQL cannot resolve instead of blanking the fields."
```

---

## Task 11: 回归基线 + 真实窗口验收 + 收尾

**Files:**
- Create: `tests/test_ledger_regression.py`

- [ ] **Step 1: 写聚合口径回归测试**

创建 `tests/test_ledger_regression.py`：

```python
"""新字段不得进入既有聚合：品牌总览 / 芯片组 / 排行榜 / 统计卡的数字必须不变。"""
import json


def test_manual_records_are_untouched_by_context_fields():
    with open("data.json", "r", encoding="utf-8") as f:
        ledger = json.load(f)
    manual = [r for r in ledger if not r.get("source_order_id")]
    assert manual, "账本里没有手动记录，这条测试失去意义"
    assert not any(k in r for r in manual for k in ("order_date", "item_title", "order_paid")), \
        "手动记录被塞了订单字段，会污染分组判据"


def test_update_stats_never_uses_order_paid():
    """order_paid 是一条订单的实付，按记录累加会让拆单后的成本翻倍。
    这条是静态守卫：谁把它加进聚合就会失败。"""
    with open("index.html", encoding="utf-8") as f:
        html = f.read()
    start = html.index("function updateStats()")
    body = html[start:html.index("\n}", start)]
    assert "order_paid" not in body, "统计卡把实付金额算进了合计，拆单后数字会翻倍"


def test_baseline_totals_for_page_comparison():
    """打印基线数字，Task 11 Step 2 拿它和页面上的统计卡逐位对照。"""
    with open("data.json", "r", encoding="utf-8") as f:
        ledger = json.load(f)
    total_cost = sum(float(r.get("cost") or 0) for r in ledger)
    total_sell = sum(float(r.get("sell") or 0) for r in ledger)
    assert total_cost > 0 and total_sell > 0
    print("cost=%d sell=%d profit=%d" % (total_cost, total_sell, total_sell - total_cost))
```

- [ ] **Step 2: 跑起来，把打印的数字与页面核对**

Run: `python -X utf8 -m pytest tests/test_ledger_regression.py -v -s`
Expected: 两条测试 PASS，打印 `cost=... sell=... profit=...`。浏览器里用
```javascript
() => document.getElementById('statsGrid').textContent.replace(/\s+/g, ' ').trim()
```
读出统计卡，**逐位比对**总成本 / 总售价 / 总利润（页面用 `fmt()` 带千分位，比对时把页面数字里的逗号去掉再转整数；不要凭眼睛判断"差不多"）。任何一项不一致就是回归，停下来查是哪块聚合把新字段算进去了。

- [ ] **Step 3: 真实窗口端到端清单（必须重启后端）**

重启 runbook（PowerShell 的 `$` 会被 Bash 吞，用 Python）：

```bash
python -X utf8 -c "import subprocess,sys,os,time; \
net=subprocess.run(['netstat','-ano','-p','TCP'],capture_output=True,text=True,errors='replace').stdout; \
pids={int(l.split()[-1]) for l in net.splitlines() if ':8765' in l and 'LISTENING' in l}; \
[subprocess.run(['taskkill','/PID',str(p),'/F'],capture_output=True) for p in pids]; time.sleep(1.5); \
subprocess.Popen([sys.executable,'-X','utf8',r'G:\claude code\app_standalone.py'],cwd=r'G:\claude code'); \
time.sleep(6); print('8765 listening:', any(':8765' in l and 'LISTENING' in l for l in subprocess.run(['netstat','-ano','-p','TCP'],capture_output=True,text=True,errors='replace').stdout.splitlines()))"
```

Expected: `8765 listening: True`。然后在真实窗口里逐条走（每条都要给出通过/失败，不许笼统说"看起来正常"）：

1. 「闲鱼订单」tab 分组数 == `data.json` 里 `source_order_id` 去重数（两边数字都打印）；组头显示日期、原标题、实付、入账。
2. 组头「拆成多条」拆一条成 2 行 → 组内 2 行、`入账` 与拆前一致、两条都有 `闲鱼` 徽章、实付不变。
3. 组内改买价保存 → 关窗口重开（或刷新）→ 值仍在，且 `order_date` / `item_title` / `order_paid` 没被抹掉。
4. 「型号明细」点徽章 → 跳到「闲鱼订单」并定位该分组。
5. 手动新增一条记录 → 不出现在「闲鱼订单」tab；品牌面板里没有它的残留。
6. 编辑弹窗里输入不存在品牌 → 回车只选中不提交；只有点 `＋ 以新品牌` 并确认后才写入。

第 2 条会在真实数据上新增一条记录 —— **拆单验证必须让他自己在场时做**，或者只在 fixture（8790）上做。默认只在 fixture 上验证，真实窗口跑 1/3/4/5/6，跑不了的就如实报告"未执行 + 原因"。

- [ ] **Step 4: 全量测试**

Run: `python -X utf8 -m pytest tests/ -v`
Expected: 全部 PASS，用例数打印出来（`test_ledger_api` + `test_backfill` + `test_review_import` + `test_ledger_regression`）。任何 skip 都要说明原因。

- [ ] **Step 5: 提交**

```bash
git add tests/test_ledger_regression.py
git commit -m "test(ledger): pin existing aggregates against the new order fields"
```

---

## 完成标准（全部满足才算做完）

- [ ] `python -X utf8 -m pytest tests/ -v` 全绿，且 `tests/test_ledger_api.py` 里那条空 `sell` 的测试在 Task 1 Step 6 确实失败过一次
- [ ] 真实 `data.json`：`records=255`、`xianyu=18`，回填后 18 条都有 `order_date`/`item_title`/`order_paid`，且 `data.json.pre_backfill` 存在
- [ ] 「闲鱼订单」tab 分组数等于 `source_order_id` 去重数，同一天两笔订单是**两个分组**
- [ ] 拆单只产生一次写盘（`.bak` 的 sha256 等于拆单前的 `data.json`）
- [ ] 品牌选择：面板开着按回车不提交；不存在品牌需显式确认才入库；无任何别名自动改写
- [ ] `git status --short` 里没有 `data.json`、`xianyu_review.py`、`xianyu_scraper.py`、`xianyu_review.html`
- [ ] 未 push（仓库无 remote），提交作者 `No_object <no_object@local>`


