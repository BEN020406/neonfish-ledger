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


def prepare_row(row):
    """把 MySQL 回来的一行规整成存储层能直接接手的形状。

    两个错形状只来自 MySQL 读路径（抓取脚本给的是真 float / list / dict），
    所以修在迁移入口，orders_db 保持薄：

    - price：DECIMAL(10,2) 由 pymysql 变成 decimal.Decimal，而 sqlite3 拒收它
      （ProgrammingError: Error binding parameter 3），第一行就炸。→ float。
    - images / raw_data：JSON 列回来是文本，insert_order 会再 json.dumps 一次，
      库里成了二次编码；fetch_orders_full 只解一层，下游拿到的是 str。→ 先 loads。

    解不开的文本原样留着：把一单的图片静默换成空列表，比后面响亮地对不齐更难查。
    NULL / 空串规整成 None —— 键存在但值是 None 时，insert_order 里 order.get(col, [])
    并不会用默认值，json.dumps(None) 存成 'null'，fetch_orders_full 解一层得到 None：
    既不会读回成 str（那正是上面那个缺陷的形状），也不会被我们凭空换成
    [] / {} 而让 compare_rows 对着源里的 NULL 报假不一致。
    """
    out = dict(row)
    if out.get("price") is not None:
        out["price"] = float(out["price"])
    for col in ("images", "raw_data"):
        if col not in out:
            continue
        value = out[col]
        if value is None or value == "":
            out[col] = None
            continue
        if isinstance(value, str):
            try:
                out[col] = json.loads(value)
            except ValueError:
                pass
    return out


def rows_from_mysql():
    import pymysql
    password = os.environ.get("XIANYU_MYSQL_PASSWORD")
    if not password:
        raise SystemExit("缺环境变量 XIANYU_MYSQL_PASSWORD —— 口令不读文件、不进参数")
    conn = pymysql.connect(
        host=os.environ.get("XIANYU_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("XIANYU_DB_PORT", "3306")),
        user=os.environ.get("XIANYU_DB_USER", "root"),
        password=password,
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
        src = [prepare_row(r) for r in rows_from_mysql()]
        with open(args.backup, "w", encoding="utf-8") as f:
            json.dump(src, f, ensure_ascii=False, default=str, indent=1)
        print("已导出备份 %s（%d 行）" % (args.backup, len(src)))
        conn = orders_db.connect()
        orders_db.ensure_schema(conn)
        new_count, dup_count = orders_db.insert_orders(conn, src)
        print("写入完成：新增 %d，已存在跳过 %d" % (new_count, dup_count))
    else:
        # 校验路径不写库：既不会撞上 Decimal 绑定，也不会二次编码，
        # 备份里的字符串/对象由 compare_rows 自己归一，不必过 prepare_row。
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
