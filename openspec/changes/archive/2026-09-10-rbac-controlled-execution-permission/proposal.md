## Why

database 多租户模式下，工具能否执行已由角色授权（`tool.execute` + 资源 grant）、租户执行隔离和配额决定，但会话里仍保留 legacy 的「自行调整权限」入口（输入框权限 chip 与「调整权限」按钮），并且旧权限模式会先于角色授权拒绝调用。结果是：按角色配置本可执行的工具被模式拦下，界面又提示用户"调整权限"，而该提示在角色控制下并不能真正解决问题，反而暗示可以绕过角色授权。

## What Changes

- database 身份模式下，工具执行不再应用 legacy 会话权限模式（`read-only` / `workspace-write` / `full-access`）；执行授权由角色 `tool.execute` + 资源 grant、租户执行隔离与配额决定。legacy 单租户安装保持原模式行为不变。
- Web 控制台与 Desktop 移除会话内"自行调整权限"入口：输入框权限选择器与「调整权限」按钮；工具被拒提示保留文字说明（无操作按钮）。
- 被拒提示在 database 模式下改为说明"当前角色未获授权"（由角色/隔离拒绝），不再显示可能误导的会话权限模式与调整入口。
- 平台设置页「默认权限」（`agent_permission_mode`）在 database 模式下改为只读说明"执行权限由角色资源授权控制"；legacy 保持可编辑。
- 会话设置接口在 database 模式下不再接受会话级 permission 覆盖。
- **BREAKING**（仅 database 模式）：此前会话级保存的权限覆盖不再生效；执行能力改由角色决定。

## Capabilities

### New Capabilities
- `execution-permission-console`: 控制台/桌面端不再提供会话内自行调整执行权限的入口；被拒提示只解释原因；全局默认权限在 database 模式下只读。

### Modified Capabilities
- `resource-execution-authorization`: 数据库模式的运行时执行授权由角色 `tool.execute`/资源 grant 与租户隔离决定，legacy 权限模式不参与；legacy 单租户行为保持。

## Impact

- `agent/protocol/agent_stream.py`：`_permission_denial` 分模式分支；被拒事件携带的 mode/原因。
- `channel/web/web_channel.py`：会话设置（`/api/sessions/<id>/settings`）与配置接口（`/api/config`）的 database 模式语义。
- `channel/web/chat.html`、`channel/web/static/js/console.js`、`channel/web/static/css/console.css`：移除权限 chip、调整按钮与相关样式/文案。
- `desktop/src/renderer/src/components/ChatInput.tsx`、`PermissionSelector.tsx`、`MessageSteps.tsx`、`store/sessionSettingsStore.ts`、`i18n.ts`：同步移除。
- 测试：`tests/` 执行授权/隔离/前端 cjs。
