# 配件知识库（品类→品牌→型号）设计

日期：2026-09-25
状态：待李秉晋审阅
范围：`app_standalone.py`、`index.html`、`data.json`、新增 `catalog.json`、新增一次性迁移脚本

## 1. 要解决的问题

现在记账时品牌/型号全靠手输，两个后果：

1. **重复劳动**：`B650m-b` 一笔就记了 39 次，每次都要打字。
2. **统计口径碎**：同一块板子被拆成多个键，品牌总览和型号总览的数字不可信。

账本里的真实脏值（2026-09-25 从 `data.json` 统计，非推测）：

| 现象 | 证据 |
|---|---|
| 大小写拆统计 | `微星 H610m-E` 9 笔 + `微星 H610m-e` 6 笔 = 同一块板 15 笔 |
| 空格/词序拆统计 | 铭瑄 B760M 终结者 D4 有 5 种写法：`B760m 终结者D4 wifi` / `B760M 终结者D4` / `B760M-D4终结者主板` / `B760m 终结者 D4` / `B760m终结者D4 wifi` |
| 品牌别名拆统计 | `INTER` 6 条 + `英特尔` 1 条，实为同一品牌；`凯侠` 8 条（正字为「铠侠」） |
| 型号字段混装备注 | `b650m gamingplus Wi-Fi主板 时好时`、`mag b650m mortar 针脚坏`、`B650m-b(7500F)` |
| 结构性缺口 | 255 条记录**没有品类字段**，字段仅 `brand/model/cost/sell/sn/accessory/accessory_price/extra_price/images/source_order_id` |

规模：255 条记录、127 个不同 `(brand, model)` 组合，其中 98 个只出现 1 次。

## 2. 目标与非目标

**目标**

1. 录入时从知识库选，最少打字（打两个字就能命中）。
2. 品牌总览 / 型号总览按规范名聚合，把上面那些拆统计并掉。
3. 每条记录有品类，能按品类看库存和利润。
4. 知识库可由他在界面上自己维护，不需要改代码。

**非目标（本轮明确不做）**

1. **不做成色/故障/状态**。`h610 坏板`、`时好时`、`拆机换下来的` 这类文字原地留在 `model` 里，不新建状态字段、不剥离、不判定。
2. **不重写 `model` 字段的任何历史值**。归并只发生在统计层。
3. **不做内存规格引擎**。`海力士 Adie 16G*2` 整串当一个型号收录，不拆颗粒厂/容量/频率。
4. **不考证厂商官方型号名**。规范名取账本内出现次数最多的写法，要改成正式名由他在管理页改。
5. 不做行情价、库存数量、撤销 UI、多设备同步。

## 3. 品类清单（8 类）

按真实数据判定，255 条全覆盖，无遗漏：

| key | 名称 | 条数 | 判定依据 |
|---|---|---|---|
| `board` | 主板 | 166 | 芯片组号 `B650/H610/X870E/Z790/A620` 等 |
| `cooler` | 散热 | 25 | `liquid R360` 24 笔、`360水冷` |
| `ram` | 内存 | 25 | 含 `海力士/镁光/长鑫/三星`、`*2`、容量+频率 |
| `ssd` | 固态硬盘 | 13 | `sd10/rc20/se10/vd10/gm7/gm7000/sn5000/nv2/AS806` |
| `cpu` | CPU | 12 | `5600x/2700X/7500F/9700X/12100f/12400f/14600kf/8600k` |
| `bundle` | 板U套装 | 10 | `H610m-E(12400)`、`B650m-b(7500F)`、`I512490F盒装搭配微星PRO H610M` |
| `gpu` | 显卡 | 2 | `Rtx5070 战斧`、`Rtx5070 魔龙` |
| `unknown` | 待确认 | 2 | 空记录、`CPU针接触不良返场维修一次` |

`bundle` 必须独立：这 10 条的成本是两块硬件合并的，并进主板会虚增主板利润、并进 CPU 会丢掉板子。

`unknown` 是兜底类，允许存在但必须可观测：顶部要有「品类待确认 N」的入口（见 §9 第 6 步）。`cat` 的合法取值只有 §3 表里的 8 个 key；记录里 `cat` 缺失或为空串，与 `unknown` 同等对待，一起进「品类待确认」。

## 4. 知识库文件 `catalog.json`

与 `data.json` 同级，单独文件，纳入 git 跟踪（同 `data.json` 的处置）。结构：

```jsonc
{
  "version": 1,
  "categories": [
    { "key": "board", "name": "主板" },
    { "key": "cpu",   "name": "CPU"  }
    // ... §3 的 8 项
  ],
  "brands": [
    { "canonical": "微星",   "aliases": ["MSI"] },
    { "canonical": "英特尔", "aliases": ["INTER", "Intel", "intel"] },
    { "canonical": "铠侠",   "aliases": ["凯侠", "Kioxia"] }
  ],
  "parts": [
    {
      "cat": "board",
      "brand": "微星",
      "name": "B650M-B",
      "aliases": ["B650m-b", "b650m-b", "PRO B650M-B 主板", "B650m-b(7500F)"]
    }
  ]
}
```

规则：

- **唯一键 = `(brand, name)`**，不设 id。改名时旧 `name` 自动进 `aliases`，所以历史记录仍然能归并过来。
- `cat` 挂在 part 上，不在运行时推断。
- `parts[].brand` 必须是某个 `brands[].canonical`，加载时校验，不满足的 part 在启动日志里报出来（不静默丢弃）。
- 播种分两遍：
  - **第一遍（脚本）**：把 127 组 `(brand, model)` 按 `N()` 归一分组，`name` 取组内出现次数最多的原写法，其余进 `aliases`。实测这一步只能从 127 组机械并到 **122 组** —— 说明纯靠大小写/空格归一远远不够，铭瑄那 5 种写法、`B650m迫击炮wifi` 与 `B650m mortar` 这类同物异名必须靠第二遍。
  - **第二遍（人工过一遍，他确认）**：手工合并剩下的同物异名、给品牌做归一（`INTER`→`英特尔`、`凯侠`→`铠侠`）、给每条 part 定 `cat`。空 `model` 的 1 条记录不生成 part。
- `catalog.json` 初稿由脚本生成后，人工过完才算 P1 完成；不能拿脚本裸输出当交付。

## 5. 归并解析 resolve(model)

前端统计层新增一个纯函数，后端不需要：

```
N(x) = 大写 + 去掉所有空白 + 全角括号转半角
resolve(brand, model):
  1. B = brand 命中 brands（canonical 或 aliases），取 canonical；未命中原样用
  2. 取该 B 名下所有 part，算 N(name) 与 N(alias)
  3. 精确命中 N(model) → 该 part
  4. 前缀命中：N(model) 以某个 N(name)/N(alias) 开头 → 取匹配串最长的那个 part
     （覆盖 `b650m gamingplus Wi-Fi主板 时好时` 这类带尾注的原值）
  5. 都没命中 → 返回 null，统计时按原值自成一类
```

第 4 步的"最长匹配优先"是硬要求：库里同时存在 `B650M-B` 与 `B650M-B PRO` 时，`b650m-b pro 带挡板` 必须落到后者。

`buildBrands()`（`index.html:1390`）和型号总览（`:1611`）改为按 `resolve()` 的 `canonical` 聚合；`resolve()` 返回 null 时退回现行为（用原值），保证没有库也能用。

`BRAND_STYLES`（`:1447`，现仅 7 个键，查表用 `brand.toLowerCase()`）补上所有 canonical 品牌的小写键，否则品牌归一到中文后图标掉成灰色兜底。

## 6. 后端接口

`catalog.json` 是独立文件，版本协议与 `data.json` 分开：

- `GET /api/catalog` — 返回整个库，响应头带 `X-Catalog-Stamp`（算法复用 `file_stamp()`：字节数-sha256 前 16 位）。
- `POST /api/catalog/upsert` — body `{brand, name, cat, aliases?, old_name?}`；用于管理页改规范名/加别名，以及录入时"库里没有→新建"。走与现有五个写盘端点相同的固定 stamp 范式：先 `file_stamp()` 采样 → `reject_if_client_stale()` → 读库 → 校验 → 写盘。带 `If-Match` 不符回 **409**，不带 `If-Match` 必须放行（导入页与 agent 不知道版本）。
- `POST /api/catalog/delete` — body `{brand, name}`；该 part 被记录引用时（`data.json` 里存在 resolve 命中它的记录）**回 400 并在响应里带 `{"referenced_by": N}`**，不允许静默删除。这里刻意不用 409：本项目的 409 专指账本版本冲突，前端 `handleConflict()`（`index.html:1205`）会按"丢表单重拉"处理，用它表达"被引用"会被吞掉。

`GET /api/data` 必须把 `cat` 透传给前端，做法与当初 `images` 一致：`parseItems` 显式读取 `r.cat`（`index.html:1191` 附近），缺省为 `""`。

`/api/add_record`、`/api/update_record`、`/api/split_record` 接受可选 `cat`；`split_record` 的每个 part 各带一个 `cat`，并随订单上下文字段一起继承给追加的新记录（与 `source_order_id/order_date/item_title/order_paid` 同一处理，`app_standalone.py:497-575`）。

## 7. 录入组件（形态 B）

新增/编辑弹窗里，`#fBrand` 与 `#fModel` 两个输入框**保留**（成色备注还要靠手输），在它们上方加一个「配件」搜索框：

- 空框聚焦：面板显示最近使用 top 6（按 `data.json` 里该 part 的记录数排），末尾固定一项 `＋ 库里没有？输入新名字回车入库`。
- 输入时：跨品牌/型号/别名做子串匹配（用 §5 的 `N()` 归一后比较），结果显示 `品牌 图标 + 规范型号 + 品类徽标 + 已记笔数`。
- 选中：自动回填 `品牌`、`型号`、`品类` 三格，焦点留在搜索框，由他 Tab 走到下一个字段。
- 手动改：他仍可自由编辑 `品牌`/`型号` 两格，改动不写库；只有面板里点 `＋ 新建` 才调 `catalog/upsert`。
- 键盘：`↑↓` 移动、`Enter` 确认、`Esc` 只关面板不关弹窗（扩展现有 Escape 分流 `index.html:2096-2104`）。
- 面板打开期间不置 `_ledgerBusy`，不触发写盘；`_formStamp` 逻辑不受影响。

拆单弹窗每一行（`index.html:1892` 的 `mk('split-brand', ...)`）复用同一组件，行内自带一个品类下拉（拆出来的子件可能分属不同品类）。

## 8. 管理页（形态 C）

账本顶部新增 tab「配件库」：

- 品类 Tab 横排，Tab 上带该品类记录数（如 `主板 166`）。
- 型号卡片网格：规范名、品牌图标、笔数、均价、别名数量。
- 点卡片展开：改规范名（旧名自动进别名）、加/删别名、改品类、删除（走 §6 的引用保护）。
- 顶部搜索框，同一套 `N()` 匹配。

## 9. 存量迁移（唯一动 `data.json` 的部分）

新增一次性脚本 `migrate_add_cat.py`，默认 dry-run：

1. **前置检查点**：确认工作区无未提交改动 → `git commit` 一次 `data.json` → 记录 SHA。
2. **dry-run**：输出 255 行报告（`下标 | 原 brand | 原 model | 判定 cat | 命中 part | 置信度`），不写文件。断言脚本运行前后 `data.json` 的 SHA 不变。
3. **判定顺序**：`bundle` 规则（含 `(CPU型号)` 或 `套装`）最先判；其余按 §3 的关键字规则；关键字冲突或全部未命中 → `unknown`。
4. **人工复核**：他确认报告后，脚本 `--apply` 写盘。写盘用与后端相同的 stamp 采样 + `If-Match` 语义（脚本自己带最新 stamp，实际是本地独占写；写盘期间要求账本窗口不处于表单编辑态，脚本执行前打印提醒）。
5. **落盘后校验**：记录数仍 255；每条都有 `cat` 键；除 `cat` 外其他字段逐条与原值相等（脚本内断言，不是事后人看）。
6. **`unknown` 的收尾**：账本顶部出现「品类待确认 N」chip（复用"未补售价"那套可发现机制，任务 #11 已建），点开筛出这些行，行内下拉改判，走现有 `update_record` + `If-Match`。

回滚：`git checkout <检查点SHA> -- data.json`，或从 IDE 回退到该提交。

## 10. 与闲鱼导入的衔接

`xianyu_review.py` 的 `split_title()`（`:147`）已能给出 `(brand, model)`。导入时多带一个 `cat`：按 `catalog.json` 的 part 命中结果取；未命中留空，落到「品类待确认」而不是硬猜。品牌别名归一在导入侧也走同一份 `catalog.json`（读文件，不走 HTTP，避免 8766 依赖 8765 活着）。

## 11. 测试

沿用现有沙盒策略：`python -X utf8 -m pytest "G:/claude code/tests/test_ledger_api.py" -q`，conftest 兜底 + 真实 `data.json` SHA 前后比对断言（现 176 项）。

新增用例：

- `resolve()`：`H610m-e` 与 `H610m-E` 归一到同一 part；铭瑄 5 种写法归一；`b650m gamingplus Wi-Fi主板 时好时` 前缀命中；`B650M-B` 与 `B650M-B PRO` 的最长匹配优先；未命中返回 null。
- 品牌归一：`INTER` → `英特尔`；`凯侠` → `铠侠`。
- 统计回归：`buildBrands()` 归一后 `H610M-E` 是 15 笔而不是 9+6 两行。
- catalog API：无 `If-Match` 放行；stamp 不符 409；改规范名后旧名进 `aliases` 且仍可 resolve；删除被引用的 part → **400** 带 `referenced_by`，且前端不把它当成版本冲突。
- `catalog.json` 装载：`parts[].brand` 不在 brands canonical 列表时，启动有告警且不崩。
- 迁移脚本：dry-run 后 `data.json` SHA 不变；`--apply` 后记录数 255 不变、每条含 `cat`、非 `cat` 字段逐条相等；`bundle` 至少覆盖那 10 条。
- 前端静态契约（沿用 `:1169-1270` 的写法）：配件搜索框存在且关联 `#fBrand`/`#fModel`；Escape 分流包含配件面板分支；拆单行内含品类下拉。
- 浏览器端到端：夹具服务上走一遍「搜索→选中→保存→落盘含 cat」和「库里没有→新建入库→再选」，与任务 #28 的做法一致。

## 12. 分阶段交付

每阶段独立提交、独立可验证，前一段不依赖后一段：

1. **P1 库与归并**：`catalog.json` + 播种脚本 + `resolve()` + `buildBrands()`/型号总览按 canonical 聚合 + `BRAND_STYLES` 补键 + catalog 只读接口。不动表单、不动 `data.json`。验收：统计里 `H610M-E` 变 15 笔。
2. **P2 录入组件**：配件搜索框接进新增/编辑/拆单，`catalog/upsert` 新建入库，`cat` 字段全链透传。验收：记一笔新单全程不手打型号。
3. **P3 存量迁移**：`migrate_add_cat.py` dry-run → 他复核 → apply → 「品类待确认」收尾视图。验收：255 条全部有 `cat`，`unknown` 为 0 或他认可的残余。
4. **P4 管理页**：配件库 tab（改名/别名/改品类/删除 + 引用保护）。

## 13. 风险

| 风险 | 处置 |
|---|---|
| 迁移写坏真实账本 | 写盘前 git 检查点；写盘后三重断言（条数/含 cat/其他字段逐条相等）；可 `git checkout` 回退 |
| `resolve()` 前缀匹配认错型号 | 最长匹配优先；管理页可看到每条命中了哪个 part；错的可直接加别名纠正，不用改代码 |
| 播种脚本把脏值当规范名固化 | `name` 取最高频写法但不改字形，正字（铠侠/英特尔）只在品牌层做；part 层要改字由他在管理页改，旧名自动留别名 |
| 面板与焦点自动同步/`_ledgerBusy` 打架 | 面板不写盘、不置忙；Esc 只关面板；沿用 `FORM_MODALS` 与拆单的既有分流实现 |
| 新增字段像 `order_paid` 那样漏传导致前端读不到 | `parseItems` 显式读 `cat`，并加静态契约测试断言这一点 |
