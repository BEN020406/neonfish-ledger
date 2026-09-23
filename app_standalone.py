"""
NO_object丰收 · NEON LEDGER — Standalone Desktop App
With Ollama AI Agent + Image Management
"""
import http.server
import json
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


# ─── Data helpers ───

def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data):
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, DATA_FILE + '.bak')
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _to_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
            self.send_json(load_data())
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
                idx = int(parts[3])
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
                idx = int(parts[3])
                self.handle_delete_record(idx)
            elif len(parts) == 6 and parts[4] == 'images':
                idx = int(parts[3])
                filename = parts[5]
                self.handle_delete_image(idx, filename)
            else:
                self.send_json({'ok': False, 'error': 'invalid path'}, 400)
        else:
            self.send_json({'ok': False, 'error': 'not found'}, 404)

    # ─── Record CRUD ───

    def handle_add_record(self):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        data = load_data()
        record = {
            'brand': body.get('brand', ''),
            'model': body.get('model', ''),
            'cost': float(body.get('cost', 0)),
            'sell': float(body.get('sell', 0)),
            'sn': body.get('sn', ''),
            'accessory': body.get('accessory', ''),
            'accessory_price': _norm_price(body.get('accessory_price', '')),
            'extra_price': _norm_price(body.get('extra_price', '')),
            'images': body.get('images', []),
        }
        data.append(record)
        save_data(data)
        self.send_json({'ok': True, 'record': record})

    def handle_update_record(self, idx):
        body = self.read_body()
        if not body:
            self.send_json({'ok': False, 'error': 'empty body'}, 400)
            return
        data = load_data()
        if 0 <= idx < len(data):
            r = data[idx]
            r['brand'] = body.get('brand', r.get('brand', ''))
            r['model'] = body.get('model', r.get('model', ''))
            r['cost'] = float(body.get('cost', r.get('cost', 0)))
            r['sell'] = float(body.get('sell', r.get('sell', 0)))
            r['sn'] = body.get('sn', r.get('sn', ''))
            r['accessory'] = body.get('accessory', r.get('accessory', ''))
            acc_price = body.get('accessory_price', r.get('accessory_price', ''))
            r['accessory_price'] = _norm_price(acc_price)
            extra = body.get('extra_price', r.get('extra_price', ''))
            r['extra_price'] = _norm_price(extra)
            if 'images' in body:
                r['images'] = body['images']
            save_data(data)
            self.send_json({'ok': True, 'record': r})
        else:
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)

    def handle_delete_record(self, idx):
        data = load_data()
        if 0 <= idx < len(data):
            deleted = data.pop(idx)
            for img in deleted.get('images', []):
                img_path = os.path.join(IMAGES_DIR, img)
                if os.path.exists(img_path):
                    os.remove(img_path)
            save_data(data)
            self.send_json({'ok': True, 'deleted': deleted})
        else:
            self.send_json({'ok': False, 'error': 'index out of range'}, 404)

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
                if 0 <= idx < len(data):
                    if 'images' not in data[idx]:
                        data[idx]['images'] = []
                    data[idx]['images'].append(filename)
                    save_data(data)
            except (ValueError, KeyError):
                pass

        self.send_json({'ok': True, 'filename': filename, 'url': f'/api/images/{filename}'})

    def handle_delete_image(self, idx, filename):
        data = load_data()
        if 0 <= idx < len(data):
            r = data[idx]
            images = r.get('images', [])
            if filename in images:
                images.remove(filename)
                r['images'] = images
                save_data(data)
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

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            raw = self.rfile.read(length)
            return json.loads(raw.decode('utf-8'))
        return None

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
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
