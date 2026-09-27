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
