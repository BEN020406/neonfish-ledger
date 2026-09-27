"""仓库内容的保密闸门：打包上云之前必须过的门。

换成 SQLite 之后敏感物换了一批：MySQL 配置层（db_config / db_secret / import_to_mysql）
整体退役，真实订单落进 orders.db 与迁移备份 JSON。闸门跟着改口，但要求不降低 ——
下面四类检查一类都没少，只是把探针从「MySQL 口令」扩到「订单原文」：
1. 该入库的必须真在版本控制里 —— 否则"打包三个项目"是句空话，别人 clone 下来缺一大半；
   只扫 git ls-files 的话，脚本被 .gitignore 挡住时口令测试会 0 命中假通过，所以这里连"已跟踪"一起断言。
   反向再加一条：退役掉的 MySQL 配置层不许复活，含从未被跟踪、闸门历来扫不到的 import_to_mysql.py。
2. 入库文件里不许有明文数据库口令赋值。
3. 形状检查能被绕开（换个变量名、拼接一下就不算口令赋值了），所以留一道真值反查：
   拿本机 db_secret.py 里的真实口令当探针扫全仓，命中即红。这条不依赖命名习惯。
   配置层虽然退役，口令本身在他机器上仍是活凭据（MySQL 还在跑），所以探针不撤。
4. 真物反查：真实订单库一旦被跟踪（git add -f 就能做到）即红；
   口令文件与订单原文所在路径必须始终被 .gitignore 挡住，否则一次 git add -A 就推上云。
"""

import importlib.util
import re
import subprocess

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 打包清单里必须进仓库的文件（历史上被 .gitignore 挡过）
MUST_BE_TRACKED = [
    "launcher.py",            # 霓虹鱼启动器
    "neon_fish_logo.png",     # 启动器的 logo 素材，缺了就只剩文字
    "app_standalone.py",      # NO_object丰收 账本
    "index.html",
    "data.json",
    "xianyu_scraper.py",      # 闲鱼抓单
    "xianyu_review.py",
    "xianyu_review.html",
    "orders_db.py",             # 抓单存储层，缺它两个脚本都起不来
    "selfcheck.py",             # 体检表
    "installer.py",             # 装机步骤
    "setup.bat",                # 双击入口
]

# 换成 SQLite 后就不该存在的东西。含 import_to_mysql.py：它从未被跟踪，
# 明文口令根本不在本文件扫描范围内 —— 那是个盲区，现在用断言堵死复活路。
MUST_NOT_EXIST = ["db_config.py", "db_secret.example.py", "import_to_mysql.py"]

# 本机可以留着、但永远不能进仓库的文件。备份那条写的是通配名而非今天这份真实文件，
# 断言的是 .gitignore 规则本身，不是"某个碰巧存在的文件恰好被挡住"。
# .venv 那条写的是树里的一个路径：installer.py 每次真装都会在本目录长出这棵树，
# 漏掉这条规则的话，一次 git add -A 就把整个 site-packages 推上云。
MUST_BE_IGNORED = ["db_secret.py", "orders.db", "orders_backup_2099-12-31.json",
                   ".venv/Lib/site-packages/not-for-git.py"]

SCAN_SUFFIX = re.compile(r"\.(py|html|js|json|md|txt|ya?ml|env|ini|cfg|sh|bat)$", re.I)

# 形如 password="xxx" / password: 'xxx'，引号内必须非空（空串本来就不算泄密）
QUOTED_ASSIGN = re.compile(r"""password\s*[=:]\s*(["'])([^"']+)\1""", re.I)

# 占位符按精确值放行：不做子串匹配，否则真口令里带个 "your" 就溜过去了
PLACEHOLDERS = {
    "your-password", "your-password-here", "your_db_password", "yourpassword",
    "changeme", "change-me", "placeholder", "replace-me", "replace_me",
    "replaceme", "xxx", "secret_here",
}

# 行内含这些标记说明口令是从环境变量/系统钥匙串取，不落地明文。
# 曾经还有个 db_secret 白名单，那是给已退役的 MySQL 配置层开的口子：留着等于
# 谁在行尾写个 db_secret 就能给明文口令放行，现在没这个场景了，删。
SAFE_MARKERS = ("os.environ", "getenv", "environ[", "keyring")

# 本文件自己也要入库，所以待测行只能运行时拼：源码里写出被引号包住的口令形状会被自家闸门判红
ASSIGN = "password" + "="


def _line(value):
    """password=<带引号的值>"""
    return ASSIGN + " " + repr(value)


def _raw(expr):
    """password=<表达式>，即从配置/环境取值的安全形态"""
    return ASSIGN + " " + expr


def _offender(line):
    """这行是否泄露明文口令。命中则返回口令值，否则 None。"""
    for m in QUOTED_ASSIGN.finditer(line):
        value = m.group(2)
        if value.strip().lower() in PLACEHOLDERS:
            continue
        if any(marker in line.lower() for marker in SAFE_MARKERS):
            continue
        return value
    return None


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _tracked_texts():
    for rel in _tracked():
        if not SCAN_SUFFIX.search(rel):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            yield rel, p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def _local_secret():
    """读本机未跟踪的 db_secret.py；不存在就返回 None（不是每个人的机器都有）。"""
    p = ROOT / "db_secret.py"
    if not p.is_file():
        return None
    spec = importlib.util.spec_from_file_location("db_secret_probe", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str((getattr(mod, "DB_CONF", {}) or {}).get("password") or "")


def test_gate_catches_synthetic_plaintext_and_ignores_safe_forms():
    """闸门必须先证明自己抓得住真口令，否则"0 命中"什么都说明不了。"""
    assert _offender(_line("synthetic-secret-abc")) == "synthetic-secret-abc"
    assert _offender("host=h, " + _line("p@ss w0rd") + ", db=1") == "p@ss w0rd"
    # 下面是抽取后应当长成的样子，误报会把闸门变成改不掉的噪音
    assert _offender(_line("your-password-here")) is None
    assert _offender(_line("changeme")) is None
    assert _offender(_raw('DB_CONF["password"]')) is None
    assert _offender(_raw('os.environ["X"]')) is None
    assert _offender(_raw('getenv("XIANYU_DB_PASSWORD")')) is None
    assert _offender(_line("")) is None
    assert _offender("query = " + repr("SELECT password FROM t")) is None
    # db_secret 白名单已随 MySQL 配置层退役删除：靠提一嘴配置文件名来给明文口令放行，行不通了
    assert _offender(_line("retired-marker-trick") + "  # read from " + "db_secret") == "retired-marker-trick"


def test_packaged_files_are_actually_tracked():
    tracked = set(_tracked())
    missing = [f for f in MUST_BE_TRACKED if f not in tracked]
    assert not missing, "这些文件没进版本控制，clone 下来是残缺的：%s" % (missing,)


def test_no_plaintext_db_password_in_tracked_files():
    offenders = []
    for rel, text in _tracked_texts():
        for n, line in enumerate(text.splitlines(), 1):
            if _offender(line) is not None:
                offenders.append("%s:%d" % (rel, n))
    assert not offenders, \
        "入库文件里有明文口令赋值（只允许读环境变量/系统钥匙串，或用占位符）：%s" % (offenders,)


# 配置层退役不等于口令作废：本机 db_secret.py 里那串仍然是活的 MySQL 凭据，
# 所以这条命名无关的真值反查保留，不随 db_config.py 一起撤。
def test_real_local_password_not_present_in_tracked_tree():
    """真值反查：不管变量叫什么名，本机真实口令出现在入库文件里就是红的。"""
    secret = _local_secret()
    if not secret or len(secret) < 4:
        pytest.skip("本机没有可用的 db_secret.py，反查探针缺失")
    hits = [rel for rel, text in _tracked_texts() if secret in text]
    assert not hits, "真实 MySQL 口令出现在入库文件里：%s" % (hits,)


def test_orders_db_is_never_tracked():
    """orders.db 是真实经营数据，与 data.json 同级敏感。

    刻意写成"若在跟踪列表里就必须被 .gitignore 挡住"，而不是简单断言它不在 ——
    前者在有人 git add -f 时会变红，后者只看错目录就永远绿。
    """
    tracked = set(_tracked())
    suspects = [f for f in tracked
                if f.endswith("orders.db") or f.endswith(".sqlite") or f.endswith(".sqlite3")]
    assert not suspects, "真实订单库被跟踪了：%s" % (suspects,)


def test_mysql_credential_layer_is_gone():
    leftovers = [f for f in MUST_NOT_EXIST if (ROOT / f).exists()]
    assert not leftovers, "MySQL 配置层残留：%s" % (leftovers,)


def test_retired_secret_and_order_data_stay_ignored():
    """本机留着、但永远不能进仓库的文件：一条 .gitignore 规则漏掉，一次 git add -A 就泄。

    用 git check-ignore 而不是"它恰好不在 ls-files 里"——前者断的是规则，后者断的是运气。
    """
    exposed = []
    for rel in MUST_BE_IGNORED:
        out = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT)
        if out.returncode != 0:
            exposed.append(rel)
    assert not exposed, "这些本地文件没被 .gitignore 挡住：%s" % (exposed,)
