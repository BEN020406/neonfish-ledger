"""自检必须会说坏消息：每行至少一条 ok 用例 + 一条 fail 或 skip 用例。

台账行额外钉一条：探针走自检专用端口，绝不碰 8765 —— 否则他常开的
台账实例会让这一行假通过（app_standalone 对 8765 是"复用不新开"）。
"""

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


def test_ledger_probe_never_touches_the_live_8765():
    """默认探针地址必须避开常驻台账的 8765，让它无从冒充"体检通过"。"""
    assert ":8765" not in sc.LEDGER_URL


def test_http_json_uses_module_ledger_url(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(url, timeout=None):
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(sc.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(sc, "LEDGER_URL", "http://127.0.0.1:9911/api/data")
    assert sc._http_json() == (200, [])
    assert seen["url"] == "http://127.0.0.1:9911/api/data"


def test_ledger_url_flag_reaches_the_probe(tmp_path, monkeypatch):
    """--ledger-url 是给安装器留的缝：真起了临时后端时把地址传进来。"""
    seen = {}

    class Resp:
        status = 200

        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(url, timeout=None):
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(sc.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(sc, "LEDGER_URL", "http://127.0.0.1:9999/placeholder")
    p = ledger(tmp_path, [])
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(sc, "_chromium_exe", lambda: str(exe))
    db = str(tmp_path / "orders.db")
    conn = orders_db.connect(db)
    orders_db.ensure_schema(conn)
    conn.close()
    code = sc.main(["--base-dir", str(tmp_path), "--data-file", str(p), "--db-path", db,
                    "--ledger-url", "http://127.0.0.1:9911/api/data"])
    assert seen["url"] == "http://127.0.0.1:9911/api/data"
    assert code == 0


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


def test_images_ok_when_ref_is_bare_filename(tmp_path):
    """真实后端往账本里存的是裸文件名（app_standalone 只 append filename）。
    这一条不过，图片行在他的真账本上就永远狼来了。"""
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "x.png").write_bytes(b"x")
    p = ledger(tmp_path, [{"images": ["x.png"]}])
    assert sc.check_images(base_dir=tmp_path, data_file=p)["status"] == "ok"


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


def test_one_failure_is_visible_in_summary(tmp_path, monkeypatch, capsys):
    """失败要被看见：体检表里那行必须打 [FAIL]，不是只改返回值。"""
    (tmp_path / "images").mkdir()
    p = ledger(tmp_path, [{"images": ["gone.jpg"]}])
    monkeypatch.setattr(sc, "_http_json", lambda: (200, []))
    exe = tmp_path / "chrome.exe"; exe.write_bytes(b"x")
    monkeypatch.setattr(sc, "_chromium_exe", lambda: str(exe))
    code = sc.main(["--base-dir", str(tmp_path), "--data-file", str(p),
                    "--db-path", str(tmp_path / "nope.db")])
    out = capsys.readouterr().out
    assert code == 1
    assert "[FAIL]" in out and "图片" in out
