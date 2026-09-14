## MODIFIED Requirements

### Requirement: 管理写统一来源与 CSRF 校验

所有改变状态的管理写请求（平台控制台与租户控制台的写接口、场景激活/导入、控制台工作区文件保存 `POST /api/workspace/write` 等）SHALL 经由同一处来源校验入口（`channel/web/auth_handlers.py` 的 `require_management_write`），MUST NOT 由各接口自行判定或默认放行。校验 SHALL 在任何上下文解析与写入之前执行。

规则：以 cookie 会话认证的写请求 SHALL 提供与请求 `Host` 同源的 `Origin` 或 `Referer`；缺失来源 SHALL 被拒绝（这些接口没有独立的 CSRF token 流程，因此以缺失即拒为默认）。以 bearer 凭据认证的写请求 SHALL 在 bearer 真实认证时豁免来源校验，以支持来源永不匹配的原生客户端；裸的、格式非法的或与 cookie 同值的重复凭据 MUST NOT 获得豁免。legacy 模式的写路径 SHALL 保持既有行为不变。

#### Scenario: 无来源的 cookie 写请求

- **WHEN** cookie 会话发起管理写请求但不带 `Origin`/`Referer`
- **THEN** 返回 403（`csrf_failed`），且不产生任何写入

#### Scenario: 跨来源的 cookie 写请求

- **WHEN** cookie 会话发起管理写请求，`Origin` 与请求 `Host` 不同源
- **THEN** 返回 403（`csrf_failed`），且不产生任何写入

#### Scenario: 同来源的 cookie 写请求

- **WHEN** cookie 会话发起管理写请求，`Origin` 或 `Referer` 与请求 `Host` 同源
- **THEN** 请求按既有业务规则继续处理

#### Scenario: bearer 客户端写请求

- **WHEN** 请求以真实有效的 bearer 凭据认证（无 cookie），即使来源与 `Host` 不同源
- **THEN** 来源校验豁免，请求按既有业务规则继续处理

#### Scenario: 伪造或不一致的凭据不获得豁免

- **WHEN** 请求同时携带 cookie 与无效 bearer，或 bearer 与 cookie 同值
- **THEN** 仍按 cookie 规则校验来源；不满足时返回 403 且不产生写入

#### Scenario: 控制台工作区文件保存被来源校验覆盖

- **WHEN** 控制台以 cookie 会话提交 `POST /api/workspace/write`，但 `Origin`/`Referer` 缺失或与 `Host` 不同源
- **THEN** 返回 403（`csrf_failed`），文件内容不被写入；同源请求按工作区边界规则继续处理
