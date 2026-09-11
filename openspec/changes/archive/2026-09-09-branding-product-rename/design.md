## Context

`rdai` 分支已将产品品牌从 `CowAgent` 替换为「容大AI」/ `RongAI`。既有 `branding-settings`（已归档）只覆盖 Web 运行时品牌设置，并在其 spec 中明确排除桌面 React 品牌位、桌面安装图标、托盘图标、文档站与各 Channel 的既有品牌行为。因此产品品牌名在 CLI、网络 User-Agent、MCP/OAuth 客户端标识、模型请求头、飞书渠道与桌面端资产上的横切替换，属于未被任何 change 覆盖的改动，需单独建模。

本 change 的代码改动已在 `rdai` 分支实现，本 design 记录实际实现范围与取舍，不声明未实现能力。

## Goals / Non-Goals

**Goals:**
- 将面向用户与对外网络的产品品牌名从 `CowAgent` 统一为「容大AI」/ `RongAI` / `Rongda AI`。
- 替换桌面与 Web 的品牌 Logo / 品牌组件资产。
- 保证重命名不改变功能行为、配置、协议、路由与数据归属。

**Non-Goals:**
- 不改变 `branding-settings`（已归档）的 Web 运行时品牌设置规范——本 change 补全它明确排除的桌面/Channel/CLI/网络品牌位。
- 不在本 change 引入新的品牌管理界面或运行时品牌切换逻辑（那属于 Web 运行时品牌设置）。
- 不迁移或改写业务数据、会话、身份或租户模型中的品牌字段（本 change 不存在此类数据模型）。

## Decisions

### 决策 1：品牌字符串横切替换（非行为变更）
采用简单字符串替换把 `CowAgent` 改成中文「容大AI」与拉丁 `RongAI`/`Rongda AI`，而不是引入品牌配置抽象。因为品牌名是展现与网络标识字符串，不涉及运行时切换；既有 `branding-settings` 已提供 Web 运行时品牌能力，本 change 只做静态品牌统一。

### 决策 2：拉丁写法允许三种兼容形式
`RongAI`、`Rongda AI`、`容大AI` 视场景选用：CLI/界面用中文「容大AI」，网络 User-Agent 与客户端标识用 `RongAI`，个别产品标题用 `Rongda AI`。三者都是同一个品牌「容大AI/容大智能」的对外写法，不视为冲突，验收时统一排除主品牌 `CowAgent`。

### 决策 3：资产替换沿用既有组件结构
桌面端 `BrandMark.tsx`、`logo.png`、`rongda-ai-mark.svg` 替换为新的品牌资产，Web 端 `favicon.ico`、`logo.jpg` 同步。不重建组件架构或主题系统，仅替换资源与引用。

### 决策 4：纯字符串替换优先于 DB/配置迁移
品牌名不是持久业务字段，无数据迁移。配置键、路由、会话/身份模型保持不变。

## Risks / Trade-offs

- **[三种拉丁写法并存]** → `RongAI`、`Rongda AI`、`容大AI` 并行，若后续要严格统一单一名可能需再改。验收按「不出现 `CowAgent` 主品牌」为硬性标准。
- **[README/文档策略性停止跟踪]** → 若干 README 已 gitignore 或删除，文档品牌文本不在本 change 强制范围，避免与既有 README 同步策略冲突。
- **[外部第三方对 `CowAgent` 标识的依赖]** → 若第三方服务识别旧 `CowAgent` User-Agent 或 `X-Title`，改品牌后可能影响其识别。本期按产品统一执行，若第三方兼容问题出现再按渠道处理。
