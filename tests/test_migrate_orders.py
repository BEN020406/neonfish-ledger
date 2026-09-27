"""迁移的四条断言必须各自能抓出问题，否则它们只是装饰。

除了比对器本身，这里还钉住 MySQL 回来的行在进入存储层之前的规整（prepare_row），
以及一条真写进 SQLite 再读回来的往返测试——两个类型缺陷只有往返测试抓得住。
"""

import copy
import decimal

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


def mysql_row(oid="1", **kw):
    """pymysql 真正会给回来的形状：DECIMAL 列是 Decimal，两个 JSON 列是文本。"""
    base = row(oid, price=decimal.Decimal("99.00"))
    base.update(kw)
    return base


def sqlite_conn(tmp_path):
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    return conn


def test_prepare_row_turns_decimal_price_into_float():
    """Decimal 进不了 sqlite3 的参数绑定，价格必须在入口就变成 float。"""
    prepared = mig.prepare_row(mysql_row())
    assert isinstance(prepared["price"], float)
    assert prepared["price"] == 99.0
    assert mig.prepare_row(row("1", price="99.00"))["price"] == 99.0
    assert mig.prepare_row(row("1", price=None))["price"] is None


def test_prepare_row_parses_json_text_columns():
    """文本必须先解成真对象，否则存储层的 json.dumps 会再包一层。"""
    prepared = mig.prepare_row(mysql_row(images='["a.jpg"]', raw_data='{"a": 1}'))
    assert prepared["images"] == ["a.jpg"]
    assert isinstance(prepared["images"], list)
    assert prepared["raw_data"] == {"a": 1}
    assert isinstance(prepared["raw_data"], dict)
    assert prepared["item_title"] == "板子"          # 其余列原样通过
    assert prepared["order_id"] == "1"


def test_prepare_row_leaves_unparsable_json_text_untouched():
    """解不开就留着原值：悄悄把一单的图片换成空列表，比后面响亮地报错更难查。"""
    assert mig.prepare_row(mysql_row(images="坏掉的文本"))["images"] == "坏掉的文本"


def test_prepare_row_keeps_missing_json_columns_readable(tmp_path):
    """NULL / 空串规整成 None：库里存 'null'，读回来是 None 而不是 str。

    不换成 [] / {}，因为 compare_rows 会拿源里的 None 去比目标里的 [] 报假不一致；
    也不能留空串，那会被存成 '""' 再读回成 str——正是双重编码那种错形状。
    """
    prepared = mig.prepare_row(mysql_row(images="", raw_data=None))
    assert prepared["images"] is None
    assert prepared["raw_data"] is None
    conn = sqlite_conn(tmp_path)
    orders_db.insert_order(conn, prepared)
    conn.commit()
    got = orders_db.fetch_orders_full(conn)[0]
    assert got["images"] is None
    assert got["raw_data"] is None


def test_prepared_mysql_row_survives_a_real_sqlite_round_trip(tmp_path):
    """MySQL 形状的行规整后写真库再读回来，类型必须是下游期望的。

    这条同时钉住两个缺陷：Decimal 价格让 sqlite3 直接拒绝绑定；JSON 文本列
    会被二次编码，读回来是 str 而不是 list / dict。
    """
    conn = sqlite_conn(tmp_path)
    prepared = mig.prepare_row(mysql_row(images='["a.jpg"]', raw_data='{"a": 1}'))
    assert orders_db.insert_order(conn, prepared) == "new"
    conn.commit()
    got = orders_db.fetch_orders_full(conn)[0]
    assert got["price"] == 99.0
    assert isinstance(got["price"], float)
    assert got["images"] == ["a.jpg"]
    assert isinstance(got["images"], list)
    assert got["raw_data"] == {"a": 1}
    assert isinstance(got["raw_data"], dict)


def test_from_mysql_wiring_actually_calls_prepare_row(tmp_path, monkeypatch):
    """钉住调用点：把 main 里的 prepare_row 摘掉，主流程会在第一行炸绑 Decimal。

    假连接只喂一行 MySQL 形状的数据，备份和库都落在 tmp_path 里，
    既不碰 MySQL 也不在仓库里留 orders.db / orders_backup.json。
    """
    conn = sqlite_conn(tmp_path)
    monkeypatch.setattr(mig, "rows_from_mysql", lambda: [mysql_row()])
    monkeypatch.setattr(orders_db, "connect", lambda path=None: conn)
    rc = mig.main(["--from-mysql", "--backup", str(tmp_path / "orders_backup.json")])
    assert rc == 0
    got = orders_db.fetch_orders_full(conn)[0]
    assert got["price"] == 99.0
    assert got["images"] == ["a.jpg"]
    assert got["raw_data"] == {"a": 1, "b": 2}


def test_compare_rows_alone_cannot_see_double_encoding(tmp_path):
    """比对器对这个缺陷是瞎的——真正的防线是上面那条真实往返测试。

    故意绕过 prepare_row，把 MySQL 原始行直接写进 SQLite：images 被二次编码存进
    库，fetch_orders_full 只解一层，读回来是字符串。compare_rows 两边各解一次，
    两边都变成 ["a.jpg"]，于是照报一致。哪天比对器学会了识别二次编码，这条会红，
    届时再决定防线放哪。
    """
    src = [mysql_row(price=99.0, images='["a.jpg"]', raw_data='{"a": 1}')]
    conn = sqlite_conn(tmp_path)
    orders_db.insert_order(conn, src[0])       # 跳过 prepare_row，只留价格这一列是干净的
    conn.commit()
    dst = orders_db.fetch_orders_full(conn)
    assert isinstance(dst[0]["images"], str)   # 双重编码确实写进了库
    assert mig.compare_rows(src, dst) == []    # 比对器对此毫无反应
