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
