## Context

见 `proposal.md - Why`。需要补充的接入点现状：

- `common/state_dir.py` 的 `_assert_tenant_roots_do_not_contain()` 在解析租户共享根时先做 home/global 逃逸校验，可信根来自 `_engineering_root()`。
- `_engineering_root()` 当前返回**默认智能体工作区**（`get_agent_registry().get(require_enabled=False).workspace`）。在缺省单智能体布局下它等于实例根，二者重合；一旦默认智能体被赋予 `<实例根>/agents/<id>` 私有工作区就分叉。
- 租户基目录可信根由 `_tenant_base_real()` 单独提供，已经是「多个可信来源」的既有形态。
- 调用方 `SessionSettingsHandler.GET`（模型选择器）、会话/项目/历史列表均通过 `state_dir.shared_root()` 间接受影响；本 change 只修根因，不改这些调用方。

## Goals / Non-Goals

**Goals:**
- 让 home/global 逃逸校验的可信根覆盖**部署实例根**，使默认租户在默认智能体迁入私有工作区后仍能解析共享根。
- 保持现有全部拒绝语义：跨租户相等/包含、身份库落点、家目录任意子目录、路径穿越与软链接逃逸。
- 让 `test_state_dir_tenant_containment.py` 既有断言（工程根 = 默认智能体工作区即通过）继续成立。

**Non-Goals:**
- 不改变租户目录布局、`agent_workspace` 语义或任何租户数据。
- 不放宽跨租户包含性校验，也不引入「已登记根即全子路径安全」的推论。
- 不修改上层消费者（会话设置、会话列表、项目列表）的失败处理策略。

## Decisions

**决策 1：可信根改为多来源并集，而非替换来源。**

新增 `_instance_root()` 读取 `conf().get("agent_workspace")`（缺省 `~/cow`，经 `expand_path` + `realpath`），并与既有默认智能体工作区、`_tenant_base_real()` 组成 `_trusted_roots()` 列表；`_is_home_or_global_escape()` 改为接收该列表。

- 为什么不是「把 `_engineering_root()` 直接改成实例根」：既有测试 `test_engineering_root_is_allowed` 的工程根就是注册表中默认智能体的工作区（测试未设 `agent_workspace`），只取实例根会让该测试回归。取并集同时覆盖两种布局，语义也更贴合「已验证来源」。
- 为什么不用「路径包含可信根即放行」：那会把家目录本身变成可信（实例根在 `~` 之下），是安全性倒退。

**决策 2：`_engineering_root()` 退役，改为 `_trusted_roots()`。**

`_engineering_root` 是私有函数且无测试直接引用（仅测试名同名），替换为列表版本可避免「单一来源」这个错误抽象再次出现。`_is_home_or_global_escape` 由 `engineering: Optional[str]` 改为 `trusted: Sequence[str]`。

**决策 3：实例根读取失败时静默跳过该来源。**

与 `_engineering_root()` 既有容错一致（`except Exception: return None`）。缺失时行为回落到「仅默认智能体工作区 + 租户基目录」，即当前行为，不引入新的 fail-open。

## Risks / Trade-offs

- [实例根缺省值 `~/cow` 在家目录下，可能意外信任 `~/cow` 内的任意目录] → 这是既有语义（实例根本就是 operator 工作区，可信），且跨租户包含性校验仍在 `validate_tenant_shared_root` 中独立执行；家目录其它位置（如 `~/Documents`）仍被拒绝，并新增回归场景覆盖。
- [多来源削弱「只有一个可信根」的直观性] → 在注释与 spec 中显式列出三个来源及各自理由；来源均为已验证配置而非用户输入。
- [测试环境未加载 config，`conf()` 可能为空] → 缺省值 `~/cow` 与既有测试所用 `tmp_path` 根不重叠，既有断言不受影响；新增测试显式 `monkeypatch` 实例根以消除环境依赖。

## Migration Plan

无数据迁移。改动为纯代码路径，部署后重启进程即生效；回滚只需还原 `common/state_dir.py`。**回滚边界**：若该修复导致默认租户解析行为异常，回退后默认租户将回到「共享根解析失败」的既有故障状态（其余租户不受影响）。
