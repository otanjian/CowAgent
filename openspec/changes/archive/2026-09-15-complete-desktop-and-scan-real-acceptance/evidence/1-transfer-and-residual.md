# 1. 移交基线与残余范围

本文件记录 `complete-database-capability-parity` 部分归档时移交给本 change 的范围、判据对照，以及未验收范围在运行期的实际表现。

## 1.1 移交来源与方式

- 来源 change：`openspec/changes/archive/2026-09-15-complete-database-capability-parity`（部分归档，2026-09-15）。
- 移交内容：
  - capability `desktop-tenant-context` 的全部 requirement（6 项）。
  - `channel-scan-onboarding` 的 ADDED requirement「微信个人执行按实际类型与作用域独立验收」。
  - 未完成任务：前序 7.8、7.10、8.7、8.8、11.3、R2。
- 移交方式：requirement 移入本 change 的 `specs/`（前序归档目录不再保留这两块增量，因此 `openspec archive` 不会把它们合并进主规范，`scripts/check_change_deltas.py` 的 applied 模式可以证明“已归档增量 = 已合并主规范”）。
- 判据对照：逐条对比移交前后文字，只新增了以下内容，未删除或放宽任何判据：
  - `desktop-tenant-context`：Purpose 增加一段移交说明；新增场景「远程 HTTPS 形态与响应丢失」「旧后端拒绝降级」（原前序 R2 的判据）、「两个租户两个成员与失效身份」（原前序 8.7 的判据）。
  - `channel-scan-onboarding`：增加一段“移交 MUST NOT 降低验收要求”和场景「移交不改变验收门槛」。
  - 任务：前序 7.8 / 7.10 / 8.7 / 8.8 / 11.3 / R2 的任务文字原样承接，仅重排编号并在开头标注承接来源。

## 1.2 未验收范围的运行期表现（不变）

| 范围 | 运行期表现 | 证据命令 |
| --- | --- | --- |
| `desktop_tenant_context` 切片 | `implemented=True, accepted=False, reason="awaiting_acceptance"`，`open={}`；消费者报告 `available=false` 且带原因 | `.venv/bin/python -m pytest tests/test_capability_matrix.py tests/test_personal_console_pages.py -q` |
| 微信个人执行 | `PERSONAL_RUNTIME_ACCEPTED_TYPES`、`PUBLIC_PERSONAL_INGRESS_TYPES` 为空；`personal_runtime_enabled()` 默认关闭；配置可保存、执行关闭并报告已保存未连接 | `.venv/bin/python -m pytest tests/test_personal_console_pages.py tests/test_weixin_qr_flow.py -q` |
| 已归档已验收切片 | scheduler / memory_browse / project_browse / weixin_scan（配置类）开放状态未变 | `.venv/bin/python -m pytest tests/test_capability_matrix.py tests/test_http_policy.py tests/test_recovered_entry_acceptance.py -q` |

`auth/capability_matrix.py` 的声明不变，因此路由、消费者投影与页面投影三处仍对同一问题给出同一答案。

## 1.3 真实验收所需的外部条件

| 任务 | 缺少的条件 |
| --- | --- |
| 2.1 / 2.2（前序 7.8 / 7.10） | 可用的微信提供方账号（含版本）与手机端；公共与个人 scope 各自的连接形态 |
| 3.1（前序 R2） | 受控远程 HTTPS 后端形态（协议级用例已覆盖本地 loopback 部分） |
| 3.2 / 3.3（前序 8.7 / 8.8） | 可运行的真实打包 Desktop 客户端与两个租户、两个成员、失效身份环境 |
| 4.1（前序 11.3） | 依赖 2.1-2.2 的真实类型验收记录 |

缺少上述条件时任务保持未勾选；不用模拟提供方、模拟客户端或自报标志替代。

## 1.4 建立本 change 时的校验（实测）

```
$ openspec validate complete-desktop-and-scan-real-acceptance --strict
Change 'complete-desktop-and-scan-real-acceptance' is valid

$ openspec list
  complete-desktop-and-scan-real-acceptance     0/11 tasks

$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py \
      tests/test_capability_matrix.py tests/test_doc_edit.py \
      tests/test_recovered_entry_acceptance.py tests/test_http_policy.py \
      tests/test_route_registry.py -q
300 passed, 19 subtests passed
```

`tests/test_capability_matrix.py::test_every_declared_capability_has_a_spec` 通过，因为 `desktop-tenant-context`
的规范现在位于 `openspec/changes/complete-desktop-and-scan-real-acceptance/specs/`（该用例接受主规范或任一
active change 的 delta）。

**关于 `scripts/check_change_deltas.py`**：它的两半作用域不同。delta 一致性部分（ADDED 不重复、MODIFIED 与
基线逐字对齐）对本 change 有意义；**冲突覆盖部分**（`scripts/conflict-baseline.txt` 的每个 `seam:` 行必须被本
change 命名）是 master → rdai 合并执行方（前序 change 的 10.x）的属性，本 change 不承担该合并。实际输出：

```
$ .venv/bin/python scripts/check_change_deltas.py complete-desktop-and-scan-real-acceptance
FAIL (proposed): 8 problem(s)
  - ... marked seam:... but this change never names it     (8 个冲突基线文件)
```

同样的 6-8 条 finding 也出现在两个兄弟 active change（`move-personal-menu-to-account`、
`enable-member-personal-console`）上，说明它是“合并执行方”之外所有 change 的既有状态，不是本 change 引入的
缺陷。本 change 不修改该检查器，也不为通过它而声称拥有未进行的合并；已归档的前序 change 在该检查器的
applied 模式下通过（见其 `evidence/12-validation-and-archive.md` §12.3）。
