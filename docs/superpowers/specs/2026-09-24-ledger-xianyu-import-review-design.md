# 账本「闲鱼订单」可视化与就地修正 — 设计文档

日期：2026-09-24
范围：NeonFish 个人硬件交易账本（`app_standalone.py` + `index.html` + `data.json`）与闲鱼导入链路（`xianyu_scraper.py` → MySQL → `xianyu_review.py` → `data.json`）
状态：已批准（方案 A + 呈现形态 1，含 `reviewed` 标记与品牌选择器）

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

结论：**不新增字段就不可能满足需求 1**，这是数据结构问题，不是 UI 问题。

## 3. 方案取舍

### 3.1 订单信息放在哪

- **A. 自包含（采用）** — 导入时把 `order_date` + `item_title` 写进 `data.json`；已有 18 条按 `source_order_id` 从 MySQL 一次性回填。账本从此不依赖 MySQL 即可完整显示订单来源。
- B. 渲染时回查 MySQL（否决）— 账本后端目前零 `pymysql` 依赖；加上后"MySQL 没起 → 账本功能残"，且启动器/离线场景直接坏掉。
- C. 侧车缓存 JSON（否决）— 多一份会与 `data.json` 失同步的状态；备份 `data.json` 时订单信息掉在备份之外。

### 3.2 呈现形态

- **1. 新增第 5 个 tab「闲鱼订单」，按订单日期分组（采用）** — 与用户熟悉的核对页同构，能容纳原标题、金额对账、拆单操作。
- 2. 只在「型号明细」加筛选胶囊（否决为主形态）— 放不下原标题上下文，拆单没有落脚点。
- 3. 订单详情抽屉（否决）— 拆单要在弹窗里再套弹窗，交互绕。

采用 1，并保留 2 的便利：「型号明细」里已有的 `闲鱼` 徽章改为可点击，跳到「闲鱼订单」tab 并定位到该订单分组。

## 4. 数据模型

记录新增 5 个**可选**键，全部向后兼容：

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `source_order_id` | str | 已有。闲鱼订单号，也是"来自导入"的唯一判据 |
| `order_date` | str `YYYY-MM-DD HH:MM:SS` | 订单日期，用于分组与排序 |
| `item_title` | str | 闲鱼原标题，用于人工核对拆分是否正确 |
| `reviewed` | bool | 用户已核对该记录；缺省视为 `false` |
| `order_paid` | number | 该订单的入账基线（拆分前 `cost` 之和），只由 §5.2 的 `split_record` 写入，用于组头对账。前端只读，不进 §5.1 的 body 白名单 |

约束：

- 所有闲鱼相关逻辑一律以 `source_order_id` 是否存在为开关。**手动录入的 237 条记录行为完全不变**，不会被分组、不参与对账、不显示新字段。
- `order_date` / `item_title` 只由导入与回填写入，UI 上只读展示，不允许手改（防止把关联信息改坏）。
- 分组键是 `source_order_id`，不是 `order_date`。一笔订单的多条记录共享同一 `source_order_id`。

## 5. 后端改动（`app_standalone.py`）

### 5.1 扩字段白名单

`handle_add_record` 与 `handle_update_record` 各加一段：仅当 body 中显式出现这 4 个键时才写入，保持现有"未传即保留"语义。

```python
for k in ("source_order_id", "order_date", "item_title", "reviewed"):
    if k in body:
        r[k] = body[k]
```

`handle_add_record` 里对应地用 `body.get(k, "")`（`reviewed` 默认 `False`）只在键存在时落键，避免给手动新增记录塞空 `source_order_id` 污染判据。

### 5.2 新增 `POST /api/split_record`

请求体：

```json
{ "idx": 254, "parts": [ {"brand":"微星","model":"B650M GAMING WIFI","cost":600},
                         {"brand":"光威","model":"神策 16G×2","cost":200} ] }
```

行为：读 `data.json` → 校验 `idx` 越界与 `parts` 非空 → **先取原记录的 `cost` 作为基线，覆盖前冻结成 `order_paid`** → 用 `parts[0]` 覆盖原记录的 `brand`/`model`/`cost` → 其余每个 part 追加一条新记录，**继承原记录的 `source_order_id` / `order_date` / `item_title` / `order_paid`**，`sell`/`sn`/`accessory`/`accessory_price`/`extra_price` 置空、`images` 置 `[]` → 一次 `save_data` → 返回受影响记录的索引列表。

必须是单端点、单次写盘。若前端循环调用 add：N 条会触发 N 次 `save_data`，把 `data.json.bak` 轮转掉，拆单前的备份就没了，中途失败也无法回退。

约束：`parts` 长度为 1 等价于普通 update，允许（用户可能只是改错了想撤销拆分结构）；`idx` 指向的记录必须有 `source_order_id`，否则返回 400 —— 手动记录不参与拆单。

### 5.3 导入侧（`xianyu_review.py`）

`import_orders` 的 record 构造补写 `order_date` 与 `item_title`（值来自 `fetch_orders` 已算好的同名字段，前端本就带着它们）。去重语义不变：`imported_order_ids()` 是集合判断，拆单后同一 `order_id` 对应多条记录仍然"已填入"，核对页不会重复提示。

## 6. 前端改动（`index.html`）

### 6.1 新 tab「闲鱼订单」

`index.html:940-943` 的 tab 条加 `data-view="orders"`，`render()` 分派加 `renderOrders()`。

结构：按 `order_date` 的日期部分倒序分组，同一天内按 `order_date` 时间倒序。每组：

- **组头**：`日期`（如 2026-09-21）· 原标题（超长省略、`title` 显示全文）· `订单实付 ¥X` · `已入账 ¥Y` · `差额 ¥(X-Y)` · `未核对 n/m` · 「拆成多条」入口
- `订单实付` = 该组任一记录的 `item_title` 对应的 MySQL `price` —— 账本里没有 `price`，因此**用「该组首次导入时的 `cost` 合计」作基线**（即原始 `cost` 之和，拆单不改变它）。实现方式：拆单时把基线冻结进每条派生记录的 `order_paid` 键（同样只读、同样向后兼容），组头直接读它。这样不引 MySQL 也能显示实付。
- `已入账` = 该组当前所有记录 `cost` 之和；`差额` 非 0 时金色提示，**不阻止任何操作**（运费、砍价零头本就不该强行摊平）。
- **组内行**：品牌 / 型号 / 买价 / 售价 / 利润 / 状态，行内可直接编辑品牌·型号·买价·售价（复用现有编辑弹窗 `openEditModal`，不新造一套表单），行尾「已核对」勾选。
- 组内记录全部 `reviewed` → 组头转灰并显示 ✓；否则显示未核对计数。

### 6.2 顶部胶囊

在现有金色「待补售价 N」旁加一枚「未核对 N」胶囊，复用 `pendingOnly`/`togglePending` 的写法（同一套 class 与计数节点），筛出 `source_order_id` 存在且 `reviewed !== true` 的记录。两个胶囊可叠加，叠加时取交集。

### 6.3 拆单弹窗

组头「拆成多条」打开弹窗：起始行数 = 该组现有记录数，每行 `[品牌] [型号] [成本]`，可增删行。底部实时显示 `合计 / 订单实付 / 差额`。提交调 `/api/split_record`，成功后 `fetchItems()` 重拉并保留当前 tab。

差额非 0 时提交按钮旁给金色提示但允许提交。

### 6.4 徽章跳转

`index.html:1395` 的 `闲鱼` 徽章加 `onclick` → 切到「闲鱼订单」tab、按该 `source_order_id` 过滤并滚动到对应分组。

### 6.5 品牌选择器

痛点：编辑记录时品牌靠手打，一个错字或别名（`微星` / `MSI` / `msi`）就会被 `buildBrands()` 当成新品牌单列一行，品牌总览和 `/api/prices` 价格参考跟着失真。

**形态：`#fBrand` 保留为唯一真值源，外面包一层下拉建议面板。** 不改成 `<select>`，理由有三条：

1. `#fBrand` 有 4 个写入方（见 §2），换成 `<select>` 要同时改模板、智能解析、聊天面板、编辑弹窗四条链路；做成"输入框 + 建议面板"则四条链路一行都不用改，它们继续写同一个 input。
2. 闲鱼 `BRANDS` 有 60+ 条，账本里出现的品牌远少于它；新硬件、新品牌是常态，纯下拉会把用户卡死。
3. 原生 `<datalist>` 的弹窗是操作系统样式，与这套霓虹暗色不一致。

交互：

- 聚焦 `#fBrand` 即展开面板，列出账本 `items` 中已有的 `brand` 去重值，按记录条数降序（常买的品牌在最上面）。当前编辑的记录自身品牌一定在列内。
- 输入时按"包含"过滤（不做大小写敏感），方向键 + 回车选择，鼠标点击选择；选择即写入 `#fBrand.value` 并触发既有的 `input` 监听链路（清 `fTemplate`、拉 `fetchPriceHint`）。
- 输入值与任何已有品牌都不精确相等时，面板末尾给一行显式的 `＋ 以新品牌 "X" 创建`。选它才真正落新品牌 —— **不做任何自动纠正或别名归一化**，避免把他的数据改成我没问过的样子。
- 面板是纯前端，不改后端、不改 `data.json`，也不新增字段。

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
| 同一 `source_order_id` 但 `order_paid` 缺失（拆单功能上线前的旧数据） | 组头不显示「订单实付」「差额」，只显示「已入账」，不报错 |
| 账本窗口长期开着 | 已有 `focus` / `visibilitychange` 自动重拉，回填与拆单结果在切回窗口时自动出现 |

## 9. 验收标准

每项检查都必须能失败；计数类断言要打印实际命中数，0 命中不得静默通过。

**后端**
1. `python -c` 起临时实例，`POST /api/add_record` 带 `source_order_id` → 读回 `data.json` 断言该键存在；不带时断言手动记录**没有**该键。
2. `PUT /api/data/<idx>` 只传 `cost` → 断言该记录的 `source_order_id`、`order_date`、`item_title`、`reviewed` 全部原值保留。
3. `POST /api/split_record` 2 个 parts → 断言：总条数 +1、两条共享同一 `source_order_id`、`order_paid` 与原 `cost` 一致、`data.json.bak` 的时间戳只滚动一次。
4. 越界 `idx`、非导入记录、空 `parts` 三种输入分别得到 404 / 400 / 400 且 `data.json` 字节未变。

**迁移**
5. `python xianyu_backfill.py` （dry-run）→ 断言 `data.json` 字节不变，且命中数打印为 18。
6. `python xianyu_backfill.py --apply` → 断言总条数仍 255、带 `source_order_id` 仍 18、`order_date` 非空数 == MySQL 命中数，并存在 `data.json.pre_backfill`。

**前端**（在真实窗口 127.0.0.1:8765 走一遍）
7. 「闲鱼订单」tab 出现且分组数 == 去重后的 `source_order_id` 数；组头显示日期、原标题、已入账。
8. 组内改一条 `cost` → 保存 → 关掉窗口重开 → 值仍在；`data.json` 里同一条记录的 `order_date` 未被抹掉。
9. 点「拆成多条」拆成 2 条 → 组内出现 2 行、`已入账` 与拆分前一致、两条都带同一 `闲鱼` 徽章。
10. 勾「已核对」→「未核对 N」胶囊计数 -1；全部核对后组头变灰打勾。
11. 在「型号明细」点 `闲鱼` 徽章 → 跳到「闲鱼订单」并定位到该分组。
12. 手动新增一条记录 → 断言它不出现在「闲鱼订单」tab、不计入「未核对 N」。
13. 编辑弹窗聚焦品牌框 → 面板列出的品牌数 == `items` 去重后的 `brand` 数（打印两侧实际数字，不等即失败）；选一个已有品牌保存 → 断言「品牌总览」品牌数不变、`data.json` 该记录 `brand` 为新值。
14. 品牌框输入一个账本里不存在的字符串 → 分别断言三件事：a) 面板末尾出现且仅出现一行 `＋ 以新品牌 "<输入值>" 创建`；b) 不点那一行直接保存也能成功，`data.json` 里存的是输入原文（证明不做自动纠正）；c) 保存后「品牌总览」品牌数 +1。
15. 回归三条既有链路：选模板、智能解析粘贴、聊天面板录入 → 各自都能把品牌写进 `#fBrand` 并正常保存，无需在面板里再点一次。

**回归**
16. 原有四个 tab 的数字（总成本、总利润、排行榜排序）与改动前一致 —— 新字段不参与既有聚合。

## 10. 明确不做

- 不改 `xianyu_scraper.py` 的抓取逻辑与 `xianyu_orders` 表结构。
- 不在账本里引入 MySQL / `pymysql` 依赖。
- 不做多币种、退款、卖出侧（`trade_type`）区分 —— 目前只导入买入单。
- 不改「品牌总览 / 芯片组 / 排行榜」的聚合口径。
- 不做品牌别名归一化 / 自动纠错（不建 `MSI → 微星` 映射表，也不批量改已有记录）。选择器只降低输入成本，不替他改写已入库的数据。
- 不把 `xianyu_review.py`、`xianyu_scraper.py`、`xianyu_review.html` 纳入 git（源码里硬编码了 MySQL 明文账号口令，抽成配置之前不动）。
- 不往真实 `data.json` 写任何测试数据；验证只在他本来就要核对的 18 条上做，配合 `.bak` / `.pre_backfill` 兜底。

## 11. 影响面与重启

| 组件 | 改动 | 生效方式 |
| --- | --- | --- |
| `app_standalone.py` | 白名单 + 新端点 | **必须重启 pid 63240**（pywebview 不热加载 Python） |
| `index.html` | 新 tab、胶囊、拆单弹窗、徽章、品牌选择器 | 刷新即可（`send_html` 每请求重读文件） |
| `xianyu_review.py` | 导入补两字段 | **必须重启 pid 48924** |
| `data.json` | 18 条回填 + 新增只读键 | 回填脚本 `--apply`，先留 `.pre_backfill` |
| 新增 `xianyu_backfill.py` | — | 一次性工具，可提交 |

回退路径：`data.json.pre_backfill` 原样覆盖回 `data.json` 即回到迁移前状态；代码侧回退用 git revert。

## 12. 风险

1. **回填依赖 MySQL 里那 18 个 `order_id` 还在**。若 `xianyu_orders` 被清过，部分记录只能保持"知道来自闲鱼但不知道哪天一"，脚本会明确报数而不是静默填空。
2. **`order_paid` 冻结基线是新概念**，拆单上线前已存在的记录没有它，相关组头会少一列显示（已在 §8 降级处理）。
3. **两组人同时改 `data.json` 会互相覆盖**：账本窗口与核对页各自 `save_data`，最后写入者赢。当前是他单人顺序操作，可接受；不做文件锁。
4. 未核对标记是可选项，若实际用下来觉得多余，删掉 `reviewed` 相关分支不影响其余功能。
