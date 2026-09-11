# 模式验收记录（4.6）

## 结论

本 change **不新增任何功能开关 / feature flag**（符合「本期不新增功能开关」的要求）。交付模式为 **legacy**（单实例、共享密码），`database` 身份模式未在本期验收范围内，因此**不宣称可交付**该模式。

## 1. 身份模式判定

判定函数为 `channel/web/web_channel.py` 的：

```python
def _is_database_identity() -> bool:
    return str(conf().get("identity_mode", "legacy") or "legacy") == "database"
```

- 本机 `config.json` 未配置 `identity_mode`，取默认值 `legacy`。
- 因此 `_is_database_identity()` 恒为 `False`，走共享密码路径（`web_password`）。

## 2. Legacy 模式能力验收

Legacy 模式下工作台列表所有**已保存且启用**的智能体 `can_chat = True`。

- 验收服务（`http://127.0.0.1:9897`，临时数据根）返回：

```json
{"status":"success","agents":[
  {"id":"default","name":"RongAI","description":"","avatar":null,"is_default":true,"can_chat":true,"unavailable_reason":null},
  {"id":"erpnext","name":"ERPnext助手","description":"负责ERPnext相关问题的处理","avatar":null,"is_default":false,"can_chat":true,"unavailable_reason":null}
]}
```

- 浏览器验收：点击卡片「开始对话」后成功进入 `chat` 视图，`localStorage.cow_active_agent = "erpnext"`，chat 头部展示身份「ERPnext助手」，构成完整闭环。
- 既有聊天能力（多智能体、历史会话、后台流式回复）未受影响（见 4.2/4.3/4.5 测试）。

## 3. Database 模式处理（未交付，但预留边界）

- `_workbench_chat_readiness(agent_id)` 为载体：当前 legacy 恒返回 `(True, None)`。
- 若未来启用 database 身份模式，该函数须读取其运行门控：运行未开放时返回 `(False, "runtime_not_enabled")`，服务端独立 `_require_auth()` / 身份校验拒绝运行，**不通过客户端参数或默认管理响应绕开边界**。
- 前端已在卡片渲染时根据 `can_chat` / `unavailable_reason` 显示「暂不可用」并**移除 onclick**（禁用启动），与服务端拒绝保持一致。

## 4. 相关链接

- `channel/web/web_channel.py::_workbench_agents_projection`、`_workbench_chat_readiness`、`_is_database_identity`
- `channel/web/static/js/console.js::loadAgentWorkbench`、`agentWorkbenchCardHTML`、`startChatWithAgent`
- `tests/test_agent_workbench.py`
- 基线文档：`evidence/menu-navigation-baseline.md`、`evidence/data-ownership-baseline.md`
