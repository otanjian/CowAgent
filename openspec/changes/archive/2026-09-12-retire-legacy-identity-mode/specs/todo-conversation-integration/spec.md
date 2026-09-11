## MODIFIED Requirements

### Requirement: Agent 使用可信请求上下文操作本人数据

系统 SHALL 仅在 todo_enabled、既有工具启用配置及当前合法 Web 聊天能力均允许时提供 todo 的 create、list、get 动作。服务端 MUST 从经过验证的独立请求上下文取得 scope、人工 owner、Agent 和会话来源，并采用与 Web 相同的权限及持久化规则；既有 user/tenant 身份 SHALL 为唯一主体来源，MUST NOT 映射为本地所有者。系统 MUST NOT 跨请求复用可变主体或来源，不接受模型自报的用户、租户、目录或其他会话作为授权。缺少可信人工委托的 scheduler、外部通道、后台运行和免登录入口不得调用本工具；隐藏工具之外，直接调用也 MUST 拒绝。

#### Scenario: 并发请求保持个人隔离
- **WHEN** 两个用户或租户的 Web 请求交错调用同一个工具实例
- **THEN** 每次调用只使用各自的可信请求上下文，创建绑定本人的事项，list/get 只返回本人授权内容，不串用其他请求的主体或来源

#### Scenario: 伪造主体或未满足能力条件
- **WHEN** 调用缺少可信 Web 委托、当前工具或聊天能力未开放，或携带与可信上下文冲突的 owner、租户、来源参数
- **THEN** 系统拒绝对应请求，不回退匿名、默认 Agent 或全局空间，也不借待办开启未验收的聊天或后台消费者
