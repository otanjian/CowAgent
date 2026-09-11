# 配置字段来源清单

复核日期：2026-09-08。记录控制台配置页面（`view-config`）与外观、个人偏好涉及的字段来源、存储键、写入接口、作用域与模式可用性。用于 phase 0（1.3）及 B4 配置职责拆分核对。

> 所有数据均来自脱敏代码快照；本清单不复制任何密钥值。

## 配置作用域图例

- `platform`：部署/全局配置，存储在服务端 `/config`（`appConfig`），影响全部运行时。
- `single-agent-default`：智能体全局默认参数（写入 `/config` 或 agent 默认配置）。
- `single-agent-override`：单体智能体覆盖（写入 agent 配置 / Registry）。
- `personal`：浏览器本地偏好（localStorage + 部分同步规则），同浏览器账号共用。
- `brand`：品牌快照（独立存储/发布）。

## `/config`（`view-config`）字段来源

| 字段 | 存储键 | 写入接口 | 作用域 | 模式 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `agent_permission_mode` | `/config` | `POST /config` | platform→agent 执行 | legacy 可用 | console.js:11200 `saveGlobalPermission` |
| `web_password` | `/config` | `POST /config` | platform 共享访问密码 | legacy | console.js:11226 掩码 `web_password_masked` |
| 模型供应商/密钥/能力 | `/config` 模型段 | `POST /config` | platform | legacy | `initConfigView`（10778）读取 |
| 最大上下文/记忆轮次/执行步数/思考/子智能体/自主进化/默认执行范围 | `/config` 智能体段 | `POST /config` | platform→agent 默认 | legacy | 智能体全局默认 |
| 执行策略（agent_permission_mode） | `/config` | `POST /config` | platform | legacy | 见上 |

## 个人偏好（浏览器本地）

| 偏好 | 存储键 | 存储位置 | 作用域 | 备注 |
| --- | --- | --- | --- | --- |
| 界面语言 | `cow_lang` | 浏览器本地 | 同浏览器共用 | console.js:2635 本地更新；存在 `/config` 运行语言链路的部分拆分为“运行语言” |
| 任务通知 | `cow_task_notify` | 浏览器本地 | 同浏览器共用 | console.js:2899 |
| 通知声音 | `cow_task_notify_sound` | 浏览器本地 | 同浏览器共用 | console.js:2900 |
| 主题/配色/字号 | appearance 偏好 | 浏览器本地 | 同浏览器共用 | `appearance.js`/`appearance.css` |
| 智能体选择、租户指针 | 原隔离规则 | 浏览器本地 | 按身份/隔离 | 业务指针，不作为全局偏好 |

## 运行语言 vs 界面语言

- **界面语言**：仅改本地展示，`POST /config { updates: { cow_lang } }` 用于展示切换（console.js:2635）。
- **运行语言**（影响命令/系统提示词）：仍属 `/config` 运行配置，需在“运行语言”部署页独立保留，不合并到个人偏好。

## 品牌

- `brand_name` / `brand_logo` 等：`branding` 模块，独立快照 + `POST /branding` 发布，含版本与访问边界。见 `add-branding-settings`。

## 现有保存/版本合同

- `/config` 走现有 `POST /config` 保存，需保留版本条件、掩码与失败反馈。
- 智能体默认/覆盖沿用 Registry 与原文件 API（`agents` 视图），不发明新覆盖字段。
- 个人偏好沿用 `cow_lang` / `cow_task_notify` / `cow_task_notify_sound` 原键及同步规则，**不新增用户/租户前缀**。
