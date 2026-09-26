"""仓库内容的保密闸门：打包上云之前必须过的门。

三个方向都要管住：
1. 抓单/填入脚本得真的在版本控制里 —— 否则"打包三个项目"是句空话，别人 clone 下来缺一大半；
   只扫 git ls-files 的话，脚本被 .gitignore 挡住时口令测试会 0 命中假通过，所以这里连"已跟踪"一起断言。
2. 入库文件里不许有明文数据库口令 —— 这三台脚本历史上就是因为这条被排除在仓库外的。
3. 形状检查能被绕开（换个变量名、拼接一下就不叫 password= 了），所以再加一道真值反查：
   拿本机 db_secret.py 里的真实口令当探针扫全仓，命中即红。这条不依赖命名习惯。
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
]

SCAN_SUFFIX = re.compile(r"\.(py|html|js|json|md|txt|ya?ml|env|ini|cfg|sh|bat)$", re.I)

# 形如 password="xxx" / password: 'xxx'，引号内必须非空（空串本来就不算泄密）
QUOTED_ASSIGN = re.compile(r"""password\s*[=:]\s*(["'])([^"']+)\1""", re.I)

# 占位符按精确值放行：不做子串匹配，否则真口令里带个 "your" 就溜过去了
PLACEHOLDERS = {
    "your-password", "your-password-here", "your_db_password", "yourpassword",
    "changeme", "change-me", "placeholder", "replace-me", "replace_me",
    "replaceme", "xxx", "secret_here",
}

# 行内含这些标记说明口令是从本地未跟踪配置或环境变量取，不落地明文
SAFE_MARKERS = ("db_secret", "os.environ", "getenv", "environ[", "keyring")

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
        "入库文件里有明文口令赋值（只允许读环境/本地未跟踪配置或占位符）：%s" % (offenders,)


def test_real_local_password_not_present_in_tracked_tree():
    """真值反查：不管变量叫什么名，本机真实口令出现在入库文件里就是红的。"""
    secret = _local_secret()
    if not secret or len(secret) < 4:
        pytest.skip("本机没有可用的 db_secret.py，反查探针缺失")
    hits = [rel for rel, text in _tracked_texts() if secret in text]
    assert not hits, "真实 MySQL 口令出现在入库文件里：%s" % (hits,)


def test_local_secret_file_is_ignored():
    out = subprocess.run(["git", "check-ignore", "-q", "db_secret.py"], cwd=ROOT)
    assert out.returncode == 0, "db_secret.py 没被 .gitignore 挡住，真口令会被推上云"
