# 账本「闲鱼订单」可视化与就地修正 — 设计文档

日期：2026-09-24
范围：NeonFish 个人硬件交易账本（`app_standalone.py` + `index.html` + `data.json`）与闲鱼导入链路（`xianyu_scraper.py` → MySQL → `xianyu_review.py` → `data.json`）
状态：已批准（方案 A + 呈现形态 1 + 品牌选择器；`reviewed` 标记经评审砍掉）
实现顺序：先修存量 bug（已完成 `de4789f`，编辑弹窗预填、配件字段丢失、写盘非原子三项），再做本设计的导入可视化。新功能建立在修好的编辑链路上。

## 1. 问题

导入链路已经把闲鱼订单写进账本，但规则拆分出的 `brand` / `model` / `cost` 经常不准，而账本里没有任何线索能说明"这条记录来自哪笔订单"。用户反馈的五项痛点：

1. 分不清哪条记录对应哪笔订单
2. 品牌 / 型号拆错了，要能就地改
3. 一笔订单实际含多件硬件，要能拆成多条账本记录
4. 买价金额不对，要能就地改
5. 编辑时品牌不该手打，应从账本已有品牌里选 —— 手输的错字 / 别名（`微星` vs `MSI`）会让 `buildBrands()` 按字符串原样分组，同一个品牌被拆成多行，品牌总览与价格参考跟着失真

现状只能靠记忆去猜，或者回到核对页重看，两边对不上。

## 2. 已核实的事实（本设计的前提）

| 事实 | 出处 |
| --- | --- |
| 导入时只写 10 个键，**不含 `order_date` / `item_title`** | `xianyu_review.py:268-280`（`import_orders`） |
| MySQL 查询已取回 `order_id, item_title, price, trade_type, counterparty, order_status, order_date` | `xianyu_review.py:216-220`（`_ORDERS_SQL`） |
| 账本 update 是逐字段合并，未传字段保留旧值 | `app_standalone.py:383-406`（`handle_update_record`） |
| 账本 add / update 白名单**都不含** `source_order_id`，走 add 新增的记录会丢订单关联 | `app_standalone.py:368-378`、`391-402` |
| 每次 `save_data` 都会把旧 `data.json` 复制成 `data.json.bak` | `app_standalone.py`（`save_data`），`xianyu_review.py:194-198`（`save_ledger`）同构 |
| `data.json` 现有 255 条记录，其中 18 条带 `source_order_id`，这 18 条全部无日期、无原标题 | 本轮实测读取 |
| 前端 tab 现有 4 个：`brands` / `models` / `chipsets` / `ranking` | `index.html:940-943`，分派在 `index.html:1563-1568` |
| 「型号明细」已渲染品红 `闲鱼` 徽章（`title` 带订单号） | `index.html:1395`，`.badge-source` 定义在 `index.html:681` |
| 已有金色「待补售价 N」筛选胶囊（`pendingOnly` / `togglePending` / `#pendingChip`） | 上一轮已上线并验证 |
| 品牌输入框 `#fBrand` 是自由文本 `<input>`，且有 **4 个写入方**：模板选择（`index.html:1740`）、智能解析（`1920`）、聊天面板 `fieldMap`（`1998`）、编辑弹窗 `openEditModal`（`1660`） | 本轮实测读取 |
| `BRAND_STYLES`（`index.html:1301-1309`）只有 7 个键，是**配色表不是品牌库**；`brandIcon`（`1310-1321`）用 `brand[0]` 兜底，任意品牌名都能渲染 | 本轮实测读取 |
| `buildBrands()` 按 `brand` 原样字符串分组，无归一化、无别名表 | 本轮实测读取 |
| `app_standalone.py` 的路由只有 `/`、`/api/chat`、`/api/data`、`/api/smart-parse`、`/api/upload`、`/api/data/<idx>`、`/api/images/<f>`；**`/api/prices`、`/api/templates` 只存在于 `server.py:242` / `:250`** | 本轮实测读取路由表；桌面版的价格参考与模板下拉因此恒为空 |
| 18 条闲鱼记录的 `sell` 是空串，而 `handle_update_record` 用 `float(...)` 取值，省略 `sell` 的 PUT 会抛 `ValueError` 且 `do_PUT` 无捕获 | 实测 `data.json`：`sell` 为空的记录集合 == `source_order_id` 的记录集合（各 18 条），且无一条使用数值 `0` |
| `togglePending`（`index.html:1291-1298`）会强制切到 `models`，tab 监听（`1593`）切走即清零 `pendingOnly` | 本轮实测读取；跨 tab 的胶囊交集语义不成立 |
| 编辑链路三处存量缺陷已随 `de4789f` 修复：预填按 `id` 反查、`accessory`/`accessory_price`/`sn2` 已透传、`save_data` 与 `save_ledger` 改临时文件 + `os.replace` | `git show de4789f`；临时目录实测注入序列化失败后 `data.json` 字节未变 |

结论：**不新增字段就不可能满足需求 1**，这是数据结构问题，不是 UI 问题。

## 3. 方案取舍

### 3.1 订单信息放在哪

- **A. 自包含（采用）** — 导入时把 `order_date` + `item_title` 写进 `data.json`；已有 18 条按 `source_order_id` 从 MySQL 一次性回填。账本从此不依赖 MySQL 即可完整显示订单来源。
- B. 渲染时回查 MySQL（否决）— 账本后端目前零 `pymysql` 依赖；加上后"MySQL 没起 → 账本功能残"，且启动器/离线场景直接坏掉。
- C. 侧车缓存 JSON（否决）— 多一份会与 `data.json` 失同步的状态；备份 `data.json` 时订单信息掉在备份之外。

### 3.2 呈现形态

- **1. 新增第 5 个 tab「闲鱼订单」，按订单分组（采用）** — 一组 = 一笔 `source_order_id`，日期只用于组头显示与排序；与用户熟悉的核对页同构，能容纳原标题、金额对账、拆单操作。
- 2. 只在「型号明细」加筛选胶囊（否决为主形态）— 放不下原标题上下文，拆单没有落脚点。
- 3. 订单详情抽屉（否决）— 拆单要在弹窗里再套弹窗，交互绕。

采用 1，并保留 2 的便利：「型号明细」里已有的 `闲鱼` 徽章改为可点击，跳到「闲鱼订单」tab 并定位到该订单分组。

## 4. 数据模型

记录新增 4 个**可选**键，全部向后兼容：

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `source_order_id` | str | 已有。闲鱼订单号，也是"来自导入"的唯一判据 |
| `order_date` | str `YYYY-MM-DD HH:MM:SS` | 订单日期，用于组头显示与排序 |
| `item_title` | str | 闲鱼原标题，用于人工核对拆分是否正确 |
| `order_paid` | number | 该订单的实付金额，即 MySQL `xianyu_orders.price`。**由导入时直接写入**（`_ORDERS_SQL` 已取回该列），拆单时派生记录继承同一个值；UI 只读 |

约束：

- 所有闲鱼相关逻辑一律以 `source_order_id` 是否存在为开关。**手动录入的 237 条记录行为完全不变**，不会被分组、不参与对账、不显示新字段。
- `order_date` / `item_title` 只由导入与回填写入，UI 上只读展示，不允许手改（防止把关联信息改坏）。
- 分组键是 `source_order_id`，不是 `order_date`。一笔订单的多条记录共享同一 `source_order_id`。

## 5. 后端改动（`app_standalone.py`）

### 5.1 扩字段白名单

`handle_add_record` 与 `handle_update_record` 各加一段：仅当 body 中显式出现这 4 个键时才写入，保持现有"未传即保留"语义。

```python
for k in ("source_order_id", "order_date", "item_title", "order_paid"):
    if k in body:
        r[k] = body[k]
```

`handle_add_record` 里对应地只在键存在时落键，避免给手动新增记录塞空 `source_order_id` 污染判据。

两个必须一起处理的既有约束（均已实测）：

- `handle_update_record` 用 `float(body.get('sell', r.get('sell', 0)))` 取值，而他 18 条闲鱼记录的 `sell` 是空串。任何省略 `sell` 的 PUT 都会走 `float('')` 抛 `ValueError`，`do_PUT` 没有捕获 → 整次写入不落盘、连接断开。修法必须是**只在请求显式带该键时才转数值、省略即原样保留**：`''` 是"还没定价"的唯一标记（实测与 `source_order_id` 集合完全重合、无人使用数值 `0`），所以既不能"显式带上 sell"（会把 `''` 塌成 `0.0`），也不能改用 `_norm_price`（它对 `0` 返回 `''`）。
- 记录没有 id，前端一律按 `data.json` 数组下标寻址，任何 append 都会改变后续下标。`split_record` 与 `add_record` 的响应必须回传**原始数组下标**，前端不得再用 `items.length - 1` 猜（`index.html:1711` 现在就是这么猜的，拆单必须避开这条路）。

### 5.2 新增 `POST /api/split_record`

请求体：

```json
{ "idx": 254, "parts": [ {"brand":"微星","model":"B650M GAMING WIFI","cost":600},
                         {"brand":"光威","model":"神策 16G×2","cost":200} ] }
```

行为：读 `data.json` → 校验 `idx` 越界与 `parts` 非空 → 用 `parts[0]` 覆盖原记录的 `brand`/`model`/`cost` → 其余每个 part 追加一条新记录，**继承原记录的 `source_order_id` / `order_date` / `item_title` / `order_paid`**，`sell`/`sn`/`accessory`/`accessory_price`/`extra_price` 置空、`images` 置 `[]` → 一次 `save_data` → 返回受影响记录的**原始数组下标列表**。

`order_paid` 由导入写入、拆单只继承，**任何情况下都不用 `cost` 反推或覆盖它**。否则对一条已拆过的记录再拆一次，基线会缩水成上一次的某条 `cost`，组头差额凭空冒出。

必须是单端点、单次写盘。若前端循环调用 add：N 条会触发 N 次 `save_data`，把 `data.json.bak` 轮转掉，拆单前的备份就没了，中途失败也无法回退。

约束：`idx` 指向的记录必须有 `source_order_id`，否则返回 400 —— 手动记录不参与拆单。`parts` 长度为 1 **不是**"撤销拆分"：它只改这一条，同订单的其余派生记录仍留在账本里。撤销拆分需要单独的合并入口，本设计不做（见 §10）。

### 5.3 导入侧（`xianyu_review.py` + `xianyu_review.html`）

核对页目前 POST 给 `/api/import` 的载荷只有 `order_id / brand / model / cost` 四个键（`xianyu_review.html:863-866`）。`order_date` 和 `item_title` 页面里**有**（`:590`、`:596` 正在渲染它们）但**没上传**，所以要改的是请求载荷而不是后端拼装：

1. `xianyu_review.html` 的选中项对象补 `order_date`、`item_title`、`paid`（值取 `o.order_date`、`o.item_title`、`o.price`）。
2. `import_orders` 的 record 构造补写 `order_date`、`item_title`、`order_paid`。`order_paid` 用真实 `price`，不再用 `cost` 之和近似 —— 这样正常导入（未拆单）的记录一进来就有实付基线，组头不必等到拆单才出数。

去重语义不变：`imported_order_ids()` 是集合判断，拆单后同一 `order_id` 对应多条记录仍然"已填入"，核对页不会重复提示。

## 6. 前端改动（`index.html`）

### 6.0 前提：`parseItems` 是字段白名单

`index.html:1135-1148` 的 `parseItems` 用 `.map()` 显式重建每条记录，只透传 `id / source_order_id / brand / model / cost / sell / sn / extra_price` 加三个 getter。**后端加了字段但这里不透传，前端就永远看不到**。新 tab 依赖的 `order_date`、`item_title`、`order_paid` 三个键必须在这里补进 map；漏掉的后果不是报错，而是页面显示空白 —— 最难查的那类失败。

（已随 `de4789f` 修掉的同类漏洞：`accessory` / `accessory_price` / `sn2` 之前也没透传，导致编辑弹窗读不到、保存时抹成空串。）

### 6.1 新 tab「闲鱼订单」

`index.html:940-943` 的 tab 条加 `data-view="orders"`，`render()` 分派加 `renderOrders()`。

**分组键是 `source_order_id`，不是日期。** 一个分组 = 一笔订单；`order_date` 只用于组头显示和组的排序（按 `order_date` 倒序，同一订单拆出的多条天然落在同一组内）。按日期分组会把同一天的两笔订单并成一组，`已入账` 与 `订单实付` 立刻对不上。

组结构：

- **组头**：`日期 时间`（如 2026-09-21 14:32）· 原标题（超长省略、`title` 显示全文）· `订单实付 ¥order_paid` · `已入账 Σcost` · `差额` · `n 条` · 「拆成多条」入口
- `差额` 非 0 时金色提示，**不阻止任何操作**（运费、砍价零头本就不该强行摊平）；`order_paid` 缺失（回填未命中的旧数据）时不显示实付与差额，只显示已入账，不报错。
- **组内行**：品牌 / 型号 / 买价 / 售价 / 利润，行内编辑复用现有弹窗 `openEditModal(origIdx)`，不新造表单。`origIdx` 一律取 `items[].id`（`de4789f` 之后 `id` 就是原始下标），禁止再用数组位置。
- 组头不做"已核对"勾选。他本来就靠金色「待补售价 N」定位待处理记录，售价空着即未处理，再加一个复选框是重复信息（评审决定砍掉 `reviewed`）。

状态穷举：

| 状态 | 呈现 |
| --- | --- |
| 还没有任何导入 | 空态卡片：说明去核对页勾选导入，附本机 8766 的启动提示，不给空表格 |
| 分组内某条 `order_paid` 缺失 | 只隐藏实付/差额两列 |
| 拆单提交失败（400 / 500 / 断连） | 保留弹窗与已填内容 + 金色 toast，不重拉列表，避免丢掉用户输入 |
| 拆单成功 | 用后端回传的原始下标定位新记录，`fetchItems()` 后停在「闲鱼订单」tab |
| 组内被删到只剩一条 | 正常显示；不提供"合并回一条"（见 §10） |

### 6.2 入口一致性

不新增筛选胶囊。改为两处更便宜的可发现性：组内行保留已有的品红 `闲鱼` 徽章；「闲鱼订单」tab 上给一枚与 tab 同色的小圆点，当存在 `source_order_id` 且 `sell` 为空的记录时点亮。

注意既有的胶囊状态机不能直接复用：`togglePending`（`index.html:1291-1298`）会强制把 `currentView` 切到 `models`，而 tab 点击监听（`1593`）一切走就把 `pendingOnly` 清零。所以"两胶囊叠加取交集"这类写法在当前状态机下不成立，本设计不去动它。

### 6.3 拆单弹窗

组头「拆成多条」打开弹窗：起始行数 = 该组现有记录数，每行 `[品牌] [型号] [成本]`，可增删行。底部实时显示 `合计 / 订单实付 / 差额`。提交调 `/api/split_record`，成功后 `fetchItems()` 重拉并保留当前 tab。

差额非 0 时提交按钮旁给金色提示但允许提交。

### 6.4 徽章跳转

`index.html:1395` 的 `闲鱼` 徽章加 `onclick` → 切到「闲鱼订单」tab、按该 `source_order_id` 过滤并滚动到对应分组。

### 6.5 品牌选择器

痛点：编辑记录时品牌靠手打，一个错字或别名（`微星` / `MSI` / `msi`）就会被 `buildBrands()` 当成新品牌单列一行，品牌总览失真。用户要求：**以选择为主，不要靠手打**。

形态：`#fBrand` 保留为唯一真值源，外面包一层霓虹下拉建议面板，而不是换成 `<select>`。理由：

1. 账本里出现过的品牌远少于闲鱼拆分库的 60+ 条，买到新品牌是常态；纯 `<select>` 会把"这个牌子我还没记过"变成录不进去。
2. `#fBrand` 有 4 个写入方（见 §2），换成 `<select>` 要逐条改这些赋值；面板方案下它们继续写同一个 input。但要说清一点：**程序化赋值不会触发 `input` 事件**，所以"改一处即全链路生效"只在用户手动输入时成立，模板 / 智能解析那两条链路仍需各自确认赋值后刷新一次面板。
3. 原生 `<datalist>` 的弹窗是操作系统样式，与这套霓虹暗色不一致。

不要拿"选择后能刷新价格参考"当理由：`fetchPriceHint` 打的 `/api/prices` 和模板用的 `/api/templates` **只存在于 `server.py:250` / `:242`，独立版 `app_standalone.py` 没有这两个路由**（实测路由表只有 `/`、`/api/chat`、`/api/data`、`/api/smart-parse`、`/api/upload`、`/api/data/<idx>`、`/api/images/<f>`）。在他日常开的桌面版里，价格参考恒为失败、模板下拉恒为空。本设计不修这个（见 §10），但也不许把它当依据。

交互：

- 聚焦 `#fBrand` 即展开面板，列出 `items` 中去重后的 `brand`，按记录条数降序，跳过 trim 后为空的值；当前记录的品牌一定在列内并高亮。默认光标落在第一项 —— 直接按回车就是"选第一个"，让他不打字也能完成选择。
- 输入时按"包含"过滤（忽略大小写），方向键移动、回车选中、Esc 只关面板不关弹窗；选中写入 `#fBrand.value`。
- 输入值与任何已有品牌都不精确相等时，面板末尾给一行 `＋ 以新品牌 "X" 创建`。
- **必须处理回车冲突**：`index.html:1728-1733` 在弹窗打开时把回车直接转成"点提交按钮"。不拦下的话，用户想选中品牌结果整条记录被保存。实现上要在捕获阶段 `preventDefault`：面板开着时回车 = 选中当前项；只有面板关闭且品牌值合法时回车才提交。
- 不选那一行也能保存，值原样入库；**选中那一行时**在提交前给一次金色确认条「账本里还没有 X，确认按新品牌保存？」。不做任何自动纠正或别名归一化。
- 纯前端，不改后端、不改 `data.json` 结构。

## 7. 迁移：回填现有 18 条

新增 `xianyu_backfill.py`（独立脚本，不进账本运行时）：

1. 读 `data.json`，挑出 `source_order_id` 存在但 `order_date` 缺失的记录。
2. 按 `order_id` 批量查 MySQL `neon_ledger.xianyu_orders` 取 `order_date`、`item_title`。
3. `--dry-run`（默认）只打印 `order_id / 命中与否 / 将写入的日期与标题前 20 字`，**不写盘**。
4. 显式 `--apply` 才写：先复制 `data.json` → `data.json.pre_backfill`，再单次 `save_data`。
5. MySQL 查不到的（例如已清库）保持缺失，不写空串掩盖，并在结尾明确报告条数。

凭据沿用 `xianyu_review.py:35-38` 的 `DB_CONF`；该文件仍留在 `.gitignore` 之外未跟踪，脚本本身不含明文口令的新增拷贝（复用同一段配置）。

## 8. 错误处理与边界

| 情况 | 处理 |
| --- | --- |
| 无 `source_order_id` 的记录（237 条手动） | 不进「闲鱼订单」tab；不显示新徽章；编辑行为不变 |
| 某订单只回填了 `order_date`、`item_title` 为空 | 组头原标题位置显示 `订单 <id 后 6 位>`，不显示空白 |
| `split_record` 的 `idx` 越界 / 目标非导入记录 / `parts` 空 | 分别返回 404 / 400 / 400，不写盘 |
| 拆单后立刻删其中一条 | 现有 `handle_delete_record` 不变；组头重算 `已入账`，`订单实付` 因冻结在 `order_paid` 而不受影响 |
| 某条导入记录的 `order_paid` 缺失（回填时 MySQL 查不到该 `order_id`） | 组头不显示「订单实付」「差额」，只显示「已入账」与条数，不报错、不写 0 冒充 |
| 账本窗口长期开着 | 已有 `focus` / `visibilitychange` 自动重拉，回填与拆单结果在切回窗口时自动出现 |

## 9. 验收标准

每条都必须**能失败**：断言写成不变量或前后对照，不写"看起来对"。中文断言只信 `grep -c` / 字节比较（本机 Bash 输出是 cp936 字节）。

**后端**（一律起临时实例 + 临时 `DATA_FILE`，绝不动真实 `data.json`）

1. `POST /api/data` 带 `source_order_id`/`order_date`/`item_title`/`order_paid` → 读回临时 `data.json` 断言四个键都在且值原样；不带时断言手动记录**没有**这四个键（键数为 0，不是值为空）。
2. 对一条 `sell` 为空串的导入记录发只含 `cost` 的 PUT → 断言进程没崩、`data.json` 合法、该记录 `order_date`/`item_title`/`order_paid` 原值保留。**这条在实现前必须失败**（`float('')` 抛 ValueError），是 §5.1 那个坑存在的证明。
3. `POST /api/split_record` 传 2 个 parts → 断言：总条数 +1、两条共享同一 `source_order_id`/`order_paid`、返回的是**原始下标**且用该下标 GET 回来正是那两条、`.bak` 的 **sha256 等于写入前的 `data.json`**（不要用 mtime —— `save_data` 用 `copy2`，会继承源文件时间戳，跨秒抖动就误判）。
4. 对已有一条 `order_paid=800` 的派生记录再拆一次 → 断言 `order_paid` 仍是 800（证明没人用 `cost` 覆盖基线）。
5. 越界 `idx` / 目标非导入记录 / 空 `parts` → 分别 404 / 400 / 400，且三种情况下 `data.json` 字节完全未变。

**迁移**

6. `python xianyu_backfill.py`（默认 dry-run）→ 断言 `data.json` 字节未变，并打印命中条数；命中数不等于 18 时必须把实际数字打出来（不许静默）。
7. `--apply` → 断言存在 `data.json.pre_backfill`、总条数不变、`order_date` 非空数 == 脚本打印的 MySQL 命中数，且 datetime 已转成字符串（用 `json.dumps` 全量序列化成功来证明 §12 那个截断风险已消除）。

**前端**（在真实窗口 `127.0.0.1:8765` 走一遍）

8. 先取改动前快照：`GET /api/data` 落一份 `before.json`。断言「闲鱼订单」tab 的分组数 == `before.json` 里 `source_order_id` 的**去重计数**（两个数都打印出来，由数据算出，不写死）。
9. 组内改一条 `cost` 并保存 → 关掉窗口重开 → 值仍在，且该记录的 `order_date`/`item_title`/`order_paid` 未被抹掉。
10. 拆成 2 条 → 组内 2 行、两条都带 `闲鱼` 徽章、`已入账` 与拆分前一致；组头 `订单实付` 不变。
11. 在「型号明细」点 `闲鱼` 徽章 → 跳到「闲鱼订单」并定位到该分组。
12. 手动新增一条记录 → 断言它不出现在「闲鱼订单」tab。
13. 品牌面板：断言面板条数 == `items` 里非空 `brand` 的去重数（两侧数字都打印）。注意 `buildBrands()` 不去空串，直接拿它的计数会比面板多 1，所以基准必须由测试自己算，不能引用 `buildBrands()` 的结果 —— 否则这条永远通过。
14. 品牌输入一个账本里没有的字符串 → a) 面板末尾出现且仅出现一行 `＋ 以新品牌 "…"`；b) **面板开着按回车只选中，不提交记录**（用回车后 `data.json` 字节未变来断言）；c) 点那一行有金色确认条；d) 确认后品牌数 +1、存的是输入原文（证明没做自动纠正）。
15. 三条既有链路回归：选模板、智能解析、聊天面板录入 → 各自都能把品牌写进 `#fBrand` 并正常保存。

**回归**

16. 用第 8 条的 `before.json` 做基线：改动后重新取 `after.json`，断言总成本、总利润、排行榜顺序、品牌数（不含新增）四项与基线一致 —— 新字段不参与既有聚合。没有 `before.json` 就跳过并明确报告"未执行"，不许凭印象说没变。

## 10. 明确不做

- 不改 `xianyu_scraper.py` 的抓取逻辑与 `xianyu_orders` 表结构。
- 不在账本里引入 MySQL / `pymysql` 依赖。
- 不做多币种、退款、卖出侧（`trade_type`）区分 —— 目前只导入买入单。
- 不改「品牌总览 / 芯片组 / 排行榜」的聚合口径。
- 不做品牌别名归一化 / 自动纠错（不建 `MSI → 微星` 映射表，也不批量改已有记录）。选择器只降低输入成本，不替他改写已入库的数据。
- 不做"撤销拆分 / 把多条合并回一条"。`split_record` 的 `parts` 长度 1 只改单条，不会合并；需要合并就得再设计一个端点，本次不做。
- 不顺手给桌面版补 `/api/prices` 与 `/api/templates` 路由。那是独立版一直缺的功能，本设计只是不再把它当依据；要不要补另开一轮。
- 不把 `xianyu_review.py`、`xianyu_scraper.py`、`xianyu_review.html` 纳入 git（源码里硬编码了 MySQL 明文账号口令，抽成配置之前不动；已写进 `.gitignore`）。
- 不往真实 `data.json` 写任何测试数据；验证只在他本来就要核对的 18 条上做，配合 `.bak` / `.pre_backfill` 兜底。

## 11. 影响面与重启

| 组件 | 改动 | 生效方式 |
| --- | --- | --- |
| `app_standalone.py` | 白名单 + 新端点 | **先杀后端进程再启动**。`main()`（`app_standalone.py:590-594`）检测到 8765 有人监听就只新开窗口、沿用旧进程，所以直接点启动器按钮**不会加载新 Python 代码** —— 看起来重启了，跑的还是旧逻辑 |
| `index.html` | 新 tab、徽章、拆单弹窗、品牌选择器、`parseItems` 透传 | 刷新即可（`send_html` 每请求重读文件）；但**已经开着的窗口仍跑着旧 JS**，要看效果必须重载或重开窗口 |
| `xianyu_review.py` / `xianyu_review.html` | 导入补三字段 | **必须重启 pid 48924**；该文件未跟踪，改动只在磁盘上 |
| `data.json` | 18 条回填 + 新增只读键 | 回填脚本 `--apply`，先留 `.pre_backfill` |
| 新增 `xianyu_backfill.py` | — | 一次性工具，可提交 |

回退路径：`data.json.pre_backfill` 原样覆盖回 `data.json` 即回到迁移前状态；代码侧回退用 git revert。

## 12. 风险

1. **回填依赖 MySQL 里那 18 个 `order_id` 还在**。若 `xianyu_orders` 被清过，部分记录只能保持"知道来自闲鱼但不知道哪天一"，脚本会明确报数而不是静默填空。`order_paid` 同理：回填漏一条，那一组的差额就永久对不上，所以 dry-run 必须先给出逐条命中表再 `--apply`。
2. **两个窗口同时写 `data.json` 会互毁**，这一点比初版判断的更严重：`ThreadingHTTPServer` 无锁 + 整文件覆盖写，最后写入者赢；而 `save_data` 只保留**一代** `.bak`，下一次任何写盘就把上一代销毁。也就是说"两个窗口各加一条、后写的把先写的整条抹掉"之后，只要再动一次盘，被抹掉的那条就再也回不来了。底线做法（必做，不是可选项）：写盘前比较内存里持有的条数 / 文件 mtime，发现外部已变则拒绝覆盖并提示刷新。不做完整文件锁。
3. 回填脚本会把 MySQL 的 `datetime` 带进来，必须在进 `save_data` 之前转成字符串（`fetch_orders` 已经 `strftime` 过，回填脚本不得偷懒直接用原始列）。`save_data` / `save_ledger` 已在 `de4789f` 改成"内存序列化 + 临时文件 + `os.replace`"，所以再出错也只会留下一个 `.tmp`，不会把账本截断——这是本设计能安全跑回填的前提。
4. 桌面版缺 `/api/prices`、`/api/templates` 两个路由，价格参考与模板下拉在 `app_standalone.py` 下恒不可用。本设计不修，但如果他以后问"这功能怎么一直是空的"，答案在这里，不在品牌选择器。
