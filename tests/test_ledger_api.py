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
