"""Task 1 的账本写入路径测试：订单上下文的 round-trip、空 sell 的不对称、坏输入的显式拒绝。

fixture（api / SEED / 沙盒兜底）在 tests/conftest.py，后面 10 个测试文件共用。
"""
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
    status, resp = call("POST", "/api/data", {
        "brand": "七彩虹", "model": "B760M", "cost": 700, "sell": "",
        "source_order_id": "20250922007", "order_date": "2025-09-22 09:10:00",
        "item_title": "七彩虹b760M主板", "order_paid": 700,
    })
    assert status == 200, resp
    records = load()
    saved = records[resp["index"]]
    assert resp["record"] == saved, "handler 返回的 record 和落盘的不是同一条，响应里的值不可信"
    assert saved["brand"] == "七彩虹"
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
