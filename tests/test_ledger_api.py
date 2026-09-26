"""后端写入路径的测试：Task 1 是订单上下文 round-trip、空 sell 的不对称、坏输入的显式拒绝；
Task 2 加上写盘冲突检测（两个进程整读整写 data.json 时，后写的必须被拒绝而不是抹掉前者）；
Task 2b 把冲突检测做到"页面是不是旧的"（客户端带着读到的版本写盘，版本不符就 409）。

fixture（api / SEED / 沙盒兜底）在 tests/conftest.py，后面 10 个测试文件共用。
"""
import json
import os
import re
from pathlib import Path

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


def test_save_data_conflict_preserves_backup_and_leaves_no_tmp(api):
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


def test_save_data_conflict_allows_later_unstamped_overwrite(api):
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


# ─── Task 3: 单条导入记录拆分 ───

SPLIT_PART = {"brand": "光威", "model": "神策 16G×2", "cost": 500}


def _prepare_split_files(api):
    """只在 api 沙盒预置备份和图片，之后逐字节核对所有文件。"""
    import app_standalone as m

    _, _, path = api
    data_file = Path(path)
    assert m.DATA_FILE == path
    images = Path(m.IMAGES_DIR)
    assert images.parent == data_file.parent
    images.mkdir(exist_ok=True)
    (images / "keep.jpg").write_bytes(b"\xff\xd8keep-image")
    (images / "unreferenced.png").write_bytes(b"unreferenced-image")
    Path(path + ".bak").write_bytes(b"previous backup generation\r\n")
    return data_file


def _split_files_snapshot(path):
    root = Path(path).parent
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def test_split_appends_raw_indices_preserves_fields_and_saves_once(api, monkeypatch):
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_split_files(api)
    manual, imported = load()
    imported.update(sell=950, sn="SN-1", sn2="SN-2", accessory="原盒",
                    accessory_price="25", extra_price=12, images=["keep.jpg"],
                    note="只属于原记录", custom={"keep": True})
    sibling = dict(imported, brand="同单兄弟", model="保持原样", cost=50)
    # 空记录会被前端隐藏，但 API 必须一直使用原数组下标。
    records = [{}, imported, sibling, manual]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    before = _split_files_snapshot(path)
    stamp = m.file_stamp()
    counts = {"load": 0, "save": 0}
    real_load, real_save = m.load_data, m.save_data

    def counted_load():
        counts["load"] += 1
        return real_load()

    def counted_save(data, saved_stamp=None):
        counts["save"] += 1
        assert saved_stamp == stamp
        return real_save(data, saved_stamp)

    monkeypatch.setattr(m, "load_data", counted_load)
    monkeypatch.setattr(m, "save_data", counted_save)
    ignored = {key: "不可覆盖" for key in ORDER_CONTEXT_KEYS}
    ignored.update(sell=999, sn="bad", sn2="bad", accessory="bad",
                   accessory_price=999, extra_price=999, images=["bad.jpg"],
                   note="bad", custom="bad")
    status, resp = call("POST", "/api/split_record", {"idx": 1, "parts": [
        dict(ignored, brand=" 光威 ", model=" 神策 16G×2\t", cost=500),
        dict(ignored, brand="十铨", model="Delta 16G", cost="300"),
        dict(ignored, brand="其他", model="配套件", cost=-5),
    ]})
    assert status == 200, resp
    assert resp == {"ok": True, "indices": [1, 4, 5], "order_paid": 800}
    assert counts == {"load": 1, "save": 1}
    expected = [dict(r) for r in records]
    expected[1].update(brand="光威", model="神策 16G×2", cost=500.0)
    context = {key: imported[key] for key in ORDER_CONTEXT_KEYS}
    for brand, model, cost in [("十铨", "Delta 16G", 300.0), ("其他", "配套件", -5.0)]:
        expected.append(dict(context, brand=brand, model=model, cost=cost, sell="",
                             sn="", accessory="", accessory_price="", extra_price="", images=[]))
    assert load() == expected
    assert all(isinstance(load()[idx]["cost"], float) for idx in resp["indices"])
    after = _split_files_snapshot(path)
    assert after.pop("data.json.bak") == before["data.json"], "备份必须是整个拆分前账本"
    after.pop("data.json")
    assert after == {k: v for k, v in before.items() if k not in ("data.json", "data.json.bak")}
    assert not Path(path + ".tmp").exists()


def test_split_derived_record_again_keeps_original_order_paid(api):
    call, load, path = api
    _prepare_split_files(api)
    status, first = call("POST", "/api/split_record", {"idx": 1, "parts": [
        SPLIT_PART, {"brand": "十铨", "model": "Delta", "cost": 300},
    ]})
    assert status == 200, first
    before = load()
    before_bytes = Path(path).read_bytes()
    status, second = call("POST", "/api/split_record", {"idx": first["indices"][1], "parts": [
        {"brand": "十铨", "model": "Delta A", "cost": 200},
        {"brand": "十铨", "model": "Delta B", "cost": 100},
    ]})
    assert status == 200, second
    assert second == {"ok": True, "indices": [2, 3], "order_paid": 800}
    assert load()[:2] == before[:2]
    assert [r["order_paid"] for r in load()[1:]] == [800, 800, 800]
    assert Path(path + ".bak").read_bytes() == before_bytes


def test_split_legacy_source_only_does_not_fabricate_order_metadata(api):
    call, load, path = api
    data_file = _prepare_split_files(api)
    records = load()
    original = {"source_order_id": "legacy", "brand": "旧牌", "model": "整包",
                "cost": 987, "sn2": "private", "note": "private"}
    records[1] = original
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    status, resp = call("POST", "/api/split_record", {"idx": 1, "parts": [SPLIT_PART, SPLIT_PART]})
    assert status == 200, resp
    assert resp == {"ok": True, "indices": [1, 2], "order_paid": ""}
    assert load()[1] == dict(original, **SPLIT_PART)
    assert load()[2] == dict(SPLIT_PART, source_order_id="legacy", sell="", sn="",
                             accessory="", accessory_price="", extra_price="", images=[])
    assert not any(key in r for r in load()[1:] for key in ORDER_CONTEXT_KEYS if key != "source_order_id")


@pytest.mark.parametrize("cost", [None, "", 0, -5, 12.5, " 12.50 ", "-3", "1e2"])
def test_split_accepts_empty_finite_and_numeric_string_costs(api, cost):
    call, load, path = api
    _prepare_split_files(api)
    status, resp = call("POST", "/api/split_record", {"idx": 1, "parts": [
        dict(SPLIT_PART, cost=cost), dict(SPLIT_PART, cost=cost),
    ]})
    assert status == 200, resp
    expected = 0.0 if cost in (None, "") else float(cost)
    assert [r["cost"] for r in load()[1:]] == [expected, expected]
    assert all(isinstance(r["cost"], float) for r in load()[1:])


@pytest.mark.parametrize("part_count", [1, 3])
def test_split_omitted_cost_preserves_first_and_defaults_appended_to_zero(api, part_count):
    call, load, path = api
    data_file = _prepare_split_files(api)
    records = load()
    records[1]["cost"] = "205.50"
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    before = data_file.read_bytes()
    status, resp = call("POST", "/api/split_record", {"idx": 1, "parts": [
        {"brand": " 光威 ", "model": " 神策 "} for _ in range(part_count)
    ]})
    assert status == 200, resp
    assert resp == {"ok": True, "indices": list(range(1, part_count + 1)), "order_paid": 800}
    assert load()[0] == records[0]
    assert load()[1] == dict(records[1], brand="光威", model="神策")
    assert [r["cost"] for r in load()[2:]] == [0] * (part_count - 1)
    assert Path(path + ".bak").read_bytes() == before


BAD_SPLIT_REQUESTS = [
    pytest.param({}, id="empty-object"),
    pytest.param({"parts": [SPLIT_PART]}, id="missing-index"),
    pytest.param({"idx": 1}, id="missing-parts"),
] + [
    pytest.param({"idx": idx, "parts": [SPLIT_PART]}, id="index-" + name)
    for name, idx in [("true", True), ("false", False), ("string", "1"), ("float", 1.0),
                      ("null", None), ("list", []), ("object", {})]
] + [
    pytest.param({"idx": 1, "parts": parts}, id="parts-" + name)
    for name, parts in [("empty", []), ("null", None), ("object", {}),
                        ("string", "part"), ("number", 1), ("bool", True)]
] + [
    pytest.param({"idx": 1, "parts": [SPLIT_PART, part]}, id="later-part-" + name)
    for name, part in [("null", None), ("number", 1), ("string", "part"),
                       ("list", []), ("bool", True), ("empty-object", {}),
                       ("missing-brand", {"model": "x"}), ("missing-model", {"brand": "x"})]
] + [
    pytest.param({"idx": 1, "parts": [SPLIT_PART, dict(SPLIT_PART, **{key: value})]},
                 id="later-" + key + "-" + name)
    for key in ("brand", "model")
    for name, value in [("empty", ""), ("whitespace", " \t\n"), ("null", None),
                        ("number", 123), ("list", []), ("object", {}), ("bool", True)]
] + [
    pytest.param({"idx": 1, "parts": [SPLIT_PART, dict(SPLIT_PART, cost=cost)]},
                 id="later-cost-" + name)
    for name, cost in [("true", True), ("false", False), ("text", "abc"), ("unit", "12元"),
                       ("object", {}), ("list", []), ("nan", float("nan")),
                       ("inf", float("inf")), ("negative-inf", float("-inf")),
                       ("nan-string", "NaN"), ("inf-string", "Infinity"),
                       ("negative-inf-string", "-Infinity"), ("large-exponent", "1e309"),
                       ("integer-overflow", 10**400)]
]


@pytest.mark.parametrize("payload", BAD_SPLIT_REQUESTS)
def test_split_bad_input_is_400_without_partial_write(api, payload):
    call, load, path = api
    _prepare_split_files(api)
    before = _split_files_snapshot(path)
    status, resp = call("POST", "/api/split_record", payload)
    assert status == 400, resp
    assert resp["ok"] is False and resp["error"]
    assert _split_files_snapshot(path) == before, "坏的后一项也不能留下前几项、备份或图片副作用"


@pytest.mark.parametrize("part_index", [0, 1], ids=["first", "later"])
@pytest.mark.parametrize("key", ["brand", "model"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"], ids=["high", "low"])
def test_split_lone_surrogate_is_400_without_writing(api, part_index, key, surrogate):
    call, _, path = api
    _prepare_split_files(api)
    before = _split_files_snapshot(path)
    payload = {"idx": 1, "parts": [dict(SPLIT_PART), dict(SPLIT_PART)]}
    payload["parts"][part_index][key] = surrogate
    # 转义后发送，避免客户端编码失败掩盖服务端的校验漏洞。
    raw = json.dumps(payload, ensure_ascii=True).encode("ascii")
    status, resp = call("POST", "/api/split_record", raw_body=raw)
    assert status == 400, resp
    assert resp["ok"] is False and key in resp["error"]
    assert surrogate not in resp["error"]
    assert _split_files_snapshot(path) == before


def test_split_accepts_json_surrogate_pair_and_saves_supplementary_character(api):
    call, load, path = api
    data_file = _prepare_split_files(api)
    before = _split_files_snapshot(path)
    character = "\U00020000"
    parts = [dict(SPLIT_PART, brand="品牌" + character, model="型号" + character)
             for _ in range(2)]
    raw = json.dumps({"idx": 1, "parts": parts}, ensure_ascii=True).encode("ascii")
    assert b"\\ud840\\udc00" in raw
    status, resp = call("POST", "/api/split_record", raw_body=raw)
    assert status == 200, resp
    assert resp == {"ok": True, "indices": [1, 2], "order_paid": 800}
    assert [{key: r[key] for key in SPLIT_PART} for r in load()[1:]] == parts
    assert character.encode("utf-8") in data_file.read_bytes()
    after = _split_files_snapshot(path)
    assert after.pop("data.json.bak") == before["data.json"]
    after.pop("data.json")
    assert after == {k: v for k, v in before.items() if k not in ("data.json", "data.json.bak")}
    assert not Path(path + ".tmp").exists()


@pytest.mark.parametrize("raw", [b'{"idx":1,', b'not json', b'[]', b'123', b'null', b'true', b'"x"', b''])
def test_split_bad_json_or_non_object_is_400_without_writing(api, raw):
    call, load, path = api
    _prepare_split_files(api)
    before = _split_files_snapshot(path)
    status, resp = call("POST", "/api/split_record", raw_body=raw)
    assert status == 400, resp
    assert resp["ok"] is False
    assert _split_files_snapshot(path) == before


@pytest.mark.parametrize("source", ["missing", None, "", " \t"])
def test_split_rejects_records_without_nonempty_source_order_id(api, source):
    call, load, path = api
    data_file = _prepare_split_files(api)
    records = load()
    if source != "missing":
        records[0]["source_order_id"] = source
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    before = _split_files_snapshot(path)
    status, resp = call("POST", "/api/split_record", {"idx": 0, "parts": [SPLIT_PART]})
    assert status == 400, resp
    assert resp["ok"] is False
    assert _split_files_snapshot(path) == before


@pytest.mark.parametrize("idx", [-1, 2, 10**40])
def test_split_integer_index_out_of_range_is_404_without_writing(api, idx):
    call, load, path = api
    _prepare_split_files(api)
    before = _split_files_snapshot(path)
    status, resp = call("POST", "/api/split_record", {"idx": idx, "parts": [SPLIT_PART]})
    assert status == 404, resp
    assert resp == {"ok": False, "error": "index out of range"}
    assert _split_files_snapshot(path) == before


def test_split_stale_client_rejected_then_refreshed_token_succeeds(api, monkeypatch):
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_split_files(api)
    status, _, headers = call("GET", "/api/data", with_headers=True)
    assert status == 200
    token = headers["X-Ledger-Stamp"]
    outside = load() + [_outside_record()]
    data_file.write_text(json.dumps(outside, ensure_ascii=False), encoding="utf-8")
    before = _split_files_snapshot(path)
    loads = []
    real_load = m.load_data

    def counted_load():
        loads.append(True)
        return real_load()

    monkeypatch.setattr(m, "load_data", counted_load)
    payload = {"idx": 1, "parts": [SPLIT_PART, SPLIT_PART]}
    status, resp = call("POST", "/api/split_record", payload, if_match=token)
    assert status == 409, resp
    assert resp == {"ok": False, "error": CONFLICT_ERROR}
    assert loads == [], "旧客户端必须在 load 之前被拒绝"
    assert _split_files_snapshot(path) == before
    status, _, headers = call("GET", "/api/data", with_headers=True)
    assert status == 200 and headers["X-Ledger-Stamp"] != token
    status, resp = call("POST", "/api/split_record", payload, if_match=headers["X-Ledger-Stamp"])
    assert status == 200, resp
    assert resp == {"ok": True, "indices": [1, 3], "order_paid": 800}
    assert load()[2] == outside[2]
    assert Path(path + ".bak").read_bytes() == before["data.json"]


def test_split_external_write_before_save_returns_real_conflict(api, monkeypatch):
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_split_files(api)
    seen = {}
    real_save = m.save_data
    original_stamp = m.file_stamp()

    def racing_save(data, stamp=None):
        assert stamp == original_stamp
        outside = load() + [_outside_record()]
        data_file.write_text(json.dumps(outside, ensure_ascii=False), encoding="utf-8")
        seen["files"] = _split_files_snapshot(path)
        return real_save(data, stamp)

    monkeypatch.setattr(m, "save_data", racing_save)
    status, resp = call("POST", "/api/split_record", {"idx": 1, "parts": [SPLIT_PART, SPLIT_PART]})
    assert status == 409, resp
    assert resp == {"ok": False, "error": CONFLICT_ERROR}
    assert _split_files_snapshot(path) == seen["files"], "冲突必须保留外部数据、旧备份和全部图片"
    assert not Path(path + ".tmp").exists()


@pytest.mark.parametrize("with_token", [True, False])
@pytest.mark.parametrize("remaining_kind", ["imported", "missing", "manual"])
def test_split_reuses_first_stamp_when_external_delete_shifts_index(
        api, monkeypatch, with_token, remaining_kind):
    """删除后的新版本不能成为拆分基准，也不能把冲突降为下标或来源错误。"""
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_split_files(api)
    manual, imported = load()
    imported["images"] = ["keep.jpg"]
    records = [dict(imported, brand="A", source_order_id="order-A")]
    if remaining_kind == "imported":
        records.append(dict(imported, brand="B", source_order_id="order-B"))
    elif remaining_kind == "manual":
        records.append(dict(manual, brand="B"))
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    status, _, headers = call("GET", "/api/data", with_headers=True)
    assert status == 200
    token = headers["X-Ledger-Stamp"]
    real_stamp, real_load = m.file_stamp, m.load_data
    samples, loaded_versions, saved_stamps, seen = [], [], [], {}
    real_save = m.save_data

    def delete_after_first_sample():
        stamp = real_stamp()
        samples.append(stamp)
        if len(samples) == 1:
            # 不伪造 token，也不依赖时钟：在第一次真实取版本后直接删除临时账本首条。
            data_file.write_text(json.dumps(records[1:], ensure_ascii=False), encoding="utf-8")
            seen["files"] = _split_files_snapshot(path)
        return stamp

    def observed_load():
        data = real_load()
        loaded_versions.append(json.loads(json.dumps(data)))
        return data

    def observed_save(data, stamp=None):
        saved_stamps.append(stamp)
        return real_save(data, stamp)

    monkeypatch.setattr(m, "file_stamp", delete_after_first_sample)
    monkeypatch.setattr(m, "load_data", observed_load)
    monkeypatch.setattr(m, "save_data", observed_save)
    status, resp = call("POST", "/api/split_record", {"idx": 0, "parts": [SPLIT_PART, SPLIT_PART]},
                        if_match=token if with_token else None)
    assert status == 409, resp
    assert resp == {"ok": False, "error": CONFLICT_ERROR}
    assert loaded_versions == [records[1:]], "确定性覆盖校验到 load 之间的下标移位窗口"
    assert samples[0] == token and real_stamp() != token
    assert all(stamp == token for stamp in saved_stamps), "允许提前拒绝，但不能重定保存基准"
    assert _split_files_snapshot(path) == seen["files"]
    assert load() == records[1:]
    assert not Path(path + ".tmp").exists()


# ─── Task 4: 旧 CRUD 也复用同一份版本基准 ───

RACE_CASES = [
    ("POST", "/api/data", {"brand": "七彩虹", "model": "B760M", "cost": 1, "sell": 1}),
    ("PUT", "/api/data/0", {"cost": 601}),
    ("DELETE", "/api/data/0", None),
    ("DELETE", "/api/data/0/images/keep.jpg", None),
]


def _prepare_race_files(api):
    """给旧 CRUD 竞态测试预置一张被账本引用的图与一份内容可辨认的 .bak，返回 data.json 的 Path。

    .bak 要先立起来、且字节和 data.json 不同，"冲突没把唯一那代备份轮掉"才是看得见的；
    逐文件字节比对直接复用 Task 3 的 _split_files_snapshot（它本身就与拆分无关）。
    """
    import app_standalone as m

    _, _, path = api
    data_file = Path(path)
    assert m.DATA_FILE == path
    images = Path(m.IMAGES_DIR)
    assert images.parent == data_file.parent
    images.mkdir(exist_ok=True)
    (images / "keep.jpg").write_bytes(b"\xff\xd8keep-image")
    Path(path + ".bak").write_bytes(b"previous backup generation\r\n")
    return data_file


def _watch_stamps(m, monkeypatch, data_file, records, seen, path):
    """在第一次真实取版本之后删掉临时账本首条：外部写插在客户端校验与 load 之间。

    不 sleep、不伪造 token —— 三个采样点谁先谁后由被包装的真实调用决定，测的是顺序而不是时长。
    """
    real_stamp, real_load, real_save = m.file_stamp, m.load_data, m.save_data
    samples, loaded_versions, saved_stamps = [], [], []

    def delete_after_first_sample():
        stamp = real_stamp()
        samples.append(stamp)
        if len(samples) == 1:
            data_file.write_text(json.dumps(records[1:], ensure_ascii=False), encoding="utf-8")
            seen["files"] = _split_files_snapshot(path)
        return stamp

    def observed_load():
        data = real_load()
        loaded_versions.append(json.loads(json.dumps(data)))
        return data

    def observed_save(data, stamp=None):
        saved_stamps.append(stamp)
        return real_save(data, stamp)

    monkeypatch.setattr(m, "file_stamp", delete_after_first_sample)
    monkeypatch.setattr(m, "load_data", observed_load)
    monkeypatch.setattr(m, "save_data", observed_save)
    return real_stamp, samples, loaded_versions, saved_stamps


@pytest.mark.parametrize("with_token", [True, False])
@pytest.mark.parametrize("method,route,payload", RACE_CASES)
def test_legacy_crud_reuses_first_stamp_when_external_delete_shifts_index(
        api, monkeypatch, method, route, payload, with_token):
    """客户端的 If-Match 必须校验在"我读到的那一份"上，而不是校验在移位前的版本、写回移位后的账本。

    with_token=False 同样要 409：load 后的复查防的是"读到的和要写回的不是同一份"，
    跟客户端有没有带版本无关，所以它对不带 If-Match 的导入页/agent 一样生效。
    """
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_race_files(api)
    first, second = load()
    # 首条带图：DELETE /api/data/0/images/... 那一路才真会走到写盘和删文件。
    records = [dict(first, images=["keep.jpg"]), second]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    status, _, headers = call("GET", "/api/data", with_headers=True)
    assert status == 200
    token = headers["X-Ledger-Stamp"]

    seen = {}
    real_stamp, samples, loaded_versions, saved_stamps = _watch_stamps(
        m, monkeypatch, data_file, records, seen, path)
    status, resp = call(method, route, payload, if_match=token if with_token else None)
    assert status == 409, (method, route, resp)
    assert resp == {"ok": False, "error": CONFLICT_ERROR}
    assert loaded_versions == [records[1:]], "外部删除确实落在客户端校验与 load 之间，旧代码从这里读出的是移位后的账本"
    assert samples[0] == token and real_stamp() != token
    assert all(stamp == token for stamp in saved_stamps), "允许提前拒绝，但不能重定保存基准"
    assert _split_files_snapshot(path) == seen["files"], "冲突之后数据、备份或图片被改过"
    assert load() == records[1:]
    assert not Path(path + ".tmp").exists()


def test_upload_reuses_first_stamp_when_external_delete_shifts_index(api, monkeypatch):
    """/api/upload 是第五个写盘点：同一份基准要贯穿 If-Match 校验、load 与 save_data。"""
    import io

    import app_standalone as m

    call, load, path = api
    data_file = _prepare_race_files(api)
    first, second = load()
    records = [dict(first, images=["keep.jpg"]), second]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    token = call("GET", "/api/data", with_headers=True)[2]["X-Ledger-Stamp"]

    boundary = "ledgeraceboundary"
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
        "If-Match": token,
    }
    handler.rfile = io.BytesIO(raw)
    replies = []
    handler.send_json = lambda payload, status=200: replies.append((status, payload))

    seen = {}
    real_stamp, samples, loaded_versions, saved_stamps = _watch_stamps(
        m, monkeypatch, data_file, records, seen, path)
    handler.handle_upload()

    assert replies == [(409, {"ok": False, "error": CONFLICT_ERROR})], replies
    assert loaded_versions == [records[1:]]
    assert samples[0] == token and real_stamp() != token
    assert all(stamp == token for stamp in saved_stamps), "允许提前拒绝，但不能重定保存基准"
    assert load() == records[1:], "上传写盘被拒后仍然改了账本"
    after = _split_files_snapshot(path)
    assert after["data.json"] == seen["files"]["data.json"]
    assert after["data.json.bak"] == seen["files"]["data.json.bak"], "冲突写盘把唯一的备份世代轮掉了"
    assert not os.path.exists(path + ".tmp"), "被拒的写盘不该留下半成品"
    # 图已经落盘、账本没引用它：留下的是孤儿文件，这是 handle_upload 里写明接受的取舍；
    # 但原本被账本引用的那张必须一个字都没动。
    extra = set(after) - set(seen["files"])
    assert not set(seen["files"]) - set(after), "被拒的上传把账本还引用着的图删了"
    assert len(extra) == 1 and next(iter(extra)).startswith("images"), extra


def test_no_write_endpoint_re_samples_the_version_after_its_client_check():
    """不带参数的 reject_if_client_stale() 就是"即时采样"：它放过的那个版本，和 handler
    随后自己 file_stamp() 采到的那份之间可以插进一次外部写，客户端的 If-Match 于是白校验一次。

    和新写盘端点忘传 stamp 的那道静态守卫互补：这里钉的是"校验与保存同一份基准"。
    """
    import inspect
    import re

    import app_standalone as m

    src = inspect.getsource(m.APIHandler)
    assert "reject_if_client_stale()" not in src, "有写盘端点又用即时采样校验客户端版本了"
    assert re.search(r"def reject_if_client_stale\(self, stamp=None\)", src), "签名变了，调用点的约定要一起复核"


# ─── 审查返工 Task 5: 删图的物理删除必须限定在"这张图确实属于这条记录" ───

def test_delete_image_of_a_record_that_never_referenced_it_leaves_the_file_alone(api):
    """要删的文件不在该记录的 images 里时，磁盘上一个字节都不许动。

    这条路径真实存在：客户端的旧下标在新账本里指向另一条记录，而那张图正被别处引用着。
    无条件 os.remove 等于用一次"删 A 的图"抹掉 B 的图 —— 账本没改、文件先没了，
    是比 409 更糟的静默数据丢失。
    """
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_race_files(api)
    first, second = load()
    # keep.jpg 属于第 1 条（images=["keep.jpg"]），第 0 条从来没引用过它。
    records = [dict(first, images=[]), dict(second, images=["keep.jpg"])]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    before = _split_files_snapshot(path)

    status, resp = call("DELETE", "/api/data/0/images/keep.jpg")
    assert status == 200, resp
    assert os.path.exists(os.path.join(m.IMAGES_DIR, "keep.jpg")), \
        "该记录根本没引用 keep.jpg，删除请求却把这张正被别处引用的图抹掉了"
    assert _split_files_snapshot(path) == before, "被拒绝的删图动了账本或图片"
    assert load()[1]["images"] == ["keep.jpg"]


# ─── 审查返工 Task 6: "复查版本"这道闸不许把正常 404 吞成 409 ───

LEGACY_404_ROUTES = [
    ("PUT", "/api/data/%d", {"cost": 601}),
    ("DELETE", "/api/data/%d", None),
    ("DELETE", "/api/data/%d/images/keep.jpg", None),
]


@pytest.mark.parametrize("idx", [-1, 2, 10**40])
@pytest.mark.parametrize("method,route_tpl,payload", LEGACY_404_ROUTES)
def test_legacy_crud_out_of_range_index_is_still_404_without_any_external_write(
        api, method, route_tpl, payload, idx):
    """没有任何外部改动时，越界下标仍然是 404。

    钉的是复查逻辑的边界：file_stamp() != stamp 那道闸只在真有外部写时才该响，
    一旦它跑在下标校验之前又写得像在拒绝，用户看到的就从"这条不存在"变成"账本被别人改过"，
    前端还会顺手丢掉表单。这里连 .bak 与图片都逐字节比对，拒绝的请求不许碰盘。
    """
    call, load, path = api
    data_file = _prepare_race_files(api)
    first, second = load()
    records = [dict(first, images=["keep.jpg"]), second]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    token = call("GET", "/api/data", with_headers=True)[2]["X-Ledger-Stamp"]
    before = _split_files_snapshot(path)

    status, resp = call(method, route_tpl % idx, payload, if_match=token)
    assert status == 404, (method, route_tpl, idx, resp)
    assert resp == {"ok": False, "error": "index out of range"}
    assert _split_files_snapshot(path) == before, "越界请求把数据、备份或图片改动了"
    assert not Path(path + ".tmp").exists(), "被拒绝的写盘不该留下半成品"
    assert load() == records


# ─── 第三轮返工 条目3: images 的元素类型必须校验（它已经进了渲染路径）───

BAD_IMAGE_VALUES = [
    ["keep.jpg", 42],
    [None],
    [{"url": "keep.jpg"}],
    [True],
    "keep.jpg",
    {"0": "keep.jpg"},
    7,
]


@pytest.mark.parametrize("images", BAD_IMAGE_VALUES)
def test_add_record_rejects_bad_images_without_touching_the_disk(api, images):
    """images 里任何非字符串元素、或整个值不是数组：400 并且沙盒逐字节不变。

    52579b6 之后 images 会透传给三处 UI（行内缩略图 / 图片查看器 / 弹窗预览），
    非字符串元素让 imgs[0].startsWith 抛错、整表渲染崩；整值是字符串则让
    handle_delete_image 的 list.remove 变成 str.remove 直接 500。崩的是渲染，
    脏数据是落盘带来的，所以只能拦在写入侧。
    """
    call, load, path = api
    data_file = _prepare_race_files(api)
    before = _split_files_snapshot(path)

    status, resp = call("POST", "/api/data",
                        {"brand": "微星", "model": "B650M", "cost": 1, "sell": 2, "images": images})
    assert status == 400, (images, resp)
    assert resp["ok"] is False and "images" in resp["error"], resp
    assert _split_files_snapshot(path) == before, "被拒绝的 images 改动了账本、备份或图片"
    assert not Path(path + ".tmp").exists(), "被拒绝的请求不该留下写盘半成品"


@pytest.mark.parametrize("images", BAD_IMAGE_VALUES)
def test_update_record_rejects_bad_images_without_touching_the_disk(api, images):
    call, load, path = api
    data_file = _prepare_race_files(api)
    before = _split_files_snapshot(path)

    status, resp = call("PUT", "/api/data/0", {"cost": 601, "images": images})
    assert status == 400, (images, resp)
    assert resp["ok"] is False and "images" in resp["error"], resp
    assert _split_files_snapshot(path) == before, "被拒绝的 images 改动了账本、备份或图片"
    assert not Path(path + ".tmp").exists(), "被拒绝的请求不该留下写盘半成品"


@pytest.mark.parametrize("images", [[], ["keep.jpg"], ["a.png", "keep.jpg"]])
def test_string_only_images_still_accepted(api, images):
    """守卫只拒非法元素；真实账本里 images 缺失/空数组/纯字符串数组这三类都必须照旧能写。"""
    call, load, path = api
    _prepare_race_files(api)

    status, resp = call("POST", "/api/data",
                        {"brand": "微星", "model": "B650M", "cost": 1, "sell": 2, "images": images})
    assert status == 200, (images, resp)
    assert load()[-1]["images"] == images

    status, resp = call("PUT", "/api/data/0", {"images": ["keep.jpg"]})
    assert status == 200, resp
    assert load()[0]["images"] == ["keep.jpg"]

    # 省略 images 键仍然是"不动这一栏"，别把守卫写成必填。
    status, resp = call("PUT", "/api/data/0", {"cost": 700})
    assert status == 200, resp
    assert load()[0]["images"] == ["keep.jpg"]


# ─── 第三轮返工 条目6: 版本复查必须排在下标/来源/金额校验之前 ───

# 外部删除把首条抹掉后，客户端那份版本里的 1 号下标在新账本中已经越界。
STALE_INDEX_CASES = [
    ("PUT", "/api/data/1", {"cost": 601}),
    ("DELETE", "/api/data/1", None),
    ("DELETE", "/api/data/1/images/keep.jpg", None),
]

# 请求体本身违法（会被降级成 400）时同样不许盖过 409。
STALE_BODY_CASES = [
    ("POST", "/api/data", {"brand": "微星", "model": "B650M", "cost": "abc"}),
    ("PUT", "/api/data/0", {"cost": "12元"}),
]


@pytest.mark.parametrize("with_token", [True, False])
@pytest.mark.parametrize("method,route,payload", STALE_INDEX_CASES)
def test_external_delete_that_moves_index_out_of_range_still_answers_409(
        api, monkeypatch, method, route, payload, with_token):
    """外部改动让下标越界时，客户端要拿到 409，而不是被降级成 404。

    前端按 status 分支：409 才会"丢掉表单+重拉列表"，404 会被读成"这条记录不存在/不能拆"，
    于是页面继续挂着一份旧视图并保留旧下标，下一次写就作用在另一条记录上。
    所以这道复查必须排在下标校验之前。
    """
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_race_files(api)
    first, second = load()
    records = [dict(first, images=["keep.jpg"]), second]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    token = call("GET", "/api/data", with_headers=True)[2]["X-Ledger-Stamp"]

    seen = {}
    real_stamp, samples, loaded_versions, saved_stamps = _watch_stamps(
        m, monkeypatch, data_file, records, seen, path)
    status, resp = call(method, route, payload, if_match=token if with_token else None)

    assert loaded_versions == [records[1:]], "外部删除没落在 load 之前，这个变异样本无效"
    assert len(load()) == 1, "新账本里 1 号下标确实已经越界"
    assert status == 409, (method, route, with_token, resp)
    assert resp == {"ok": False, "error": CONFLICT_ERROR}
    assert saved_stamps == [], "被拒的写盘仍然动了保存基准"
    assert _split_files_snapshot(path) == seen["files"], "冲突之后数据、备份或图片被改过"
    assert not Path(path + ".tmp").exists()
    assert os.path.exists(os.path.join(m.IMAGES_DIR, "keep.jpg"))


@pytest.mark.parametrize("method,route,payload", STALE_BODY_CASES)
def test_stale_client_wins_over_its_own_invalid_body(api, monkeypatch, method, route, payload):
    """版本已经过期时，请求体校验的 400 不许抢在 409 前面。

    400 的语义是"你这个请求写坏了，改好再发"，前端会保留表单等用户改；
    409 的语义是"你这份视图是旧的"。两者同时成立时只有后者能纠正客户端，
    所以复查必须排在金额/images 这些请求体校验之前。
    """
    import app_standalone as m

    call, load, path = api
    data_file = _prepare_race_files(api)
    first, second = load()
    records = [dict(first, images=["keep.jpg"]), second]
    data_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    token = call("GET", "/api/data", with_headers=True)[2]["X-Ledger-Stamp"]

    seen = {}
    _watch_stamps(m, monkeypatch, data_file, records, seen, path)
    status, resp = call(method, route, payload, if_match=token)

    assert status == 409, (method, route, resp)
    assert resp == {"ok": False, "error": CONFLICT_ERROR}
    assert _split_files_snapshot(path) == seen["files"]
    assert load() == records[1:]


# ─── 第三轮返工：前端这几条契约也要有牙（沿用本文件既有的源码静态扫描风格）───

INDEX_HTML = Path(__file__).resolve().parent.parent / "index.html"


def _index_js():
    return INDEX_HTML.read_text(encoding="utf-8")


def _top_level_fn(src, header):
    """取一个顶层函数体：从签名那行到第一个顶格的 }。这几个函数都是这个形状。"""
    start = src.index(header)
    end = src.index("\n}\n", start)
    return src[start:end + 3]


def _submit_wrap_up(src):
    """modalSubmit 监听器到下一个监听器为止 —— 图片收尾窗口就在这段里。"""
    start = src.index("document.getElementById('modalSubmit').addEventListener")
    end = src.index("document.getElementById('modal').addEventListener", start)
    return src[start:end]


def test_parse_items_only_keeps_string_images():
    """parseItems 只把字符串元素带进渲染路径。

    写入侧的 400 拦不到账本里已有的历史脏数据，而这里的输出直接喂给
    imgs[0].startsWith / img.startsWith，一个非字符串元素就能让整表崩。
    """
    src = _index_js()
    body = _top_level_fn(src, "function parseItems(raw)")
    assert re.search(r"images:[\s\S]{0,200}?\.filter\(s => typeof s === 'string'\)", body), \
        "parseItems 的 images 不再过滤非字符串元素，脏数据会直接把 renderModels 打崩"


def test_conflict_strategy_has_exactly_one_implementation():
    """409 的处理策略只许有一份实现，两处只共享文案不同。

    两份副本会分叉：改了一处的策略（比如"要不要自动重试"）另一处悄悄没跟上，
    而这条是产品定死的行为，分叉的代价比文案分叉大得多。
    """
    src = _index_js()
    assert "handleConflictAfterFormClosed" not in src, "又出现了第二份 409 策略实现"
    assert src.count("async function handleConflict(") == 1, "409 策略被复制成了多份"
    for const in ("CONFLICT_FORM_MSG", "CONFLICT_IMAGE_MSG"):
        assert src.count("const %s =" % const) == 1, const + " 的文案被复制成了多份"
        assert src.count(const) >= 2, const + " 只剩声明，没人用了"
    # 表单那一路挂在默认参数上：调用点不带参数，合并没有改掉 saveItem/deleteItem 的语义
    assert re.search(r"async function handleConflict\(msg = CONFLICT_FORM_MSG\)", src), \
        "默认文案不再是表单那一路，saveItem/deleteItem 的 409 提示会被顺手改掉"
    assert "handleConflict(CONFLICT_IMAGE_MSG)" in _submit_wrap_up(src), \
        "收尾窗口没吃到图片那一路的文案"
    assert src.count("await handleConflict()") >= 2, "saveItem/deleteItem 的 409 分支被改了调用形状"


def test_stale_write_early_return_clears_both_image_queues():
    """提交失败（409）时两条待处理队列都要显式清掉，不靠 closeModal() 的副作用。

    只清 _pendingImages 的话，陈着的待删任务会挂在 window 上；
    依赖副作用意味着谁把 closeModal 里那句清理挪走，这里就静默漏一条。
    """
    src = _index_js()
    wrap = _submit_wrap_up(src)
    m = re.search(r"if \(!written\) \{(.*?)\n  \}", wrap, re.S)
    assert m, "找不到提交失败后的早退分支，返回形状变了要一起复核"
    early = m.group(1)
    assert "window._pendingImages = []" in early, "早退分支漏清待传图片"
    assert "window._pendingImageRemovals = []" in early, "早退分支只清了待传图片，待删队列还靠着 closeModal 的副作用"


def test_wrap_up_window_blocks_reopening_and_clears_in_finally():
    """写盘收尾窗口期间不许重开表单/发行内删除，且标志必须在 finally 里清。

    这段窗口里每写一次盘都会推进全局版本：期间锁到的 _formStamp 必然会被后面的写
    推过期，用户白吃一次虚警 409、刚填的输入被丢掉。真实图片上传耗时几百毫秒，踩得到。
    """
    src = _index_js()
    wrap = _submit_wrap_up(src)
    assert "_ledgerBusy = true" in wrap, "收尾窗口不再置位，重开表单又会必现虚警 409"
    assert wrap.index("_ledgerBusy = true") < wrap.index("closeModal();"), \
        "置位必须排在收窗之前：晚一步用户就能在中间点开一份会吃到假 409 的表单"
    assert re.search(r"finally\s*\{[\s\S]*?_ledgerBusy = false", wrap), \
        "_ledgerBusy 没在 finally 里清，收尾中途抛异常会把页面永久锁死"
    for header in ("async function openAddModal()", "function openEditModal(",
                   "async function confirmDelete(", "function openSplitModal("):
        assert "_ledgerBusy" in _top_level_fn(src, header), "%s 少了收尾窗口的守卫" % header


def test_pending_image_removals_carry_and_recheck_identity():
    """待删图片入队时记下身份快照，发 DELETE 前复核下标归属。

    idx 是打开弹窗那一刻的原始下标；收尾窗口每写一次都要重读列表，外部增删之后
    同一个 idx 已经指向另一条记录。后端只会静默 no-op，客户端必须自己发现这一刀没砍中。
    """
    src = _index_js()
    enqueue = _top_level_fn(src, "function removeExistingImage(")
    assert re.search(r"push\(\{[^}]*brand:\s*item\.brand[^}]*model:\s*item\.model", enqueue, re.S), \
        "待删队列不再带身份快照，收尾时下标归属无从复核"
    wrap = _submit_wrap_up(src)
    assert "items.find(x => x.id === job.idx)" in wrap, "收尾循环不再按下标找回那条记录"
    assert "target.brand !== job.brand" in wrap and "target.model !== job.model" in wrap, \
        "身份核对被删掉了：外部增删之后这一刀会静默作用到别的记录上"
    assert wrap.index("items.find(x => x.id === job.idx)") < wrap.index("method: 'DELETE'"), \
        "复核必须排在发 DELETE 之前"


# ─── 拆单弹窗：前端契约（同样沿用本文件的源码静态扫描风格）───

SPLIT_OPEN = "function openSplitModal(idx)"
SPLIT_SUBMIT = "async function submitSplit()"
SPLIT_FETCH = "fetch('/api/split_record'"


def test_split_submit_locks_the_form_version_and_the_original_index():
    """拆单提交带的是弹窗自己那份版本，idx 带的是原始下标。

    回落到全局 _ledgerStamp 等于让"这份表单依据的版本"和"客户端最新读到的版本"混为一谈；
    items 的位置更不能当 API 下标用 —— parseItems 把 brand/model 全空的行过滤掉了。
    """
    src = _index_js()
    submit = _top_level_fn(src, SPLIT_SUBMIT)
    assert SPLIT_FETCH in submit, "submitSplit 不再打 /api/split_record"
    assert "writeHeaders(_formStamp)" in submit, "拆单提交没带表单自己锁住的那份版本"
    assert "_ledgerStamp" not in submit, "提交路径绕过 _formStamp 直接读全局版本"
    assert "idx: _splitIdx" in submit, "请求体里的 idx 不是弹窗打开时认准的那个原始下标"

    opener = _top_level_fn(src, SPLIT_OPEN)
    assert "items.find(x => x.id === idx)" in opener, "openSplitModal 不按 item.id 找记录，改用过滤后位置了"
    assert "_splitIdx = idx" in opener, "_splitIdx 没被赋成原始下标"
    assert "items.indexOf(" not in opener and "items.length - 1" not in opener, \
        "拆单又拿 items 的位置当 API 下标用"
    # 锁版本必须排在弹窗 active 之后：反过来的话，中间回来的在途 GET 会把版本推新而表单不知情。
    assert opener.index("classList.add('active')") < opener.index("_formStamp = _ledgerStamp"), \
        "先锁版本再开窗，在途 GET 能把这份锁作废"


def test_split_conflict_path_reuses_the_single_409_policy():
    """拆单的 409 走那份唯一策略：丢弃输入、重拉、提示，不回填也不重试。

    自己另写一份"把行内容留着再问一次"就是请用户确认一份错数据 —— 旧下标在新账本里
    可能已经指向另一条记录。分支只许按 res.status，error 文案不是契约。
    """
    src = _index_js()
    submit = _top_level_fn(src, SPLIT_SUBMIT)
    assert "if (res.status === 409) return await handleConflict();" in submit, \
        "409 没走统一的 handleConflict，或者调用形状被改了"
    assert "CONFLICT_" not in submit, "拆单自己复制了一份冲突文案"
    assert re.search(r"res\.status\s*!==\s*409", submit) or "!res.ok" in submit, \
        "非 200 分支不再按 status 判，改成拿 error 文案当分支了"
    assert "data.error" in submit or "body.error" in submit, "非 200 时没把后端文案透出来"
    # 成功那一路：先关窗清锁再重读，否则 loadPayload 的弹窗守卫会把这次重读吞掉。
    assert submit.index("closeModal()") < submit.index("await fetchItems()"), \
        "成功后先重读再关窗，这次重读会被弹窗守卫拒掉、版本停在旧的一代"
    assert "indices.length" in submit, "toast 的行数不再取自响应的 indices"

    closer = _top_level_fn(src, "function closeModal()")
    assert "splitRows.innerHTML = ''" in closer and "_splitIdx = -1" in closer, \
        "关窗不清空拆单输入：409 之后旧行内容会假装成新账本下重新编辑好的"


def test_split_entry_appears_only_on_imported_model_rows():
    """「拆单」只在带 source_order_id 的型号明细行出现，且全页只此一个入口。

    没有订单号就没有"一笔订单其实是几件硬件"这回事，后端也会直接 400；
    编辑弹窗里再加一个入口会静默丢掉用户正在编辑还没保存的内容。
    """
    src = _index_js()
    helper = _top_level_fn(src, "function splitActionBtn(item)")
    assert re.search(r"if \(!item\.source_order_id\) return '';", helper), \
        "拆单入口不再只对带 source_order_id 的记录出现"
    assert "openSplitModal(${item.id})" in helper, "入口传的不是 item.id（原始下标）"
    assert "splitActionBtn(i)" in _top_level_fn(src, "function renderModels()"), \
        "renderModels 的行操作区不再挂拆单入口"
    assert src.count("onclick=\"event.stopPropagation(); openSplitModal(") == 1, \
        "拆单入口不止一个了：编辑弹窗里再挂一个会吞掉未保存的编辑"


def test_form_modal_open_guard_is_one_predicate_covering_the_split_modal():
    """"有没有表单弹窗开着"只有一个判断口径，且它认得拆单弹窗。

    各写一遍 classList.contains('active') 的话，新弹窗必然从其中一处漏出去：
    拆单弹窗开着时在途 GET 照样换掉 items、刷新 _ledgerStamp，旧下标配新版本又回来了。
    """
    src = _index_js()
    assert src.count(".matches('.active')") == 1, "弹窗开合的判断被抄成了多份"
    predicate = _top_level_fn(src, "function openFormModal()")
    assert ".matches('.active')" in predicate, "唯一那份判断不在 openFormModal 里"
    registry = src[src.index("const FORM_MODALS"):src.index("];", src.index("const FORM_MODALS"))]
    assert "'modal'" in registry and "'splitModal'" in registry, \
        "弹窗登记表漏了一个，守卫又会只认得其中一个"
    assert not re.search(r"getElementById\('(modal|splitModal)'\)\.classList\.contains\('active'\)", src), \
        "又出现了绕过统一判断、自己问 #modal 要不要算开着的写法"
    for header in ("async function loadPayload()", "async function syncFromDisk()"):
        assert "openFormModal()" in _top_level_fn(src, header), \
            "%s 不再走统一判断，弹窗开着时它照样会换 items / 刷新版本" % header
    for header in ("async function openAddModal()", "function openEditModal(", SPLIT_OPEN):
        guard = _top_level_fn(src, header)
        assert "if (openFormModal()) return;" in guard, \
            "%s 没有互斥守卫：一把 _formStamp 同时锁两份表单，必有一份提交到错版本" % header

    closer = _top_level_fn(src, "function closeModal()")
    assert "FORM_MODALS.forEach" in closer, "关窗不再统一收掉所有表单弹窗，会留下一份开着却没了锁的表单"


def test_split_local_validation_blocks_one_row_and_blank_parts():
    """本地校验排在写请求之前：至少两行才叫拆分，每行品牌型号非空。

    只改一条该走行内编辑 —— 单行"拆分"会白占一次写盘还把 sell/配件清成新的追加项；
    空品牌型号后端必 400，先在页面上说清楚，别让请求跑一趟。
    """
    src = _index_js()
    submit = _top_level_fn(src, SPLIT_SUBMIT)
    assert SPLIT_FETCH in submit
    assert re.search(r"if \(rows\.length < 2\)", submit), "少了“至少两行”的本地校验"
    assert "行内编辑" in submit, "单行时没告诉用户改走行内编辑"
    assert submit.index("rows.length < 2") < submit.index(SPLIT_FETCH), "校验必须排在写请求之前"
    assert "!row.brand || !row.model" in submit and submit.index("!row.brand || !row.model") < submit.index(SPLIT_FETCH), \
        "空品牌/空型号没在本地拦住"
    # 只剩一行时删行按钮必须禁掉：删到 0 行再提交就是一趟注定 400 的请求。
    summary = _top_level_fn(src, "function updateSplitSummary()")
    assert re.search(r"children\.length <= 1", summary) and "disabled" in summary, \
        "删到只剩一行不再禁删按钮"
    for header in ("function addSplitRow()", "function removeSplitRow("):
        assert "updateSplitSummary()" in _top_level_fn(src, header), "%s 改完行没刷新合计" % header


def test_split_paid_delta_stays_hidden_without_order_paid():
    """拿不到 order_paid 时"订单实付 / 差额"整段不显示。

    真实账本 254 条里一条 order_paid 都没有（那是后续任务才补的），
    这段要是照抄公式就会长期挂着 ¥NaN / ¥undefined，或者谎报"差额 = -合计"。
    """
    src = _index_js()
    parse = _top_level_fn(src, "function parseItems(raw)")
    assert "order_paid: normPaid(r.order_paid)" in parse, \
        "parseItems 不再用 normPaid 归一 order_paid，缺失值会塌成 0"
    norm = _top_level_fn(src, "function normPaid(v)")
    assert "return null" in norm and "isFinite" in norm, \
        "normPaid 不再把空串/非数字归成 null，0 和“没记实付”就分不开了"
    summary = _top_level_fn(src, "function updateSplitSummary()")
    assert "_splitPaid === null" in summary and "display = 'none'" in summary, \
        "没有 order_paid 时差额段不再整段隐藏"
    assert summary.index("_splitPaid === null") < summary.index("订单实付"), \
        "隐藏判断排到了拼装之后，等于先算一遍 NaN 再藏起来"
    assert "¥${fmt(_splitPaid)}" in summary or "fmt(_splitPaid)" in summary, \
        "差额段干脆不显示实付/差额了"


# ─── Task 6：前端按知识库规范名归并（同一文件的静态契约段）───

def test_frontend_ships_a_catalog_resolver():
    """JS 侧必须有与 Python 同一个两趟规则，否则统计还是按脏值算。

    读取器复用本文件既有的 _index_js()（:1174），不再加第二份 index.html 读取实现。
    """
    src = _index_js()
    assert "function normKey(" in src
    assert "function resolvePart(" in src
    assert "needle.includes(k)" in src         # 第二趟：最长包含（执行期修订 1）
    assert "candidates" in src


def test_frontend_aggregates_statistics_by_canonical_names():
    src = _index_js()
    assert "map[i.canonicalBrand]" in src            # buildBrands 用规范品牌当键
    assert "i.canonicalModel" in src                 # 型号明细/型号总览用规范型号
    assert "i.brand + '|||' + i.model" not in src    # 老的脏键必须消失


def test_resolve_cases_fixture_matches_js_contract_shape():
    """用例表是前后端唯一的契约，字段名一改两边都会瞎。"""
    cases = _read_resolve_cases()["cases"]
    assert cases, "用例表不能为空"
    for case in cases:
        assert set(case) == {"brand", "model", "name", "cat"}


def test_brand_styles_cover_every_seeded_canonical_brand():
    """品牌归一到中文后，BRAND_STYLES 缺键会让图标掉成灰色兜底。"""
    src = _index_js()
    catalog = _read_catalog_json()
    block = src.split("const BRAND_STYLES", 1)[1].split("};", 1)[0]
    missing = [b["canonical"] for b in catalog["brands"]
               if b["canonical"].lower() not in block.lower()]
    assert missing == []


def _read_resolve_cases():
    import json
    path = Path(__file__).resolve().parent / "fixtures" / "resolve_cases.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_catalog_json():
    import json
    with open(Path(__file__).resolve().parent.parent / "catalog.json", encoding="utf-8") as f:
        return json.load(f)


# ─── P3 Task 6：cat 在三条写路径上不丢 ───

def _seed_cats(api, cats):
    """把 cat 直接写进沙盒账本，模拟迁移落盘之后的状态。

    不能用写端点造这份数据：POST /api/data 是「新增一条」，不是整份替换，
    发一份数组只会往沙盒里追加一条脏记录。
    """
    _, load, path = api
    records = load()
    for idx, value in cats.items():
        records[idx]["cat"] = value
    Path(path).write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def test_ledger_meta_key_list_is_the_agreed_five():
    """三条写路径共用一张元数据键表，清单不许在各处各写一份。

    和 ORDER_CONTEXT_KEYS 同理：跟着常量断言查不出常量被改窄，所以再用字面量钉一次。
    """
    from app_standalone import LEDGER_META_KEYS

    assert set(LEDGER_META_KEYS) == AGREED_ORDER_KEYS | {"cat"}
    assert len(LEDGER_META_KEYS) == 5, "有重复键，写入循环会覆盖"


def test_cat_survives_update_and_split(api):
    """迁移写进记录的 cat 不能被一次普通编辑或一次拆单抹掉。

    抹掉的后果不是报错，是「品类待确认」的数量在下一次保存后莫名变少。
    """
    call, load, _ = api
    _seed_cats(api, {0: "board", 1: "ram"})

    # 编辑第 0 条的售价：盘上的 cat 必须还在
    status, body = call("PUT", "/api/data/0", {"brand": "微星", "model": "B650M GAMING WIFI",
                                               "cost": "600", "sell": "950"})
    assert status == 200, body
    assert load()[0].get("cat") == "board", "一次普通编辑就把 cat 抹了"

    # 编辑也能显式改品类：客户端发的合法 cat 必须写进去（透传是双向的）
    status, body = call("PUT", "/api/data/0", {"cat": "ssd"})
    assert status == 200, body
    assert load()[0]["cat"] == "ssd", "改品类这件事在编辑路径上静默失效"

    # 拆单：派生出的新行继承 cat。只有第 1 条带 source_order_id，拆单只认它。
    status, body = call("POST", "/api/split_record", {"idx": 1, "parts": [
        {"brand": "光威", "model": "神策 16G×2", "cost": "100"},
        {"brand": "十铨", "model": "Delta 16G", "cost": "100"},
    ]})
    assert status == 200, body
    records = load()
    assert records[body["indices"][0]].get("cat") == "ram", "拆完原记录的 cat 没了"
    assert records[body["indices"][1]].get("cat") == "ram", \
        "拆出来的新行没继承 cat，新行会凭空落「品类待确认」"


def test_add_record_accepts_cat_when_client_sends_it(api):
    call, load, _ = api
    status, body = call("POST", "/api/data", {"brand": "光威", "model": "神策 16G",
                                              "cost": "200", "sell": "", "cat": "ram"})
    assert status == 200, body
    assert load()[body["index"]].get("cat") == "ram", "固定字段表把客户端发的 cat 丢了"

    # 空串与缺失同等对待：都是「品类待确认」，不是 400
    status, body = call("POST", "/api/data", {"brand": "光威", "model": "神策 8G",
                                              "cost": "100", "sell": "", "cat": ""})
    assert status == 200, body
    assert load()[body["index"]].get("cat") == ""


def test_add_record_rejects_unknown_cat(api):
    """cat 只认 §3 那 8 个 key：非法值既不能静默吞掉，也不能落脏值。"""
    call, load, _ = api
    before = load()
    status, body = call("POST", "/api/data", {"brand": "光威", "model": "X",
                                              "cost": "1", "sell": "", "cat": "主板"})
    assert status == 400, body
    assert body["ok"] is False and "cat" in body["error"], body
    assert load() == before, "回 400 之前已经写盘了"


def test_update_record_rejects_unknown_cat(api):
    """cat 一旦能在编辑路径上透传，编辑路径就必须同样认这份白名单。

    只挡新增的话，PUT 就是那条能把脏品类写进账本的侧门。
    """
    call, load, _ = api
    _seed_cats(api, {0: "board"})
    status, body = call("PUT", "/api/data/0", {"cat": "内存条"})
    assert status == 400, body
    assert load()[0]["cat"] == "board", "非法 cat 已经落盘"


# 脏值不止「中文名」这一种形状。大小写也是脏的：8 个 key 就是那 8 个 key，
# BOARD 落不进任何一桶，和「主板」一样会在统计里凭空多一类。
BAD_CAT_SHAPES = ["主板", "BOARD", 0, ["ram"], {"key": "board"}]


@pytest.mark.parametrize("bad", BAD_CAT_SHAPES, ids=[repr(v) for v in BAD_CAT_SHAPES])
def test_both_cat_write_paths_reject_every_illegal_shape(api, bad):
    """非字符串的 cat 也必须是 400，不能让 handler 抛 TypeError 把连接打断。

    白名单判断写成 `value in VALID_CAT_KEYS` 时，list/dict 这种不可哈希的值会直接炸在
    成员判断上：账本倒是没写坏，但前端拿到的是断连，用户看到的是一句没有原因的保存失败。
    """
    call, load, _ = api
    before = load()
    status, body = call("POST", "/api/data", {"brand": "光威", "model": "X",
                                              "cost": "1", "sell": "", "cat": bad})
    assert status == 400, body
    assert body["ok"] is False and "cat" in body["error"], body
    status, body = call("PUT", "/api/data/0", {"cat": bad})
    assert status == 400, body
    assert load() == before, "非法 cat 已经落盘"


# ─── P3 Task 8：前端品类维度（品类列 / 「品类待确认」/ 行内改判）───
# 沿用本文件既有的源码静态扫描写法（_index_js / _top_level_fn）。
# 判定口径那一条另外配一例真跑 JS 的行为用例：计划正文写的 `if (i.cat) return false`
# 在「254 条全带 cat、未判定的是字面量 unknown」这份账本上是恒假 —— 入口做成死的，
# 光看文本断言看不出来，所以把它交给 node 跑一遍。

def _js_with_node():
    """没有 node 就跳过这条，别把「跑不了」写成「跑过了」。"""
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node 才能真正执行一遍 JS 判定")
    return node


def _run_node(snippet):
    import subprocess
    node = _js_with_node()
    proc = subprocess.run([node, "-e", snippet], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, "node 跑这段 JS 就炸了：%s" % (proc.stderr or proc.stdout,)
    return json.loads(proc.stdout.strip())


def test_parse_items_carries_the_record_cat():
    """parseItems 必须把记录自己的 cat 带进 items。

    不带的话品类筛选与行内下拉都读不到值，整条 Task 8 在数据源头就断了；
    而它不会报错，只会安静地显示成「全部没有品类」。
    """
    body = _top_level_fn(_index_js(), "function parseItems(raw)")
    assert re.search(r"cat:\s*\(r\.cat \|\| ''\)\.trim\(\)", body), \
        "parseItems 没带出 cat，品类筛选与行内下拉都拿不到值"


def test_category_pending_chip_exists_and_is_not_the_always_false_shape():
    """「品类待确认」入口必须存在，且判定不能是计划正文那种恒假写法。

    账本里每条都带 cat，未判定的那条值是 'unknown'（truthy）。
    `if (i.cat) return false` 会把它们一起挡掉 → 计数永远 0、chip 永远 hidden。
    """
    src = _index_js()
    assert "const UNKNOWN_CAT = 'unknown';" in src, "没有把 unknown 这个占位品类显式命名"

    m = re.search(r"<button[^>]*id=\"catChip\"[^>]*>", src)
    assert m, "筛选区没有「品类待确认」chip"
    tag = m.group(0)
    assert "filter-chip" in tag, "chip 没复用既有 filter-chip 样式类"
    assert "toggleCatPending" in tag and "hidden" in tag, "chip 少了 toggle 或默认 hidden"
    tail = src[m.end():m.end() + 200]
    assert "品类待确认" in tail, "chip 文案不是「品类待确认」"
    assert 'id="catCount"' in tail, "chip 没带计数"

    body = _top_level_fn(src, "function pendingCat(i)")
    assert "if (i.cat) return false" not in body, \
        "pendingCat 还是恒假写法：unknown 也是 truthy，chip 永远是 0"
    assert re.search(r"i\.cat\s*&&\s*i\.cat\s*!==\s*UNKNOWN_CAT", body), \
        "pendingCat 没把 unknown 当作「记录自己没有可信品类」"
    assert re.search(r"part\.cat\s*===\s*UNKNOWN_CAT", body), \
        "pendingCat 没排除知识库归出 unknown 的情况"


def test_pending_cat_behaviour_on_the_migrated_ledger_shape():
    """把 index.html 里的 pendingCat/effectiveCat 抠出来真跑一遍，钉住四种行。

    静态断言只能证明"写了这句话"，证明不了"unknown 真的算待确认"。
    """
    src = _index_js()
    assert "function effectiveCat(i)" in src, "没有 effectiveCat：下拉没有可复核的默认值"
    snippet = "\n".join([
        "const UNKNOWN_CAT = 'unknown';",
        "const KB = {};",
        "function resolvePart(b, m) { return KB[b + '|' + m] || null; }",
        _top_level_fn(src, "function pendingCat(i)"),
        _top_level_fn(src, "function effectiveCat(i)"),
        "KB['微星|B650M GAMING WIFI'] = { name: 'B650M GAMING WIFI', cat: 'board' };",
        "KB['杂牌|X'] = { name: 'X', cat: 'unknown' };",
        "const rows = [",
        "  { cat: 'unknown', brand: '', model: 'CPU针接触不良返场维修一次' },",
        "  { cat: 'unknown', brand: '微星', model: 'B650M GAMING WIFI' },",
        "  { cat: '', brand: '微星', model: 'B650M GAMING WIFI' },",
        "  { cat: '', brand: '杂牌', model: 'X' },",
        "  { cat: 'board', brand: '微星', model: 'B650M GAMING WIFI' },",
        "];",
        "console.log(JSON.stringify({ pending: rows.map(pendingCat), cat: rows.map(effectiveCat) }));",
    ])
    out = _run_node(snippet)
    # 1 判不出来 → 待确认；2/3 记录自己没有/空但库归得出 → 不算待确认；
    # 4 库里那条也是 unknown → 待确认；5 记录自己有可信品类 → 不算。
    assert out["pending"] == [True, False, False, True, False], out
    # 归得出的行，下拉默认就得停在库判的那个品类上（给人复核的起点）。
    assert out["cat"] == ["unknown", "board", "board", "unknown", "board"], out


def test_category_chip_counter_and_active_state_are_wired_into_update_stats():
    """计数、hidden、高亮三件事都必须在 updateStats 里跟着品类判定算。

    少一条就是 chip 长在那里但永远不亮 / 永远不消失，等于没有入口。
    """
    body = _top_level_fn(_index_js(), "function updateStats()")
    assert "document.getElementById('catChip')" in body, "updateStats 没管品类 chip"
    assert re.search(r"reduce\(\(n,\s*i\)\s*=>\s*n\s*\+\s*\(pendingCat\(i\)", body), \
        "品类待确认的数量不是按 pendingCat 算的"
    assert re.search(r"catChip\.hidden\s*=\s*catPending\s*===\s*0", body), \
        "计数为 0 时品类 chip 没有隐藏"
    assert re.search(r"catChip\.classList\.toggle\('on',\s*catPendingOnly\)", body), \
        "品类 chip 的高亮没跟着筛选状态走"
    assert re.search(r"document\.getElementById\('catCount'\)\.textContent\s*=\s*catPending", body), \
        "品类待确认的计数没写进 chip"


def test_the_two_inline_filters_have_separate_state_and_are_mutually_exclusive():
    """品类筛选另起一个状态变量，并且和「待补售价」互斥。

    复用 pendingOnly 会把两个入口焊成一个；互不排斥的话两个 chip 同时高亮，
    筛出来的是谁的子集没人说得清 —— 这是个账本，行数对不上就是钱对不上。
    """
    src = _index_js()
    assert re.search(r"^let catPendingOnly = false;", src, re.M), "品类筛选没有独立状态变量"
    assert re.search(r"^let pendingOnly = false;", src, re.M), "待补售价的原有状态被挪走了"

    toggle_pending = _top_level_fn(src, "function togglePending()")
    toggle_cat = _top_level_fn(src, "function toggleCatPending()")
    assert re.search(r"if \(pendingOnly\) catPendingOnly = false;", toggle_pending), \
        "打开待补售价时没关掉品类筛选"
    assert re.search(r"if \(catPendingOnly\) pendingOnly = false;", toggle_cat), \
        "打开品类筛选时没关掉待补售价"

    # 两个 chip 都只在型号明细视图有意义：点亮时把视图切过去（沿用既有做法）
    assert "focusModelsView()" in toggle_cat and "focusModelsView()" in toggle_pending

    seg = src[src.index("getElementById('tabs').addEventListener"):
              src.index("getElementById('tableHead').addEventListener")]
    assert re.search(r"currentView !== 'models'[\s\S]{0,120}?pendingOnly = false"
                     r"[\s\S]{0,60}?catPendingOnly = false", seg), \
        "离开型号明细时没把两个筛选一起复位"

    body = _top_level_fn(src, "function renderModels()")
    assert re.search(r"catPendingOnly\s*\?\s*all\.filter\(pendingCat\)", body), \
        "renderModels 没按品类筛选过 visible"
    assert "!i.sell" in body, "待补售价那条既有筛选被改坏了"


def test_inline_category_cell_is_rendered_under_its_own_header_column():
    """品类下拉排在利润率之后、图片之前，选项来自 CATALOG.categories，写盘用原始下标。

    origIdx 那一格错下去不是显示错，是把品类写到另一条记录上。
    现在中间多了一跳（按钮 → 共享面板 → onPick），所以两跳都要盯住。
    """
    src = _index_js()
    body = _top_level_fn(src, "function renderModels()")
    assert "mkTh('cat','品类')" in body, "表头没有品类这一列"
    assert body.index("mkTh('margin','利润率')") < body.index("mkTh('cat','品类')") < \
        body.index("<th>图片</th>"), "品类列没排在利润率之后、图片之前"
    assert "${catSelect(i)}" in body, "行模板里没渲染品类下拉"

    seg = _top_level_fn(src, "function catSelect(i)")
    assert "CATALOG.categories" in seg, "下拉的 8 个选项不是取自知识库品类表"
    assert 'class="cat-select' in seg, "下拉没挂上自己的样式类（默认灰 select 会破坏霓虹主题）"
    assert "event.stopPropagation()" in seg, "下拉没挡住行点击"
    assert re.search(r"openCatCombo\(this,\s*\$\{i\.origIdx\}\)", seg), \
        "按钮没把原始下标 origIdx 交给面板，改判会写到另一条记录上"
    combo = _top_level_fn(src, "function openCatCombo(btn, origIdx)")
    assert "saveCatPatch(origIdx," in combo, "面板选中后没把 origIdx 传进写盘函数"


def test_inline_category_write_reuses_the_existing_inline_put_channel():
    """行内改判照 deleteItem 的形状写：writeHeaders() 无参回落全局版本。

    自己新造一套 fetch/版本逻辑的话，这条路径就绕开了 409 复核；
    body 里回填整条 record 更糟 —— 弹窗那份旧值会把别人刚改的字段盖回去。
    """
    src = _index_js()
    body = _top_level_fn(src, "async function saveCatPatch(idx, value)")
    assert re.search(r"fetch\(API \+ '/' \+ idx", body), "没走既有 PUT 通道"
    assert "method: 'PUT'" in body, "不是 PUT"
    assert re.search(r"writeHeaders\(\)", body), "没调用无参 writeHeaders()（行内写回落全局版本）"
    assert "_ledgerStamp" not in body and "_formStamp" not in body, \
        "行内改判自己碰版本号了，等于新造一套版本逻辑"
    assert "editId" not in body and "saveItem(" not in body, "蹭了弹窗表单的写盘"
    assert re.search(r"if \(res\.status === 409\) return await handleConflict\(\)", body), \
        "409 没交给统一的 handleConflict"
    assert re.search(r"body:\s*JSON\.stringify\(\{ cat: value \}\)", body), \
        "请求体不是只带 cat —— 后端逐键赋值，多带的字段会覆盖别人刚写的值"
    assert "_ledgerBusy" in body and "LEDGER_BUSY_MSG" in body, "少了行内写共用的收尾窗口守卫"
    assert "await fetchItems()" in body, "写完没重拉列表，与行内删除的既有形状不一致"


def test_window_has_a_reload_button_that_reloads_the_document():
    """窗口里要有一个真的重载文档的按钮，不是「重新拉一次数据」。

    后端 send_html 每次请求都重读 index.html，所以前端改了只要重载就能看见；
    可 pywebview 那个窗口没有菜单可点，之前只能靠人记得按 F5。
    写成 fetchItems()/syncFromDisk() 那种「看着像刷新」的东西不算 —— 它只换数据，
    拿不到新的 HTML/JS，改了前端照样是旧页面。
    """
    src = _index_js()
    assert 'onclick="reloadPage()"' in src, "重载没挂到任何按钮上，函数就是死代码"
    header = "function reloadPage() {"
    assert header in src, "按钮点得到但函数没定义，点了只会报 ReferenceError"
    body = _top_level_fn(src, header)
    assert "location.reload()" in body, \
        "reloadPage 没有重载文档；只刷数据拿不到改过的 index.html"
    assert "openFormModal()" in body, \
        "表单开着时不拦一下，重载会把已填未存的内容静默丢掉"


def _reload_page_effect(src, modal_open):
    """在 node 里真跑一遍 src 里的 reloadPage，返回 (重载次数, 提示列表)。

    参数收的是源码文本而不是直接读文件：同一套判定要能喂进改坏的源码，
    才能证明它不是恒绿的。
    """
    js = (
        _top_level_fn(src, "function reloadPage() {") + "\n"
        "let reloaded = 0; const toasts = [];\n"
        "const location = { reload: () => { reloaded++; } };\n"
        "function showToast(msg, kind) { toasts.push([msg, kind]); }\n"
        "function openFormModal() { return %s; }\n"
        "reloadPage();\n"
        "process.stdout.write(JSON.stringify({ reloaded, toasts }));\n"
        % ("{ id: 'modal' }" if modal_open else "null")
    )
    out = _run_node(js)
    return out["reloaded"], out["toasts"]


def test_reload_page_reloads_only_when_no_form_is_open():
    """按下去要真的重载文档；表单开着时按下不能重载，只给一句提示。

    上面那条只查字符串在不在，这条查行为：写成 fetchItems() 的话字符串照样在、
    行为却是拿不到新前端；少了互斥守卫，重载会把填了一半的表单静默清空。
    """
    src = _index_js()

    reloaded, toasts = _reload_page_effect(src, modal_open=False)
    assert (reloaded, toasts) == (1, []), \
        "没有表单时应当直接重载且不弹提示，实到 重载 %s 次、提示 %s" % (reloaded, toasts)

    reloaded, toasts = _reload_page_effect(src, modal_open=True)
    assert reloaded == 0, "表单开着还重载，填了一半的内容直接没了"
    assert len(toasts) == 1 and toasts[0][1] == "err", \
        "拦下来了却不吭声，用户只会以为这个按钮坏了"


def _css_rule(src, selector):
    """取一条 CSS 规则的大括号内容，用于断言声明确实写在样式表里。"""
    start = src.index(selector + " {")
    return src[start + len(selector) + 2:src.index("}", start)]


def test_toolbar_actions_group_wraps_so_the_last_button_stays_reachable():
    """工具栏右侧按钮组必须能换行，不能靠溢出把最右边的按钮推出局。

    实测（真浏览器 831px 视口）：#reloadBtn 右边界 860 > 831、document.scrollWidth 860，
    按钮在窗口里根本点不到 —— 而它唯一的用途就是「改完前端之后让人够得着」。
    同一行里的「AI 录入」早就被裁掉了，只是没人发现。
    """
    src = _index_js()
    start = src.index('<div class="toolbar">')
    block = src[start:src.index("<!-- SMART ENTRY PANEL -->", start)]
    assert 'class="toolbar-actions"' in block, \
        "右侧按钮组没有类名，宽度不够时无从施加约束"
    rule = _css_rule(src, ".toolbar-actions")
    assert "flex-wrap: wrap" in rule, \
        "不换行的话窗口一窄，最右边的按钮就被挤出视口，点都点不到"
    assert "justify-content: flex-end" in rule, \
        "换行后不右对齐，掉下去的那半行会孤零零贴在左边"


# ─── 四处下拉统一成一套自定义暗色面板（浏览器原生弹出改不了样式，是割裂感的根）───

def test_no_native_popups_remain_in_the_dropdown_sites():
    """浏览器原生弹出 CSS 一行都改不了 —— 风格统一只有换自定义面板一条路。

    四处：品牌格（原 datalist）、模板格（原 select）、表格行内品类（原 select），
    型号格则从无到有接同一套面板。只要还剩一个原生控件，那一处就还是白底系统菜单，
    割裂感只是换了个位置，没有消失。
    """
    src = _index_js()
    assert "<datalist" not in src, "品牌格还挂着 datalist，弹出仍由浏览器画"
    assert 'list="brandOptions"' not in src, "品牌格还指向 datalist"
    assert "<select" not in src, "页面里还有原生 select（模板/行内品类），弹的还是白底系统菜单"
    assert 'id="comboPanel"' in src, "页面里没有自定义面板，四处下拉无处可挂"


def test_combo_panel_is_painted_from_the_ledger_design_tokens():
    """面板必须和弹窗同一家族：暗色渐变面、青色细边、高亮行走 cyan token。

    他嫌的就是「选择表出来割裂」；面板若自带一套新色号，统一就变成了又一种割裂。
    """
    src = _index_js()
    rule = _css_rule(src, ".combo-panel")
    assert "position: fixed" in rule, "不用 fixed 定位就会被表格滚动容器裁掉、跟不住输入框"
    assert "linear-gradient" in rule, "面板面不是弹窗那套渐变配方，视觉上仍是外来户"
    assert "rgba(0,229,255" in rule, "边框不在青色族，和弹窗边框对不上"
    hl = _css_rule(src, ".combo-item.hl, .combo-item:hover")
    assert "var(--cyan)" in hl, "高亮行不用青色 token，选中态和全局色彩语言脱节"
    val = _css_rule(src, ".combo-item .combo-val")
    assert "text-overflow: ellipsis" in val, "长型号不截断会折行，把右侧品牌提示挤掉"
    assert "min-width: 0" in val, "flex 子项默认 min-width:auto，不写这句 ellipsis 根本不生效"


def _model_options(parts, used, resolve_map):
    # modelOptions 依赖 partKey，partKey 又依赖 normKey：node 里没有整份文件，三个都得搬过去。
    src = _index_js()
    js = "\n".join([
        _top_level_fn(src, "function normKey(text) {"),
        _top_level_fn(src, "function partKey(brand, key) {"),
        _top_level_fn(src, "function modelOptions(parts, used, resolve) {"),
        "const m = %s;" % json.dumps(resolve_map, ensure_ascii=False),
        "process.stdout.write(JSON.stringify(modelOptions(%s, %s, b => m[b] || b)));"
        % (json.dumps(parts, ensure_ascii=False), json.dumps(used, ensure_ascii=False)),
    ])
    return _run_node(js)


def test_model_options_merge_catalog_parts_with_models_already_used():
    """型号选择表和品牌表同一条规矩：知识库 parts + 账本用过没入库的写法，按归一键去重。

    去重键必须带品牌：同一型号串在两个品牌下是两个不同 part，折成一行就有一个选不到；
    但账本写 'MSI'、库里写 '微星' 的同一 part 必须折成一行 ——
    否则型号表自己就在复制统计分叉的老毛病。
    """
    got = _model_options(
        [{"brand": "微星", "name": "B650M GAMING WIFI", "aliases": ["B650M-GAMING-WIFI"]},
         {"brand": "技嘉", "name": "B650M GAMING WIFI", "aliases": []}],
        [{"brand": "MSI", "model": "B650M GAMING WIFI"},
         {"brand": "华硕", "model": "TUF GAMING X570-PLUS"},
         {"brand": "", "model": ""}],
        {"MSI": "微星"},
    )
    assert got == [
        {"value": "B650M GAMING WIFI", "hint": "微星"},
        {"value": "B650M GAMING WIFI", "hint": "技嘉"},
        {"value": "TUF GAMING X570-PLUS", "hint": "华硕"},
    ], "实到 %s" % (got,)


def _template_options(records, brand_map, model_map):
    src = _index_js()
    parts = [
        _top_level_fn(src, "function normKey(text) {"),
        _top_level_fn(src, "function partKey(brand, key) {"),
        _top_level_fn(src, "function templateOptions(records, resolveB, resolveM) {"),
        "const RB = %s;\n" % json.dumps(brand_map, ensure_ascii=False),
        "const RM = %s;\n" % json.dumps(model_map, ensure_ascii=False),
        "const rb = b => RB[b] || b;\n",
        "const rm = (b, m) => RM[String(m).toUpperCase()] || m;\n",
        "process.stdout.write(JSON.stringify(templateOptions(%s, rb, rm)));\n"
        % json.dumps(records, ensure_ascii=False),
    ]
    return _run_node("".join(parts))


def test_template_options_lists_only_repeated_configs_sorted_by_count():
    """模板候选按归一后的「品牌+型号」计数，只留 2 笔以上的，笔数多的排前面。

    真实账本 263 条归一后是 121 组，其中 92 组只有 1 笔；全列会把面板变成一屏流水账，
    而一次性配置本来就不配叫模板 —— 品牌框和型号下拉仍然选得到它们。
    聚合键必须带品牌：B650M GAMING WIFI 在微星下 3 笔、技嘉下 1 笔，
    只按型号计数会顶成 4 笔并把技嘉那条藏起来。
    记录故意把 H610M-E 排在最前，所以顺序断言考的是真排序而不是插入序。
    """
    assert "function templateOptions(" in _index_js(), "templateOptions 还不存在"
    got = _template_options(
        [{"brand": "微星", "model": "H610M-E"},
         {"brand": "微星", "model": "h610m-e"},
         {"brand": "MSI", "model": "B650M GAMING WIFI"},
         {"brand": "微星", "model": "B650M GAMING WIFI"},
         {"brand": "微星", "model": "b650m-gaming-wifi"},
         {"brand": "技嘉", "model": "B650M GAMING WIFI"},
         {"brand": "华硕", "model": "TUF GAMING X570-PLUS"},
         {"brand": "", "model": "B650M GAMING WIFI"},
         {"brand": "微星", "model": ""}],
        {"MSI": "微星", "": ""},
        {"B650M-GAMING-WIFI": "B650M GAMING WIFI", "H610M-E": "H610M-E"},
    )
    assert got == [
        {"brand": "微星", "model": "B650M GAMING WIFI", "count": 3},
        {"brand": "微星", "model": "H610M-E", "count": 2},
    ], "实到 %s" % (got,)


def test_template_candidates_no_longer_depend_on_the_dead_endpoint():
    """模板候选改成前端现算后，源码里不许再出现那个死接口和它的手写缓存。

    /api/templates 在 app_standalone.py 里根本没有路由，fetch 到 404 被 catch 静默
    吞成空数组，于是这个字段从引入那天起永远只显示「无匹配」。现算的数据源就是内存里的
    items，跟着刷新自动更新，那一圈 window._tplCache = null 手动失效也该一起消失。
    """
    src = _index_js()
    for dead in ("api/templates", "_tplCache"):
        assert dead not in src, "还留着 %s，模板数据源没换干净" % (dead,)


def _filter_combo(cands, query):
    js = (
        _top_level_fn(_index_js(), "function normKey(text) {") + "\n"
        + _top_level_fn(_index_js(), "function filterCombo(cands, query) {") + "\n"
        + "process.stdout.write(JSON.stringify(filterCombo(%s, %s)));\n"
        % (json.dumps(cands, ensure_ascii=False), json.dumps(query, ensure_ascii=False))
    )
    return _run_node(js)


def test_combo_filter_matches_value_and_hint_case_insensitively():
    """打字即子串过滤：命中规范名或提示文字都算，大小写空白不敏感。

    他用 datalist 时习惯了「打几个字就收窄」，换自定义面板不能丢这个手感；
    提示（品牌/别名）也要可搜，否则九十多条型号他只能 eyeball 滚。
    """
    got = _filter_combo(
        [{"value": "微星", "hint": "MSI"},
         {"value": "B650M GAMING WIFI", "hint": "微星"},
         {"value": "英特尔", "hint": "INTER / Intel"}],
        "msi",
    )
    assert [o["value"] for o in got] == ["微星"], "hint 命中错：实到 %s" % (got,)
    got = _filter_combo(
        [{"value": "微星", "hint": "MSI"},
         {"value": "B650M GAMING WIFI", "hint": "微星"}],
        "b650m",
    )
    assert [o["value"] for o in got] == ["B650M GAMING WIFI"], "value 命中错：实到 %s" % (got,)
    assert _filter_combo([], "x") == []
    assert len(_filter_combo([{"value": "a", "hint": ""}], "")) == 1


def test_cat_cell_is_a_button_that_opens_the_shared_combo():
    """表格行内品类从 select 改 button：保留 .cat-select 那张皮，弹的换成自家面板。

    catSelect 在渲染热路径上每行跑一次；每行塞一份面板 DOM 会爆，
    所以 button 只负责记「点的是哪一行」，面板全局共用一份。
    """
    fn = _top_level_fn(_index_js(), "function catSelect(i) {")
    assert "<button" in fn, "品类格还不是 button"
    assert "<select" not in fn, "品类格还在渲染原生 select"
    assert "openCatCombo(" in fn, "button 没接共享面板，点了没反应"


def test_combo_keys_are_intercepted_before_the_modal_submit_handler():
    """面板开着时 Enter 选中、Esc 关面板，都不能漏给 document 级「回车提交 / Esc 关窗」。

    既有 document keydown 会无条件拿 Enter 去点提交按钮；combo 的监听若注册在它后面，
    回车选品牌的那一下会把整张表单一起提交 —— 他只会看到弹窗莫名其妙关了。
    同一目标上监听按注册顺序执行，所以 combo 的监听在源码里必须更靠前。
    """
    src = _index_js()
    combo_at = src.index("COMBO KEYBOARD")
    submit_at = src.index("if (e.key === 'Enter' && open)")
    assert combo_at < submit_at, "combo 键盘监听注册在提交监听之后，回车会先被提交吞掉"


def _brand_options(brands, used):
    js = (
        _top_level_fn(_index_js(), "function normKey(text) {") + "\n"
        + _top_level_fn(_index_js(), "function brandOptions(brands, usedNames) {") + "\n"
        + "process.stdout.write(JSON.stringify(brandOptions(%s, %s)));\n"
        % (json.dumps(brands, ensure_ascii=False), json.dumps(used, ensure_ascii=False))
    )
    return _run_node(js)


def test_brand_options_come_from_the_catalog_and_from_names_already_used():
    """选项 = 知识库规范名（别名当提示）+ 账本里用过但没入库的写法。

    只喂知识库：账本里那些还没入库的写法选不到，等于逼他继续手打；
    只喂账本：新品牌永远进不了候选，知识库白建。
    另外 'MSI' 已经被 '微星' 的别名覆盖，不能再单独占一行 ——
    否则下拉框里又把同一个品牌拆成两种写法，正是统计分叉的老毛病。
    """
    got = _brand_options(
        [{"canonical": "微星", "aliases": ["MSI"]},
         {"canonical": "英特尔", "aliases": ["INTER", "Intel"]}],
        ["微星", "MSI", "华硕", "", None],
    )
    assert got == [
        {"value": "微星", "label": "MSI"},
        {"value": "英特尔", "label": "INTER / Intel"},
        {"value": "华硕", "label": ""},
    ], "实到 %s" % (got,)


def test_brand_options_degrade_to_nothing_when_there_is_no_catalog():
    """知识库拉不到时返回空数组，不许抛。

    loadCatalog 失败是设计里允许的（记账不能被知识库阻塞）；
    这里一抛，弹窗直接开不出来，比没有下拉框严重得多。
    """
    assert _brand_options(None, None) == []
    assert _brand_options([], []) == []
