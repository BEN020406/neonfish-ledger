"""MySQL 存储层已退役：静态断言，不等运行时炸出来。

前两节管代码（抓单与填单台不许再 import 那套），最后一节管"当前使用说明书"
（全部入库的 .md/.py）—— README 说错一句就是一次装机失败，而它恰恰长期停留在
"先建库、再拷口令模板"的年代。
"""

import inspect
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import orders_db
import xianyu_review
import xianyu_scraper

ROOT = Path(__file__).resolve().parents[1]

MYSQL_TOKENS = ["pymysql", "db_config", "DB_CONF", "CREATE_TABLE_SQL"]


@pytest.mark.parametrize("mod", [xianyu_scraper, xianyu_review],
                         ids=lambda m: m.__name__)
def test_no_mysql_left_in_source(mod):
    src = inspect.getsource(mod)
    leftovers = [t for t in MYSQL_TOKENS if re.search(r"\b%s\b" % t, src)]
    assert not leftovers, "%s 还在引用 MySQL：%s" % (mod.__name__, leftovers)


def test_scraper_insert_orders_reports_new_and_dup(tmp_path, monkeypatch):
    """脚本不再自己连库，只把订单交给存储层，并回报新增/重复各几条。"""
    monkeypatch.setattr(xianyu_scraper, "connect",
                        lambda: orders_db.connect(str(tmp_path / "orders.db")))
    o = {"order_id": "1", "item_title": "技嘉 B650M", "price": 300.0,
         "trade_type": "bought", "counterparty": "", "order_status": "",
         "order_date": None, "images": [], "raw_data": {}}
    assert xianyu_scraper.insert_orders([o, o]) == (1, 1)


def test_scraper_ensure_table_creates_readable_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setattr(xianyu_scraper, "connect",
                        lambda: orders_db.connect(str(db)))
    xianyu_scraper.ensure_table()
    conn = orders_db.connect(str(db))
    assert orders_db.count_orders(conn) == 0


def test_review_fetch_orders_gives_text_date(tmp_path, monkeypatch):
    """SQLite 读回来是字符串，填单台不能再对日期调 strftime。"""
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    orders_db.insert_order(conn, {
        "order_id": "77", "item_title": "微星 h610 坏板 出售", "price": 95.0,
        "trade_type": "bought", "counterparty": "乙", "order_status": "交易成功",
        "order_date": datetime(2026, 9, 19, 8, 30, 0), "images": [], "raw_data": {},
    })
    conn.commit()
    monkeypatch.setattr(xianyu_review, "connect", lambda: conn)
    monkeypatch.setattr(xianyu_review, "imported_order_ids", lambda: set())
    got = xianyu_review.fetch_orders()
    assert got[0]["order_date"] == "2026-09-19 08:30:00"
    assert got[0]["brand"] == "微星"
    assert got[0]["model"] == "h610 坏板"
    assert got[0]["imported"] is False


def test_review_marks_already_imported(tmp_path, monkeypatch):
    conn = orders_db.connect(str(tmp_path / "orders.db"))
    orders_db.ensure_schema(conn)
    orders_db.insert_order(conn, {"order_id": "77", "item_title": "微星 h610", "price": 95.0})
    conn.commit()
    monkeypatch.setattr(xianyu_review, "connect", lambda: conn)
    monkeypatch.setattr(xianyu_review, "imported_order_ids", lambda: {"77"})
    assert xianyu_review.fetch_orders()[0]["imported"] is True


# ══════════════ 仓库级：说明书不许再教已退役的那套 ══════════════
# 词表只收被 orders_db 顶替掉的具体名字，不收 MySQL 本身 ——
# "不再需要数据库服务"这类否定句是必须留着的信息。
RETIRED_MYSQL_SURFACE = ["neon_ledger", "db_config", "db_secret.example", "DB_CONF", "pymysql"]
DOC_SUFFIXES = (".md", ".py")

# 例外要显式列，且只准两类：
#   1) docs/superpowers/ 下的历史规格与计划：写清"当初是什么"正是它们的职责；
#   2) 定义这些禁令的守卫测试自己：不念出被禁的名字就禁不了。
# 想给 README 或某个脚本开后门？下面那条元测试直接红。
RETIRED_EXEMPT_DIRS = ("docs/superpowers/",)
RETIRED_EXEMPT_FILES = (
    "tests/test_orders_backend.py",   # 本文件：词表在这里
    "tests/test_repo_secrets.py",     # 保密闸门：MUST_NOT_EXIST 列的就是这些退役文件
    "tests/test_installer.py",        # setup.bat / requirements.txt 的死词清单
)


def _tracked_docs_and_sources():
    """git ls-files 里所有 .md / .py，读工作区内容（与 test_repo_secrets 同一口径）。"""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    rels = [ln.strip() for ln in out.splitlines() if ln.strip().endswith(DOC_SUFFIXES)]
    return [(rel, (ROOT / rel).read_text(encoding="utf-8"))
            for rel in rels if (ROOT / rel).is_file()]


def _retired_exempt(rel):
    return rel.startswith(RETIRED_EXEMPT_DIRS) or rel in RETIRED_EXEMPT_FILES


def test_no_retired_mysql_surface_outside_history():
    scanned = _tracked_docs_and_sources()
    # 先证明这道门扫得到东西：口径一变（改名、换 ls-files 用法）它就会静默空转
    assert scanned, "一个 .md/.py 都没扫到，这道守卫已经在空转"
    assert any(rel == "README.md" for rel, _ in scanned), "README.md 不在扫描范围内，空转"

    hits = []
    for rel, text in scanned:
        if _retired_exempt(rel):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            hits += ["%s:%d 提到 %r：%s" % (rel, n, tok, line.strip())
                     for tok in RETIRED_MYSQL_SURFACE if tok in line]
    assert not hits, ("这些地方还在把已退役的 MySQL 那套当成现存的来写"
                      "（历史记录请写进 docs/superpowers/ 下的规格或计划）：\n  "
                      + "\n  ".join(hits))


def test_retired_surface_exemptions_stay_narrow_and_needed():
    """管住例外清单本身：它既不能扩到说明书/代码，也不能留着已经不需要的口子。"""
    outside = [p for p in RETIRED_EXEMPT_FILES if not p.startswith("tests/")]
    assert not outside, "例外清单混进了非守卫文件：%s" % (outside,)
    assert not [d for d in RETIRED_EXEMPT_DIRS if not d.startswith("docs/superpowers/")], \
        "目录例外只准给 docs/superpowers/：%s" % (RETIRED_EXEMPT_DIRS,)

    texts = dict(_tracked_docs_and_sources())
    stale = [rel for rel in RETIRED_EXEMPT_FILES
             if not any(tok in texts.get(rel, "") for tok in RETIRED_MYSQL_SURFACE)]
    assert not stale, "这些守卫文件已经不含退役词，例外该删掉：%s" % (stale,)
