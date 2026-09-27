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
    """order_id 撞键算 dup、不炸批，而且整批真的提交进了库。

    计数走同一文件的第二个连接：它只看得见已提交的数据，
    所以 insert_orders 里少掉 conn.commit() 这条就会红。
    """
    conn = make_conn(tmp_path)
    assert orders_db.insert_orders(conn, [order(), order(oid="other"), order()]) == (2, 1)
    reader = orders_db.connect(str(tmp_path / "orders.db"))
    try:
        assert orders_db.count_orders(reader) == 2      # 重复那条没多写一行
    finally:
        reader.close()


def test_bad_last_row_rolls_back_the_whole_batch(tmp_path):
    """批里最后一行违反 CHECK，前面几行也不能留在库里。

    抛出去之前不回滚的话，已经写进去的那几行会卡在未结束的事务里，
    被下一次无关的批量写入一起提交上去。
    """
    conn = make_conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        orders_db.insert_orders(conn, [
            order(oid="good-1"), order(oid="good-2"), order(oid="bad", trade_type="退掉"),
        ])
    assert orders_db.count_orders(conn) == 0
    assert conn.in_transaction is False


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
    """JSON 两列写进去是文本、读回来是对象（解码只发生在 fetch_orders_full）。"""
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    got = orders_db.fetch_orders_full(conn)[0]
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


def test_two_null_dates_come_back_newest_insert_first(tmp_path):
    """两条都没有 order_date 时，先后只由 id 倒序决定。

    日期解析不出来是常态（页面上就有解析不到的行），所以这个兜底项是活的：
    把 id DESC 改成 id ASC，这两条就会反过来。
    """
    conn = make_conn(tmp_path)
    orders_db.insert_orders(conn, [
        order(oid="earlier", order_date=None),
        order(oid="later", order_date=None),
    ])
    ids = [r["order_id"] for r in orders_db.fetch_orders(conn)]
    assert ids == ["later", "earlier"]


def test_fetch_orders_keys_are_the_legacy_ones(tmp_path):
    """填单台按这批键名取值，改名就是自找麻烦。"""
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    assert set(orders_db.fetch_orders(conn)[0]) == {
        "order_id", "item_title", "price", "trade_type",
        "counterparty", "order_status", "order_date",
    }


def test_fetch_orders_full_returns_all_nine_columns_by_name(tmp_path):
    """全视图 = 7 个遗留列 + images / raw_data 两个 JSON 列，列名逐个钉死。

    不能拿去和 orders_db.COLS 比：那个常量正是拼 SELECT 的东西，
    从它里面删掉一列，两边依然相等，这条测试照样绿。
    """
    conn = make_conn(tmp_path)
    orders_db.insert_order(conn, order())
    conn.commit()
    assert set(orders_db.fetch_orders_full(conn)[0]) == {
        "order_id", "item_title", "price", "trade_type", "counterparty",
        "order_status", "order_date", "images", "raw_data",
    }


def test_to_date_text_forms():
    assert orders_db.to_date_text(datetime(2026, 9, 20, 14, 5, 6)) == "2026-09-20 14:05:06"
    assert orders_db.to_date_text("2026-09-20 14:05:06") == "2026-09-20 14:05:06"
    assert orders_db.to_date_text(None) is None
    assert orders_db.to_date_text("") is None
