# 霓虹鱼 · 闲鱼小本账（NeonFish Ledger）

给大学生做小体量闲鱼生意用的记账工具：进价、卖价、配件成本一笔笔记下来，
利润、库存周转、哪个型号赚得多就都有了。抓订单 → 核对填单 → 记账统计三段可以整条跑，
也可以只开台账窗口手动记。

作者自己拿它记装机配件（主板、显卡、CPU），所以下面的示例数据和自动识别规则都偏硬件 —— 
记账本身与品类无关，卖书、卖球鞋、卖化妆品一样能用（差别见「能记哪些货」一节）。

```
闲鱼(goofish) ──xianyu_scraper.py──▶ orders.db（本地 SQLite，抓到的订单原文）
                                          │
                                   xianyu_review.py  (勾选/拆标题/改品牌型号成本)
                                          ▼
                                      data.json ◀──▶ app_standalone.py + index.html
                                                        （NO_object丰收 台账窗口）
```

| 组件 | 入口 | 干什么 |
| --- | --- | --- |
| 一键装机 | 双击 `setup.bat` | 拉起 `installer.py`：查 Python 版本 → 建 `.venv` → 装依赖 → 下 Chromium → 交给 `selfcheck.py` 出体检表 |
| 霓虹鱼启动器 | `python launcher.py` | tkinter 手绘的霓虹风格启动面板，三个按钮分别拉起下面两个脚本和台账窗口 |
| NO_object丰收 台账 | `python app_standalone.py` | stdlib `http.server` + pywebview 原生窗口，记进价/卖价/配件费，看利润、周转和品牌型号分布 |
| 闲鱼抓单 | `python xianyu_scraper.py` | Playwright 自动登录并翻页抓「我买到的」订单，写进本地 `orders.db` |
| 闲鱼数据填入 | `python xianyu_review.py` | 读 `orders.db`，按日期分组核对抓到的订单，拆分标题里的品牌/型号/成色，写入 `data.json` |

## 能记哪些货

**记账内核与品类无关**：任何「进价 → 卖出价 → 利润」的流水都能记，字段就是
品牌、型号、品类、进价、卖价、配件费、序列号、图片。

**两处自动化是按电脑硬件写死的**，换品类时它们认不出来，只会退化成手填：

- `cat_rules.py` 判品类靠芯片组（B650M/Z790…）、CPU 型号、SSD/内存/散热/显卡关键词，
  认不出的落「品类待确认」等你手选，不猜。
- `xianyu_review.py` 从闲鱼标题里拆品牌/型号/成色，规则同样是硬件向的。

所以卖书、卖球鞋能用，但品类和型号要你自己在台账里填；想让别人少改代码就能换赛道，
把这两处规则抽成配置是个明确的 TODO（`catalog.json` 已经是数据驱动的，不是障碍）。

## 环境

- Windows（台账与填入台用 pywebview 起原生窗口，只在 Windows 上跑过）
- Python 3.10 以上（`installer.py` 硬性检查这一点；作者本机 3.13，tkinter 要随解释器一起装好）
- 不需要 MySQL 或任何数据库服务：抓单落地就是本目录一个 `orders.db` 文件

```bat
:: 第一次用：双击 setup.bat，它查版本、建 .venv、装依赖、下 Chromium，最后出一张体检表
:: 已经装过再跑，已完成的步骤会自动跳过
```

同一件事的命令行版本，以及"只告诉我会做哪几步、一条命令都不执行"的版本：

```bat
python -X utf8 installer.py
python -X utf8 installer.py --dry-run
```

Chromium 是整条流程里唯一要联网的大件（`installer.py` 的 `browser` 步），失败基本是网络/代理问题；
只记账不抓单的话，这一步可以不管。装完的东西全在本目录 `.venv` 里，
`installer.py` 不写系统 Python，也不碰任何数据文件。

## 装什么、不装什么

抓单落地的存储现在是一个本地文件 `orders.db`（SQLite），**不再需要 MySQL 服务**。
原先那套 MySQL 的配置层、驱动依赖和口令模板一起退场了，所以装机流程里没有
"先配数据库口令"这一步 —— `requirements.txt` 里只剩 playwright / pywebview / requests / pillow。

只有两处例外要知道：

- 之前用 MySQL 跑过抓单的，历史订单还在原来那张 `xianyu_orders` 表里；本次迁移把它
  原样搬进 `orders.db`（列名一一对齐），搬迁脚本是一次性的，跑完即删。
- `orders.db` 里是订单原文，和 `data.json` 同级敏感，已进 `.gitignore`。仓库带的
  `data.example.json` 是脱敏样例，别拿它当真账本。

## 启动

```bat
:: setup.bat 只建环境、不激活；日常使用先挂上它建出来的那一个，否则用的还是系统 Python
.venv\Scripts\activate

:: 1) 抓单：首次会弹浏览器要求扫码，登录态存在 .browser_data/ 里可复用
python xianyu_scraper.py
python xianyu_scraper.py --login     :: 只验证/补登录态
python xianyu_scraper.py --debug     :: 导出原始接口响应，排查字段变动

:: 2) 核对填单：起本地服务并开窗口
python xianyu_review.py
python xianyu_review.py --dry-run    :: 只打印标题拆分结果，不起服务

:: 3) 台账
python app_standalone.py

:: 或者一个面板全搞定（三个按钮就是上面那三件事）
python launcher.py

:: 4) 随时复查这台机器：台账 / 图片 / 抓单库 / 浏览器内核 四行
python selfcheck.py
```

端口：台账 `8765`（已有后端在跑时会复用、只新开窗口），填入台 `8766-8776` 依次试探。
体检表的台账那一行刻意不去探 `8765`，它探自己那一个 `8865`：常驻着的台账会把这一行顶成假 ok。
自检也绝不替你起后端，所以后端没跑时那一行报 skip（skip 不算失败，退出码仍是 0），
其余三行报的是实际查过盘的结果。

## 数据与文件

| 文件 | 说明 |
| --- | --- |
| `data.json` | 账本本体，裸数组，**数组下标就是记录 id**（改序即改 id，所以只做追加和原地更新）。不进版本控制；首次运行没有它就是空账本，想先看效果把 `data.example.json` 复制一份过来 |
| `catalog.json` | 硬件知识库：品类 / 品牌 / 型号的规范名与别名，统计前先归一 |
| `index.html` | 台账前端，单文件原生 JS，无构建步骤；后端每次请求重读该文件，改完按 F5 即生效 |
| `orders.db` | 抓单落地库（SQLite）：抓到的订单原文先进这里，再由填入台核对进 `data.json`。默认就在本目录，想放别处设环境变量 `XIANYU_ORDERS_DB` |
| `orders_backup_*.json` | 一次性搬迁历史订单时自动导出的库内容备份（含订单原文），跑完就只是留底 |

不进版本控制的：`orders.db`、`orders_backup_*.json`、`xianyu_cookies.json`、`.browser_data/`、
`images/`（照片原件）、`debug_api_responses.json`（抓单原始响应转储，含订单原文）、`data.json`
（账本本体，里面有客户号段与真实进价卖价）、`data.json.bak`
（台账与填入台每次写盘前刷新的单代备份），以及 `setup.bat` 装出来的 `.venv/`。
`catalog.json` 是跟着仓库走的例外：它是硬件知识库，不含业务数据。
别顺手 `git add -A` 把上面那些本机文件一并带进快照 —— `tests/test_repo_secrets.py`
会因为你真加了 `data.json` 而变红。

`tests/test_repo_secrets.py` 是这道门：它既断言该入库的文件真在版本控制里（否则扫描是空转），
也断言入库文件里没有明文口令赋值、真实订单库永远进不了跟踪列表。

换机要搬的是这四样：`data.json`、`catalog.json`、`images/`、`orders.db`
（想免扫码登录再带上 `xianyu_cookies.json` 与 `.browser_data/`）。其中 `images/` 与 `orders.db`
被 `.gitignore` 挡着，clone 或 pull 都拿不到，只能整个目录拷过去；`data.json` 与 `catalog.json`
虽然跟仓库走，但最新的那一份只在你本机，覆盖之前先想清楚拷贝方向。只拷前两个的话，
记录里的图片会是空图，`selfcheck.py` 的图片那一行就是报这个的（它按 `data.json` 里的引用
逐个查盘，引用几张、缺几张都写出来）。

## 可选：录入辅助

表单里的「智能解析」和聊天走 `/api/chat`，转发到本机 Ollama（`http://localhost:11434/api/chat`）。
没起 Ollama 时这两个按钮会失败，其余功能不受影响。

## 测试

```bat
python -X utf8 -m pytest tests -q
```

后端接口、型号归并、拆分规则、抓单存储层的读写、装机步骤表、体检表那四行、上面那道保密闸门，
外加一条静态守卫 —— 全部入库的 `.md` / `.py` 不许再把已退役的那套当成现存的来写
（历史规格与计划除外，它们放在 `docs/superpowers/` 下）。少数用例把账本记录数写成了
断言（`test_migrate_cat.py` / `test_catalog.py` 等）：日常录入让数据变多之后它们会红，
那是故意留的漂移探针，先核对规格文档再改数字。
