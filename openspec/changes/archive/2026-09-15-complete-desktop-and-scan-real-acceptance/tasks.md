## 1. 移交基线与残余范围

- [ ] 1.1 核对移交范围：`desktop-tenant-context` 全部 requirement 与「微信个人执行按实际类型与作用域独立验收」已从 `openspec/changes/archive/2026-09-15-complete-database-capability-parity` 移入本 change，且主规范 `openspec/specs/channel-scan-onboarding/spec.md` 不含该 requirement；移交只改变承接方，判据文字未放宽。
- [ ] 1.2 复核未验收范围的当前表现：`auth/capability_matrix.py` 的 `desktop_tenant_context` 为 `implemented=True, accepted=False, reason="awaiting_acceptance"` 且 `open={}`；`PERSONAL_RUNTIME_ACCEPTED_TYPES`、`PUBLIC_PERSONAL_INGRESS_TYPES` 为空且 `personal_runtime_enabled()` 默认关闭；已归档切片（scheduler / memory_browse / project_browse / weixin_scan 配置面）的开放状态未被本 change 改动。
- [ ] 1.3 记录进行真实验收所需的外部条件清单（提供方账号与版本、手机端、打包客户端环境、后端形态），缺少条件时保持任务未勾选，不以模拟件替代。

## 2. 微信真实执行与类型开放

- [ ] 2.1 （承接前序 7.8）对本 change 首个承诺支持的微信适配器完成真实扫码、密文落库、连接和收发验收，分别记录提供方/版本/公共或个人 scope/连接形态；个人范围包含本人私聊、他人/群聊拒绝、解绑及治理停用，公共成功不能代替个人验收。缺少外部条件保持未完成，不沿用依赖的“关闭即结项”分支。
- [ ] 2.2 （承接前序 7.10）前置：对应类型/scope 的 2.1、适用审批 7.9 及既有隔离/凭据/配额门槛通过；按证据更新 `PERSONAL_RUNTIME_ACCEPTED_TYPES`、`PUBLIC_PERSONAL_INGRESS_TYPES` 及必要的适配范围约束，复用部署总开关与统一运行判定，类型记录不自动打开部署。验证配置已保存但执行关闭、个人实例执行和公共个人入口分别投影，未验收类型/版本/scope 不扩大开放范围。

## 3. Desktop 原生协议闭环与真实客户端验收

- [ ] 3.1 （承接前序 R2）Desktop 原生协议闭环：分别通过本地 loopback 与远程 HTTPS 后端完成系统浏览器确认、PKCE 兑换、主进程请求及在线撤销；覆盖强制改密、错误 state/redirect/verifier、60 秒过期、授权码重放、发起会话撤销、兑换响应丢失、旧后端拒绝降级、Web 自报 Desktop 仍只获空 token、非主 frame IPC、任意 URL/认证头覆盖、通用 httpRelay 旁路和携带凭据重定向。令牌不得进入 renderer/浏览器存储/日志/URL，上传、流、预览及下载均验证 broker 路径；未完成不得进入 3.2、3.3 的交付验收。
- [ ] 3.2 （承接前序 8.7）使用真实后端及受控 Desktop 客户端验证登录到聊天、附件回读、任务、记忆、项目及个人页完整链路，覆盖两个租户、两个成员、失效身份和重连；增加零租户普通账号、零租户平台管理员及受限改密平台账号矩阵，证明平台入口不被租户 gate 误挡且不能越权进入租户业务。
- [ ] 3.3 （承接前序 8.8）前置：3.2 通过；由统一开放矩阵更新 Desktop 及兼容 `desktop_enterprise` 投影，清理永久 deferred，不把只完成 LoginGate 计作桌面适配完成。

## 4. 联合验收

- [ ] 4.1 （承接前序 11.3）复用已交付个人渠道、身份挑战与路由服务，使用 2.1-2.2 的实际验收类型联测扫码到个人收发、适用审批、解绑、治理停用及群聊隔离；管理员只获允许的治理元数据，未验收类型仍关闭。

## 5. 交付与归档

- [ ] 5.1 全部必要依赖和验收完成后，按 requirement 合并主规范并归档本 change，保留产品 3.1 与兄弟 change 边界；归档后再验证主规范与增量一致（`scripts/check_change_deltas.py` 的 applied 模式）。
- [ ] 5.2 交付真实实现、客户端/提供方验收及 master/rdai 合并证据；未完成项保持未勾选，规划产物完成不等于功能实现完成。归档前同步更新 `auth/capability_matrix.py` 的切片接受状态与 `accepted` 依据注释。
