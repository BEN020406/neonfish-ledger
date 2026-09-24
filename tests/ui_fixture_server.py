"""账本前端的隔离实例：8790 端口 + fixture 数据，真实 data.json 完全不参与。

    python -X utf8 tests/ui_fixture_server.py              # 3 笔订单
    LEDGER_FIXTURE=empty python -X utf8 tests/ui_fixture_server.py

为什么要它：账本前端没有测试框架，而真实账本（127.0.0.1:8765 那份）绝对不能当试验品。
这里复用同一个 app_standalone 后端，只把 DATA_FILE / IMAGES_DIR 指向 mkdtemp 里的副本，
所以浏览器在那上面怎么点、怎么写都伤不到用户的数据。
"""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WHICH = os.environ.get('LEDGER_FIXTURE', 'orders')
FIXTURE = os.path.join(ROOT, 'tests', 'fixtures',
                       'ledger_empty_orders.json' if WHICH == 'empty' else 'ledger_with_orders.json')
PORT = int(os.environ.get('LEDGER_FIXTURE_PORT', '8790'))

import app_standalone as m  # noqa: E402


def main():
    workdir = tempfile.mkdtemp(prefix='ledger-fixture-')
    data_file = os.path.join(workdir, 'data.json')
    shutil.copyfile(FIXTURE, data_file)
    m.DATA_FILE = data_file
    m.IMAGES_DIR = os.path.join(workdir, 'images')
    os.makedirs(m.IMAGES_DIR, exist_ok=True)

    server = m.LedgerServer(('127.0.0.1', PORT), m.APIHandler)
    print('fixture backend on http://127.0.0.1:%d  data=%s' % (PORT, data_file), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()
