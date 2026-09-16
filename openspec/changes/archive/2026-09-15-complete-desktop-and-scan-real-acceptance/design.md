## Context

`complete-database-capability-parity` 的部分归档留下了两类未完成验收，它们都不是设计或实现缺口，而是本环境无法提供的外部条件：

- **真实提供方**：微信扫码、连接与收发的真实验收需要可用的提供方账号与手机端；在缺少该条件时，`PERSONAL_RUNTIME_ACCEPTED_TYPES` 保持为空，个人执行保持关闭。
- **真实客户端**：Desktop 的原生协议、broker 与租户上下文实现已完成并有协议级用例，但“真实打包客户端 + 两个租户 + 两个成员 + 失效身份 + 重连”的演练未执行，因此切片保持 `accepted=false`。

本 change 的存在意义是让这些残余验收在 OpenSpec 中有一个明确归属：既不被归档掩盖，也不被“规划产物完成”替换。

## Goals / Non-Goals

**Goals**

- 以 requirement 与任务的形式显式承接剩余验收，并保留原门槛文字（不弱化、不改写判据）。
- 保持未验收范围关闭：Desktop 消费者、微信个人执行与公共实例个人入口都维持现状。
- 记录移交边界，使 `complete-database-capability-parity` 的归档检查（`scripts/check_change_deltas.py` 的 applied 模式）能证明“已归档增量=已合并主规范”，残余项不在其中。

**Non-Goals**

- 不重新打开任何已验收切片（scheduler / memory_browse / project_browse / weixin_scan 配置面）。
- 不在本 change 内重建审批引擎、渠道实例模型或 Desktop 认证协议。
- 不用模拟提供方、模拟客户端或自报标志替代真实验收。

## Decisions

### D1 部分归档而非“全部勾选后归档”

前序 change 的 12.3 要求“完成全部必要依赖和验收后”归档。用户决策改为按已验收切片部分归档，理由是：继续把 8 项因外部条件无法完成的验收挂在同一个 change 上，会让已交付且有真实证据的 4 个切片也无法进入主规范，且不符合“按切片验收”的既有治理口径。

取舍：本 change 承担“显式残余”的可追踪性成本，换取主规范与已交付实现的一致性。

### D2 残余以 requirement 移交，不以任务移交

`desktop-tenant-context` 全部 requirement 与微信执行验收 requirement 原样移入本 change；任务的编号与文字保留原意（仅重排编号）。原因是 requirement 是需求基线，任务是执行记录；把二者一起移出可以避免主规范出现“没有验收依据却声称已实现”的条目。移交条目中增加一句“移交 MUST NOT 降低其验收要求”，防止后来者把归属变更当作降级许可。

### D3 Desktop 规范补入未验收场景

移交时在 `desktop-tenant-context` 补入三个场景（远程 HTTPS 形态与响应丢失、旧后端拒绝降级、两个租户两个成员与失效身份）。这不是新增需求，而是把 R2、8.7 原本写在任务里的判据提升到 requirement 层，避免“任务未勾选”成为唯一的验收记录。

### D4 未验收范围的运行期表现保持现状

- `auth/capability_matrix.py` 的 `desktop_tenant_context` 保持 `implemented=True, accepted=False, reason="awaiting_acceptance"`，`open={}`。
- `channel/channel_instances.py` 的 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES` 保持为空；`personal_runtime_enabled()` 部署开关默认关闭。
- 已归档切片的开放状态不因本 change 变动。

## Risks / Trade-offs

- **风险：残余长期未验收**。缓解：证据与任务都指名外部条件，任何一次真实提供方/客户端可用即可按任务顺序推进，不需要重新设计。
- **风险：归档阅读者误以为桌面已支持**。缓解：主规范暂不含 `desktop-tenant-context`，切片 `accepted=false` 且投影报告 `awaiting_acceptance`；交付文档同步说明。
- **风险：用“移交”掩盖未完成**。缓解：requirement 内显式写明移交不改变门槛，任务保持未勾选，证据文件记录移交前后的判据对照。

## Migration Plan

1. 本 change 建立时保持全部运行期开关与投影不变，无数据迁移。
2. 真实提供方验收（任务 2.1/2.2）通过后，按逐类型记录更新运行判定；未验收类型/版本/scope 不扩大。
3. Desktop 验收（任务 3.1-3.3）通过后，才由统一开放矩阵更新桌面投影并清理永久 deferred。
4. 全部残余验收通过后，按 requirement 合并主规范并归档；归档后再验证主规范与增量一致。

## Open Questions

- 首个承诺支持的微信提供方版本与连接形态（由真实验收记录确定，不预先假定）。
- 远程 HTTPS 形态的验收是否需要独立的部署形态（当前以受控 HTTPS 后端验证协议边界，真实客户端演练在任务 3.2 覆盖）。
