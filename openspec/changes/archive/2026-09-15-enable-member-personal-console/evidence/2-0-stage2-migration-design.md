# 2.0 Stage 2 迁移与 schema 方案（待归档后落码）

本文件是**实施前的预置方案**，在等待 `restrict-knowledge-write-authorization` 归档期间编写，不触碰任何代码文件。
落码时以当时工作树为准复核行号与迁移号。

## 1. 迁移号分配

| 版本 | 内容 | 状态 |
| --- | --- | --- |
| `_migration_15` | 剥离各角色 `knowledge.write` | ✅ 已被 `restrict-knowledge-write-authorization` 实现（`auth/store.py:813`） |
| `_migration_16` | 渠道实例 `scope` + `owner_user_id` | ⬜ 本 change（2.1） |
| `_migration_17` | 私有 Agent 来源标识 `origin` | ⬜ 本 change（2.2） |
| `_migration_18` | 绑定挑战与个人渠道关联存储 | ⬜ 本 change（2.3） |

`migration_versions()` 按位置派生（`auth/store.py:31-33`），函数名须与位置一致。

> 2.4（个人工具/技能参数 + 凭证引用）优先复用既有存储，**未必需要新迁移**；若需，顺延 19。

## 2. `_migration_16`：渠道实例作用域与归属（2.1）

### 现状

```472:487:auth/store.py
        CREATE TABLE tenant_channel_instances (
            id            TEXT PRIMARY KEY NOT NULL,
            tenant_id     TEXT NOT NULL REFERENCES tenants(id),
            channel_type  TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            agent_id      TEXT NOT NULL DEFAULT '',
            active        INTEGER NOT NULL DEFAULT 1,
            version       INTEGER NOT NULL DEFAULT 1,
            created_by    TEXT NOT NULL,
            created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX idx_tenant_channel_instances_name
            ON tenant_channel_instances(tenant_id, display_name) WHERE active = 1;
        CREATE INDEX idx_tenant_channel_instances_tenant_active
            ON tenant_channel_instances(tenant_id, active);
```

### 方案

```sql
ALTER TABLE tenant_channel_instances ADD COLUMN scope TEXT NOT NULL DEFAULT 'tenant';
ALTER TABLE tenant_channel_instances ADD COLUMN owner_user_id TEXT;
DROP INDEX IF EXISTS idx_tenant_channel_instances_name;
CREATE UNIQUE INDEX idx_tenant_channel_instances_name
    ON tenant_channel_instances(tenant_id, scope, COALESCE(owner_user_id,''), channel_type, display_name)
    WHERE active = 1;
CREATE INDEX idx_tenant_channel_instances_owner
    ON tenant_channel_instances(tenant_id, owner_user_id) WHERE scope = 'user';
```

要点与理由：

1. **`scope` 用 `DEFAULT 'tenant'` 而非可空** —— 历史实例自动成为 `tenant`，无需回填 UPDATE，
   且 `NOT NULL` 让"忘记设置作用域"不可能发生。这与 design §4「历史实例统一保持 `tenant` 且 owner 为空」一致。
2. **`owner_user_id` 可空** —— tenant 实例为 NULL；user 实例必须非空（由服务层断言，SQL 无法表达该条件约束）。
3. **唯一索引加入 `channel_type`** —— design §4 要求「名称唯一性按租户、作用域、owner 和渠道类型判断」。
   现状只按 `(tenant_id, display_name)`，会阻止"同名不同渠道类型"。**这是行为变更，须在测试中固定。**
4. **`COALESCE(owner_user_id,'')`** —— SQLite 中 NULL 互不相等，直接用 `owner_user_id` 会让两个 tenant 实例的同名冲突漏检。
   这是本迁移最容易踩的坑。
5. **`DROP INDEX` 必须 `IF EXISTS`** —— 迁移须可重入；`schema_migrations` 去重之外的重复执行要安全。

### 服务层配套（2.1）

| 函数 | 位置 | 改动 |
| --- | --- | --- |
| `create_tenant_channel_instance` | `auth/service.py:5291` | 新增 `scope`/`owner_user_id` 入参；`user` 作用域须校验 owner 为当前租户有效成员；重复名检查加上 scope/owner/channel_type |
| `list_tenant_channel_instances` | `:5418` | **公共列表只返回 `scope='tenant'`**（现返回全部，须加过滤，否则个人实例泄漏给管理员） |
| `update_tenant_channel_instance` | `:5436` | owner 本人可改；管理员不得跨 owner 改 |
| `set_tenant_channel_instance_active` | `:5545` | 治理停用与 owner 启停分离（2.5 暂缓，此处只做归属校验） |
| `channel_instance_credentials` | `:5618` | 由实例真实归属推导个人所有权；管理 API 只返回掩码 |
| `get_tenant_channel_instance_row` | `:5399` | 投影增加 `scope`/`owner_user_id`（运行消费者需要按归属路由） |

### 兼容性红线（tasks 2.1 明文要求）

- 历史实例 **ID 不变**、**凭证版本不变**、**公共管理入口不变**。
- `list_tenant_channel_instances` 加 `scope='tenant'` 过滤后，公共管理页行为对管理员应**完全不变**（历史实例都是 tenant）。
- 认证路径（`_is_control`）不改。

## 3. `_migration_17`：私有 Agent 来源（2.2）

`agent_bindings` 增加 `origin TEXT NOT NULL DEFAULT 'unknown'`，取值 `provisioned_assistant` / `user_created` / `unknown`。

- **`DEFAULT 'unknown'` 是刻意选择**：design §3 要求「未知历史来源保持显式未知，不猜测」，禁止把存量回填为可删除。
- 供应幂等键改为 `(tenant_id, owner_user_id, origin='provisioned_assistant')`，不再以"已拥有任意私有智能体"为跳过条件。
- 只按可信证据回填：`agent/personal_assistant.py` 的供应路径可标记 `provisioned_assistant`。

## 4. `_migration_18`：绑定挑战与个人渠道关联（2.3）

新表（方案草案，落码时以 `design.md` §5 复核）：

```sql
CREATE TABLE binding_challenges (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    user_id       TEXT NOT NULL,
    instance_id   TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    code_hash     TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    expires_at    INTEGER NOT NULL,
    consumed_at   INTEGER,
    created_at    INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE personal_channel_links (
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    user_id       TEXT NOT NULL,
    instance_id   TEXT NOT NULL REFERENCES tenant_channel_instances(id),
    external_identity_id TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, user_id, instance_id)
);
```

红线：**解绑个人关联不得删除 `external_identities` 的全局映射**（其他租户可能仍在使用）——
两表分离正是为此；个人关联随成员失效而失效，全局映射独立保留。

## 5. 落码顺序建议

1. `_migration_16` + 2.1（渠道实例作用域）—— 独立、可单独验收。
2. `_migration_17` + 2.2（来源与幂等）—— 与 2.1 无耦合。
3. `_migration_18` + 2.3（绑定挑战）—— 依赖 2.1 的实例表。
4. 2.6（菜单 grant，纯数据迁移，可并行）。
5. 2.4（参数与凭证引用，可能无需新迁移）。
6. 2.7（真实临时身份库上的迁移/重入/中断/双请求竞争证据）—— 收口，且是 Q1 的解除证据来源。

每步遵循 TDD：先写失败测试（迁移在真实临时身份库上跑、断言列与索引、断言旧行为保持），再实现。

> 状态：**预置方案就绪**，未落码。开工前须复核工作树（迁移号可能因其他 change 再度漂移）。
