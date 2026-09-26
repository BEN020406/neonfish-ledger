"""本机 MySQL 口令模板：复制成 db_secret.py 并填入真实值，别把真口令写回这个文件。

db_secret.py 已在 .gitignore 里，不会被提交；抓单与填入脚本都通过 db_config.load_db_conf()
读它，也可以改用环境变量 XIANYU_DB_PASSWORD 等而不建文件。
"""

DB_CONF = dict(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="your-password-here",
    database="neon_ledger",
    charset="utf8mb4",
)
