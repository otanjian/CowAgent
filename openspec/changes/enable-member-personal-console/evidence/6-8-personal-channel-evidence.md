# 6.8 个人渠道接入启用证据

对应 `personal-channel-configuration` 的「本人渠道实例登记」「本人凭证与关联管理」
「个人接入就绪与外部应用占用」与「本人身份关联与解绑」四组 requirement。
用例：`tests/test_personal_channel_console.py`（54 项）+ `tests/test_personal_channel_binding.py`（31 项），
两文件全绿；相邻回归档（渠道实例/迁移/路由/入站隔离/外部身份/身份授权）454 项全绿。

## 1. 落地实现

| 关注点 | 位置 | 说明 |
| --- | --- | --- |
| 个人渠道服务 | `auth/service.py` | `list/get/create/update/set_active/revoke_personal_channel_credentials` |
| 复用而非复制 | `auth/service.py` | `allow_owner=True` 让 `create/update/set_tenant_channel_instance_active` 沿用原版本/审计/加密/运行时事务，只替换「谁有权写这一行」 |
| scope/owner 固定 | `auth/service.py` | `create_tenant_channel_instance` 中 `scope, owner_user_id = "user", actor_user_id`；请求体没有这两个字段 |
| 掩码投影 | `_personal_channel_projection` / `_personal_credential_projection` / `_personal_binding_projection` | 只回字段名与版本号，不回 bundle；subject 只回后 4 位 |
| 就绪登记 | `channel/channel_instances.py` | `PERSONAL_READY_CHANNEL_TYPES`、`personal_channel_ready`、`personal_channel_types` |
| 应用指纹 | `auth/crypto.py::fingerprint` + `channel/channel_instances.py::app_identity` | 凭证中标识外部应用的字段做 keyed digest，无需解密即可比对 |
| 占用冲突 | `auth/service.py::_assert_no_app_conflict` + `auth/store.py::_migration_23` | 服务层判决 + `idx_tenant_channel_instances_app` 唯一索引兜并发 |
| 绑定挑战 | `auth/service.py` | `start_personal_channel_binding` / `redeem_personal_channel_challenge` / `personal_channel_binding_status` |
| 关联与解绑 | `auth/service.py` | `link_personal_channel` / `unlink_personal_channel(_instance)` |
| 采集脱敏 | `auth/service.py` | `record_external_identity_attempt(personal_flow=…)`、`_instance_is_personal`、`list_external_identity_attempts` 读时再脱敏 |
| 挑战识别 | `channel/external_identity.py::looks_like_binding_code` + `channel/chat_channel.py` | 未绑定私聊里像挑战码的消息按 personal flow 记账 |
| 控制台入口 | `channel/web/web_channel.py` | `PersonalChannelHandler`、`PersonalChannelInstanceHandler` |
| 路由 | `channel/web/route_registry.py` | `/api/personal/channels`、`/api/personal/channels/([^/]+)`，策略 `personal`（与 `fork:member-personal-console` 同步，公共接口策略未改） |

## 2. 需求 → 用例

| 需求场景 | 用例 |
| --- | --- |
| 登记固定当前 tenant/owner | `PersonalSurfaceTests::test_create_forces_the_callers_own_scope_and_owner` |
| 只看得到自己的实例 | `test_the_listing_shows_only_my_own_instances` |
| 寻址他人实例被拒（403，不泄露存在性） | `test_another_members_instance_is_not_addressable` |
| 跨租户 id 不可寻址 | `test_another_tenants_personal_instance_is_out_of_reach` |
| 公共接口维持原管理员门槛 | `test_the_public_surface_keeps_its_administrator_gate`（create/list/set_active 三项） |
| 非成员不得登记 | `test_a_non_member_cannot_register_anything` |
| 投影只给字段名与版本 | `CredentialMaskingTests::test_the_projection_reports_only_names_and_a_version` |
| 更换凭证追加版本、只保留一行凭证 | `test_a_rotation_appends_a_version_and_keeps_one_credential_row` |
| 轮换后运行的是新 bundle | `test_the_running_bundle_is_the_new_one_not_the_old` |
| 旧版本写入被拒且不留痕 | `test_a_stale_version_is_refused_and_changes_nothing` |
| 他人不能轮换我的凭证 | `test_another_member_cannot_rotate_my_credential` |
| 本人撤销凭证即停用 | `test_revoke_deactivates_the_credential_and_stops_the_instance` |
| 撤销需本人密码 | `test_revoke_requires_the_owners_password` |
| 就绪目录逐个给判定 | `ReadinessTests::test_the_type_catalog_carries_a_verdict_per_type` |
| 未就绪类型给出可行动理由 | `test_an_unknown_type_is_refused_with_an_actionable_reason` |
| 配置只能收窄就绪集 | `test_configuration_can_narrow_the_ready_set_but_never_widen_it` |
| 个人实例不得占用租户应用 | `test_a_personal_instance_may_not_borrow_the_tenants_application` |
| 两个个人实例不得共用应用 | `test_two_personal_instances_may_not_share_an_application` |
| 两个公共实例仍可共用应用（不回溯收窄） | `test_two_shared_instances_may_still_share_an_application` |
| 轮换撞上已占应用被拒 | `test_a_rotation_onto_a_taken_application_is_refused` |
| 指纹不是应用 id 本身 | `test_the_fingerprint_is_not_the_application_id` |
| 启用时同样判占用 | `test_enabling_an_instance_whose_application_is_taken_is_refused` |
| 挑战只存哈希 | `BindingChallengeTests::test_a_started_challenge_stores_only_a_hash` |
| 一次消费并绑定「观察到的」发送者 | `test_a_code_is_redeemed_once_and_links_the_observed_sender` |
| 同码重放被拒且不改绑 | `test_the_same_code_cannot_be_replayed` |
| 同码并发到达只成功一次 | `test_one_code_arriving_twice_at_once_links_only_its_sender` |
| 错码消耗次数并最终锁定 | `test_a_wrong_code_burns_an_attempt_and_then_locks` |
| 过期码不可用 | `test_an_expired_code_is_usable_no_more` |
| 指向别的实例的码不绑我 | `test_a_code_for_another_instance_does_not_link_mine` |
| 他人已绑三元组不被夺取 | `test_a_triple_bound_to_someone_else_is_never_taken_over` |
| 控制台没有任何手填 subject 的入口 | `test_the_console_surface_offers_no_way_to_name_a_subject` |
| 共享实例没有自助绑定 | `test_a_shared_instance_has_no_self_service_link` |
| 解绑只删本人路由、保留全局映射 | `IdentityLinkTests::test_unlinking_removes_the_route_and_keeps_the_identity` |
| 解绑不影响他租户路由 | `test_unlinking_here_does_not_touch_another_tenants_mapping` |
| 他人不能解绑我的路由 | `test_another_member_cannot_unlink_my_route` |
| 个人尝试不存昵称/预览 | `AttemptRedactionTests::test_a_personal_attempt_stores_no_preview_or_nickname` |
| 挑战码永不落库 | `test_a_challenge_code_never_reaches_the_store` |
| 共享实例上的 personal flow 同样脱敏 | `test_a_flagged_flow_is_redacted_even_on_a_shared_instance` |
| 普通公共尝试仍保留证据 | `test_an_ordinary_shared_attempt_keeps_its_evidence` |
| 管理员列表读时再脱敏历史行 | `test_the_administrator_list_redacts_personal_rows` |
| 停用保留凭证、启用恢复 | `RuntimeStateTests::test_disabling_keeps_the_credential_and_enabling_restores_it` |
| 治理停用优先于 owner 启用 | `test_a_governance_stop_outranks_the_owners_enable` |
| 治理停用同样阻断轮换 | `test_a_governance_stop_also_blocks_a_credential_rotation` |
| owner 视图含治理标记 | `test_the_owner_listing_shows_the_governance_flag` |
| 已保存 ≠ 已连接（连接失败单列） | `test_a_saved_instance_reports_a_failed_connection_separately` |
| 失败诊断不触发二次保存 | `test_the_runtime_report_is_not_a_second_save` |
| 连接失败可诊断、不静默 | `test_a_connection_failure_is_diagnosable_not_silent` |
| 停用实例不报「待应用」 | `test_a_disabled_instance_reports_nothing_to_run` |
| 并发版本冲突恰一个赢 | `IntegrationTests::test_two_pages_saving_the_same_version_produce_one_winner` |
| 审计失败整笔回滚、不留半行 | `test_a_failed_audit_rolls_the_whole_create_back` |
| 配额拒绝不留半行 | `test_a_quota_refusal_leaves_no_partial_row` |
| 收窄策略后不可重新启用 | `test_a_disabled_type_cannot_be_re_enabled_after_the_policy_narrows` |
| 解绑后发送者不再解析 | `test_an_unlinked_route_stops_resolving_its_sender` |
| 公共路由策略未被放宽 | `test_the_public_route_policies_are_unchanged` |

`tests/test_personal_channel_binding.py` 另在存储层固定挑战/路由表的列集、NOT NULL、
外键、唯一键与既有库升级（不臆造挑战、不臆造路由、全局映射不被连带删除）。

## 3. 变异检查（8 项，全部被捕获）

逐个破坏一处判决后重跑对应用例；仍全绿即为「用例没有钉住该行为」。

| # | 破坏的判决 | 判据用例 | 结果 |
| --- | --- | --- | --- |
| 1 | `_personal_instance_row` 去掉 owner 比较 | `test_another_members_instance_is_not_addressable` | caught |
| 2 | 列表 SQL 去掉 owner 过滤 | `test_the_listing_shows_only_my_own_instances` | caught |
| 3 | 同时去掉服务层占用判决与存储唯一索引 | `test_two_personal_instances_may_not_share_an_application` | caught |
| 4 | 错码不再消耗尝试次数 | `test_a_wrong_code_burns_an_attempt_and_then_locks` | caught |
| 5 | 同时去掉消费判读与条件 UPDATE | `test_one_code_arriving_twice_at_once_links_only_its_sender` | caught |
| 6 | personal flow 不再脱敏 | `test_a_personal_attempt_stores_no_preview_or_nickname` | caught |
| 7 | 治理停用不再优先 | `test_a_governance_stop_outranks_the_owners_enable` | caught |
| 8 | 撤销不再要求本人密码 | `test_revoke_requires_the_owners_password` | caught |

**两处刻意的双保险**（#3、#5 需同时破坏两层才被捕获）：

- 应用占用：服务层 `_assert_no_app_conflict` 判「能不能」，`idx_tenant_channel_instances_app`
  兜「两个并发写同时通过判读」。唯一索引只管个人行（`scope='user'`），因为公共/个人
  边界是不对称判决（个人要对全部活动实例让路，公共只对个人实例让路，两个公共实例共用
  应用是上游既有行为，本 change 不回溯收窄）。
- 挑战单次消费：`_claim_challenge` 的 `BEGIN IMMEDIATE` 判读 + `UPDATE … WHERE consumed_at
  IS NULL` 条件更新。前者给准确回答，后者才是并发下真正串行化的那一步；重放用例（顺序）
  只能钉住读路径，因此 6.8 补了并发用例 `test_one_code_arriving_twice_at_once_links_only_its_sender`。

## 4. 边界与已知取舍

- **执行不在本切片**：本阶段只固定「登记/配置/关联」的写入边界与投影；个人路由在入站
  路径上的判定、群聊拒绝与失效连接关闭属 Stage 7（`7.1`–`7.5`），控制台页面与 i18n 属 Stage 8。
- **未就绪类型**：个人接入就绪集当前等于已支持多实例的渠道类型（`PERSONAL_READY_CHANNEL_TYPES`）；
  真实渠道端到端验收（`7.5`）未完成前，运行时开关仍保持关闭，目录按就绪判定如实展示「不可接入」。
- **指纹的历史行**：`app_fingerprint` 默认 `''` 的既有行不进唯一索引，迁移不会因历史数据失败；
  它们在下一次写入时获得指纹，在此之前服务层判决看不到它们。
- **脱敏是读写两侧**：写入侧按 `personal_flow` 或实例归属决定是否记录内容，读取侧再按实例
  归属清空一次，因此本规则之前落库的历史行也不会显示私聊预览。
- **解绑与全局映射**：`unlink` 只删 `(tenant, user, instance)` 路由；`external_identities`
  的全局映射按设计保留，因为同一人在其他租户或公共绑定流程可能仍在使用它。
