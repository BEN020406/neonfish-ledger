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
