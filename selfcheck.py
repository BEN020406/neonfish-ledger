r"""装机后的四行体检：台账 / 图片 / 抓单库 / 浏览器内核。

规矩：每一行都必须能给出 fail 并附下一步动作。连不上后端这种"还没跑起来"
的情况报 skip 而不是 ok —— 否则体检表只会说漂亮话。

台账行刻意探"自检专用端口"（8865），不探 8765：app_standalone 对 8765
是"复用不新开"的语义，他那个常开实例会把这一行顶成假 ok。自检也绝不
自己拉后端 —— 最多告诉你要怎么拉。安装器真起了临时后端时，用
--ledger-url（或 SELFCHECK_LEDGER_URL 环境变量）把地址传进来。

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

import orders_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 8765 常驻台账、8766-8776 填入台、8790 UI 夹具，8865 谁都不占。
PROBE_PORT = 8865
LEDGER_URL = (os.environ.get("SELFCHECK_LEDGER_URL")
              or "http://127.0.0.1:%d/api/data" % PROBE_PORT)


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
    except (OSError, ValueError):        # URLError/socket 错都是 OSError 的子孙
        return _result("台账", "skip",
                       "自检专用端口 %d 上没起后端（刻意不探 8765，免得常驻实例冒充通过）；"
                       "手动复查台账：跑 python app_standalone.py 后开 "
                       "http://127.0.0.1:8765" % PROBE_PORT)
    if status != 200:
        return _result("台账", "fail", "/api/data 返回 HTTP %s" % status)
    if not isinstance(payload, list):
        return _result("台账", "fail", "/api/data 返回的不是账本数组，接口形状变了")
    return _result("台账", "ok", "后端在跑，%d 条记录" % len(payload))


def _ref_present(base_dir, ref):
    """账本里引用是否落盘：真实后端存的是裸文件名（app_standalone 只 append
    filename），历史上也见过带 images/ 前缀的写法，两种形态都认，
    两处都找不到才算缺 —— 对真账本狼来了的检查器不是检查器。"""
    rel = ref.replace("\\", "/")
    return (os.path.exists(os.path.join(base_dir, rel))
            or os.path.exists(os.path.join(base_dir, "images", os.path.basename(rel))))


def check_images(base_dir=BASE_DIR, data_file=None):
    data_file = data_file or os.path.join(base_dir, "data.json")
    if not os.path.exists(data_file):
        return _result("图片", "fail", "找不到账本文件 %s" % data_file)
    with open(data_file, encoding="utf-8") as f:
        records = json.load(f)
    refs = [p for r in records if isinstance(r, dict)
            for p in (r.get("images") or []) if isinstance(p, str) and p]
    missing = [p for p in refs if not _ref_present(base_dir, p)]
    if missing:
        return _result("图片", "fail",
                       "引用 %d 张、缺 %d 张（例：%s）—— 换机时 images/ 目录要整个拷过来"
                       % (len(refs), len(missing), missing[0]))
    return _result("图片", "ok", "引用 %d 张全在" % len(refs))


def check_orders_db(db_path=None):
    db_path = db_path or orders_db.DB_PATH      # 认 XIANYU_ORDERS_DB，测试指哪儿查哪儿
    if not os.path.exists(db_path):
        return _result("抓单库", "skip",
                       "还没有 orders.db；抓一次单（python xianyu_scraper.py）就有了")
    try:
        conn = sqlite3.connect(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM xianyu_orders").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        # 必须在 DatabaseError 之前接：它是 DatabaseError 的子类，反过来写
        # 这一支就是死代码，"缺表"会被下一支误报成"不像 SQLite"。
        return _result("抓单库", "fail", "缺 xianyu_orders 表：%s" % exc)
    except sqlite3.DatabaseError as exc:
        return _result("抓单库", "fail", "文件打不开，不像 SQLite：%s" % exc)
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
    ap.add_argument("--ledger-url", default=None,
                    help="台账行真正要探的地址；安装器起好临时后端后传进来")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.ledger_url:
        globals()["LEDGER_URL"] = args.ledger_url

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
