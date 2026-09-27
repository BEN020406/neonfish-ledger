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
_NEWEST_FIRST = " ORDER BY order_date IS NULL, order_date DESC, id DESC"


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
    rows = conn.execute("SELECT %s FROM xianyu_orders%s" % (_PENDING_COLS, _NEWEST_FIRST)).fetchall()
    return [dict(r) for r in rows]


def fetch_orders_full(conn):
    """迁移校验专用：连 images / raw_data 一起读回，JSON 列解成对象。"""
    cols = ", ".join(COLS)
    rows = conn.execute("SELECT %s FROM xianyu_orders%s" % (cols, _NEWEST_FIRST)).fetchall()
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
