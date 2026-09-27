"""
闲鱼订单 → 账本 填入台

读本地抓单库 orders.db，按日期分组展示；勾选后可改写品牌/型号/成本，
再写入 data.json（price → cost，sell 留空，带 source_order_id 去重）。

用法:
  python xianyu_review.py            # 起本地服务并打开原生窗口
  python xianyu_review.py --dry-run  # 只打印标题拆分结果，不起服务
"""

import http.server
import json
import os
import re
import shutil
import sys
import threading

import webview

import orders_db

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ═══════════════════════ Config ═══════════════════════

PORT_START = 8766
PORT_END = 8776

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
HTML_FILE = os.path.join(BASE_DIR, "xianyu_review.html")

# ═══════════════════════ 标题拆分规则 ═══════════════════════

BRANDS = [
    ("微星", ["微星", "msi"]),
    ("华硕", ["华硕", "asus", "玩家国度", "rog"]),
    ("技嘉", ["技嘉", "gigabyte", "aorus"]),
    ("华擎", ["华擎", "asrock"]),
    ("映泰", ["映泰", "biostar"]),
    ("铭瑄", ["铭瑄", "maxsun"]),
    ("七彩虹", ["七彩虹", "colorful", "battle-ax", "战斧"]),
    ("昂达", ["昂达", "onda"]),
    ("精粤", ["精粤", "jingyue"]),
    ("华南金牌", ["华南金牌"]),
    ("梅捷", ["梅捷", "soyo"]),
    ("盈通", ["盈通", "yeston"]),
    ("英特尔", ["英特尔", "intel"]),
    ("AMD", ["amd", "锐龙", "ryzen"]),
    ("英伟达", ["英伟达", "nvidia"]),
    ("影驰", ["影驰", "galax"]),
    ("索泰", ["索泰", "zotac"]),
    ("蓝宝石", ["蓝宝石", "sapphire"]),
    ("迪兰", ["迪兰", "dataland"]),
    ("耕升", ["耕升", "gainward"]),
    ("讯景", ["讯景", "xfx"]),
    ("三星", ["三星", "samsung"]),
    ("西部数据", ["西数", "西部数据", "western digital"]),
    ("希捷", ["希捷", "seagate"]),
    ("铠侠", ["铠侠", "kioxia"]),
    ("致态", ["致态", "zhitai"]),
    ("闪迪", ["闪迪", "sandisk"]),
    ("雷克沙", ["雷克沙", "lexar"]),
    ("金士顿", ["金士顿", "kingston"]),
    ("威刚", ["威刚", "adata"]),
    ("光威", ["光威", "gloway"]),
    ("芝奇", ["芝奇", "gskill", "g.skill"]),
    ("海盗船", ["海盗船", "corsair"]),
    ("长城", ["长城", "greatwall"]),
    ("航嘉", ["航嘉", "huntkey"]),
    ("振华", ["振华", "superflower"]),
    ("鑫谷", ["鑫谷", "segotep"]),
    ("先马", ["先马", "sama"]),
    ("爱国者", ["爱国者", "aigo"]),
    ("九州风神", ["九州风神", "deepcool"]),
    ("利民", ["利民", "thermalright"]),
    ("雅浚", ["雅浚", "proartist"]),
    ("猫头鹰", ["猫头鹰", "noctua"]),
    ("追风者", ["追风者", "phanteks"]),
    ("联力", ["联力", "lianli", "lian li"]),
    ("酷冷至尊", ["酷冷至尊", "cooler master", "coolermaster"]),
    ("戴尔", ["戴尔", "dell", "外星人", "alienware"]),
    ("联想", ["联想", "lenovo", "拯救者", "legion", "thinkpad"]),
    ("惠普", ["惠普", "暗影精灵", "omen"]),
    ("苹果", ["苹果", "apple", "iphone", "macbook", "ipad"]),
    ("小米", ["小米", "xiaomi", "红米", "redmi"]),
    ("华为", ["华为", "huawei", "荣耀", "honor"]),
    ("索尼", ["索尼", "sony", "playstation"]),
    ("微软", ["微软", "microsoft", "xbox"]),
    ("任天堂", ["任天堂", "nintendo"]),
    ("罗技", ["罗技", "logitech"]),
    ("雷蛇", ["雷蛇", "razer"]),
    ("飞利浦", ["飞利浦", "philips"]),
    ("AOC", ["aoc", "冠捷"]),
    ("明基", ["明基", "benq", "zowie"]),
    ("LG", ["lg"]),
    ("大疆", ["大疆", "dji"]),
    ("京东京造", ["京东京造"]),
    ("台电", ["台电", "teclast"]),
]

_LATIN = re.compile(r"^[a-z0-9 .\-+]+$")


def _brand_patterns():
    """拉丁别名加词边界，避免 lg / hp 命中单词内部。"""
    out = []
    for canonical, aliases in BRANDS:
        for a in aliases:
            low = a.lower()
            pat = rf"(?<![a-z0-9]){re.escape(low)}(?![a-z0-9])" if _LATIN.match(low) else re.escape(low)
            out.append((canonical, a, re.compile(pat)))
    return out


_BRAND_PATTERNS = _brand_patterns()

# 卖家口水词：切句后仍可能残留在型号首尾
_LEAD_NOISE = re.compile(
    r"^(?:[\s,，。、!！~～\-—*【】\[\]]+|【[^】]*】|\[[^\]]*\]|"
    r"几乎全新|全新未拆封|全新盒装|全新|未拆封|盒装|闲置|二手|拆机件|拆机|出售|出掉|急出|转出|出|"
    r"九成新|九五新|九九新|成色新|有质保的|有质保|包邮|现货|正品|自用|捡漏|低价|特价)+",
    re.I,
)
_TRAIL_NOISE = re.compile(
    r"(?:[\s,，。、!！~～\-—*【】\[\]]+|(?:出售|急出|包邮|低价|特价|自提|到付|勿扰|谢绝|可小刀|不议价|"
    r"懂修的拿去|拿去|出))+$",
    re.I,
)
# 切掉第一个句读之后的卖家描述，只留商品名
_SENTENCE_CUT = re.compile(r"[，,。；;！!？?]")
# 品牌之前那截里可能藏着型号（如「新的b760M精粤主板」）
_MODEL_TOKEN = re.compile(r"[A-Za-z]+[-]?\d+[A-Za-z0-9\-]*")


def split_title(title):
    """把闲鱼标题拆成 (brand, model)。匹配不到品牌时 brand 为空，model 保留可编辑的原文。"""
    t = (title or "").strip()
    if not t:
        return "", ""

    head = _SENTENCE_CUT.split(t)[0].strip() or t
    low = head.lower()

    best = None
    for canonical, alias, pat in _BRAND_PATTERNS:
        m = pat.search(low)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, canonical)

    if best is None:
        model = _clean(head)
        return "", model or head

    m, brand = best
    prefix, rest = head[:m.start()], head[m.end():]
    tokens = _MODEL_TOKEN.findall(prefix)
    model = (" ".join(tokens) + " " if tokens else "") + rest
    model = _clean(model)
    return brand, model or head


def _clean(s):
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev = s
        s = _LEAD_NOISE.sub("", s).strip()
        s = _TRAIL_NOISE.sub("", s).strip()
    return s


# ═══════════════════════ 账本读写 ═══════════════════════

def load_ledger():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_ledger(data):
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, DATA_FILE)


def imported_order_ids():
    return {str(r.get("source_order_id")) for r in load_ledger() if r.get("source_order_id")}


def _to_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ═══════════════════════ 抓单库 ═══════════════════════

def connect():
    return orders_db.connect()


def fetch_orders():
    done = imported_order_ids()
    conn = connect()
    try:
        rows = orders_db.fetch_orders(conn)
    finally:
        conn.close()

    out = []
    for r in rows:
        title = r.get("item_title") or ""
        brand, model = split_title(title)
        oid = str(r.get("order_id") or "")
        out.append({
            "order_id": oid,
            "item_title": title,
            "price": _to_float(r.get("price")),
            "trade_type": r.get("trade_type") or "",
            "counterparty": r.get("counterparty") or "",
            "order_status": r.get("order_status") or "",
            "order_date": str(r.get("order_date") or ""),
            "brand": brand,
            "model": model,
            "imported": oid in done,
        })
    return out


def import_orders(items):
    """items: [{order_id, brand, model, cost}] → 追加进 data.json，已存在的 order_id 跳过。"""
    data = load_ledger()
    done = {str(r.get("source_order_id")) for r in data if r.get("source_order_id")}

    added, skipped = [], []
    for it in items or []:
        oid = str(it.get("order_id") or "").strip()
        if not oid:
            continue
        if oid in done:
            skipped.append(oid)
            continue
        record = {
            "brand": str(it.get("brand") or "").strip(),
            "model": str(it.get("model") or "").strip(),
            "cost": _to_float(it.get("cost")),
            # 买入订单没有卖出价，留空由用户之后自己补
            "sell": "",
            "sn": "",
            "accessory": "",
            "accessory_price": "",
            "extra_price": "",
            "images": [],
            "source_order_id": oid,
        }
        data.append(record)
        done.add(oid)
        added.append(oid)

    if added:
        save_ledger(data)
    return added, skipped, len(data)


# ═══════════════════════ HTTP ═══════════════════════

class ReviewHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 5_000_000:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._page()
        if path == "/api/orders":
            try:
                return self._json({"ok": True, "orders": fetch_orders()})
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/import":
            return self._json({"ok": False, "error": "not found"}, 404)
        body = self._body()
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            return self._json({"ok": False, "error": "请求体需要 {items: [...]}"}, 400)
        try:
            added, skipped, total = import_orders(body["items"])
        except Exception as e:
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"ok": True, "added": added, "skipped": skipped, "ledger_total": total})

    def _page(self):
        with open(HTML_FILE, "r", encoding="utf-8") as f:
            content = f.read().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))
        sys.stderr.flush()


def bind_server():
    last = None
    for port in range(PORT_START, PORT_END + 1):
        try:
            return http.server.ThreadingHTTPServer(("127.0.0.1", port), ReviewHandler), port
        except OSError as e:
            last = e
    raise RuntimeError(f"{PORT_START}-{PORT_END} 端口都被占用: {last}")


def main():
    httpd, port = bind_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[xianyu_review] http://127.0.0.1:{port}")

    webview.create_window(
        title="闲鱼订单 · 填入账本",
        url=f"http://127.0.0.1:{port}",
        width=1360,
        height=900,
        min_size=(980, 620),
        resizable=True,
        easy_drag=False,
    )
    webview.start()
    httpd.shutdown()


def dry_run():
    for o in fetch_orders():
        flag = "已填入" if o["imported"] else "  待填"
        print(f'{o["order_date"][:10]} [{flag}] ¥{o["price"]:<8} '
              f'品牌={o["brand"] or "-":<8} 型号={o["model"]}')
        print(f'{"":<12}原标题: {o["item_title"]}')


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        dry_run()
    else:
        main()
