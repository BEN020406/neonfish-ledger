import json
import threading
import urllib.error
import urllib.request

import pytest


SEED = [
    {"brand": "微星", "model": "B650M GAMING WIFI", "cost": 600, "sell": 900,
     "sn": "", "accessory": "原盒", "accessory_price": "", "extra_price": "", "images": []},
    {"brand": "光威", "model": "神策 16G", "cost": 200, "sell": "",
     "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": [],
     "source_order_id": "20250921001", "order_date": "2025-09-21 14:32:05",
     "item_title": "光威神策16G，成色新", "order_paid": 800},
]

ORDER_KEYS = ("source_order_id", "order_date", "item_title", "order_paid")


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def api(monkeypatch, tmp_path):
    """账本后端的隔离实例：DATA_FILE 指向 tmp，真实 data.json 绝不参与。"""
    import app_standalone as m

    data_file = tmp_path / "data.json"
    data_file.write_text(json.dumps(SEED, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(m, "DATA_FILE", str(data_file))
    monkeypatch.setattr(m, "IMAGES_DIR", str(tmp_path / "images"))

    server = m.LedgerServer(("127.0.0.1", 0), m.APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    def call(method, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + path, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionResetError) as exc:
            raise AssertionError("backend aborted the request (likely an unhandled exception): %r" % (exc,))

    yield call, lambda: read_json(str(data_file)), str(data_file)
    server.shutdown()
    server.server_close()


def test_add_record_persists_order_context(api):
    call, load, _ = api
    status, resp = call("POST", "/api/data", {
        "brand": "七彩虹", "model": "B760M", "cost": 700, "sell": "",
        "source_order_id": "20250922007", "order_date": "2025-09-22 09:10:00",
        "item_title": "七彩虹b760M主板", "order_paid": 700,
    })
    assert status == 200, resp
    records = load()
    saved = records[resp["index"]]
    assert saved["brand"] == "七彩虹"
    assert [saved.get(k) for k in ORDER_KEYS] == [
        "20250922007", "2025-09-22 09:10:00", "七彩虹b760M主板", 700,
    ]


def test_manual_add_has_no_order_keys(api):
    call, load, _ = api
    status, resp = call("POST", "/api/data", {"brand": "AMD", "model": "7800X3D", "cost": 2200, "sell": 2500})
    assert status == 200, resp
    assert not any(k in load()[resp["index"]] for k in ORDER_KEYS), \
        "手动新增被塞了订单键，会污染闲鱼分组判据"


def test_update_partial_body_keeps_order_context_and_survives_empty_sell(api):
    """seed[1] 的 sell 是空串：省略 sell 的 PUT 过去会走 float('') 抛 ValueError，
    整次写入不落盘且连接被掐断。这条必须先失败，才能证明修复有效。"""
    call, load, _ = api
    status, resp = call("PUT", "/api/data/1", {"cost": 260})
    assert status == 200, resp
    saved = load()[1]
    assert saved["cost"] == 260
    assert [saved.get(k) for k in ORDER_KEYS] == [
        "20250921001", "2025-09-21 14:32:05", "光威神策16G，成色新", 800,
    ]
    assert saved["sell"] == ""
