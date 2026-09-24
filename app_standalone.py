"""
NO_object丰收 · NEON LEDGER — Standalone Desktop App
With Ollama AI Agent + Image Management
"""
import http.server
import json
import hashlib
import math
import os
import shutil
import socket
import urllib.parse
import threading
import sys
import uuid
import mimetypes
import re
import traceback
import webview

try:
    import requests as _requests
except ImportError:
    _requests = None

PORT = 8765
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.8:27b-q4_K_M"
MAX_AGENT_ITER = 6

if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS
    APP_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BUNDLE_DIR

os.chdir(BUNDLE_DIR)
DATA_FILE = os.path.join(APP_DIR, 'data.json')
HTML_FILE = os.path.join(BUNDLE_DIR, 'index.html')
IMAGES_DIR = os.path.join(APP_DIR, 'images')
os.makedirs(IMAGES_DIR, exist_ok=True)

# 闲鱼订单上下文：只有导入路径会带这四个键，手动记账的 body 里一个都不该出现。
# 新增/更新两个写入端点和测试都读这一个常量，避免清单在某一侧悄悄漂移。
ORDER_CONTEXT_KEYS = ('source_order_id', 'order_date', 'item_title', 'order_paid')


# ─── Data helpers ───

class WriteConflict(Exception):
    """文件在本进程读取之后被别处改过，拒绝覆盖。"""


def file_stamp():
    """data.json 的版本 token：size + 内容 sha256 前 16 位，纯十六进制，可直接进 HTTP header。

    账本 255 条约 49 KB，一次 sha256 远小于一毫秒；比 (mtime_ns, size) 可靠 ——
    等长修改照样能发现，也不依赖文件系统时间精度。
    """
    if not os.path.exists(DATA_FILE):
        return ''
    with open(DATA_FILE, 'rb') as f:
        blob = f.read()
    return '%d-%s' % (len(blob), hashlib.sha256(blob).hexdigest()[:16])


def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data, stamp=None):
    """整份写盘。带 stamp 时会先校验文件仍是调用方读到的那一份，不是就抛 WriteConflict。

    检查必须排在 .bak 轮转之前：备份只有一代，被拒的写盘顺手把 .bak 冲掉就等于
    在一次没生效的保存里销毁了唯一的救命数据。stamp=None 保持旧的无条件语义，
    一次性脚本（回填）与 agent 工具走这条。
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if stamp is not None and file_stamp() != stamp:
        raise WriteConflict('data.json changed since it was read')
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, DATA_FILE + '.bak')
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(payload)
    os.replace(tmp, DATA_FILE)


def _money(value):
    """严格解析金额：空/None -> 0.0，数字 -> float，其他一律 None（调用方必须拒绝，别当成 0）。"""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_float(value, default=0.0):
    """宽松版本，只给读取路径（利润统计等）用；写入路径走 _money + 400。"""
    parsed = _money(value)
    return default if parsed is None else parsed


def _norm_price(value):
    if value in ("", None):
        return ""
    return float(value) or ""


# ─── Agent tools ───

def _agent_query_records(keyword=None, limit=20):
    data = load_data()
    if keyword:
        kw = str(keyword).lower()
        data = [
            r for r in data
            if kw in str(r.get("brand", "")).lower()
            or kw in str(r.get("model", "")).lower()
            or kw in str(r.get("sn", "")).lower()
        ]
    return data[:int(limit)]


def _agent_calc_profit():
    data = load_data()
    total_cost = sum(_to_float(r.get("cost")) for r in data)
    total_sell = sum(_to_float(r.get("sell")) for r in data)
    return {
        "total_records": len(data),
        "total_cost": total_cost,
        "total_sell": total_sell,
        "total_profit": total_sell - total_cost,
    }


def _agent_add_record(brand, model, cost, sell, sn="", extra_price="", accessory="", accessory_price=""):
    data = load_data()
    record = {
        "brand": brand,
        "model": model,
        "cost": float(cost),
        "sell": float(sell),
        "sn": sn,
        "accessory": accessory,
        "accessory_price": _norm_price(accessory_price),
        "extra_price": _norm_price(extra_price),
        "images": [],
    }
    data.append(record)
    # 故意不带 stamp：Agent 在一次 LLM 回合里可以连着调好几次 add_record，
    # 每次都重新 load_data()，加上冲突保护会把"再记一条"这种正常连续录入直接判成 409。
    # UI 侧的并发防护走 HTTP 那几个写端点，那里才有真正的两个进程互相覆盖风险。
    save_data(data)
    return record


AGENT_TOOLS = {
    "query_records": _agent_query_records,
    "calc_profit": _agent_calc_profit,
    "add_record": _agent_add_record,
}

AGENT_SYSTEM_PROMPT = """你是「NO_object丰收 · NEON LEDGER」的记账助手AI。
你可以调用以下工具操作账本数据：
- query_records(keyword, limit): 按关键词搜索记录（搜索品牌、型号、序列号），返回匹配的记录列表
- calc_profit(): 计算总利润（总成本、总售价、总利润、记录数）
- add_record(brand, model, cost, sell, sn, extra_price, accessory, accessory_price): 新增一条交易记录

你必须以JSON格式回复，格式为：
{"thought": "你的思考过程", "action": "工具名或answer", "params": {...}}

当你可以直接回答用户问题时，使用 action: "answer"，params: {"answer": "你的回答"}
当你需要查询数据才能回答时，先调用工具，再根据工具结果回答。
回答时用中文，简洁明了，可以包含数字和统计信息。"""

PARSE_SYSTEM_PROMPT = """你是一个数据提取助手。从用户输入的自然语言文本中提取硬件交易信息。
提取以下字段（如果文本中没有该字段，设为空字符串""）：
- brand: 品牌（如：微星、华硕、技嘉、AMD、INTER、金士顿等）
- model: 型号（如：B650m爆破弹、ROG B760等）
- cost: 成本价格（数字）
- sell: 售价（数字）
- sn: 序列号
- accessory: 配件名称（如：光威神策内存、西数SN580等）
- accessory_price: 配件价格（数字）
- extra_price: 额外价格（数字）

你必须只输出一个JSON对象，不要输出其他内容：
{"brand": "", "model": "", "cost": 0, "sell": 0, "sn": "", "accessory": "", "accessory_price": 0, "extra_price": 0}"""


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def _call_ollama(messages, model=MODEL):
    if _requests is None:
        return None
    try:
        resp = _requests.post(
            OLLAMA_URL,
            json={"model": model, "messages": messages, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except Exception as e:
        return None


def _agent_react_loop(user_input):
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]
    steps = []
    for _ in range(MAX_AGENT_ITER):
        raw = _call_ollama(messages)
        if raw is None:
            return "无法连接到 Ollama，请确认 Ollama 正在运行。", steps

        try:
            obj = _parse_json(raw)
        except (ValueError, json.JSONDecodeError):
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "你的输出不是合法JSON，请重新只输出JSON。"})
            steps.append({"type": "error", "message": "JSON解析失败，重试中..."})
            continue

        action = obj.get("action")
        params = obj.get("params") or {}
        thought = obj.get("thought", "")

        if thought:
            steps.append({"type": "thought", "message": thought})

        if action == "answer":
            return params.get("answer", ""), steps

        if action not in AGENT_TOOLS:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"没有工具 {action}，可用工具：{', '.join(AGENT_TOOLS)}"})
            steps.append({"type": "error", "message": f"未知工具: {action}"})
            continue

        try:
            result = AGENT_TOOLS[action](**params)
        except Exception as e:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"工具 {action} 调用失败：{e}"})
            steps.append({"type": "error", "message": f"工具调用失败: {e}"})
            continue

        steps.append({"type": "tool", "tool": action, "params": params, "result": result})
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"工具结果：{json.dumps(result, ensure_ascii=False)}"})

    return "抱歉，我暂时没想清楚，请换个说法试试。", steps


def _smart_parse(text):
    messages = [
        {"role": "system", "content": PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    raw = _call_ollama(messages)
    if raw is None:
        return None
    try:
        return _parse_json(raw)
    except (ValueError, json.JSONDecodeError):
        return None


# ─── Multipart parser ───

def _parse_multipart(content_type, body):
    boundary = None
    for part in content_type.split(';'):
        part = part.strip()
        if part.startswith('boundary='):
            boundary = part[len('boundary='):]
            break
    if not boundary:
        return None

    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]

    delimiter = ('--' + boundary).encode('utf-8')
    parts = body.split(delimiter)

    result = {}
    for part in parts[1:]:
        if part.startswith(b'--'):
            break
        part = part.strip(b'\r\n')
        if not part:
            continue

        header_end = part.find(b'\r\n\r\n')
        if header_end == -1:
            continue

        headers_raw = part[:header_end].decode('utf-8', errors='replace')
        content = part[header_end + 4:]
        if content.endswith(b'\r\n'):
            content = content[:-2]

        name = None
        filename = None
        for header_line in headers_raw.split('\r\n'):
            if header_line.lower().startswith('content-disposition:'):
                for param in header_line.split(';')[1:]:
                    param = param.strip()
                    if param.startswith('name='):
                        name = param[5:].strip('"')
                    elif param.startswith('filename='):
                        filename = param[9:].strip('"')

        if name:
            result[name] = {
                'content': content,
                'filename': filename,
            }

    return result


# ─── HTTP Handler ───

class APIHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/api/data':
            # 先取 stamp 再 load：万一外部写正好插在两者之间，客户端拿到的是
            # 「数据新、token 旧」，下一次写盘会被判 409 —— 虚警但安全。
            # 反过来（先 load 再取 stamp）会把「数据旧、token 新」发出去，
            # 旧视图照样能盖掉新数据，恰好放过这个头要拦的那次事故。
            stamp = file_stamp()
            self.send_json(load_data(), extra_headers={'X-Ledger-Stamp': stamp})
        elif path == '/' or path == '/index.html':
            self.send_html()
        elif path.startswith('/api/images/'):
            filename = path.split('/')[-1]
            self.serve_image(filename)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/api/data':
            self.handle_add_record()
        elif path == '/api/split_record':
            self.handle_split_record()
        elif path == '/api/chat':
            self.handle_chat()
        elif path == '/api/smart-parse':
            self.handle_smart_parse()
        elif path == '/api/upload':
            self.handle_upload()
        else:
            self.send_json({'ok': False, 'error': 'not found'}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/data/'):
            parts = parsed.path.split('/')
            if len(parts) == 4:
                idx = self.read_index(parts)
                if idx is not None:
                    self.handle_update_record(idx)
            else:
                self.send_json({'ok': False, 'error': 'invalid path'}, 400)
        else:
            self.send_json({'ok': False, 'error': 'not found'}, 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith('/api/data/'):
            parts = path.split('/')
            if len(parts) == 4:
                idx = self.read_index(parts)
                if idx is not None:
                    self.handle_delete_record(idx)
            elif len(parts) == 6 and parts[4] == 'images':
                idx = self.read_index(parts)
                if idx is not None:
                    self.handle_delete_image(idx, parts[5])
            else:
                self.send_json({'ok': False, 'error': 'invalid path'}, 400)
        else:
            self.send_json({'ok': False, 'error': 'not found'}, 404)

    # ─── Record CRUD ───

    def reject_bad_money(self, body):
        """已发送但既非空串也非数字的 cost/sell 直接 400，一条都不写盘。返回 True 表示已回复。"""
        for key in ('cost', 'sell'):
            if key in body and _money(body[key]) is None:
                self.send_json({'ok': False, 'error': 'invalid %s: %r' % (key, body[key])}, 400)
                return True
        return False

    def reject_bad_images(self, body):
        """images 出现了就必须是「每个元素都是字符串的数组」，否则 400、一条都不写。

        52579b6 之后 images 会透传进行内缩略图 / 图片查看器 / 弹窗预览三处渲染，
        非字符串元素让 imgs[0].startsWith 抛错、整表渲染崩；整值是字符串则让
        handle_delete_image 的 list.remove 变成 str.remove 直接 500。渲染崩是落盘脏数据的后果，
        只能拦在写入侧。省略这个键不算违规（新增按空数组、更新按"不动这一栏"）。
        """
        if 'images' in body:
            value = body['images']
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                self.send_json({'ok': False, 'error': 'images must be an array of strings'}, 400)
                return True
        return False

    def handle_add_record(self):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        # 一份基准贯穿"校验客户端 → load → 复查 → 写盘"：中间任何一次重新采样都可能采到
        # 外部写之后的新账本，那时客户端的 If-Match 已经校验在旧版本上，守卫就白过一次。
        stamp = file_stamp()
        if self.reject_if_client_stale(stamp):
            return
        data = load_data()
        # 复查排在请求体校验之前：视图已经过期时，400 的语义（"你请求写坏了，改好再发"）
        # 会让前端留着这份旧表单，下一次写就作用在另一条记录上；只有 409 能纠正客户端。
        if file_stamp() != stamp:
            self.handle_write_conflict()
            return
        if self.reject_bad_money(body):
            return
        if self.reject_bad_images(body):
            return
        record = {
            'brand': body.get('brand', ''),
            'model': body.get('model', ''),
            'cost': _money(body.get('cost', '')),
            'sell': _money(body.get('sell', '')),
            'sn': body.get('sn', ''),
            'accessory': body.get('accessory', ''),
            'accessory_price': _norm_price(body.get('accessory_price', '')),
            'extra_price': _norm_price(body.get('extra_price', '')),
            'images': body.get('images', []),
        }
        for key in ORDER_CONTEXT_KEYS:
            if key in body:
                record[key] = body[key]
        data.append(record)
        try:
            save_data(data, stamp)
        except WriteConflict:
            self.handle_write_conflict()
            return
        self.send_json({'ok': True, 'record': record, 'index': len(data) - 1})

    def handle_update_record(self, idx):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        # 一份基准贯穿校验与写盘，理由同 handle_add_record；下标是客户端给的，
        # 采到新版本再按旧下标动盘，改的就是另一条记录。
        stamp = file_stamp()
        if self.reject_if_client_stale(stamp):
            return
        data = load_data()
        # 复查排在下标校验之前：外部改动必须优先回 409，而不是被降级成 404/400。
        if file_stamp() != stamp:
            self.handle_write_conflict()
            return
        if not (0 <= idx < len(data)):
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)
            return
        if self.reject_bad_money(body):
            return
        if self.reject_bad_images(body):
            return
        r = data[idx]
        r['brand'] = body.get('brand', r.get('brand', ''))
        r['model'] = body.get('model', r.get('model', ''))
        r['cost'] = _money(body['cost']) if 'cost' in body else r.get('cost', 0)
        r['sell'] = _money(body['sell']) if 'sell' in body else r.get('sell', 0)
        r['sn'] = body.get('sn', r.get('sn', ''))
        r['accessory'] = body.get('accessory', r.get('accessory', ''))
        r['accessory_price'] = _norm_price(body.get('accessory_price', r.get('accessory_price', '')))
        r['extra_price'] = _norm_price(body.get('extra_price', r.get('extra_price', '')))
        for key in ORDER_CONTEXT_KEYS:
            if key in body:
                r[key] = body[key]
        if 'images' in body:
            r['images'] = body['images']
        try:
            save_data(data, stamp)
        except WriteConflict:
            self.handle_write_conflict()
            return
        self.send_json({'ok': True, 'record': r})

    def handle_split_record(self):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        idx = body.get('idx')
        parts = body.get('parts')
        if type(idx) is not int:
            self.send_json({'ok': False, 'error': 'idx must be an integer'}, 400)
            return
        if not isinstance(parts, list) or not parts:
            self.send_json({'ok': False, 'error': 'parts must be a non-empty array'}, 400)
            return

        updates = []
        for part in parts:
            if not isinstance(part, dict):
                self.send_json({'ok': False, 'error': 'each part must be an object'}, 400)
                return
            update = {}
            for key in ('brand', 'model'):
                value = part.get(key)
                if not isinstance(value, str) or not value.strip():
                    self.send_json({'ok': False, 'error': '%s must be a non-empty string' % key}, 400)
                    return
                try:
                    value.encode('utf-8')
                except UnicodeEncodeError:
                    self.send_json({'ok': False, 'error': '%s must be valid UTF-8 text' % key}, 400)
                    return
                update[key] = value.strip()
            if 'cost' in part:
                try:
                    cost = None if isinstance(part['cost'], bool) else _money(part['cost'])
                except OverflowError:
                    cost = None
                if cost is None or not math.isfinite(cost):
                    self.send_json({'ok': False, 'error': 'invalid cost'}, 400)
                    return
                update['cost'] = cost
            updates.append(update)

        # 校验客户端与最终保存必须用同一份版本，避免两次采样间删除导致下标错位。
        stamp = file_stamp()
        if self.reject_if_client_stale(stamp):
            return
        data = load_data()
        # 新版本的下标或来源错误也属于冲突，客户端必须丢弃旧输入后重拉。
        if file_stamp() != stamp:
            self.handle_write_conflict()
            return
        if not (0 <= idx < len(data)):
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)
            return
        record = data[idx]
        source_order_id = record.get('source_order_id')
        if not source_order_id or (isinstance(source_order_id, str) and not source_order_id.strip()):
            self.send_json({'ok': False, 'error': 'record must have a source_order_id'}, 400)
            return

        context = {key: record[key] for key in ORDER_CONTEXT_KEYS if key in record}
        updates[0].setdefault('cost', record.get('cost', 0))
        record.update(updates[0])
        indices = [idx]
        for update in updates[1:]:
            derived = {
                'brand': update['brand'], 'model': update['model'], 'cost': update.get('cost', 0),
                'sell': '', 'sn': '', 'accessory': '', 'accessory_price': '', 'extra_price': '',
                'images': [],
            }
            derived.update(context)
            indices.append(len(data))
            data.append(derived)
        try:
            save_data(data, stamp)
        except WriteConflict:
            self.handle_write_conflict()
            return
        self.send_json({'ok': True, 'indices': indices, 'order_paid': record.get('order_paid', '')})

    def handle_delete_record(self, idx):
        # 一份基准贯穿校验与写盘，理由同 handle_add_record：删除只认下标，
        # 校验通过后再采一次版本，等于允许"按旧下标删新账本里的另一条"。
        stamp = file_stamp()
        if self.reject_if_client_stale(stamp):
            return
        data = load_data()
        if file_stamp() != stamp:
            self.handle_write_conflict()
            return
        if not (0 <= idx < len(data)):
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)
            return
        deleted = data.pop(idx)
        try:
            save_data(data, stamp)
        except WriteConflict:
            # 图片文件留到写盘成功之后再删：冲突时 data.json 仍然引用着它们，
            # 先删文件就会在账本里留下一堆指向空气的缩略图。
            self.handle_write_conflict()
            return
        for img in deleted.get('images', []):
            img_path = os.path.join(IMAGES_DIR, img)
            if os.path.exists(img_path):
                os.remove(img_path)
        self.send_json({'ok': True, 'deleted': deleted})

    # ─── Image handling ───

    def handle_upload(self):
        content_type = self.headers.get('Content-Type', '')
        content_length = int(self.headers.get('Content-Length', 0))

        if 'multipart/form-data' not in content_type:
            self.send_json({'ok': False, 'error': 'expected multipart/form-data'}, 400)
            return

        body = self.rfile.read(content_length)
        parsed = _parse_multipart(content_type, body)
        if not parsed or 'file' not in parsed:
            self.send_json({'ok': False, 'error': 'no file field found'}, 400)
            return

        # 版本先查再落图：请求体已经读完（不读会把连接留成半截），所以客户端那份版本
        # 已经过期时，这里连图片文件都不必落盘就回 409。
        # 但别把它读成"永远不会产生孤儿文件"：落图之后还要复查版本（见下），
        # 那次拒绝和 save_data 抛的 WriteConflict 都发生在文件已经写盘之后，孤儿文件照样留下。
        # 上传走 multipart，Content-Type 不能动，If-Match 照样是普通请求头。
        stamp = file_stamp()
        if self.reject_if_client_stale(stamp):
            return

        file_info = parsed['file']
        original_name = file_info.get('filename', 'image.jpg')
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
            ext = '.jpg'

        filename = str(uuid.uuid4()) + ext
        filepath = os.path.join(IMAGES_DIR, filename)
        with open(filepath, 'wb') as f:
            f.write(file_info['content'])

        record_idx = parsed.get('record_idx')
        if record_idx:
            try:
                idx = int(record_idx['content'].decode('utf-8').strip())
                data = load_data()
                # 落图那几毫秒里外部完全可能改过账本：仍按请求开头那份基准判，
                # 否则客户端的旧下标会挂到新账本的另一条记录上。
                if file_stamp() != stamp:
                    self.handle_write_conflict()
                    return
                if 0 <= idx < len(data):
                    if 'images' not in data[idx]:
                        data[idx]['images'] = []
                    data[idx]['images'].append(filename)
                    save_data(data, stamp)
            except (ValueError, KeyError):
                pass
            except WriteConflict:
                # 图片文件此刻已经在盘上，只是没写进账本：留下的是一个没人引用的孤儿文件，
                # 比"账本指向一张不存在的图"轻得多，所以照样回 409 让前端重拉列表。
                self.handle_write_conflict()
                return

        self.send_json({'ok': True, 'filename': filename, 'url': f'/api/images/{filename}'})

    def handle_delete_image(self, idx, filename):
        # 一份基准贯穿校验与写盘，理由同 handle_add_record：旧下标在新账本里
        # 可能已经是另一条记录的图，先采版本再复查才能保住那条图。
        stamp = file_stamp()
        if self.reject_if_client_stale(stamp):
            return
        data = load_data()
        if file_stamp() != stamp:
            self.handle_write_conflict()
            return
        if 0 <= idx < len(data):
            r = data[idx]
            images = r.get('images', [])
            if filename in images:
                images.remove(filename)
                r['images'] = images
                try:
                    save_data(data, stamp)
                except WriteConflict:
                    # 还没写成就返回：磁盘上的图仍然被账本引用着，状态是自洽的。
                    self.handle_write_conflict()
                    return
                # 只有这条记录确实引用过它才轮到物理删除：文件名不在 images 里时
                # 这个文件很可能正被账本另一条记录引用着，无条件删就是一场静默的数据丢失。
                img_path = os.path.join(IMAGES_DIR, filename)
                if os.path.exists(img_path):
                    os.remove(img_path)
            self.send_json({'ok': True})
        else:
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)

    def serve_image(self, filename):
        safe_name = os.path.basename(filename)
        filepath = os.path.join(IMAGES_DIR, safe_name)
        if not os.path.exists(filepath):
            self.send_json({'ok': False, 'error': 'not found'}, 404)
            return

        mime, _ = mimetypes.guess_type(filepath)
        if not mime:
            mime = 'application/octet-stream'

        with open(filepath, 'rb') as f:
            content = f.read()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', len(content))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        self.wfile.write(content)

    # ─── AI endpoints ───

    def handle_chat(self):
        try:
            body = self.read_body()
            if not body or 'message' not in body:
                self.send_json({'ok': False, 'error': 'missing message'}, 400)
                return

            if _requests is None:
                self.send_json({'ok': False, 'error': 'requests library not installed'}, 500)
                return

            user_message = body['message']
            answer, steps = _agent_react_loop(user_message)
            self.send_json({'ok': True, 'answer': answer, 'steps': steps})
        except Exception as e:
            traceback.print_exc()
            self.send_json({'ok': False, 'error': str(e)}, 500)

    def handle_smart_parse(self):
        try:
            body = self.read_body()
            if not body or 'text' not in body:
                self.send_json({'ok': False, 'error': 'missing text'}, 400)
                return

            if _requests is None:
                self.send_json({'ok': False, 'error': 'requests library not installed'}, 500)
                return

            result = _smart_parse(body['text'])
            if result is None:
                self.send_json({'ok': False, 'error': 'parse failed'})
            else:
                self.send_json({'ok': True, 'parsed': result})
        except Exception as e:
            traceback.print_exc()
            self.send_json({'ok': False, 'error': str(e)}, 500)

    # ─── HTTP helpers ───

    def reject_if_client_stale(self, stamp=None):
        """客户端带的版本（If-Match）已经不是当前这份文件了：回 409，返回 True 让调用方别碰盘。
        可传入调用方固定的 stamp；省略时保持原来的即时采样行为。

        和 Task 2 的内部 stamp 是互补的两道闸，都要留着：
        - handler 里那份 stamp 只看住"我自己 load → save 这几毫秒"，防的是同一请求内的竞态；
        - 这里看的是"你这个页面是什么时候读的"，防的是账本窗口开了一早上、中途导入页写过账本，
          用户在停在旧数据的窗口里改一条并保存 —— 那时 stamp 那道闸必然是过的，只有版本对不上能发现。

        If-Match 缺席一律放行：导入页（8766）和 agent 从来不知道版本，把它变成必填会直接砍掉那两条路。
        """
        expected = self.headers.get('If-Match')
        if expected is not None and expected != (file_stamp() if stamp is None else stamp):
            self.handle_write_conflict()
            return True
        return False

    def handle_write_conflict(self):
        """所有写端点共用的冲突回复：HTTP 409 + ok:false + 固定的 error 串。

        契约的另一半已经落在前端（index.html 的 handleConflict，计划 Task 2b）：
        只按 status === 409 分支，重拉 /api/data、丢掉正在填的表单、提示用户重新编辑，
        既不回填也不重试。这里刻意不回任何数据，判断只看 status，error 只是给人看的。
        """
        self.send_json({'ok': False, 'error': 'data.json changed elsewhere, reloaded'}, 409)

    def read_index(self, parts):
        """路径里的记录下标；不是整数就自己发出 400 并返回 None，让调用方跳过派发。"""
        try:
            return int(parts[3])
        except ValueError:
            self.send_json({'ok': False, 'error': 'invalid index %r' % parts[3]}, 400)
            return None

    def read_body(self):
        """请求体里的 JSON 对象；坏 JSON 或不是对象的 JSON 都返回 None，让调用方回 400。"""
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode('utf-8'))
            except ValueError:
                # 坏 JSON 也要正常回 400，不能让异常把连接掐断（调用方按空 body 处理）。
                return None
            # 数组/数字这类合法但非对象的 JSON 同样会让 body.get 抛 AttributeError。
            return body if isinstance(body, dict) else None
        return None

    def send_json(self, data, status=200, extra_headers=None):
        """回一段 JSON。extra_headers 只加头、不动响应体形状：
        GET /api/data 必须继续返回裸数组（前端 parseItems(INITIAL) 与既有断言都按数组读），
        所以版本 token 只能走 X-Ledger-Stamp 头。"""
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self):
        with open(HTML_FILE, 'rb') as f:
            content = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))
        sys.stderr.flush()


class LedgerServer(http.server.ThreadingHTTPServer):
    # False keeps "one backend per port" deterministic instead of leaving it to
    # the OS default SO_REUSEADDR behavior, which varies across Windows builds.
    allow_reuse_address = False


def start_server():
    with LedgerServer(('127.0.0.1', PORT), APIHandler) as httpd:
        httpd.serve_forever()


def backend_is_live() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(('127.0.0.1', PORT)) == 0


def main():
    if backend_is_live():
        print(f'检测到 127.0.0.1:{PORT} 已有账本后端在跑，复用该后端，只新开窗口。')
    else:
        threading.Thread(target=start_server, daemon=True).start()

    webview.create_window(
        title='NO_object丰收 · NEON LEDGER',
        url=f'http://127.0.0.1:{PORT}',
        width=1280,
        height=860,
        min_size=(900, 600),
        resizable=True,
        easy_drag=False,
    )
    webview.start()


if __name__ == '__main__':
    main()
