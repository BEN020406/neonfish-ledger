# 霓虹鱼：抓单存储 SQLite 化 + 一键安装器

日期：2026-09-27　状态：待他审
前置决定（已烤清）：目标是"自己换机能装 + 别人下载也能装"；安装器只做环境搭建与检测提示，
不代装数据库；形态是 `setup.bat` 入口 + 命令行进度；**单后端 SQLite**，不做 MySQL 适配层。

## 1. 为什么换

抓单链路只用到 5 个存储动作（建表、插一条、查待核对、查已导入 order_id、取连接），
MySQL 真正有价值的部分——多客户端并发、远程访问、`JSON_*` 函数、JOIN——一行都没用上。
代价却是：装机必须先有一个数据库服务、要管口令、换机要重建库。

本次顺带修掉三个已核实的问题：
- `import_to_mysql.py` 未被 git 跟踪，因此**保密闸门扫不到它**，而它里面写死着明文口令
  （`password="root123"`）。方向还与主链路相反（把 `data.json` 灌进 `records` 表），功能已被
  `xianyu_review.py` 取代 → 删除。
- 口令是 4 位纯数字时，闸门的真值反查探针会撞上文档里当成本示例的同一个数字（已实测发生一次），
  失去区分力。存储层不再有口令，这个失败模式一并消失。
- `neon_ledger.records` 表来历不明的问题结案：就是上面那个脚本建的。

## 2. 目标与非目标

目标
1. `orders.db`（SQLite，仓库根目录，不进版本控制）成为抓单/填入的唯一存储；`pymysql` 从依赖里消失。
2. 现有 31 行历史订单迁过去，一条不丢、一条不多，且核对过程可复现。
3. `setup.bat` 双击可用：建 venv → 装依赖 → 下 Chromium → 跑自检；换机后不需要再问"该装什么"。
4. 自检表四行真做（起服务并发请求、图片缺件统计、orders.db 可读、Chromium 在不在），
   且每一项都能失败——不写只会在成功时说话的探测。

非目标
- 不做 MySQL 适配器、不打包 exe、不加图形安装向导、不改前端 `index.html`、不动 `data.json` 结构。
- `launcher.py` 不改：它只负责拉起三个脚本，跟存储后端无关。
- 不替他决定 MySQL 里那 31 行的去留：迁移只读导出，删不删库他自己定。
- 不替他决定要不要保留 MySQL 服务（他练 SQL 还在用，只是这套工具不再依赖它）。

## 3. 存储层设计

新文件 `orders_db.py`，只暴露 5 个函数，`xianyu_scraper.py` / `xianyu_review.py` 改成只依赖它：

| 函数 | 替代掉现在的 |
| --- | --- |
| `connect()` | `pymysql.connect(**DB_CONF)` |
| `ensure_schema()` | `CREATE_TABLE_SQL` |
| `insert_order(order) -> "new" \| "dup"` | `INSERT ...` + `except pymysql.err.IntegrityError` |
| `fetch_orders_raw()` | `_ORDERS_SQL` + `DictCursor` |
| `imported_order_ids()` | 已导入标记查询 |

Schema 映射（列名**全部保持不变**，`xianyu_review.py` 的取值键与迁移脚本都因此零转换）：

| MySQL | SQLite | 备注 |
| --- | --- | --- |
| `id INT AUTO_INCREMENT PRIMARY KEY` | `id INTEGER PRIMARY KEY AUTOINCREMENT` | |
| `order_id VARCHAR(64) UNIQUE` | `order_id TEXT UNIQUE NOT NULL` | 去重仍靠唯一约束 + 捕获 `sqlite3.IntegrityError` |
| `price DECIMAL(10,2)` | `price REAL` | 读侧已经过 `_to_float`，口径不变 |
| `trade_type ENUM('sold','bought')` | `TEXT NOT NULL DEFAULT 'sold' CHECK(trade_type IN ('sold','bought'))` | 约束保住，枚举语义不丢 |
| `order_date DATETIME` | `TEXT`（ISO `'YYYY-MM-DD HH:MM:SS'`） | 见下方"必须显式格式化" |
| `images JSON` / `raw_data JSON` | `TEXT` | 代码本来就 `json.dumps` 成字符串，读侧未用 JSON 函数 |
| `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | 同名，SQLite 支持 | |

两个容易写错的点，明确下来：
- **日期不靠 sqlite3 适配器**。`xianyu_scraper._parse_date()` 返回 `datetime` 对象，Python 3.12 起
  默认 date/datetime 适配器已废弃（他本机是 3.13），所以写入前必须在 `orders_db` 里显式
  `strftime("%Y-%m-%d %H:%M:%S")`，`NULL` 保持 `NULL`。读出来是字符串，`xianyu_review` 里
  凡按 `datetime` 用 `order_date` 的地方都要跟着改成"先解析再格式化"——实施时逐个核实，不许假设。
- **`ORDER BY order_date DESC, id DESC` 在 TEXT 上仍成立**，因为 ISO 格式字典序即时间序；
  迁移时要断言迁移前后同一排序下的 order_id 序列一致，作为回归证据。

## 4. 退役的东西

删文件：`import_to_mysql.py`、`db_config.py`、`db_secret.example.py`；他本机 `db_secret.py` 由他自己删
（含真口令，我不碰）。`.gitignore` 去掉 `/db_secret.py`、`/xianyu_cookies.json` 保留，
新增 `/orders.db`（真实经营数据，和 `data.json` 同级敏感）。`requirements.txt` 去掉 `pymysql` 那行。

保密闸门 `tests/test_repo_secrets.py` 相应调整，但**要求不降**：
- 保留：入库文件明文口令赋值扫描（防以后有人图省事写回口令）、打包清单必须真被跟踪。
- 删掉：两条 MySQL 专用检查（`db_secret.py` 被忽略、真值反查）——口令层不存在了。
- 新增：`orders.db` 出现在 `git ls-files` 里就红，且这条要能用变异测试证伪
  （临时 `git add` 一个假 `orders.db` 必须变红，撤销后复绿）。
- `MUST_BE_TRACKED` 补 `installer.py`、`selfcheck.py`、`setup.bat`。

## 5. 迁移

`migrate_mysql_to_sqlite.py`（一次性，跑完即从仓库删）：
1. 口令只从环境变量 `XIANYU_MYSQL_PASSWORD` 读，绝不落盘、绝不出现在命令行参数里。
2. 先把 MySQL 里 31 行原样导出成 `orders_backup_<日期>.json`（迁移失败可回退）。
3. 写 `orders.db`，逐行 `insert_order`，统计 new/dup。
4. 断言：两侧行数相等、`order_id` 集合相等、按 `order_date DESC, id DESC` 排出的 order_id 序列相等、
   `price` 与 `images`/`raw_data` 的 JSON 解析结果相等。任一不等就退出码非 0 且不删任何东西。

## 6. 安装器

- `setup.bat`：唯一入口，双击即可。只做一件事——找 Python、拉起 `installer.py`，出错时把窗口留住。
- `installer.py`：步骤化，每步打 `[ok]/[FAIL]` 与一行"下一步你该做什么"：
  1) Python ≥ 3.10（实机 3.13.12）；2) 建 `.venv` 并升级 pip；3) `pip install -r requirements.txt`；
  4) `playwright install chromium`（唯一要联网的大件，失败要指名是网络/代理问题）；
  5) `python -m compileall` 过一遍语法；6) 跑 `selfcheck.py`。
- `selfcheck.py`（也可 `--check` 单独调用）四行：
  1. **台账**：子进程起后端 → 真发一个 `/api/items` 请求 → 断言返回是 JSON 数组且条数 > 0。
     注意 8765 上若已有后端在跑，`app_standalone.py` 是"复用不新开"的语义，所以自检用**独立端口**，
     别把"端口被占"误报成失败——这是他真实会遇到的场景。
  2. **图片**：扫 `data.json` 每条 `images` 路径查盘，报"引用 N 张、缺 M 张"，M>0 时提示换机要拷 `images/`。
  3. **抓单存储**：`orders.db` 能 `SELECT count(*)`，并报告条数；文件不存在则报"未抓单或未迁移"（不算失败）。
  4. **浏览器内核**：用 Playwright 的 API 拿 chromium 可执行路径并判存在，不猜 `ms-playwright` 目录名。
- README：把"配 MySQL 口令"整节换成"双击 `setup.bat`" + "换机要搬走的 4 个东西"
  （`data.json`、`catalog.json`、`images/`、`orders.db`；`xianyu_cookies.json`/`.browser_data/` 可选搬，
  搬了免扫码），并说明抓取失败最常见原因是代理而不是代码。

## 7. 验收（必须真跑，不接受"应该能行"）

1. `python -X utf8 -m pytest tests -q`：除 4 条既有的"写死记录数"漂移探针外全绿。
2. 迁移四条断言全过，并打印前后对照表。
3. `python xianyu_review.py --dry-run`：从 `orders.db` 读到 31 行并按日期分组，标题拆分结果与
   MySQL 版逐条一致（拿迁移前导出的 JSON 做基准比对）。
4. `python -X utf8 installer.py` 在全新 `.venv` 里跑通；**不动他现在用的系统 Python 环境**。
5. `selfcheck.py` 四行都能失败：逐项人为制造坏条件（关掉后端、改一个不存在的图片路径、
   重命名 `orders.db`、把 playwright 指向空目录），确认每行会红并给出可执行的下一步。
6. `git status` 干净：`orders.db` 与被删的 MySQL 文件都不在跟踪列表里；`gh-pack` 快照重建时不含 `orders.db`。

## 8. 风险与处置

- 那 31 行是唯一副本 → 先导 JSON 备份再迁（第 5 节第 2 步），迁移脚本幂等、不 DROP。
- `orders.db` 成了新的敏感载体 → 靠 gitignore + 新增闸门断言（第 4 节），而不是靠"我记得别提交"。
- 前端/账本零改动，台账那条线不受影响：`app_standalone.py` 从头到尾没 import 过 pymysql（已核实）。
- 万一以后真要多端共享订单：那时再谈服务端数据库，不在本次范围内留半成品开关。
