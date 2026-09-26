"""
闲鱼 (Goofish) 订单抓取脚本
使用 Playwright 自动登录并抓取订单数据，写入 MySQL neon_ledger.xianyu_orders

用法:
  python xianyu_scraper.py            # 抓取订单（会话过期时提示扫码重登）
  python xianyu_scraper.py --login    # 打开浏览器，验证或完成扫码登录
  python xianyu_scraper.py --debug    # 导出原始 API 响应到 debug_api_responses.json
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime

import pymysql
from playwright.async_api import async_playwright

# Windows 控制台可能是 GBK，强制 UTF-8 防止打印中文/¥ 时崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ═══════════════════════ Config ═══════════════════════

from db_config import DB_CONF  # 真口令在本机 db_secret.py，不进版本控制

_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DUMP = os.path.join(_DIR, "debug_api_responses.json")
BASE_URL = "https://www.goofish.com"

# 真实买入订单接口（mtop）关键字；登录态有效性以它的 ret 字段为准，
# 不能靠登录弹窗判断——未登录时首页可正常浏览、不弹登录框
BOUGHT_API_KW = "trade.bought.list"

# ═══════════════════════ DB Schema ═══════════════════════

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS xianyu_orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  order_id VARCHAR(64) UNIQUE,
  item_title VARCHAR(500),
  price DECIMAL(10,2),
  trade_type ENUM('sold','bought') DEFAULT 'sold',
  counterparty VARCHAR(200),
  order_status VARCHAR(50),
  order_date DATETIME,
  images JSON,
  raw_data JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# ═══════════════════════ Helpers ═══════════════════════

def safe_price(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[\d.]+", str(v))
    return float(m.group()) if m else 0.0


def try_parse_json(text):
    if isinstance(text, (bytes, bytearray)):
        for enc in ("utf-8", "gbk"):
            try:
                text = text.decode(enc)
                break
            except Exception:
                continue
    if not isinstance(text, str):
        return None
    m = re.match(r"^\w+\((.*)\);?$", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except Exception:
        return None


def dump_debug(captured):
    with open(DEBUG_DUMP, "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=2)
    print(f"[debug] {len(captured)} responses -> {DEBUG_DUMP}")


# ═══════════════════════ Login ═══════════════════════

def _on_login_page(url):
    return any(k in url for k in ("login", "passport", "auth"))


async def _is_logged_in(page):
    """goofish.com 未登录时页面会自动弹出登录框，检测它是否还在。"""
    if _on_login_page(page.url):
        return False
    for txt in ("手机扫码安全登录", "扫码登录", "其他方式登录"):
        try:
            if await page.get_by_text(txt).first.is_visible():
                return False
        except Exception:
            continue
    return True


async def _open_browser(pw):
    return await pw.chromium.launch_persistent_context(
        user_data_dir=os.path.join(_DIR, ".browser_data"),
        headless=False,
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled"],
    )


def _make_collector(captured):
    async def on_response(resp):
        if "h5api" not in resp.url:
            return
        try:
            body = await resp.body()
            data = try_parse_json(body)
            if data:
                captured.append({"url": resp.url, "data": data})
        except Exception:
            pass
    return on_response


def _bought_entry(captured):
    for e in reversed(captured):
        if BOUGHT_API_KW in e.get("url", "") or (
            isinstance(e.get("data"), dict) and BOUGHT_API_KW in e["data"].get("api", "")
        ):
            return e
    return None


def _ret_ok(entry):
    if not entry or not isinstance(entry["data"], dict):
        return False
    return any("SUCCESS" in str(r) for r in entry["data"].get("ret", []))


def _ret_expired(entry):
    if not entry or not isinstance(entry["data"], dict):
        return False
    return any("SESSION_EXPIRED" in str(r) for r in entry["data"].get("ret", []))


async def _ensure_bought_session(page, captured):
    """打开 /bought 并用真实订单接口的 ret 判断会话；过期则等待扫码重登。"""
    await _safe_goto(page, BASE_URL + "/bought")
    await asyncio.sleep(5)
    if _ret_ok(_bought_entry(captured)):
        print("[OK] mtop 会话有效，已登录")
        return True

    reason = "mtop 会话已过期" if _ret_expired(_bought_entry(captured)) else "未捕获到买入订单接口响应"
    print(f"[!] {reason}，需要重新扫码登录")
    print()
    print("+--------------------------------------+")
    print("|  请用闲鱼/淘宝/支付宝 APP 扫码登录   |")
    print("|      登录成功后脚本自动继续           |")
    print("+--------------------------------------+")
    print()

    # 登录框渲染在 iframe 里，page.get_by_text 探测不到（截图证实弹框可见但文本检测为不可见），
    # 因此改用轮询 mtop ret 判断登录；页面登录成功后通常会自动刷新，
    # 每 25 秒兜底手动刷新一次以便重新请求 bought.list
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 600
    last_reload = 0.0
    while loop.time() < deadline:
        await asyncio.sleep(3)
        if _ret_ok(_bought_entry(captured)):
            print("[OK] 检测到登录成功（mtop ret SUCCESS）")
            return True
        if loop.time() - last_reload >= 25:
            last_reload = loop.time()
            captured.clear()
            await _safe_goto(page, BASE_URL + "/bought")
            await asyncio.sleep(5)
            if _ret_ok(_bought_entry(captured)):
                print("[OK] 检测到登录成功（mtop ret SUCCESS）")
                return True
            print(f"[i] 仍未登录，继续等待扫码（剩 {max(0, int(deadline - loop.time()))} 秒）...")
    print("[!] 600 秒内未检测到登录成功，放弃本次抓取")
    return False


# ═══════════════════════ Scrape ═══════════════════════

async def scrape_orders(ctx, page, debug=False):
    captured = []
    on_response = _make_collector(captured)
    page.on("response", on_response)
    all_orders = []

    if not await _ensure_bought_session(page, captured):
        if debug:
            dump_debug(list(captured))
        page.remove_listener("response", on_response)
        return []

    print("\n[i] 抓取买入订单 (/bought)...")
    await _scroll_and_wait(page)
    bought_orders = _extract_from_captured(captured, "bought")
    if not bought_orders:
        bought_orders = await _extract_from_dom(page, "bought")
    print(f"    买入: {len(bought_orders)} 条")
    all_orders.extend(bought_orders)
    bought_dump = list(captured)

    print('[i] 卖出订单: goofish.com 网页端暂不支持（提示"待上线，扫码去APP查看"），跳过')

    if debug:
        dump_debug(bought_dump + list(captured))

    page.remove_listener("response", on_response)
    return all_orders


async def _safe_goto(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"    [warn] 导航 {url}: {e}")


async def _scroll_and_wait(page):
    await asyncio.sleep(2)
    for i in range(6):
        await page.evaluate(f"window.scrollTo(0, {(i + 1) * 800})")
        await asyncio.sleep(0.6)
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1)


# ── API 解析 ──

def _extract_from_captured(captured, trade_type):
    orders = []
    for entry in captured:
        data = entry["data"]
        url = entry["url"]

        if not isinstance(data, dict):
            continue

        ret_list = data.get("ret", [])
        is_success = any("SUCCESS" in str(r) for r in ret_list)

        api_name = data.get("api", "")
        is_order_api = any(kw in api_name for kw in [
            "order", "trade", "sold", "bought", "my", "sell", "buy",
        ])
        if not is_order_api:
            is_order_api = any(kw in url for kw in [
                "order", "trade", "sold", "bought",
            ])

        if not is_success or not is_order_api:
            continue

        inner = data.get("data")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except Exception:
                continue

        if not isinstance(inner, dict):
            continue

        items = _dig_order_list(inner)
        print(f"    [api] {api_name or url[:80]} -> {len(items)} items")

        for item in items:
            order = _parse_api_order(item, trade_type)
            if order:
                orders.append(order)

    seen = set()
    unique = []
    for o in orders:
        key = o["order_id"] or o["item_title"]
        if key and key not in seen:
            seen.add(key)
            unique.append(o)
    return unique


def _dig_order_list(data, depth=0):
    if depth > 8:
        return []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        flat_keys = (
            "orderId", "orderStatus", "title", "itemTitle",
            "tradeId", "bizOrderId", "itemId",
        )
        if any(k in data[0] for k in flat_keys):
            return data
        cd = data[0].get("commonData")
        if isinstance(cd, dict) and any(k in cd for k in ("orderId", "itemId", "tradeId")):
            return data
    if isinstance(data, dict):
        for key in ("orderList", "list", "data", "result", "items",
                     "content", "module", "model", "value"):
            if key in data:
                found = _dig_order_list(data[key], depth + 1)
                if found:
                    return found
    return []


def _safe(d, *keys, default=""):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is None:
            return default
    return d if d != "" else default


def _parse_api_order(item, trade_type):
    cd = item.get("commonData") if isinstance(item, dict) else None
    content_data = _safe(item, "content", "data", default=None)
    head_data = _safe(item, "head", "data", default=None)

    if isinstance(cd, dict) and isinstance(content_data, dict):
        detail = content_data.get("detailInfo") or {}
        price_info = content_data.get("priceInfo") or {}
        user_info = (head_data or {}).get("userInfo") or {}

        title = detail.get("auctionTitle") or ""
        price = price_info.get("price") or 0
        order_id = str(cd.get("orderId") or cd.get("orderIdStr") or "")
        status = cd.get("tradeStatusEnum") or ""
        if head_data:
            status = head_data.get("statusViewMsg") or status
        date_str = (head_data or {}).get("createTime") or ""
        counterparty = user_info.get("userNick") or ""

        images = []
        pic = detail.get("auctionPic") or ""
        if pic:
            images.append(pic)
    else:
        title = (
            item.get("title") or item.get("itemTitle") or item.get("desc")
            or item.get("itemName") or ""
        )
        price = (
            item.get("price") or item.get("soldPrice") or item.get("totalFee")
            or item.get("actualFee") or item.get("amount")
            or item.get("tradePrice") or item.get("itemPrice") or 0
        )
        order_id = str(
            item.get("orderId") or item.get("id") or item.get("tradeId")
            or item.get("bizOrderId") or ""
        )
        status = (
            item.get("orderStatus") or item.get("status")
            or item.get("statusStr") or item.get("bizStatusStr") or ""
        )
        date_str = (
            item.get("createTime") or item.get("orderTime")
            or item.get("gmtCreate") or item.get("gmtModified")
            or item.get("createTimeStr") or ""
        )
        counterparty = (
            item.get("buyerNick") or item.get("sellerNick")
            or item.get("userNick") or item.get("counterpartNick")
            or item.get("peerNick") or ""
        )
        images = []
        for key in ("pic", "picUrl", "itemPic", "imageUrl", "img", "pic_path"):
            v = item.get(key)
            if v and isinstance(v, str):
                images.append(v)
        if not images and isinstance(item.get("pics"), list):
            images = [
                p.get("url", "") if isinstance(p, dict) else str(p)
                for p in item["pics"]
            ]

    if not title and not order_id:
        return None

    return {
        "order_id": order_id,
        "item_title": title,
        "price": safe_price(price),
        "trade_type": trade_type,
        "counterparty": counterparty,
        "order_status": str(status),
        "order_date": _parse_date(date_str),
        "images": images,
        "raw_data": item,
    }


def _parse_date(s):
    if not s:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(s / 1000)
        except Exception:
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(s), fmt)
        except Exception:
            continue
    return None


# ── DOM 兜底 ──

async def _extract_from_dom(page, trade_type):
    print(f"    [fallback] DOM 解析 ({trade_type})...")
    orders = []
    try:
        cards = await page.query_selector_all(
            '[class*="order-item"], [class*="orderItem"], '
            '[class*="trade-item"], [class*="tradeItem"], '
            '[class*="order-card"], [class*="orderCard"]'
        )
        print(f"    [fallback] 找到 {len(cards)} 个候选卡片")

        for card in cards:
            text = (await card.inner_text()).strip()
            if len(text) < 5:
                continue

            title_el = await card.query_selector(
                '[class*="title"], [class*="name"], h3, h4, a'
            )
            title = (await title_el.inner_text()).strip() if title_el else text[:60]

            price_el = await card.query_selector(
                '[class*="price"], [class*="Price"], [class*="amount"]'
            )
            price_text = (await price_el.inner_text()).strip() if price_el else ""
            price = safe_price(price_text)

            date_el = await card.query_selector('[class*="time"], [class*="date"]')
            date_text = (await date_el.inner_text()).strip() if date_el else ""

            img_els = await card.query_selector_all("img")
            images = []
            for img in img_els[:5]:
                src = await img.get_attribute("src") or await img.get_attribute("data-src")
                if src and not src.startswith("data:"):
                    images.append(src)

            if title and len(title) > 2:
                orders.append({
                    "order_id": "",
                    "item_title": title,
                    "price": price,
                    "trade_type": trade_type,
                    "counterparty": "",
                    "order_status": "",
                    "order_date": _parse_date(date_text),
                    "images": images,
                    "raw_data": {"dom_text": text[:500]},
                })
    except Exception as e:
        print(f"    [warn] DOM 解析异常: {e}")
    return orders


# ═══════════════════════ DB Write ═══════════════════════

def ensure_table():
    conn = pymysql.connect(**DB_CONF)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def insert_orders(orders):
    conn = pymysql.connect(**DB_CONF)
    new_count = 0
    dup_count = 0
    try:
        with conn.cursor() as cur:
            for o in orders:
                try:
                    cur.execute(
                        """INSERT INTO xianyu_orders
                           (order_id, item_title, price, trade_type,
                            counterparty, order_status, order_date, images, raw_data)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            o.get("order_id") or None,
                            o.get("item_title", ""),
                            o.get("price", 0),
                            o.get("trade_type", "sold"),
                            o.get("counterparty", ""),
                            o.get("order_status", ""),
                            o.get("order_date"),
                            json.dumps(o.get("images", []), ensure_ascii=False),
                            json.dumps(o.get("raw_data", {}), ensure_ascii=False, default=str),
                        ),
                    )
                    new_count += 1
                except pymysql.err.IntegrityError:
                    dup_count += 1
        conn.commit()
    finally:
        conn.close()
    return new_count, dup_count


# ═══════════════════════ Main ═══════════════════════

async def run(debug=False):
    async with async_playwright() as pw:
        ctx = await _open_browser(pw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            print("\n[i] 开始抓取订单...")
            orders = await scrape_orders(ctx, page, debug)
            print(f"\n[i] 共抓取 {len(orders)} 条订单")

            if orders:
                ensure_table()
                new, dup = insert_orders(orders)
                print(f"    新增 {new} 条，跳过重复 {dup} 条")
                print(f"    -> 表: neon_ledger.xianyu_orders")
            else:
                print("[!] 未抓到数据。可能原因:")
                print("    1. 账号无交易记录")
                print("    2. 页面结构变化，需更新选择器")
                print("    3. 触发了反爬验证")
                print(f"    4. 用 --debug 运行并检查 {DEBUG_DUMP}")
        finally:
            await ctx.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="闲鱼订单抓取")
    ap.add_argument("--login", action="store_true", help="打开浏览器，验证或完成扫码登录")
    ap.add_argument("--debug", action="store_true", help="导出原始 API 响应")
    args = ap.parse_args()

    if args.login:
        asyncio.run(_login_only())
    else:
        asyncio.run(run(debug=args.debug))


async def _login_only():
    async with async_playwright() as pw:
        ctx = await _open_browser(pw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        captured = []
        on_response = _make_collector(captured)
        page.on("response", on_response)
        ok = await _ensure_bought_session(page, captured)
        page.remove_listener("response", on_response)
        print("\n[OK] 登录有效，mtop 会话正常" if ok else "\n[!] 登录未完成或会话验证失败")
        try:
            input("按 Enter 关闭浏览器...")
        except EOFError:
            pass
        await ctx.close()


if __name__ == "__main__":
    main()
