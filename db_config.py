"""MySQL 连接配置：真口令只放本机未跟踪的 db_secret.py，仓库里只有占位模板。

取值优先级：
1. db_secret.DB_CONF —— 本机私有文件，克隆仓库后复制 db_secret.example.py 得到；
2. 环境变量 XIANYU_DB_* —— 适合换机器/临时跑，不用建文件；
3. 都没有则直接报错并说明怎么建，不静默回落到任何默认口令。
"""

import os

REQUIRED_KEYS = ("host", "port", "user", "password", "database", "charset")


def _from_secret_file():
    try:
        import db_secret
    except ImportError:
        return None
    conf = getattr(db_secret, "DB_CONF", None)
    return dict(conf) if conf else None


def _from_env():
    if not os.environ.get("XIANYU_DB_PASSWORD"):
        return None
    return {
        "host": os.environ.get("XIANYU_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("XIANYU_DB_PORT", "3306")),
        "user": os.environ.get("XIANYU_DB_USER", "root"),
        "password": os.environ["XIANYU_DB_PASSWORD"],
        "database": os.environ.get("XIANYU_DB_NAME", "neon_ledger"),
        "charset": os.environ.get("XIANYU_DB_CHARSET", "utf8mb4"),
    }


def load_db_conf():
    conf = _from_secret_file() or _from_env()
    if conf is None:
        raise RuntimeError(
            "缺少 MySQL 连接配置：把 db_secret.example.py 复制为 db_secret.py 并填入真实口令，"
            "或设置环境变量 XIANYU_DB_PASSWORD。"
        )
    missing = [k for k in REQUIRED_KEYS if not conf.get(k)]
    if missing:
        raise RuntimeError("MySQL 配置缺字段：%s" % ", ".join(missing))
    conf["port"] = int(conf["port"])
    return conf


DB_CONF = load_db_conf()
