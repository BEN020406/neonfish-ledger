"""迁移的四条断言必须各自能抓出问题，否则它们只是装饰。

除了比对器本身，这里还钉住 MySQL 回来的行在进入存储层之前的规整（prepare_row），
以及真写进 SQLite 再读回来的往返测试——两个类型缺陷和同一天多单的破平错位
只有往返测试抓得住。四条断言各自一条用例：条数 / order_id 集合 /
配不上号的行数 / 字段值。
"""

import copy
import decimal
import json
from datetime import datetime

import migrate_orders_to_sqlite as mig
import orders_db


def row(oid, price="99.00", raw='{"a":1,"b":2}', order_date="2026-09-20 14:05:06", **kw):
    base = {"order_id": oid, "item_title": "板子", "price": price,
            "trade_type": "bought", "counterparty": "甲", "order_status": "成功",
            "order_date": order_date, "images": '["a.jpg"]', "raw_data": raw}
    base.update(kw)
    return base


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
    # 只在一侧有的行由集合那条报，不再逐列刷一遍假的字段不等
    assert not any(p.startswith("字段") for p in problems)


def test_row_position_is_not_an_assertion():
    """位置顺序不当断言：值一律按 order_id 对齐取，两侧各自排各自的。

    两侧都按 ORDER BY order_date DESC, id DESC 读，可 id 是两个引擎各的一套自增号，
    SQLite 侧按到达顺序编号，同一天的几单读回来会整体反序——位置配对在一模一样的
    迁移上也会报出假的不一致。
    """
    assert mig.compare_rows(SRC, as_sqlite([row("3"), row("1"), row("2")])) == []


def test_detects_unalignable_row_count_drift():
    """第四条断言抓的是「按 order_id 配不上号的行」：只在它上面出问题时得单独响。

    两侧条数相同、order_id 集合也相同，前两条都哑的；源里两条没有 order_id 的脏行
    到目标只剩一条——这是数据能悄悄少掉的唯一通道。
    """
    src = [row("A"), row("A"), row(None)]
    dst = [row("A"), row(None), row(None)]
    problems = mig.compare_rows(src, dst)
    assert sum("配不上" in p for p in problems) == 1
    # 空 order_id 两侧各零条，只有重复的个数不同：一个号出现三次 vs 两个号各两次，
    # 条数与集合照旧全等，只数空值的实现在这里必须露馅。
    src2 = [row("A"), row("A"), row("A"), row("B")]
    dst2 = [row("A"), row("A"), row("B"), row("B")]
    problems2 = mig.compare_rows(src2, dst2)
    assert sum("配不上" in p for p in problems2) == 1


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


def test_unparsable_values_become_a_loud_mismatch_not_a_crash():
    """归一化解不开的值必须原样退回，响成一条字段不等，而不是抛在结算之前。

    这发生在 insert_orders 已提交、备份已写盘之后，抛出去只会留下一段 traceback
    和一行没打印的合计， operator 看不到「不一致」这个结论。
    """
    src = [row("1", price="面议", raw="坏掉的文本")]
    dst = [{**src[0], "price": 99.0, "images": ["a.jpg"], "raw_data": {"a": 1}}]
    problems = mig.compare_rows(src, dst)
    assert any(p.startswith("字段") and "price" in p for p in problems)
    assert any(p.startswith("字段") and "raw_data" in p for p in problems)


def mysql_row(oid="1", **kw):
    """pymysql 真正会给回来的形状：DECIMAL 是 Decimal，JSON 列是文本，DATETIME 是 datetime。"""
    base = row(oid, price=decimal.Decimal("99.00"),
               order_date=datetime(2026, 9, 20, 14, 5, 6))
    base.update(kw)
    return base


def sqlite_conn(tmp_path):
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    return conn


def test_datetime_and_text_order_date_compare_equal():
    """同一时刻的 datetime 形状与文本形状必须判等，--from-mysql 才可能过。

    源侧 order_date 是 pymysql 给的 datetime，目标侧是存储层落的 ISO 文本
    （orders_db 有意不依赖 sqlite3 的日期适配器）。
    """
    src = [mysql_row(images=["a.jpg"], raw_data={"a": 1})]
    dst = [{**src[0], "price": 99.0, "order_date": "2026-09-20 14:05:06"}]
    assert mig.compare_rows(src, dst) == []
    assert mig.compare_rows(dst, src) == []


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


def test_tied_order_dates_survive_a_real_sqlite_round_trip(tmp_path):
    """同一天多单是常态：位置配对在这种数据上会对着一次干净的迁移报假不一致。

    源是 MySQL 按 ORDER BY order_date DESC, id DESC 给的顺序（m2 的 MySQL id 比
    m1 大，所以它在前）；SQLite 侧的 id 是我们按到达顺序另起的一套自增号，
    同一天的那两单读回来会整个反向。两侧排序键一样、破平的 id 却各是各的。
    """
    src = [
        mig.prepare_row(mysql_row(oid="m2", item_title="光威神策 16G",
                                  price=decimal.Decimal("200.00"),
                                  order_date=datetime(2026, 9, 21, 10, 0, 0))),
        mig.prepare_row(mysql_row(oid="m1", item_title="技嘉 B650M",
                                  price=decimal.Decimal("700.00"),
                                  order_date=datetime(2026, 9, 21, 10, 0, 0))),
        mig.prepare_row(mysql_row(oid="m0", item_title="微星 PRO H610M-E",
                                  price=decimal.Decimal("400.00"),
                                  order_date=datetime(2026, 9, 20, 9, 0, 0))),
    ]
    conn = sqlite_conn(tmp_path)
    orders_db.insert_orders(conn, src)
    assert mig.compare_rows(src, orders_db.fetch_orders_full(conn)) == []


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


def test_verify_only_reports_missing_backup_instead_of_traceback(tmp_path, monkeypatch, capsys):
    """校验路径缺备份文件：一行中文 + 退出码 1，别把 FileNotFoundError 甩在屏幕上。"""
    monkeypatch.setattr(orders_db, "DB_PATH", str(tmp_path / "orders.db"))
    rc = mig.main(["--backup", str(tmp_path / "orders_backup.json")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "备份" in out and "orders_backup.json" in out
    assert "Traceback" not in out


def test_verify_only_reports_missing_db_without_creating_it(tmp_path, monkeypatch, capsys):
    """库还不存在时只做「看」这件事：一行中文、退出码 1，且不许留下空 orders.db。

    仓库里凭空多出一个空 orders.db，用户下一次就会被它骗过「已经迁移过了」这个判断。
    """
    db = tmp_path / "orders.db"
    monkeypatch.setattr(orders_db, "DB_PATH", str(db))
    backup = tmp_path / "orders_backup.json"
    backup.write_text("[]", encoding="utf-8")
    rc = mig.main(["--backup", str(backup)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "orders.db" in out and "Traceback" not in out
    assert not db.exists(), "只读校验不该把库创建出来"


def test_verify_only_reports_db_without_the_table(tmp_path, monkeypatch, capsys):
    """文件在、表不在（指错了库或迁移没跑完）：同样是一行中文，不是 OperationalError。"""
    db = tmp_path / "orders.db"
    db.write_bytes(b"")                        # 空文件是个合法库，只是没有表
    monkeypatch.setattr(orders_db, "DB_PATH", str(db))
    backup = tmp_path / "orders_backup.json"
    backup.write_text("[]", encoding="utf-8")
    rc = mig.main(["--backup", str(backup)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "xianyu_orders" in out and "Traceback" not in out


def test_verify_only_path_still_works_when_both_files_are_there(tmp_path, monkeypatch, capsys):
    """两个前置检查都不能把正常路径一起挡掉：备份 + 已迁移的库 -> 退出码 0。"""
    conn = sqlite_conn(tmp_path)
    orders_db.insert_orders(conn, [mig.prepare_row(mysql_row())])
    conn.close()
    monkeypatch.setattr(orders_db, "DB_PATH", str(tmp_path / "orders.db"))
    backup = tmp_path / "orders_backup.json"
    backup.write_text(json.dumps([mysql_row()], default=str), encoding="utf-8")
    assert mig.main(["--backup", str(backup)]) == 0
    assert "一致" in capsys.readouterr().out


def test_verify_only_reports_unparsable_backup_value_as_a_mismatch(tmp_path, monkeypatch, capsys):
    """备份里解不开的值走完整主流程：合计行照样打印、退出码 1，不抛。"""
    conn = sqlite_conn(tmp_path)
    orders_db.insert_orders(conn, [mig.prepare_row(mysql_row())])
    conn.close()
    monkeypatch.setattr(orders_db, "DB_PATH", str(tmp_path / "orders.db"))
    backup = tmp_path / "orders_backup.json"
    backup.write_text(json.dumps([row("1", raw="坏掉的文本")], ensure_ascii=False),
                      encoding="utf-8")
    rc = mig.main(["--backup", str(backup)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "raw_data" in out and "不一致" in out
    assert "Traceback" not in out
