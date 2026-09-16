## ADDED Requirements

### Requirement: 微信个人执行按实际类型与作用域独立验收

微信个人 QR SHALL 复用已交付的个人实例、身份挑战、配额及治理服务；配置可保存 MUST NOT 等同于个人执行已开放。个人实例执行与公共实例承载个人入口 SHALL 分别取得提供方、版本、scope 和连接形态的真实验收证据；公共扫码或公共收发成功 MUST NOT 替代本人私聊、他人/群聊拒绝、解绑及治理停用的个人执行验收。

本 change SHALL 承接所承诺微信范围的剩余真实执行验收，复用既有逐类型验收记录、`personal_runtime_enabled()`、`public_personal_ingress_ready()` 及部署总开关的单一判定。类型记录 MUST NOT 自动打开部署开关或扩大至未验收提供方、版本或 scope。依赖 change 因保持执行关闭而结项 MUST NOT 作为本 change 已覆盖的证明；外部条件不足时相应实施任务 SHALL 保持未完成。

本条由 `complete-database-capability-parity` 部分归档时移交本 change：移交改变承接方，MUST NOT 降低其验收要求，MUST NOT 使未取得真实验收的类型或 scope 由“已移交”变为可用。

#### Scenario: 配置完成但个人执行未验收
- **WHEN** 个人渠道配置已经保存，依赖 change 已结项，但该类型的个人执行没有真实验收记录
- **THEN** 配置保持可管理，执行继续关闭并报告已保存未连接，不因任务勾选或扫码成功自动连接

#### Scenario: 仅公共范围完成验收
- **WHEN** 某微信提供方仅有公共实例扫码和收发证据
- **THEN** 仅允许该已验收范围，个人实例执行和公共实例个人入口不据此开放

#### Scenario: 逐类型记录与部署开关分别生效
- **WHEN** 某类型的个人执行已经验收，但部署总开关仍关闭，或请求使用另一未验收类型/版本/scope
- **THEN** 对应执行仍被拒绝，其他已交付目录及配置不被误报为未实现

#### Scenario: 移交不改变验收门槛
- **WHEN** 本 change 由前序 change 部分归档接手该 requirement
- **THEN** 未验收类型/版本/scope 的配置仍可管理而执行仍关闭，不因 requirement 改了归属就标记为已覆盖
