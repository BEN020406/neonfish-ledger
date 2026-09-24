"""后端测试共用的基础设施：可导入的 app_standalone + 隔离的账本实例。

两个职责：
1. 把仓库根塞进 sys.path，让裸 `pytest tests`（不带 `-m`）也能 `import app_standalone`；
2. 一个 session 级 autouse 兜底，在任何测试体跑起来之前就把 DATA_FILE / IMAGES_DIR
   搬进会话临时目录。真实 data.json 由此在结构上不可达 —— 将来新 fixture 忘了
   per-test patch，也只是撞到沙盒里那个故意不存在的路径，而不是先写坏用户账本再报错。
   兜底不替代 per-test patch：种子数据仍然由各测试自己的 fixture 决定。
"""
import hashlib
import http.client
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 会话级兜底要保护的那个真实账本（与 app_standalone.DATA_FILE 同源，但这里自己算，
# 免得 patch 之后再也拿不到原始路径）。
REAL_DATA_FILE = os.path.join(ROOT, "data.json")

SEED = [
    {"brand": "微星", "model": "B650M GAMING WIFI", "cost": 600, "sell": 900,
     "sn": "", "accessory": "原盒", "accessory_price": "", "extra_price": "", "images": []},
    {"brand": "光威", "model": "神策 16G", "cost": 200, "sell": "",
     "sn": "", "accessory": "", "accessory_price": "", "extra_price": "", "images": [],
     "source_order_id": "20250921001", "order_date": "2025-09-21 14:32:05",
     "item_title": "光威神策16G，成色新", "order_paid": 800},
]


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _sha256(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def ledger_sandbox(tmp_path_factory):
    """结构性安全网：先于任何测试体把模块级路径指向会话临时目录。"""
    import app_standalone as m

    sandbox = tmp_path_factory.mktemp("ledger-sandbox")
    # 故意不预置数据：忘了 patch 的测试会立刻炸在 FileNotFoundError 上，
    # 而不是"读到了真的账本"这种既静默又危险的状态。
    m.DATA_FILE = str(sandbox / "unseeded-data.json")
    m.IMAGES_DIR = str(sandbox / "images")
    os.makedirs(m.IMAGES_DIR, exist_ok=True)

    before = _sha256(REAL_DATA_FILE)
    yield sandbox
    after = _sha256(REAL_DATA_FILE)
    assert before == after, (
        "测试期间真实 data.json 变了（sha %s -> %s）：隔离泄漏，或者有别的东西在写账本"
        % (before, after)
    )


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

    def decode(raw):
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise AssertionError(
                "backend replied with a non-JSON body: %r" % (raw[:200],)
            ) from exc

    def shaped(status, payload, headers, with_headers):
        """默认只回 (status, json)，所有既有调用点不受影响；
        with_headers=True 时多回一份响应头（http.client.HTTPMessage，取头大小写不敏感）。"""
        return (status, payload, headers) if with_headers else (status, payload)

    def call(method, path, payload=None, raw_body=None, with_headers=False, if_match=None):
        """一次 HTTP 往返；断连/坏响应都翻译成能读懂的 AssertionError，不抛裸异常。

        raw_body 用来发"不是合法 JSON"的请求体（payload 会被 json.dumps，做不到）。
        with_headers=True 额外返回响应头，用来看 GET /api/data 的 X-Ledger-Stamp。
        if_match=<stamp> 给请求加 If-Match 头，模拟"前端拿自己读到的版本去写盘"。
        """
        body = raw_body
        if body is None and payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + path, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if if_match is not None:
            req.add_header("If-Match", if_match)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return shaped(resp.status, decode(resp.read()), resp.headers, with_headers)
        except urllib.error.HTTPError as exc:
            return shaped(exc.code, decode(exc.read()), exc.headers, with_headers)
        except (urllib.error.URLError, ConnectionResetError, socket.timeout,
                http.client.HTTPException) as exc:
            raise AssertionError(
                "backend aborted the request (likely an unhandled exception): %r" % (exc,)
            ) from exc

    yield call, lambda: read_json(str(data_file)), str(data_file)
    server.shutdown()
    server.server_close()
