# 霓虹鱼 · 闲鱼小本账（NeonFish Ledger）

给大学生做小体量闲鱼生意用的记账工具：进价、卖价、配件成本一笔笔记下来，
利润、库存周转、哪个型号赚得多就都有了。抓订单 → 核对填单 → 记账统计三段可以整条跑，
也可以只开台账窗口手动记。

作者自己拿它记装机配件（主板、显卡、CPU），所以下面的示例数据和自动识别规则都偏硬件 —— 
记账本身与品类无关，卖书、卖球鞋、卖化妆品一样能用（差别见「能记哪些货」一节）。

```
闲鱼(goofish) ──xianyu_scraper.py──▶ MySQL neon_ledger.xianyu_orders
                                          │
                                   xianyu_review.py  (勾选/拆标题/改品牌型号成本)
                                          ▼
                                      data.json ◀──▶ app_standalone.py + index.html
                                                        （NO_object丰收 台账窗口）
```

| 组件 | 入口 | 干什么 |
| --- | --- | --- |
| 霓虹鱼启动器 | `python launcher.py` | tkinter 手绘的霓虹风格启动面板，三个按钮分别拉起下面两个脚本和台账窗口 |
| NO_object丰收 台账 | `python app_standalone.py` | stdlib `http.server` + pywebview 原生窗口，记进价/卖价/配件费，看利润、周转和品牌型号分布 |
| 闲鱼抓单 | `python xianyu_scraper.py` | Playwright 自动登录并翻页抓「我买到的」订单，落 MySQL |
| 闲鱼数据填入 | `python xianyu_review.py` | 按日期分组核对抓到的订单，拆分标题里的品牌/型号/成色，写入 `data.json` |

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
- Python 3.13（tkinter 需随解释器装好）
- MySQL 8，本地一个 `neon_ledger` 库即可，表 `xianyu_orders` 由抓单脚本自己建

```bat
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 配 MySQL 口令

代码里不留明文口令：真口令放本机未跟踪的 `db_secret.py`，仓库里只有模板。

```bat
copy db_secret.example.py db_secret.py
:: 然后编辑 db_secret.py 里的 DB_CONF
```

不想建文件也可以走环境变量：`XIANYU_DB_PASSWORD`（可选 `XIANYU_DB_HOST` /
`XIANYU_DB_PORT` / `XIANYU_DB_USER` / `XIANYU_DB_NAME`）。两处都没有时
`db_config.py` 会直接报错说明缺什么，不会静默回落到某个默认口令。

`tests/test_repo_secrets.py` 是这道门：既断言三个组件真的在版本控制里（否则扫口令是空转），
也扫全部入库文件里的明文口令赋值，还会拿本机 `db_secret.py` 的真实口令当探针反查全仓。

## 启动

```bat
:: 1) 抓单：首次会弹浏览器要求扫码，登录态存在 .browser_data/ 里可复用
python xianyu_scraper.py
python xianyu_scraper.py --login     :: 只验证/补登录态
python xianyu_scraper.py --debug     :: 导出原始接口响应，排查字段变动

:: 2) 核对填单：起本地服务并开窗口
python xianyu_review.py
python xianyu_review.py --dry-run    :: 只打印标题拆分结果，不起服务

:: 3) 台账
python app_standalone.py

:: 或者一个面板全搞定
python launcher.py
```

端口：台账 `8765`（已有后端在跑时会复用、只新开窗口），填入台 `8766-8776` 依次试探。
`start.bat` 起的是另一个旧后端，别用它启动台账。

## 数据与文件

| 文件 | 说明 |
| --- | --- |
| `data.json` | 账本本体，裸数组，**数组下标就是记录 id**（改序即改 id，所以只做追加和原地更新）。本仓库随包带的 `data.json` 是几条脱敏样例（序列号与图片已清空），拿来覆盖成你自己的账本即可 |
| `catalog.json` | 硬件知识库：品类 / 品牌 / 型号的规范名与别名，统计前先归一 |
| `index.html` | 台账前端，单文件原生 JS，无构建步骤；后端每次请求重读该文件，改完按 F5 即生效 |
| `db_config.py` | MySQL 配置读取层（`db_secret.py` → 环境变量 → 报错） |

不进版本控制的：`db_secret.py`、`xianyu_cookies.json`、`.browser_data/`、`images/`（照片原件）、
`debug_api_responses.json`（抓单原始响应转储，含订单原文）、`data.json.bak`。
换机器时 `images/` 需要自己拷过去，否则记录里的图片路径会是空图。

## 可选：录入辅助

表单里的「智能解析」和聊天走 `/api/chat`，转发到本机 Ollama（`http://localhost:11434/api/chat`）。
没起 Ollama 时这两个按钮会失败，其余功能不受影响。

## 测试

```bat
python -X utf8 -m pytest tests -q
```

后端接口、型号归并、拆分规则、以及上面那道保密闸门都在这里。少数用例把账本记录数写成了
断言（`test_migrate_cat.py` / `test_catalog.py` 等）：日常录入让数据变多之后它们会红，
那是故意留的漂移探针，先核对规格文档再改数字。
