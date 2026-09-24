"""后端写入路径的测试：Task 1 是订单上下文 round-trip、空 sell 的不对称、坏输入的显式拒绝；
Task 2 加上写盘冲突检测（两个进程整读整写 data.json 时，后写的必须被拒绝而不是抹掉前者）；
Task 2b 把冲突检测做到"页面是不是旧的"（客户端带着读到的版本写盘，版本不符就 409）。

fixture（api / SEED / 沙盒兜底）在 tests/conftest.py，后面 10 个测试文件共用。
"""
import json
import os
import re

import pytest

from app_standalone import ORDER_CONTEXT_KEYS

# 订单上下文的四个键，测试和 app_standalone 共用同一个常量，清单不会在两处各写一份。
# 但常量被改窄/改错时"跟着它一起断言"是查不出来的，所以额外用字面量钉一次。
AGREED_ORDER_KEYS = {"source_order_id", "order_date", "item_title", "order_paid"}


def test_order_context_key_list_is_the_agreed_four():
    """钉住清单本身：谁动了 ORDER_CONTEXT_KEYS，这里先红，别处的共用断言才有意义。"""
    assert set(ORDER_CONTEXT_KEYS) == AGREED_ORDER_KEYS
    assert len(ORDER_CONTEXT_KEYS) == 4, "有重复键，写入循环会覆盖"


def test_add_record_persists_order_context(api):
    call, load, _ = api
    # cost 用字符串发：表单里拿到的就是字符串，新增侧同样必须存成 float 而不是 "700"。
    status, resp = call("POST", "/api/data", {
        "brand": "七彩虹", "model": "B760M", "cost": "700", "sell": "",
        "source_order_id": "20250922007", "order_date": "2025-09-22 09:10:00",
        "item_title": "七彩虹b760M主板", "order_paid": 700,
    })
    assert status == 200, resp
    records = load()
    saved = records[resp["index"]]
    assert resp["record"] == saved, "handler 返回的 record 和落盘的不是同一条，响应里的值不可信"
    assert saved["brand"] == "七彩虹"
    assert saved["cost"] == 700.0 and isinstance(saved["cost"], float), \
        '新增侧把 "700" 存成了 %r，账本里会混进字符串金额' % (saved["cost"],)
    assert [saved.get(k) for k in ORDER_CONTEXT_KEYS] == [
        "20250922007", "2025-09-22 09:10:00", "七彩虹b760M主板", 700,
    ]
    # POST/PUT 的不对称，钉死：新增时空 sell 折算成 0.0，更新时省略 sell 才保留 ""。
    # ""（未定价）在这本账里是唯一标记，18 条空 sell 正好就是 18 条带 source_order_id 的记录，
    # 所以「按 0 计价」和「还没定价」必须能区分 —— 导入任务依赖这条结论。
    assert saved["sell"] == 0.0 and isinstance(saved["sell"], float), (
        "POST 把空 sell 存成了 %r；update 侧是刻意保留空串的，这条不对称是指南定过的结论，"
        "要改就显式改这里" % (saved["sell"],)
    )


def test_manual_add_has_no_order_keys(api):
    call, load, _ = api
    status, resp = call("POST", "/api/data", {"brand": "AMD", "model": "7800X3D", "cost": 2200, "sell": 2500})
    assert status == 200, resp
    saved = load()[resp["index"]]
    assert resp["record"] == saved, "handler 返回的 record 和落盘的不是同一条，响应里的值不可信"
    assert not any(k in saved for k in ORDER_CONTEXT_KEYS), \
        "手动新增被塞了订单键，会污染闲鱼分组判据"


def test_update_record_stores_order_context_and_preserves_omitted_keys(api):
    """PUT 的订单上下文路径（6292e85 里唯一没被任何测试覆盖的行为）：
    四个键全发送 → 原样落盘；第二次只发 item_title → 另外三个必须保持上一次的值。"""
    call, load, _ = api
    sent = {"source_order_id": "20250922007", "order_date": "2025-09-22 09:10:00",
            "item_title": "七彩虹b760M主板", "order_paid": 700}
    status, resp = call("PUT", "/api/data/0", dict(sent))
    assert status == 200, resp
    saved = load()[0]
    assert resp["record"] == saved, "handler 返回的 record 和落盘的不是同一条，响应里的值不可信"
    assert [saved[k] for k in ORDER_CONTEXT_KEYS] == [sent[k] for k in ORDER_CONTEXT_KEYS], \
        "PUT 白名单没生效：发过去的订单上下文没有原样落盘"

    untouched = [k for k in ORDER_CONTEXT_KEYS if k != "item_title"]
    status, resp = call("PUT", "/api/data/0", {"item_title": "改名后的板子"})
    assert status == 200, resp
    after = load()[0]
    assert resp["record"] == after
    assert after["item_title"] == "改名后的板子"
    assert [after.get(k) for k in untouched] == [sent[k] for k in untouched], \
        "body 里没出现的订单键被抹掉了，合并逻辑漏了 preserve 分支"


def test_update_partial_body_keeps_order_context_and_survives_empty_sell(api):
    """seed[1] 的 sell 是空串：省略 sell 的 PUT 过去会走 float('') 抛 ValueError，
    整次写入不落盘且连接被掐断。这条必须先失败，才能证明修复有效。"""
    call, load, _ = api
    # cost 用字符串发：表单里拿到的就是字符串，存成 "260" 还是 260.0 必须能被测出来。
    status, resp = call("PUT", "/api/data/1", {"cost": "260"})
    assert status == 200, resp
    saved = load()[1]
    assert resp["record"] == saved, "handler 返回的 record 和落盘的不是同一条，响应里的值不可信"
    assert saved["cost"] == 260.0 and isinstance(saved["cost"], float), \
        "cost 没做数值转换，存进去的是 %r" % (saved["cost"],)
    assert [saved.get(k) for k in ORDER_CONTEXT_KEYS] == [
        "20250921001", "2025-09-21 14:32:05", "光威神策16G，成色新", 800,
    ]
    assert saved["sell"] == ""


@pytest.mark.parametrize("bad", ["abc", "12元", {"v": 1}, [7]])
def test_sent_non_numeric_price_is_rejected_without_writing(api, bad):
    """发过来的 cost/sell 既不是空串也不是数字：必须 400 且一条都不写。
    提交前它是 raise（吵但不错），6292e85 里 _to_float 把 "abc" 咽成了 0.0 —— 那是把钱记错。"""
    call, load, _ = api
    before = load()

    status, resp = call("POST", "/api/data", {"brand": "微星", "cost": bad})
    assert status == 400, ("POST cost", bad, resp)
    assert "cost" in resp["error"], resp

    status, resp = call("PUT", "/api/data/0", {"sell": bad})
    assert status == 400, ("PUT sell", bad, resp)
    assert "sell" in resp["error"], resp

    assert load() == before, "非法金额请求之后盘上变了"
    # 省略键仍然保留旧值、空串仍然折算成 0.0 —— 400 只针对「发了但不是数字」。
    status, resp = call("PUT", "/api/data/1", {"cost": ""})
    assert status == 200, resp
    assert load()[1]["sell"] == "", "省略 sell 时把未定价标记写坏了"
    assert load()[1]["cost"] == 0.0, '显式发 "" 的 cost 必须落成 0.0，实际是 %r' % (load()[1]["cost"],)


def test_non_integer_index_answers_400(api):
    """/api/data/abc 不能再用 ValueError 掐断连接（本提交的主题：坏请求要吵但要能读懂）。"""
    call, load, _ = api
    before = load()
    status, resp = call("PUT", "/api/data/abc", {"cost": 1})
    assert status == 400, resp
    assert "abc" in resp["error"], resp
    status, resp = call("DELETE", "/api/data/abc")
    assert status == 400, resp
    assert "abc" in resp["error"], resp
    assert load() == before, "非法下标请求不该写盘"


def test_malformed_json_body_answers_400(api):
    """坏 JSON / 合法但不是对象的 JSON 都得正常回 400，不能 raise 到连接被掐断。"""
    call, load, _ = api
    before = load()
    for raw in (b'{"brand": "x",', b'not json at all', b'[1, 2, 3]', b'123'):
        status, resp = call("POST", "/api/data", raw_body=raw)
        assert status == 400, (raw, resp)
        assert resp["ok"] is False, (raw, resp)
        status, resp = call("PUT", "/api/data/0", raw_body=raw)
        assert status == 400, (raw, resp)
    assert load() == before, "坏请求不该写盘"


# ─── Task 2: 写盘冲突检测 ───

# 409 的 error 字符串是前端契约的一部分，这里逐字钉住。
# 但前端（Task 6 / Task 8）只按 status == 409 分支，别拿这句话当判据；
# 要改它必须连同计划文件里那条前端步骤一起改。
CONFLICT_ERROR = 'data.json changed elsewhere, reloaded'


def _outside_record():
    return {"brand": "外部", "model": "写入", "cost": 1, "sell": 1, "sn": "",
            "accessory": "", "accessory_price": "", "extra_price": "", "images": []}


def _sha(path):
    import hashlib

    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_save_data_refuses_to_clobber_external_writer(api):
    """载入后又出现第三方写入时，save_data(stamp) 必须拒绝，而不是覆盖。

    被拒的那次写盘连 .bak 都不许轮掉：备份只有一代，轮掉就等于把"还能救回来"
    这件事销毁在一次本来就没生效的保存里。这正是本任务存在的理由。
    """
    import app_standalone as m

    call, load, path = api
    m.save_data(m.load_data())              # 先正常写一次，制造唯一的那代 .bak
    data = m.load_data()
    stamp = m.file_stamp()
    bak_before = _sha(path + ".bak")

    outside = load() + [_outside_record()]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(outside, f, ensure_ascii=False)

    with pytest.raises(m.WriteConflict):
        m.save_data(data, stamp)
    assert len(load()) == 3, "冲突时不得写盘"
    assert not os.path.exists(path + ".tmp"), "被拒的写盘不该留下半成品"
    assert _sha(path + ".bak") == bak_before, "冲突写盘把唯一的备份世代轮掉了"

    m.save_data(data)  # 无 stamp 的写盘保持旧语义（回填脚本与 agent 需要）
    assert len(load()) == 2


def _race_save(m, load, path, seen):
    """包住真 save_data：在 handler 读完 data.json 之后、写盘之前，让导入页（8766）整份落一次盘。

    外部写入沿用一个真实形状：追加一条记录（导入页干的就是这么件事）。
    Task 2b 之后 file_stamp 已经是 size + 内容 sha256，等长的外部写照样能被发现，
    所以这里不再需要"必须改体积才拦得住"这种妥协 —— 那句话属于旧的 (mtime_ns, size) 时代。
    """
    real_save = m.save_data

    def racing(data, stamp=None):
        outside = load() + [_outside_record()]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(outside, f, ensure_ascii=False)
        seen["outside"] = outside
        return real_save(data, stamp)  # 真 save_data 在这里抛 WriteConflict

    return racing


def test_save_data_refuses_to_clobber_external_writer(api):
    """载入后又出现第三方写入时，save_data(stamp) 必须拒绝，而不是覆盖。"""
    import app_standalone as m

    call, load, path = api
    data = m.load_data()
    stamp = m.file_stamp()

    outside = load() + [_outside_record()]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(outside, f, ensure_ascii=False)

    with pytest.raises(m.WriteConflict):
        m.save_data(data, stamp)
    assert len(load()) == 3, "冲突时不得写盘"

    m.save_data(data)  # 无 stamp 的写盘保持旧语义（回填脚本与 agent 需要）
    assert len(load()) == 2


def test_update_returns_409_on_conflict(api, monkeypatch):
    import app_standalone as m

    call, load, path = api
    seen = {}

    def fake_save(data, stamp=None):
        seen["stamp"] = stamp
        raise m.WriteConflict("stale")

    monkeypatch.setattr(m, "save_data", fake_save)
    status, resp = call("PUT", "/api/data/0", {"cost": 601})
    assert status == 409, resp
    assert resp["ok"] is False, resp
    assert resp["error"] == CONFLICT_ERROR, resp
    assert seen["stamp"] is not None, "handler 没把 stamp 传给 save_data"
    assert seen["stamp"] == m.file_stamp(), "handler 传的 stamp 不是它自己刚看到的那份文件"


def _give_record_zero_an_image(m, load, path):
    """让 DELETE /api/data/0/images/<f> 真能走到 save_data（SEED 里所有 images 都是空的）。"""
    os.makedirs(m.IMAGES_DIR, exist_ok=True)
    with open(os.path.join(m.IMAGES_DIR, "ghost.jpg"), "wb") as f:
        f.write(b"\xff\xd8fake")
    records = load()
    records[0]["images"] = ["ghost.jpg"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)


CONFLICT_CASES = [
    ("POST", "/api/data", {"brand": "七彩虹", "model": "B760M", "cost": 1, "sell": 1}, None),
    ("PUT", "/api/data/0", {"cost": 601}, None),
    ("DELETE", "/api/data/0", None, None),
    ("DELETE", "/api/data/0/images/ghost.jpg", None, _give_record_zero_an_image),
]


@pytest.mark.parametrize("method,route,payload,prepare", CONFLICT_CASES)
def test_write_endpoints_reject_a_stale_writer(api, monkeypatch, method, route, payload, prepare):
    """真冲突（不是假异常）走 HTTP：四个写盘端点都得 409，且文件停在别人那一份上。

    save_data 抛的是真 WriteConflict（stamp 真的不匹配），所以这条同时证明了
    每个端点都把自己 load 到的那份 stamp 传了下去。
    """
    import app_standalone as m

    call, load, path = api
    seen = {}
    if prepare:
        prepare(m, load, path)
    monkeypatch.setattr(m, "save_data", _race_save(m, load, path, seen))

    status, resp = call(method, route, payload)
    assert status == 409, (method, route, resp)
    assert resp["ok"] is False, resp
    assert resp["error"] == CONFLICT_ERROR, resp
    assert load() == seen["outside"], "%s %s 冲突后仍然改写了账本" % (method, route)
    if prepare is _give_record_zero_an_image:
        # 账本还引用着那张图，图就不能被删 —— 冲突时文件和 data.json 必须一起停在旧状态。
        assert os.path.exists(os.path.join(m.IMAGES_DIR, "ghost.jpg"))


def test_upload_conflict_answers_409_and_leaves_the_ledger_alone(api, monkeypatch):
    """handle_upload 是第五个写盘点，conftest 的 call() 只会发 JSON，够不着 multipart，
    所以这里在进程内直接驱动 handler：手工拼请求体 + 桩掉 send_json。
    """
    import io

    import app_standalone as m

    call, load, path = api
    os.makedirs(m.IMAGES_DIR, exist_ok=True)

    boundary = "ledgerconflictboundary"
    raw = (
        ('--%s\r\nContent-Disposition: form-data; name="file"; filename="shot.jpg"\r\n'
         'Content-Type: image/jpeg\r\n\r\n' % boundary).encode("utf-8")
        + b"\xff\xd8fakedata\r\n"
        + ('--%s\r\nContent-Disposition: form-data; name="record_idx"\r\n\r\n0\r\n' % boundary).encode("utf-8")
        + ('--%s--\r\n' % boundary).encode("utf-8")
    )

    handler = m.APIHandler.__new__(m.APIHandler)
    handler.headers = {
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "Content-Length": str(len(raw)),
    }
    handler.rfile = io.BytesIO(raw)
    replies = []
    handler.send_json = lambda payload, status=200: replies.append((status, payload))

    seen = {}
    monkeypatch.setattr(m, "save_data", _race_save(m, load, path, seen))
    handler.handle_upload()

    assert replies == [(409, {"ok": False, "error": CONFLICT_ERROR})], replies
    assert load() == seen["outside"], "上传写盘被拒后仍然改了账本"
    # 图已经落盘、账本没引用它：留下的是孤儿文件，这是 app_standalone 里写明接受的取舍。
    assert len(os.listdir(m.IMAGES_DIR)) == 1


def test_plain_post_put_delete_sequence_never_conflicts(api):
    """守卫不能挡正常用法：同一个沙盒里连着新增→改→删，一步都不许 409。"""
    call, load, _ = api
    status, resp = call("POST", "/api/data", {"brand": "AMD", "model": "7800X3D", "cost": 2200, "sell": 2500})
    assert status == 200, resp
    idx = resp["index"]

    status, resp = call("PUT", "/api/data/%d" % idx, {"sell": 2600})
    assert status == 200, resp
    assert load()[idx]["sell"] == 2600.0

    status, resp = call("DELETE", "/api/data/%d" % idx)
    assert status == 200, resp
    assert len(load()) == 2, "正常序列写盘后条数不对"


def test_every_http_write_path_passes_a_stamp():
    """新加写盘端点最容易忘的就是 stamp。静态扫一遍 handler，比给每个端点造假冲突更耐久。

    Task 3 的 handle_split_record 也必须写成 save_data(data, stamp) 才能过这里。
    """
    import inspect
    import re

    import app_standalone as m

    calls = re.findall(r"save_data\(([^)\n]*)\)", inspect.getsource(m.APIHandler))
    assert calls, "APIHandler 里找不到 save_data 调用，这个守卫已经失效"
    unstamped = [c for c in calls if c.strip() != "data, stamp"]
    assert not unstamped, "这些写盘没带 stamp，冲突时会把别人刚加的记录整条抹掉: %r" % (unstamped,)

    agent_src = inspect.getsource(m._agent_add_record)
    assert re.search(r"save_data\(data\)", agent_src), "agent 的写盘语义变了，Task 2 的例外要一起复核"
    assert re.search(r"#.*stamp", agent_src), "_agent_add_record 不带 stamp 的理由必须留在注释里，别被当成漏改"


# ─── Task 2b: 客户端带着版本写盘 ───

def test_client_version_mismatch_returns_409(api):
    call, load, path = api
    status, resp, hdr = call("GET", "/api/data", with_headers=True)
    assert status == 200 and hdr.get("X-Ledger-Stamp"), resp
    stamp = hdr["X-Ledger-Stamp"]
    assert re.fullmatch(r"[0-9a-fA-F-]+", stamp), stamp  # header-safe, no spaces or CJK

    outside = load() + [{"brand": "外部", "model": "写入", "cost": 1, "sell": 1}]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(outside, f, ensure_ascii=False)
    before = open(path, "rb").read()

    status, resp = call("PUT", "/api/data/0", {"cost": 700}, if_match=stamp)
    assert status == 409, resp
    assert resp.get("ok") is False
    assert open(path, "rb").read() == before, "409 时一个字节都不能写"

    _, _, hdr2 = call("GET", "/api/data", with_headers=True)
    status, resp = call("PUT", "/api/data/0", {"cost": 700}, if_match=hdr2["X-Ledger-Stamp"])
    assert status == 200, resp
    assert load()[0]["cost"] == 700.0


def test_write_without_if_match_still_works(api):
    """导入页与 agent 不带版本，不能被这个机制挡住。"""
    call, load, _ = api
    status, resp = call("POST", "/api/data", {"brand": "希捷", "model": "2T", "cost": 380, "sell": 0})
    assert status == 200, resp
