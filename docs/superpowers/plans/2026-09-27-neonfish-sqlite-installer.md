# 霓虹鱼 SQLite 化 + 一键安装器 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把抓单/填入的存储从 MySQL 换成本机单文件 SQLite，并交付一个双击即用的安装器 + 四行自检表。

**Architecture:** 新增 `orders_db.py` 作为唯一存储层（连接、建表、写入、读取、计数），`xianyu_scraper.py` 与 `xianyu_review.py` 不再自己连库；MySQL 配置层（`db_config.py` / `db_secret.example.py` / `import_to_mysql.py`）整体退役；`installer.py` + `setup.bat` 搭环境，`selfcheck.py` 体检，两者都用依赖注入做到可单测、可失败。

**Tech Stack:** Python 3.13（stdlib `sqlite3` / `urllib` / `venv`）、pytest、Playwright（仅抓单侧）。

规格：`docs/superpowers/specs/2026-09-27-neonfish-sqlite-installer-design.md`（commit `f38cd8f`）

**本仓库约定（每个任务都适用）**
- 工作目录固定 `G:\claude code`（路径含空格，命令必须加引号），Git Bash。
- 测试一律 `python -X utf8 -m pytest ...`；裸 `pytest` 会收到隔壁项目。
- 提交只 `git add <文件名>`，**禁止** `git add -A` / `git add .`；提交信息走 heredoc，正文别用英文双引号。
- `data.json` 是他在用的真实账本，**任何任务都不许改它**。
- 校验器必须能失败：每个"闸门 / 自检"类任务都要有一步人为制造坏条件、确认变红、再撤销。

**已实测的事实（计划据此写成，不需要执行者再猜）**
- `app_standalone.py:26` 是 `PORT = 8765`，`GET /api/data` 返回账本数组（`app_standalone.py:488`）。
- SQLite 撞唯一键的消息是 `UNIQUE constraint failed: <表>.<列>`，撞检查约束是 `CHECK constraint failed: ...` —— 两者可区分，去重判定不能吞掉后者。
- `xianyu_review.split_title("微星 h610 坏板 出售")` → `('微星', 'h610 坏板')`。
- MySQL 侧 `neon_ledger.xianyu_orders` 现有 31 行；本机 root 口令已轮换，值只在 `db_secret.py`。
- Python 3.12 起 `sqlite3` 的 date/datetime 适配器已废弃 → 日期必须自己格式化成文本。

**文件结构**

| 文件 | 动作 | 责任 |
| --- | --- | --- |
| `orders_db.py` | 新建 | 抓单库唯一的连接 / 建表 / 写入 / 读取 / 计数 |
| `tests/test_orders_db.py` | 新建 | 存储层行为：去重、约束、日期文本化、排序、JSON 往返 |
| `migrate_orders_to_sqlite.py` | 新建（跑完删） | MySQL→SQLite 一次性搬迁 + 四条一致性断言 |
| `tests/test_migrate_orders.py` | 新建 | 断言函数本身能抓出四类不一致（不需要真 MySQL） |
| `xianyu_scraper.py` | 修改 | 去 pymysql / db_config / 建表 SQL，改用 orders_db |
| `xianyu_review.py` | 修改 | 去掉 pymysql 与 db_config；`fetch_orders` 改读 orders_db |
| `tests/test_orders_backend.py` | 新建 | 静态契约（不许再有 MySQL 痕迹）+ 两侧行为 |
| `db_config.py`、`db_secret.example.py`、`import_to_mysql.py` | 删除 | MySQL 配置层退役 |
| `.gitignore`、`requirements.txt` | 修改 | 忽略 `orders.db`；去 pymysql |
| `tests/test_repo_secrets.py` | 修改 | MySQL 专用两条 → orders.db / 配置层复活 两条 |
| `selfcheck.py` / `tests/test_selfcheck.py` | 新建 | 四行体检，每行能 ok / fail / skip |
| `installer.py` / `tests/test_installer.py` / `setup.bat` | 新建 | 步骤表 + 顺序执行 + 失败即停 |
| `README.md` | 修改 | 安装章节 + 换机要搬的东西 |

---

### Task 1: 存储层 `orders_db.py`

**Files:**
- Create: `orders_db.py`
- Test: `tests/test_orders_db.py`

- [ ] **Step 1: 写失败的测试** — 创建 `tests/test_orders_db.py`

```python
"""抓单存储层：只测行为，不测 SQL 写法。"""

import sqlite3
from datetime import datetime

import pytest

import orders_db


def make_conn(tmp_path):
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    return conn


def order(oid="3316448967004022690", **kw):
    base = {
        "order_id": oid,
        "item_title": "微星PRO H610M-E DDR4主板",
        "price": 100.0,
        "trade_type": "bought",
        "counterparty": "卖家甲",
        "order_status": "交易成功",
        "order_date": datetime(2026, 9, 20, 14, 5, 6),
        "images": ["images/a.jpg"],
        "raw_data": {"bizOrderId": oid},
    }
    base.update(kw)
    return base


def test_schema_is_reentrant(tmp_path):
    conn = make_conn(tmp_path)
    orders_db.ensure_schema(conn)          # 第二次不炸
    assert orders_db.count_orders(conn) == 0


def test_insert_returns_new_then_dup(tmp_path):
    conn = make_conn(tmp_path)
    assert orders_db.insert_order(conn, order()) == "new"
    conn.commit()
    assert orders_db.insert_order(conn, order()) == "dup"
    conn.commit()
    assert orders_db.count_orders(conn) == 1


def test_duplicate_does_not_break_the_batch(tmp_path):
    conn = make_conn(tmp_path)
    assert orders_db.insert_orders(conn, [order(), order(oid="other"), order()]) == (2, 1)
    assert orders_db.count_orders(conn) == 2


def test_bad_trade_type_raises_instead_of_counting_as_dup(tmp_path):
    """只有 order_id 撞唯一键才算重复；CHECK 违反必须抛出来。

    否则拼错 trade_type 会被静默记成 dup，数据悄悄少一条。
    """
    conn = make_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        orders_db.insert_order(conn, order(trade_type="退掉"))


def test_order_date_is_stored_as_text(tmp_path):
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    kind, value = conn.execute(
        "SELECT typeof(order_date), order_date FROM xianyu_orders").fetchone()
    assert kind == "text"
    assert value == "2026-09-20 14:05:06"


def test_null_date_and_null_order_id_stay_null(tmp_path):
    """SQLite 与 InnoDB 一样把 NULL 视为互不相等，缺 order_id 的脏数据不互相撞键。"""
    conn = make_conn(tmp_path)
    assert orders_db.insert_order(conn, order(order_id=None, order_date=None)) == "new"
    assert orders_db.insert_order(conn, order(order_id=None, order_date=None)) == "new"
    conn.commit()
    rows = conn.execute("SELECT order_id, order_date FROM xianyu_orders").fetchall()
    assert [tuple(r) for r in rows] == [(None, None), (None, None)]


def test_images_and_raw_data_survive_as_objects(tmp_path):
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    got = orders_db.fetch_orders(conn)[0]
    assert got["images"] == ["images/a.jpg"]
    assert got["raw_data"] == {"bizOrderId": "3316448967004022690"}


def test_fetch_orders_sorted_newest_first(tmp_path):
    conn = make_conn(tmp_path)
    orders_db.insert_orders(conn, [
        order(oid="old", order_date=datetime(2026, 1, 1, 0, 0, 0)),
        order(oid="new", order_date=datetime(2026, 9, 9, 9, 9, 9)),
        order(oid="nodate", order_date=None),
    ])
    conn.commit()
    ids = [r["order_id"] for r in orders_db.fetch_orders(conn)]
    assert ids == ["new", "old", "nodate"]      # 空日期沉底，不参与时间比较


def test_fetch_orders_keys_are_the_legacy_ones(tmp_path):
    """填单台按这批键名取值，改名就是自找麻烦。"""
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    assert set(orders_db.fetch_orders(conn)[0]) == {
        "order_id", "item_title", "price", "trade_type",
        "counterparty", "order_status", "order_date",
    }


def test_fetch_orders_full_adds_the_two_json_columns(tmp_path):
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    assert set(orders_db.fetch_orders_full(conn)[0]) == set(orders_db.COLS)


def test_to_date_text_forms():
    assert orders_db.to_date_text(datetime(2026, 9, 20, 14, 5, 6)) == "2026-09-20 14:05:06"
    assert orders_db.to_date_text("2026-09-20 14:05:06") == "2026-09-20 14:05:06"
    assert orders_db.to_date_text(None) is None
    assert orders_db.to_date_text("") is None
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python -X utf8 -m pytest tests/test_orders_db.py -q`
Expected: 收集期 `ModuleNotFoundError: No module named 'orders_db'`

- [ ] **Step 3: 写实现** — 创建 `orders_db.py`

```python
"""抓单落地库：一个本地 SQLite 文件，替代原先的 MySQL neon_ledger.xianyu_orders。

列名与原表逐一对齐，读取侧与历史数据都不用改。有意保留三处与 MySQL 的差别：
- order_id 用 TEXT UNIQUE 且允许 NULL：SQLite 与 InnoDB 都把 NULL 视为互不相等，
  缺 order_id 的脏数据不会互相撞唯一键，与迁移前一致。
- trade_type 用 CHECK 代替 ENUM('sold','bought')。
- order_date 存 ISO 文本：sqlite3 的 date/datetime 适配器自 3.12 起已废弃，不依赖它。
"""

import json
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("XIANYU_ORDERS_DB") or os.path.join(BASE_DIR, "orders.db")
DATE_FMT = "%Y-%m-%d %H:%M:%S"
COLS = ["order_id", "item_title", "price", "trade_type", "counterparty",
        "order_status", "order_date", "images", "raw_data"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS xianyu_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT UNIQUE,
  item_title TEXT,
  price REAL,
  trade_type TEXT DEFAULT 'sold' CHECK (trade_type IN ('sold','bought')),
  counterparty TEXT,
  order_status TEXT,
  order_date TEXT,
  images TEXT,
  raw_data TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_PENDING_COLS = ("order_id, item_title, price, trade_type, "
                 "counterparty, order_status, order_date")
NEWEST_FIRST = " ORDER BY order_date DESC, id DESC"


def connect(path=None):
    """打开（必要时创建）抓单库；行按列名取值。"""
    target = path or DB_PATH
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    conn.executescript(_SCHEMA)
    conn.commit()


def to_date_text(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime(DATE_FMT)
    return str(value)


def insert_order(conn, order):
    """写一条。返回 'new'，或因为 order_id 已存在返回 'dup'。

    只吞 order_id 上的 UNIQUE 冲突；CHECK 之类的完整性错误必须冒出来，
    否则字段写错会被记成"重复"并悄悄少一条。
    """
    try:
        conn.execute(
            "INSERT INTO xianyu_orders (order_id, item_title, price, trade_type,"
            " counterparty, order_status, order_date, images, raw_data)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                order.get("order_id") or None,
                order.get("item_title", ""),
                order.get("price", 0),
                order.get("trade_type", "sold"),
                order.get("counterparty", ""),
                order.get("order_status", ""),
                to_date_text(order.get("order_date")),
                json.dumps(order.get("images", []), ensure_ascii=False),
                json.dumps(order.get("raw_data", {}), ensure_ascii=False, default=str),
            ),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: xianyu_orders.order_id" in str(exc):
            return "dup"
        raise
    return "new"


def insert_orders(conn, orders):
    new_count = dup_count = 0
    for o in orders or []:
        if insert_order(conn, o) == "new":
            new_count += 1
        else:
            dup_count += 1
    conn.commit()
    return new_count, dup_count


def fetch_orders(conn):
    rows = conn.execute("SELECT %s FROM xianyu_orders%s" % (_PENDING_COLS, NEWEST_FIRST)).fetchall()
    return [dict(r) for r in rows]


def fetch_orders_full(conn):
    """迁移校验专用：连 images / raw_data 一起读回，JSON 列解成对象。"""
    cols = ", ".join(COLS)
    rows = conn.execute("SELECT %s FROM xianyu_orders%s" % (cols, NEWEST_FIRST)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["images"] = _load_json(d.get("images"), [])
        d["raw_data"] = _load_json(d.get("raw_data"), {})
        out.append(d)
    return out


def count_orders(conn):
    return conn.execute("SELECT COUNT(*) FROM xianyu_orders").fetchone()[0]


def _load_json(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `python -X utf8 -m pytest tests/test_orders_db.py -q`
Expected: `11 passed`

- [ ] **Step 5: 提交**

```bash
git add orders_db.py tests/test_orders_db.py
git commit -q -F - <<'MSG'
新增抓单存储层 orders_db（SQLite）

只暴露连接、建表、写入、读取、计数，日期一律文本化，不靠 sqlite3 那个
3.12 起就废弃的 date 适配器。列名与原 MySQL 表一致，读取侧零改动。
去重只认 order_id 的 UNIQUE 冲突，CHECK 违反照抛——否则字段写错会被记成
重复、数据悄悄少一条。
MSG
```

---

### Task 2: 迁移比对函数（先做成可单测的纯函数）

**Files:**
- Create: `migrate_orders_to_sqlite.py`
- Test: `tests/test_migrate_orders.py`

- [ ] **Step 1: 写失败的测试** — 创建 `tests/test_migrate_orders.py`

```python
"""迁移的四条断言必须各自能抓出问题，否则它们只是装饰。"""

import copy

import migrate_orders_to_sqlite as mig
import orders_db


def row(oid, price="99.00", raw='{"a":1,"b":2}', date="2026-09-20 14:05:06"):
    return {"order_id": oid, "item_title": "板子", "price": price,
            "trade_type": "bought", "counterparty": "甲", "order_status": "成功",
            "order_date": date, "images": '["a.jpg"]', "raw_data": raw}


SRC = [row("1"), row("2"), row("3")]


def as_sqlite(rows):
    """模拟目标库读回来的形状：price 是 float，images/raw_data 已解成对象。"""
    return [{**r, "price": float(r["price"]),
             "images": ["a.jpg"], "raw_data": {"a": 1, "b": 2}} for r in rows]


def test_equal_migration_is_clean():
    assert mig.compare_rows(SRC, as_sqlite(SRC)) == []


def test_detects_missing_row():
    problems = mig.compare_rows(SRC, as_sqlite(SRC[:2]))
    assert any("条数" in p for p in problems)
    assert any("order_id 集合" in p for p in problems)


def test_detects_swapped_id_with_equal_count():
    problems = mig.compare_rows(SRC, as_sqlite([row("1"), row("2"), row("4")]))
    assert not any("条数" in p for p in problems)
    assert any("order_id 集合" in p for p in problems)


def test_detects_row_order_drift():
    problems = mig.compare_rows(SRC, as_sqlite([row("2"), row("1"), row("3")]))
    assert any("顺序" in p for p in problems)


def test_detects_field_value_drift():
    bumped = copy.deepcopy(SRC)
    bumped[1]["price"] = "99.01"
    problems = mig.compare_rows(SRC, as_sqlite(bumped))
    assert any(p.startswith("字段") and "price" in p and "order_id=2" in p for p in problems)


def test_json_columns_compare_structurally_not_by_text():
    """源里键序与目标不同不该算不一致。"""
    src = [row("1", raw='{"a":1,"b":2}')]
    dst = as_sqlite(src)
    dst[0]["raw_data"] = {"b": 2, "a": 1}
    assert mig.compare_rows(src, dst) == []


def test_empty_destination_is_not_silently_ok():
    assert any("条数" in p for p in mig.compare_rows(SRC, []))
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python -X utf8 -m pytest tests/test_migrate_orders.py -q`
Expected: `ModuleNotFoundError: No module named 'migrate_orders_to_sqlite'`

- [ ] **Step 3: 写实现** — 创建 `migrate_orders_to_sqlite.py`

口令只从环境变量读：不落盘、不进命令行参数。

```python
r"""一次性把 MySQL 的 xianyu_orders 搬进 orders.db。跑完即可删除本文件。

用法（PowerShell / CMD 都要先设好环境变量）：
    set XIANYU_MYSQL_PASSWORD=***
    python -X utf8 migrate_orders_to_sqlite.py --from-mysql

不带 --from-mysql 时只做校验：把 orders.db 与之前导出的 JSON 备份再比一遍。
四条断言任一不过 -> 退出码 1，且不删任何已有数据。
"""

import argparse
import json
import os
import sys

import orders_db

_SELECT = "SELECT %s FROM xianyu_orders%s" % (", ".join(orders_db.COLS), orders_db.NEWEST_FIRST)


def compare_rows(src_rows, dst_rows):
    """源（MySQL 导出，JSON 列是字符串）与目标（orders.db 读回，JSON 列是对象）四处对照。"""
    problems = []
    if len(src_rows) != len(dst_rows):
        problems.append("条数不等：源 %d，目标 %d" % (len(src_rows), len(dst_rows)))
    src_ids = [str(r.get("order_id")) for r in src_rows]
    dst_ids = [str(r.get("order_id")) for r in dst_rows]
    if set(src_ids) != set(dst_ids):
        problems.append("order_id 集合不等：缺 %s，多 %s"
                        % (sorted(set(src_ids) - set(dst_ids)),
                           sorted(set(dst_ids) - set(src_ids))))
    elif src_ids != dst_ids:
        problems.append("顺序不等：同一排序下 order_id 序列与源不一致")
    for srow, drow in zip(src_rows, dst_rows):
        for col in orders_db.COLS:
            want = _normalize(col, srow.get(col))
            got = _normalize(col, drow.get(col))
            if want != got:
                problems.append("字段不等：order_id=%s 列 %s，源 %r 目标 %r"
                                % (srow.get("order_id"), col, want, got))
    return problems


def _normalize(col, value):
    if value is None or value == "":
        return None
    if col == "price":
        return round(float(value), 2)
    if col in ("images", "raw_data"):
        return json.loads(value) if isinstance(value, str) else value
    if col == "order_date":
        return str(value)
    return value


def rows_from_mysql():
    import pymysql
    password = os.environ.get("XIANYU_MYSQL_PASSWORD")
    if not password:
        raise SystemExit("缺环境变量 XIANYU_MYSQL_PASSWORD —— 口令不读文件、不进参数")
    conn = pymysql.connect(
        host=os.environ.get("XIANYU_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("XIANYU_DB_PORT", "3306")),
        user=os.environ.get("XIANYU_DB_USER", "root"),
        password=***
        database=os.environ.get("XIANYU_DB_NAME", "neon_ledger"),
        charset="utf8mb4")
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(_SELECT)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-mysql", action="store_true")
    ap.add_argument("--backup", default="orders_backup.json")
    args = ap.parse_args(argv)

    if args.from_mysql:
        src = rows_from_mysql()
        with open(args.backup, "w", encoding="utf-8") as f:
            json.dump(src, f, ensure_ascii=False, default=str, indent=1)
        print("已导出备份 %s（%d 行）" % (args.backup, len(src)))
        conn = orders_db.connect()
        orders_db.ensure_schema(conn)
        new_count, dup_count = orders_db.insert_orders(conn, src)
        print("写入完成：新增 %d，已存在跳过 %d" % (new_count, dup_count))
    else:
        with open(args.backup, encoding="utf-8") as f:
            src = json.load(f)
        conn = orders_db.connect()

    dst = orders_db.fetch_orders_full(conn)
    problems = compare_rows(src, dst)
    for p in problems:
        print("  不一致：%s" % p)
    print("源 %d 行 / 目标 %d 行 -> %s"
          % (len(src), len(dst), "一致" if not problems else "不一致"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `python -X utf8 -m pytest tests/test_migrate_orders.py tests/test_orders_db.py -q`
Expected: `18 passed`

- [ ] **Step 5: 提交**

```bash
git add migrate_orders_to_sqlite.py tests/test_migrate_orders.py
git commit -q -F - <<'MSG'
新增 MySQL 到 SQLite 的一次性迁移与四条一致性校验

比对是纯函数，四类不一致（条数、order_id 集合、行顺序、字段值）各有用例证明
它真会报错；JSON 列按对象比，避免键序不同造成假红。口令只认环境变量。
MSG
```

---

### Task 3: 两个脚本切到 orders_db

**Files:**
- Create: `tests/test_orders_backend.py`
- Modify: `xianyu_scraper.py:14-56`、`xianyu_scraper.py:484-527`
- Modify: `xianyu_review.py:20`、`xianyu_review.py:34-35`、`xianyu_review.py:214-252`

- [ ] **Step 1: 写失败的静态契约测试** — 创建 `tests/test_orders_backend.py`

```python
"""抓单与填单台不许再碰 MySQL：静态断言，不等运行时炸出来。"""

import inspect
import re
from datetime import datetime

import pytest

import orders_db
import xianyu_review
import xianyu_scraper

MYSQL_TOKENS = ["pymysql", "db_config", "DB_CONF", "CREATE_TABLE_SQL"]


@pytest.mark.parametrize("mod", [xianyu_scraper, xianyu_review],
                         ids=lambda m: m.__name__)
def test_no_mysql_left_in_source(mod):
    src = inspect.getsource(mod)
    leftovers = [t for t in MYSQL_TOKENS if re.search(r"\b%s\b" % t, src)]
    assert not leftovers, "%s 还在引用 MySQL：%s" % (mod.__name__, leftovers)
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python -X utf8 -m pytest tests/test_orders_backend.py -q`
Expected: 2 条全 FAIL，列出 `pymysql`、`db_config`、`DB_CONF` 等残留

- [ ] **Step 3: 补行为测试** — 追加到 `tests/test_orders_backend.py`

```python
def test_scraper_insert_orders_reports_new_and_dup(tmp_path, monkeypatch):
    """脚本不再自己连库，只把订单交给存储层，并回报新增/重复各几条。"""
    monkeypatch.setattr(xianyu_scraper, "connect",
                        lambda: orders_db.connect(str(tmp_path / "orders.db")))
    o = {"order_id": "1", "item_title": "技嘉 B650M", "price": 300.0,
         "trade_type": "bought", "counterparty": "", "order_status": "",
         "order_date": None, "images": [], "raw_data": {}}
    assert xianyu_scraper.insert_orders([o, o]) == (1, 1)


def test_scraper_ensure_table_creates_readable_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setattr(xianyu_scraper, "connect",
                        lambda: orders_db.connect(str(db)))
    xianyu_scraper.ensure_table()
    conn = orders_db.connect(str(db))
    assert orders_db.count_orders(conn) == 0


def test_review_fetch_orders_gives_text_date(tmp_path, monkeypatch):
    """SQLite 读回来是字符串，填单台不能再对日期调 strftime。"""
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    orders_db.insert_order(conn, {
        "order_id": "77", "item_title": "微星 h610 坏板 出售", "price": 95.0,
        "trade_type": "bought", "counterparty": "乙", "order_status": "交易成功",
        "order_date": datetime(2026, 9, 19, 8, 30, 0), "images": [], "raw_data": {},
    })
    conn.commit()
    monkeypatch.setattr(xianyu_review, "connect", lambda: conn)
    monkeypatch.setattr(xianyu_review, "imported_order_ids", lambda: set())
    got = xianyu_review.fetch_orders()
    assert got[0]["order_date"] == "2026-09-19 08:30:00"
    assert got[0]["brand"] == "微星"
    assert got[0]["model"] == "h610 坏板"
    assert got[0]["imported"] is False


def test_review_marks_already_imported(tmp_path, monkeypatch):
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    orders_db.insert_order(conn, {"order_id": "77", "item_title": "微星 h610", "price": 95.0})
    conn.commit()
    monkeypatch.setattr(xianyu_review, "connect", lambda: conn)
    monkeypatch.setattr(xianyu_review, "imported_order_ids", lambda: {"77"})
    assert xianyu_review.fetch_orders()[0]["imported"] is True
```

- [ ] **Step 4: 跑测试，确认行为测试也失败**

Run: `python -X utf8 -m pytest tests/test_orders_backend.py -q`
Expected: 6 条里 4 条 FAIL（`connect` 属性还不存在）

- [ ] **Step 5: 改 `xianyu_scraper.py`**

删第 18 行 `import pymysql`、第 30 行 `from db_config import DB_CONF`（连同其上的注释），改成：

```python
from playwright.async_api import async_playwright

import orders_db
```

删掉第 40-56 行整段 `# ═══ DB Schema ═══` 与 `CREATE_TABLE_SQL`。把第 484-527 行 `# ═══ DB Write ═══` 整段替换为：

```python
# ═══════════════════════ 落库 ═══════════════════════

def connect():
    return orders_db.connect()


def ensure_table():
    conn = connect()
    try:
        orders_db.ensure_schema(conn)
    finally:
        conn.close()


def insert_orders(orders):
    conn = connect()
    try:
        return orders_db.insert_orders(conn, orders)
    finally:
        conn.close()
```

- [ ] **Step 6: 改 `xianyu_review.py`**

删第 20 行 `import pymysql` 与第 34-35 行（注释 + `from db_config import DB_CONF`），在原 import 区加：

```python
import orders_db
```

把第 214-252 行 `# ═══ MySQL ═══` 整段（含 `_ORDERS_SQL` 与 `fetch_orders`）替换为：

```python
# ═══════════════════════ 抓单库 ═══════════════════════

def connect():
    return orders_db.connect()


def fetch_orders():
    done = imported_order_ids()
    conn = connect()
    try:
        rows = orders_db.fetch_orders(conn)
    finally:
        conn.close()

    out = []
    for r in rows:
        title = r.get("item_title") or ""
        brand, model = split_title(title)
        oid = str(r.get("order_id") or "")
        out.append({
            "order_id": oid,
            "item_title": title,
            "price": _to_float(r.get("price")),
            "trade_type": r.get("trade_type") or "",
            "counterparty": r.get("counterparty") or "",
            "order_status": r.get("order_status") or "",
            "order_date": str(r.get("order_date") or ""),
            "brand": brand,
            "model": model,
            "imported": oid in done,
        })
    return out
```

- [ ] **Step 7: 跑测试，确认全绿**

Run: `python -X utf8 -m pytest tests/test_orders_backend.py tests/test_orders_db.py -q`
Expected: `17 passed`

- [ ] **Step 8: 全量回归**

Run: `python -X utf8 -m pytest tests -q`
Expected: 失败数必须正好是那 4 条既有"写死记录数"漂移探针（`test_apply_patch` 2 条、`test_catalog` 1 条、`test_migrate_cat` 1 条），不多不少。出现新失败就停下查因，禁止继续往下做。

- [ ] **Step 9: 提交**

```bash
git add xianyu_scraper.py xianyu_review.py tests/test_orders_backend.py
git commit -q -F - <<'MSG'
抓单与填单台改走 SQLite 存储层

两个脚本不再自己连库，落库与读取统一经 orders_db；日期从 datetime 对象
改成读回即字符串。附一条静态契约测试，防止以后图快把 pymysql 塞回来。
MSG
```

---

### Task 4: 真迁移（一次性，需要他给口令）

**Files:**
- Create（产物）: `orders_backup_2026-09-27.json`
- Delete（跑完）: `migrate_orders_to_sqlite.py`

- [ ] **Step 1: 确认源侧形状**

请他在自己的终端里设好口令后，你读取计数：

Run:
```bash
cd "G:/claude code" && python -X utf8 -c "import os,pymysql;conn=pymysql.connect(host='127.0.0.1',port=3306,user='root',password=os.environ['XIANYU_MYSQL_PASSWORD'],database='neon_ledger',charset='utf8mb4');cur=conn.cursor();cur.execute('select count(*), sum(order_date is null), sum(images is null) from xianyu_orders');print(cur.fetchone());conn.close()"
```
Expected: `(31, <null_date>, <null_images>)` — 记下后两个数，Step 4 要比对

- [ ] **Step 2: 跑迁移**

Run: `python -X utf8 migrate_orders_to_sqlite.py --from-mysql --backup orders_backup_2026-09-27.json`
Expected: `已导出备份 ...（31 行）`、`写入完成：新增 31，已存在跳过 0`、`源 31 行 / 目标 31 行 -> 一致`，退出码 0

若打印任何 `不一致：`：**停住**。删除 `orders.db`，从 Step 1 重来；仍不一致就把差异原文交给他判断，不许改断言让它变绿。

- [ ] **Step 3: 填单台端到端确认**

Run: `python -X utf8 xianyu_review.py --dry-run`
Expected: 列出 31 单、每单有品牌/型号、无 `AttributeError`（这条在验 Task 3 的日期类型没漏）

- [ ] **Step 4: 再交叉核对一次**

Run: `python -X utf8 migrate_orders_to_sqlite.py --backup orders_backup_2026-09-27.json`
Expected: `一致`，退出码 0（这步不连 MySQL，只比 `orders.db` 与 JSON 备份）

- [ ] **Step 5: 收尾提交**

```bash
cd "G:/claude code" && rm -f migrate_orders_to_sqlite.py
```

`.gitignore` 追加两行（在数据/备份那段注释下面）：

```
/orders.db
/orders_backup_*.json
```

```bash
git add .gitignore
git rm -q --cached migrate_orders_to_sqlite.py tests/test_migrate_orders.py
git rm -q migrate_orders_to_sqlite.py tests/test_migrate_orders.py 2>/dev/null || true
git commit -q -F - <<'MSG'
订单已迁进 orders.db，移除一次性迁移脚本

31 行订单从 MySQL 搬进本地 SQLite，四条一致性断言全过。备份 JSON 与
orders.db 一起排除在版本控制外，真实订单不外流。
MSG
```

（注：Task 5 会再动 `.gitignore` 里的 MySQL 段；两处改动分开提交，避免一次 diff 混两件事。）

---

### Task 5: MySQL 配置层退役 + 保密闸门改口

**Files:**
- Delete: `db_config.py`、`db_secret.example.py`、`import_to_mysql.py`
- Modify: `.gitignore`、`requirements.txt`、`tests/test_repo_secrets.py`

- [ ] **Step 1: 改测试（先让它失败）** — `tests/test_repo_secrets.py`

删除这两个函数以及只为它们服务的 `_local_secret()`：

```python
def test_real_local_password_not_present_in_tracked_tree(): ...
def test_local_secret_file_is_ignored(): ...
```

在原位置加上两条（`MUST_NOT_EXIST` 放到常量区 `MUST_BE_TRACKED` 下面）：

```python
# 换成 SQLite 后就不该存在的东西。含 import_to_mysql.py：它从未被跟踪，
# 明文口令根本不在本文件扫描范围内 —— 那是个盲区，现在用断言堵死复活路。
MUST_NOT_EXIST = ["db_config.py", "db_secret.example.py", "import_to_mysql.py"]


def test_orders_db_is_never_tracked():
    """orders.db 是真实经营数据，与 data.json 同级敏感。

    刻意写成"若在跟踪列表里就必须被 .gitignore 挡住"，而不是简单断言它不在 ——
    前者在有人 git add -f 时会变红，后者只看错目录就永远绿。
    """
    tracked = set(_tracked())
    suspects = [f for f in tracked
                if f.endswith("orders.db") or f.endswith(".sqlite") or f.endswith(".sqlite3")]
    assert not suspects, "真实订单库被跟踪了：%s" % (suspects,)


def test_mysql_credential_layer_is_gone():
    leftovers = [f for f in MUST_NOT_EXIST if (ROOT / f).exists()]
    assert not leftovers, "MySQL 配置层残留：%s" % (leftovers,)
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python -X utf8 -m pytest tests/test_repo_secrets.py -q`
Expected: `test_mysql_credential_layer_is_gone` FAIL 并列出三个文件；其余 PASS

- [ ] **Step 3: 删文件、清依赖**

```bash
cd "G:/claude code" && git rm -q db_config.py db_secret.example.py import_to_mysql.py
```

`requirements.txt` 删掉这一行（含它上面的注释）：

```
pymysql==1.1.0            # xianyu_scraper / xianyu_review 连 MySQL
```

`.gitignore` 里把

```
# 真实 MySQL 口令不进仓库；模板 db_secret.example.py 才进
/db_secret.py
```

换成

```
# 抓单落地库：真实订单原文，与 data.json 同级敏感
/orders.db
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `python -X utf8 -m pytest tests/test_repo_secrets.py -q`
Expected: `5 passed`（原有 3 条 + 新增 2 条）

- [ ] **Step 5: 变异测试 —— 证明新闸门真会红**

```bash
cd "G:/claude code" && printf 'not-a-real-db\n' > orders.db && git add -f orders.db
python -X utf8 -m pytest tests/test_repo_secrets.py -q 2>&1 | tail -4
git rm --cached -q orders.db && rm -f orders.db
python -X utf8 -m pytest tests/test_repo_secrets.py -q 2>&1 | tail -2
```
Expected: 第一次出现 `FAILED tests/test_repo_secrets.py::test_orders_db_is_never_tracked`；撤销后 `5 passed`

- [ ] **Step 6: 确认没有代码还在导入已删模块**

Run: `grep -rn "db_config\|pymysql" --include="*.py" . | grep -v "^./.venv" | grep -v "^./.git"`
Expected: 只剩 `tests/test_repo_secrets.py` 里那条静态断言用到的字符串字面量（`MYSQL_TOKENS`）。若出现 `import`，回到 Task 3 补漏。

- [ ] **Step 7: 提交**

```bash
git add .gitignore requirements.txt tests/test_repo_secrets.py
git commit -q -F - <<'MSG'
MySQL 配置层退役，保密闸门改成盯 orders.db

删掉 db_config 与口令模板，顺带堵一个盲区：import_to_mysql.py 从来没被跟踪，
里面写死的明文口令根本不在闸门扫描范围内。新增两条断言——真实订单库不得入库、
MySQL 配置层不得复活——并用变异测试确认前者真会变红。
MSG
```

---

### Task 6: 四行自检 `selfcheck.py`

**Files:**
- Create: `selfcheck.py`
- Test: `tests/test_selfcheck.py`

- [ ] **Step 1: 写失败的测试** — 创建 `tests/test_selfcheck.py`

```python
"""自检必须会说坏消息：每行至少一条 ok 用例 + 一条 fail 或 skip 用例。"""

import json

import pytest

import orders_db
import selfcheck as sc


def ledger(tmp_path, records):
    p = tmp_path / "data.json"
    p.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return p


# ── 行 1 台账 ────────────────────────────────────────────────

def test_ledger_ok(monkeypatch):
    monkeypatch.setattr(sc, "_http_json", lambda: (200, [{"brand": "微星"}]))
    assert sc.check_ledger()["status"] == "ok"


def test_ledger_skip_when_backend_not_running(monkeypatch):
    def boom():
        raise OSError("拒绝连接")
    monkeypatch.setattr(sc, "_http_json", boom)
    r = sc.check_ledger()
    assert r["status"] == "skip"
    assert "app_standalone.py" in r["detail"]


def test_ledger_fail_on_http_error(monkeypatch):
    monkeypatch.setattr(sc, "_http_json", lambda: (500, None))
    assert sc.check_ledger()["status"] == "fail"


def test_ledger_fail_when_payload_is_not_a_list(monkeypatch):
    monkeypatch.setattr(sc, "_http_json", lambda: (200, {"msg": "对象"}))
    assert sc.check_ledger()["status"] == "fail"


# ── 行 2 图片 ────────────────────────────────────────────────

def test_images_ok_when_all_files_exist(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.jpg").write_bytes(b"x")
    p = ledger(tmp_path, [{"images": ["images/a.jpg"]}, {"images": []}])
    r = sc.check_images(base_dir=tmp_path, data_file=p)
    assert r["status"] == "ok" and "引用 1" in r["detail"]


def test_images_fail_and_lists_missing(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.jpg").write_bytes(b"x")
    p = ledger(tmp_path, [{"images": ["images/a.jpg", "images/gone.jpg"]}])
    r = sc.check_images(base_dir=tmp_path, data_file=p)
    assert r["status"] == "fail"
    assert "缺 1 张" in r["detail"] and "images/gone.jpg" in r["detail"]


def test_images_ok_when_ledger_has_no_images(tmp_path):
    p = ledger(tmp_path, [{"images": []}])
    assert sc.check_images(base_dir=tmp_path, data_file=p)["status"] == "ok"


def test_images_fail_when_ledger_missing(tmp_path):
    r = sc.check_images(base_dir=tmp_path, data_file=tmp_path / "nope.json")
    assert r["status"] == "fail"


# ── 行 3 抓单库 ──────────────────────────────────────────────

def test_orders_db_ok_with_count(tmp_path):
    db = str(tmp_path / "orders.db")
    conn = orders_db.connect(db)
    orders_db.ensure_schema(conn)
    orders_db.insert_order(conn, {"order_id": "1", "item_title": "t", "price": 1})
    conn.commit()
    conn.close()
    r = sc.check_orders_db(db)
    assert r["status"] == "ok" and "1 单" in r["detail"]


def test_orders_db_skip_when_absent(tmp_path):
    assert sc.check_orders_db(str(tmp_path / "nope.db"))["status"] == "skip"


def test_orders_db_fail_when_not_a_database(tmp_path):
    db = tmp_path / "orders.db"
    db.write_text("这不是数据库", encoding="utf-8")
    assert sc.check_orders_db(str(db))["status"] == "fail"


def test_orders_db_fail_when_table_missing(tmp_path):
    import sqlite3
    db = tmp_path / "orders.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table other(x int)")
    conn.commit()
    conn.close()
    r = sc.check_orders_db(str(db))
    assert r["status"] == "fail" and "xianyu_orders" in r["detail"]


# ── 行 4 浏览器内核 ──────────────────────────────────────────

def test_browser_ok_when_exe_present(tmp_path, monkeypatch):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(sc, "_chromium_exe", lambda: str(exe))
    assert sc.check_browser()["status"] == "ok"


def test_browser_fail_when_exe_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_chromium_exe", lambda: str(tmp_path / "gone.exe"))
    assert sc.check_browser()["status"] == "fail"


def test_browser_fail_when_playwright_absent(monkeypatch):
    monkeypatch.setattr(sc, "_chromium_exe", lambda: None)
    r = sc.check_browser()
    assert r["status"] == "fail" and "playwright install chromium" in r["detail"]


# ── 汇总 ────────────────────────────────────────────────────

def test_run_all_returns_four_named_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_http_json", lambda: (200, []))
    monkeypatch.setattr(sc, "_chromium_exe", lambda: None)
    rows = sc.run_all(base_dir=tmp_path, data_file=ledger(tmp_path, []),
                      db_path=str(tmp_path / "nope.db"))
    assert [r["name"] for r in rows] == ["台账", "图片", "抓单库", "浏览器内核"]


def test_exit_code_is_one_when_anything_fails(tmp_path, monkeypatch):
    p = ledger(tmp_path, [{"images": ["nope.jpg"]}])
    monkeypatch.setattr(sc, "_http_json", lambda: (200, []))
    exe = tmp_path / "chrome.exe"; exe.write_bytes(b"x")
    monkeypatch.setattr(sc, "_chromium_exe", lambda: str(exe))
    assert sc.main(["--base-dir", str(tmp_path), "--data-file", str(p),
                    "--db-path", str(tmp_path / "orders.db")]) == 1


def test_exit_code_is_zero_when_nothing_failed(tmp_path, monkeypatch):
    p = ledger(tmp_path, [{"images": []}])
    monkeypatch.setattr(sc, "_http_json", lambda: (200, []))
    exe = tmp_path / "chrome.exe"; exe.write_bytes(b"x")
    monkeypatch.setattr(sc, "_chromium_exe", lambda: str(exe))
    db = str(tmp_path / "orders.db")
    conn = orders_db.connect(db); orders_db.ensure_schema(conn); conn.close()
    assert sc.main(["--base-dir", str(tmp_path), "--data-file", str(p),
                    "--db-path", db]) == 0
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python -X utf8 -m pytest tests/test_selfcheck.py -q`
Expected: `ModuleNotFoundError: No module named 'selfcheck'`

- [ ] **Step 3: 写实现** — 创建 `selfcheck.py`

```python
r"""装机后的四行体检：台账 / 图片 / 抓单库 / 浏览器内核。

规矩：每一行都必须能给出 fail 并附下一步动作。连不上后端这种"还没跑起来"
的情况报 skip 而不是 ok —— 否则体检表只会说漂亮话。

    python -X utf8 selfcheck.py
    python -X utf8 selfcheck.py --json
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER_URL = "http://127.0.0.1:8765/api/data"


def _result(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def _http_json():
    with urllib.request.urlopen(LEDGER_URL, timeout=4) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _chromium_exe():
    """交给 Playwright 自己报路径，不猜 ms-playwright 目录名。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as pw:
        return pw.chromium.executable_path


def check_ledger():
    try:
        status, payload = _http_json()
    except (OSError, urllib.error.URLError, ValueError):
        return _result("台账", "skip",
                       "8765 上没起后端；跑 python app_standalone.py 或点启动器台账按钮后复查")
    if status != 200:
        return _result("台账", "fail", "/api/data 返回 HTTP %s" % status)
    if not isinstance(payload, list):
        return _result("台账", "fail", "/api/data 返回的不是账本数组，接口形状变了")
    return _result("台账", "ok", "后端在跑，%d 条记录" % len(payload))


def check_images(base_dir=BASE_DIR, data_file=None):
    data_file = data_file or os.path.join(base_dir, "data.json")
    if not os.path.exists(data_file):
        return _result("图片", "fail", "找不到账本文件 %s" % data_file)
    with open(data_file, encoding="utf-8") as f:
        records = json.load(f)
    refs = [p for r in records if isinstance(r, dict)
            for p in (r.get("images") or []) if isinstance(p, str) and p]
    missing = [p for p in refs
               if not os.path.exists(os.path.join(base_dir, p.replace("\\", "/")))]
    if missing:
        return _result("图片", "fail",
                       "引用 %d 张、缺 %d 张（例：%s）—— 换机时 images/ 目录要整个拷过来"
                       % (len(refs), len(missing), missing[0]))
    return _result("图片", "ok", "引用 %d 张全在" % len(refs))


def check_orders_db(db_path=None):
    db_path = db_path or os.path.join(BASE_DIR, "orders.db")
    if not os.path.exists(db_path):
        return _result("抓单库", "skip",
                       "还没有 orders.db；抓一次单（python xianyu_scraper.py）就有了")
    try:
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM xianyu_orders").fetchone()[0]
        conn.close()
    except sqlite3.DatabaseError as exc:
        return _result("抓单库", "fail", "文件打不开，不像 SQLite：%s" % exc)
    except sqlite3.OperationalError as exc:
        return _result("抓单库", "fail", "缺 xianyu_orders 表：%s" % exc)
    return _result("抓单库", "ok", "%d 单待核对或已导入" % n)


def check_browser():
    try:
        exe = _chromium_exe()
    except Exception as exc:                       # 浏览器起不来时给结论，不抛栈
        return _result("浏览器内核", "fail", "Playwright 探测失败：%s" % exc)
    if exe is None:
        return _result("浏览器内核", "fail",
                       "没装 playwright 内核；跑 python -m playwright install chromium")
    if not os.path.exists(exe):
        return _result("浏览器内核", "fail", "Chromium 路径不存在：%s" % exe)
    return _result("浏览器内核", "ok", os.path.basename(exe))


def run_all(base_dir=BASE_DIR, data_file=None, db_path=None):
    return [check_ledger(), check_images(base_dir, data_file),
            check_orders_db(db_path), check_browser()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=BASE_DIR)
    ap.add_argument("--data-file", default=None)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = run_all(args.base_dir, args.data_file, args.db_path)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        mark = {"ok": "[ok]  ", "fail": "[FAIL]", "skip": "[--]  "}
        for r in rows:
            print("%s %-5s %s" % (mark[r["status"]], r["name"], r["detail"]))
    return 1 if any(r["status"] == "fail" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `python -X utf8 -m pytest tests/test_selfcheck.py -q`
Expected: `17 passed`

- [ ] **Step 5: 真实环境跑一次（这次不注入假东西）**

Run: `python -X utf8 selfcheck.py`
Expected: 台账行 `ok` 或 `skip`（后端没起就是 skip，正常）；图片行给出真实计数；抓单库 `ok` 且条数 = Task 4 迁过去的 31；浏览器内核 `ok`。把四行原文贴给他看。

- [ ] **Step 6: 提交**

```bash
git add selfcheck.py tests/test_selfcheck.py
git commit -q -F - <<'MSG'
新增四行装机自检 selfcheck

台账/图片/抓单库/浏览器内核各自可注入依赖，每行都有 ok 与 fail/skip 用例；
连不上后端报 skip 而非 ok，免得体检表只报喜。图片行是换机最实用的报警器：
账本里引用的图少了哪几张、少在哪，会直接点出来。
MSG
```

---

### Task 7: 安装器 `installer.py` + `setup.bat`

**Files:**
- Create: `installer.py`、`setup.bat`
- Test: `tests/test_installer.py`
- Modify: `tests/test_repo_secrets.py`（`MUST_BE_TRACKED` 补三件）

- [ ] **Step 1: 写失败的测试** — 创建 `tests/test_installer.py`

```python
"""安装器的步骤表必须可单测：不真装东西也能验证顺序、失败即停、dry-run 不动盘。"""

import os

import pytest

import installer as ins


class Recorder:
    def __init__(self):
        self.calls = []

    def step(self, name):
        def run(ctx):
            self.calls.append(name)
            return "做完了 %s" % name
        return run


def test_steps_run_in_declared_order(tmp_path):
    rec = Recorder()
    steps = [ins.Step(n, rec.step(n)) for n in ("python", "venv", "deps", "browser", "check")]
    report = ins.run_steps(steps, {"tmp": tmp_path})
    assert [c for c in rec.calls] == ["python", "venv", "deps", "browser", "check"]
    assert all(r["status"] == "ok" for r in report)


def test_a_failing_step_stops_the_rest():
    rec = Recorder()

    def broken(ctx):
        raise RuntimeError("pip 炸了")
    steps = [ins.Step("a", rec.step("a")), ins.Step("b", broken), ins.Step("c", rec.step("c"))]
    report = ins.run_steps(steps, {})
    assert [r["name"] for r in report] == ["a", "b"]
    assert report[1]["status"] == "fail"
    assert "pip 炸了" in report[1]["detail"]
    assert rec.calls == ["a"]                       # c 没被执行


def test_skip_is_reported_not_ok():
    """已装过的步骤必须报 skip，不能冒充 ok —— 否则第二次跑就看不出它做了什么。"""
    def skipped(ctx):
        return "%s：.venv 已存在" % ins.SKIP
    report = ins.run_steps([ins.Step("venv", skipped)], {})
    assert report[0]["status"] == "skip"
    assert ".venv 已存在" in report[0]["detail"]


def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(ins, "run_steps", lambda steps, ctx: ran.append(len(steps)))
    assert ins.main(["--dry-run"]) == 0
    assert ran == []                                # dry-run 不执行任何步骤
    for name in ("python", "venv", "deps", "browser", "check"):
        assert name in [s.name for s in ins.build_steps(dry_run=True)]


def test_exit_code_one_when_a_step_failed(tmp_path, monkeypatch):
    def broken(ctx):
        raise RuntimeError("网络不通")
    monkeypatch.setattr(ins, "build_steps", lambda dry_run=False: [ins.Step("deps", broken)])
    assert ins.main([]) == 1
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python -X utf8 -m pytest tests/test_installer.py -q`
Expected: `ModuleNotFoundError: No module named 'installer'`

- [ ] **Step 3: 写实现** — 创建 `installer.py`

```python
r"""一键装环境：venv + 依赖 + Playwright Chromium + 自检。

刻意不碰数据库（MySQL 那套依赖已经退役），也不改他的系统 Python。

    python -X utf8 installer.py            # 真装
    python -X utf8 installer.py --dry-run  # 只打印要做哪几步
"""

import argparse
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, ".venv")
MIN_PYTHON = (3, 10)
SKIP = "skip"


class Step:
    def __init__(self, name, run):
        self.name = name
        self.run = run


def _python_ok(ctx):
    v = sys.version_info[:2]
    if v < MIN_PYTHON:
        raise RuntimeError("需要 Python %d.%d 以上，当前 %d.%d" % (*MIN_PYTHON, *v))
    return "Python %d.%d" % v


def _venv(ctx):
    python = os.path.join(VENV_DIR, "Scripts", "python.exe")
    if os.path.exists(python):
        return "%s（已存在，跳过）" % SKIP
    subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
    return "建好 .venv"


def _deps(ctx):
    subprocess.run([_venv_python(), "-m", "pip", "install", "-r",
                    os.path.join(BASE_DIR, "requirements.txt")], check=True)
    return "依赖装完"


def _browser(ctx):
    subprocess.run([_venv_python(), "-m", "playwright", "install", "chromium"], check=True)
    return "Chromium 就位（抓单要用；只记账可不装）"


def _check(ctx):
    proc = subprocess.run([_venv_python(), os.path.join(BASE_DIR, "selfcheck.py")],
                          cwd=BASE_DIR)
    if proc.returncode != 0:
        raise RuntimeError("自检有 FAIL 项，见上方体检表")
    return "体检通过"


def _venv_python():
    return os.path.join(VENV_DIR, "Scripts", "python.exe")


def build_steps(dry_run=False):
    return [Step("python", _python_ok), Step("venv", _venv), Step("deps", _deps),
            Step("browser", _browser), Step("check", _check)]


def run_steps(steps, ctx):
    report = []
    for step in steps:
        try:
            detail = step.run(ctx) or ""
            status = SKIP if str(detail).startswith(SKIP) else "ok"
        except Exception as exc:
            status, detail = "fail", str(exc)
        report.append({"name": step.name, "status": status, "detail": detail})
        print("[%s] %-8s %s" % ({"ok": "ok  ", "fail": "FAIL", "skip": "--  "}[status],
                                step.name, detail))
        if status == "fail":
            break
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.dry_run:
        print("将依次执行：" + " → ".join(s.name for s in build_steps(dry_run=True)))
        return 0
    report = run_steps(build_steps(), {})
    failed = [r for r in report if r["status"] == "fail"]
    if failed:
        print("\n停在这一步：%s。修好后重新双击 setup.bat 即可，已完成的步骤会跳过。"
              % failed[0]["name"])
        return 1
    print("\n装好了。日常使用：双击 setup.bat 旁的 python launcher.py 打开霓虹鱼面板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试，确认全绿**

Run: `python -X utf8 -m pytest tests/test_installer.py -q`
Expected: `5 passed`

- [ ] **Step 5: 写 `setup.bat`**（CRLF 换行，纯 ASCII 注释避免 CMD 代码页乱码）

```bat
@echo off
REM NeonFish one-click setup. Does not touch the system Python interpreter.
setlocal
cd /d "%~dp0"
chcp 65001 >nul

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -X utf8 installer.py
) else (
  python -X utf8 installer.py
)
if %errorlevel% neq 0 (
  echo.
  echo 安装没有完成，把上面的 FAIL 行截图发给维护者，或按提示自行处理后重跑本文件。
)
echo.
pause
end
```

- [ ] **Step 6: 让它真的是 CRLF 换行**

`setup.bat` 必须 CRLF，否则某些 Windows 版本下 `if (...)` 块解析异常：

```bash
cd "G:/claude code" && python -X utf8 -c "p=open('setup.bat','rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'); open('setup.bat','wb').write(p)"
```

Run: `file setup.bat`（或 `grep -c $'\r' setup.bat`）
Expected: 每行都以 `\r\n` 结尾（`grep -c` 结果等于总行数）

- [ ] **Step 7: 把三件新东西登记进闸门**

在 `tests/test_repo_secrets.py` 的 `MUST_BE_TRACKED` 末尾追加：

```python
    "orders_db.py",             # 抓单存储层，缺它两个脚本都起不来
    "selfcheck.py",             # 体检表
    "installer.py",             # 装机步骤
    "setup.bat",                # 双击入口
```

Run: `python -X utf8 -m pytest tests/test_repo_secrets.py -q`
Expected: 先 FAIL（列出没被跟踪的 `setup.bat` 等），`git add` 后转绿 —— 这条顺序正好证明断言在工作。

- [ ] **Step 8: 全量回归 + 真跑一次 dry-run**

```bash
python -X utf8 -m pytest tests -q
python -X utf8 installer.py --dry-run
```
Expected: 失败仍只有那 4 条既有漂移探针；dry-run 打印
`将依次执行：python → venv → deps → browser → check`

- [ ] **Step 9: 提交**

```bash
git add installer.py setup.bat tests/test_installer.py tests/test_repo_secrets.py
git commit -q -F - <<'MSG'
新增一键安装器与 setup.bat 双击入口

步骤表可单测：顺序、失败即停、跳过语义都有用例，不必真装一遍才能信。
安装器只建 .venv、装依赖、下 Chromium，然后交给 selfcheck 出体检表；
不碰系统 Python，也不碰数据库。四件新文件登记进必须跟踪清单。
MSG
```

---

### Task 8: README 换掉 MySQL 安装说明

**Files:**
- Modify: `README.md`（`环境` 一节、`配 MySQL 口令` 整节、`启动` 一节、`数据与文件` 表、`不进版本控制` 段、`可选：录入辅助` 前后）

- [ ] **Step 1: 把环境一节改成"先双击"**

`README.md` 里现有的这段：

````bat
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```
````

替换为：

````bat
:: 第一次用：双击 setup.bat，它建 .venv、装依赖、下 Chromium，最后出一张体检表
:: 已经装过再跑，已完成的步骤会自动跳过
```
````

- [ ] **Step 2: 整节替换"配 MySQL 口令"**

把从 `## 配 MySQL 口令` 到下一节 `## 启动` 之前的全部内容换成：

```markdown
## 装什么、不装什么

抓单落地的存储现在是一个本地文件 `orders.db`（SQLite），**不再需要 MySQL 服务**。
`pymysql`、`db_config.py`、口令模板这些配套一起退场，所以也没有"先配数据库口令"这一步。

只有两处例外要知道：

- 之前用 MySQL 跑过抓单的，历史订单还在 `neon_ledger.xianyu_orders` 里；本次迁移把它
  原样搬进 `orders.db`（列名一一对齐），迁移脚本是一次性的，跑完即删。
- `orders.db` 里是订单原文，和 `data.json` 同级敏感，已进 `.gitignore`。仓库里那份
  `data.json` 仍是脱敏样例，别拿它当真账本。
```

- [ ] **Step 3: 启动一节补一行体检命令**

在 `## 启动` 的代码块末尾（`python launcher.py` 之后）加：

```bat
python selfcheck.py            :: 体检表：台账 / 图片缺件 / orders.db / Chromium
```

- [ ] **Step 4: 文件表与"不进版本控制"清单同步**

`数据与文件` 表格加一行：

```markdown
| `orders.db` | 抓单落地库（SQLite）：抓到的订单原文先进这里，再由填入台核对进 `data.json` |
```

"不进版本控制的"那一句里的文件清单，把 `db_secret.py` 换成 `orders.db`、`orders_backup_*.json`，并在结尾补一句换机须知：

```markdown
换机要搬的是：`data.json`、`catalog.json`、`images/`、`orders.db`（想免扫码再带上
`xianyu_cookies.json` 与 `.browser_data/`）。只拷前两个的话，记录里的图片会是空图，
`selfcheck.py` 的图片那一行就是报这个的。
```

- [ ] **Step 5: 自查 README 与代码不再矛盾**

Run: `grep -n "MySQL\|pymysql\|db_secret\|neon_ledger\|8766" README.md`
Expected: 只剩"不再需要 MySQL 服务"这类否定式说明和端口 `8766`（填入台端口范围，未变）。若还残留"pip install pymysql"或"建 `neon_ledger` 库"的指导，改掉。

- [ ] **Step 6: 提交**

```bash
git add README.md
git commit -q -F - <<'MSG'
简介改成双击安装，去掉 MySQL 配置步骤

存储换成 orders.db 之后，装机的门槛从"先装一个数据库服务"降到"双击"。
顺手把换机要搬哪四样东西写清，并指向 selfcheck 的图片那一行 —— 漏拷 images/
是换机最常踩、又不报错的坑。
MSG
```

---

### Task 9: 云端快照重建 + 交付级验收

**Files:**
- 不改代码；重建 `gh-pack` 孤儿分支并推送

- [ ] **Step 1: 重建 gh-pack 快照**

按既有 plumbing（同一套命令，索引文件用临时路径，`commit-tree` 不带 `-p`）：

```bash
cd "G:/claude code" && export GIT_INDEX_FILE=/tmp/ghpack.idx$$
rm -f "$GIT_INDEX_FILE" && git read-tree master
for f in orders_db.py selfcheck.py installer.py setup.bat; do
  git update-index --add --cacheinfo 100644,$(git rev-parse master:$f),$f
done
git update-index --add --cacheinfo 100644,$(git rev-parse master:data.json),data.json
TREE=$(git write-tree) && COMMIT=$(git commit-tree $TREE -m "样例快照：SQLite 存储 + 一键安装器")
git branch -f gh-pack $COMMIT && unset GIT_INDEX_FILE && git rev-parse gh-pack
```
Expected: 打印新的 commit 号（不再是 `40dfba3`）

- [ ] **Step 2: 验快照里没有敏感物**

```bash
cd "G:/claude code" && git ls-tree -r --name-only gh-pack > /tmp/ghpack.files
grep -E "orders\.db$|orders_backup|db_secret|db_config|xianyu_cookies" /tmp/ghpack.files
echo "命中行数：$(grep -cE 'orders\.db$|orders_backup|db_secret|db_config' /tmp/ghpack.files)"
git show gh-pack:data.json | python -X utf8 -c "import json,sys; d=json.load(sys.stdin); print('样例条数',len(d),'| sn 全空',all(not r.get('sn') for r in d))"
```
Expected: 第一条 grep **无输出**、命中行数 **0**；样例条数 = 7、`sn 全空 True`

（0 命中要报数：所以这里显式打印计数，而不是"看起来没有"。）

- [ ] **Step 3: 推送（要他点头才做）**

```bash
cd "G:/claude code" && git push -f origin gh-pack:refs/heads/main
git ls-remote origin refs/heads/main
```
Expected: 远端 `main` 的 hash 与 Step 1 一致。**只推 `gh-pack`**；`master` 永远不推。

- [ ] **Step 4: 交付级验收（跑给他看，不是嘴上说完成）**

```bash
python -X utf8 -m pytest tests -q
python -X utf8 selfcheck.py
python -X utf8 xianyu_review.py --dry-run
```
Expected 三件：全量只剩 4 条既有漂移探针红；体检表四行无 FAIL（后端没起时台账行 skip 属正常）；填单台 dry-run 读出 31 单并拆出品牌型号。

把这四行体检表原文贴给他，并明确说清没测到的部分：真抓单（`xianyu_scraper.py`）要扫码登录，本计划没跑通在线抓取，只验了落库与读取。

---

## 执行期勘误（Task 1-2 实测后回写，代码为准）

| 计划原文 | 实际落地 | 依据 |
| --- | --- | --- |
| `_NEWEST_FIRST = " ORDER BY order_date IS NULL, order_date DESC, id DESC"` | `NEWEST_FIRST = " ORDER BY order_date DESC, id DESC"` | 实测 SQLite（与 MySQL 一样）在 `DESC` 下本就把 NULL 排最后，那个前置项不可观测＝死代码；改名是因为迁移模块要跨模块读它 |
| `insert_orders` 出错时不回滚 | 整批 `try/except sqlite3.Error: conn.rollback(); raise` | 不回滚时半途的行会留在未结束的事务里，被下一次无关批次一起提交（已实测） |
| `test_images_and_raw_data_survive_as_objects` 用 `fetch_orders` | 改用 `fetch_orders_full` | `fetch_orders` 只出 7 个遗留列，按计划写必然假绿 |
| Task 2 只有 `compare_rows` + `rows_from_mysql` | 增加 `prepare_row`，`--from-mysql` 路径先规整类型 | MySQL 的 `DECIMAL` 回来是 Decimal，sqlite3 拒绝绑定；两个 JSON 列回来是文本，存储层再 dumps 成双重编码，读回来是字符串。四条比对断言看不出这件事（源值与目标值字面相同），真正的护栏是一次真实 SQLite 往返测试 |
| 提交信息里 `18 passed` | Task 2 完成时该两文件 `27 passed` | Task 1 评审补了 2 条测试、Task 2 补丁补了 7 条 |

**给 Task 3 的提醒：** `orders.db` 里的 `images` / `raw_data` 经 `fetch_orders_full` 才是对象；`fetch_orders` 那 7 列里没有它们。任何绕开 `prepare_row` 直接写库的路径都会重现双重编码。

| Task 2 的第四条断言 | 计划写的「行顺序」已删除，换成「对不上号的行数」（order_id 为空或在单侧重复） | 两侧各按自己的自增 id 破平，同一天的订单在 SQLite 侧整体反序；实测一次零误差的迁移会报 1 条顺序不等 + 6 条假字段不等，而计划的处置是删库重来 —— 对唯一一份数据太危险。展示顺序由 `tests/test_orders_db.py` 钉住 |
| Task 2 `_normalize` | 坏值原样返回、报成不等，不再抛异常；校验路径缺备份文件/缺库/缺表时打一行中文并返回 1，且不会因为「只是看一眼」而新建空库 | 抛异常发生在写完备份、提交完数据之后，操作者只看到 traceback |
