## Why

租户默认智能体已经存在、且决定了「进入对话不选智能体时锚定谁」，但系统里没有任何地方能改它：`tenants.default_agent_id` 只能由 `appoint_tenant_default_agent` 写入，而全仓唯一调用点是「租户创建第一个智能体时自动承接」。结果是租户一旦有了默认智能体就再也换不掉——管理员在「智能体配置」页看到「默认」徽标，却没有任何入口改变它，只能接受按最小稳定 id 抖动出来的那个默认（例如 `business-analysis`）。

## What Changes

- 在控制台「智能体配置」页的智能体详情操作区新增「设为默认」：把**当前租户**的默认智能体显式改到所选智能体，复用既有 `appoint_tenant_default_agent`（绑定校验、清除私有归属、`tenant.set_default_agent` 审计全部沿用，不新增第二处写入逻辑）。
- 复用既有 `POST /api/agents` 增加 `set_default` 动作，不新增路由；操作者沿用既有租户管理员 / 平台管理员门槛，不放开给普通成员。
- 任命即显式动作，界面不做二次确认；已是默认的智能体不显示该操作。
- 默认智能体被删除时不再留下脏指针：允许删除，删除成功后清除所有指向该 `agent_id` 的 `tenants.default_agent_id`，缺省解析按既有规则回落到其它可用绑定。模板默认（`registry.default_agent_id`）不可删除的既有规则保持不变。
- 明确「默认标识同源」：配置页徽标、工作台排序与聊天锚定都必须来自租户默认，不得各自另算。

## Capabilities

### New Capabilities
- `tenant-default-agent-administration`: 控制台对当前租户默认智能体的显式任命入口、权限门槛、任命副作用（转租户共享）与「默认标识同源」约束。

### Modified Capabilities
- `agent-chat-launch`: 「租户恒有可解析的默认智能体」补充「默认指针随智能体删除清理、不留脏指针」的场景，明确删除默认智能体后缺省解析的回落行为。

## Impact

- 后端：`channel/web/web_channel.py`（`POST /api/agents` 新增 `set_default` 动作；删除成功后清理租户默认指针）、`auth/service.py`（新增清除 `tenants.default_agent_id` 引用的服务方法；沿用既有 `appoint_tenant_default_agent`）。
- 前端：`channel/web/static/js/console.js`（`renderAgentDetail` 操作区新增「设为默认」并按 `isDefault` 隐藏；调用后重载目录并提示）、`channel/web/static/js/i18n/agents.js`（三语文案）。
- 测试：新增 `tests/test_tenant_default_agent_selection.py`（任命权限、绑定校验、审计、删除清指针与释放绑定、缺省回落），并更新既有智能体配置前端断言。
- 不改动：`agent_bindings` 表结构、私有归属的显式设置路径、租户编辑页的复制/承接默认流程、「个人偏好默认智能体」路线（本次不做）、模板默认的模型/知识库特殊化规则。
