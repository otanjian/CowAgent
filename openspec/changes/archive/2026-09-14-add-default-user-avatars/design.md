## Context

现状（见 proposal.md - Why 与 `user-avatar` 规范）：

- 账号头像标记只有 `users.avatar`（`"image"` / `NULL`），图片字节在 `shared_root()/avatars/user-<id><ext>`；`GET/POST /auth/profile/avatar` 仅服务本人。
- 账号界面当前口径不一：侧栏账号按钮与账号菜单显示展示名首字；个人资料 hero 在无上传时显示首字；管理员的租户成员列表和平台账号列表显示通用 `fa-user` / `fa-user-cog` 图标，完全没有头像。
- Agent 头像已有一套前端哈希取模选 tone 的先例（`avatarTone`，`console.js`），但那是「首字 + 背景色」，不是图片资源。
- 路由策略由 `channel/web/route_registry.py` 单一权威表派生（`http_policy.py`），`personal` 类只要求已认证、由 handler 负责属主/范围校验。

## Goals / Non-Goals

**Goals:**
- 5 个内置默认头像资源 + 按 `user.id` 稳定哈希分配，零数据库迁移、零建号流程改动。
- 上传头像优先，默认头像兜底；同一用户在侧栏/菜单/个人资料/管理员列表口径一致。
- 管理员列表也能显示已上传头像，同时不扩大原有可见范围。

**Non-Goals:**
- 不做头像裁剪、压缩、格式转换或 CDN；沿用现有上传校验（≤2 MiB、png/jpg/jpeg/webp/gif）。
- 不改 Agent 头像体系、品牌默认头像、身份/租户/权限授权规则。
- 不引入运行时默认头像接口（默认头像是静态资源，由前端直接引用）。
- 不做默认头像的用户自选/管理端配置。

## Decisions

### D1. 默认头像用 5 个手写 SVG 静态资源，不用图片生成模型
新增 `channel/web/static/avatars/default-1.svg` ~ `default-5.svg`，通过既有 `/assets/(.*)` `AssetsHandler` 提供；统一 `viewBox 0 0 64 64`、不透明圆形底 + 扁平人物剪影，5 套品牌配色 + 5 个小配件区分，避免透明描边在深色侧栏消失。
- 备选 A：用 image-generation 技能生成 PNG。否决——当前实例未配置图片模型 key（`config.json` 仅有文本模型 key），且默认头像必须离线、确定、可版本化。
- 备选 B：emoji + CSS 背景。否决——无法保证跨平台渲染一致，也不能作为 `img` 复用同一呈现路径。

### D2. 分配用前端稳定哈希，后端不参与
在 `console.js` 定义 `userDefaultAvatarIndex(userId) = hash(userId) % 5 + 1`（复用 `avatarTone` 同款 `hash*31` 且 `>>>0` 的算法），映射只依赖 `user.id`。
- 备选：后端新增 `default_avatar` 列或投影字段。否决——只有前端渲染，落库会引入迁移且与「稳定哈希」等价；列出后端字段反而制造第二处口径。
- 权衡：哈希算法一旦上线不宜再改（会改变所有用户的默认头像）；把它固定为常量并加测试锁定。

### D3. 上传头像优先，统一 `userAvatarHTML` helper
新增 `userAvatarHTML({id, avatar, self, version})`：
- `avatar === 'image'` 且 `self` → `/auth/profile/avatar?v=...`（沿用本人接口与 `_accountAvatarVersion` 破缓存）。
- `avatar === 'image'` 且非本人（管理员列表）→ `/api/users/<id>/avatar`。
- 否则 → `/assets/avatars/default-<n>.svg`。
- 上传文件缺失时 `onerror` 回退到默认头像，避免破图（对应用户头像规范的上传文件缺失场景）。

### D4. 新增只读路由 `GET /api/users/([^/]+)/avatar`，`personal` 策略 + handler 内范围校验
- 路由表注册为 `P("personal", ...)`：`personal` 只要求已认证，不强制租户/权限，符合「本人 / 平台管理员（可能无租户上下文） / 同租户成员」三种调用方。
- handler 内授权规则（服务层单一入口）：本人；或有效平台管理员；或与目标存在至少一个共同 `active` 租户成员关系（双方 memberships、accounts 均 active）。这正好覆盖「成员名录可见范围」，不放大权限。
- 有上传返回字节 + Content-Type；无上传返回 404；未认证 401、越权 403；不写任何状态。
- 备选：复用 `/auth/profile/avatar` 加 `?user_id=`。否决——会让一个 `personal` 本人接口承担他人范围校验，语义更易误用。

### D5. 列表投影补 `avatar` 标记
`list_members` / `list_platform_users_paged` 的 SELECT 各补 `u.avatar` / `avatar`，前端据此决定走只读头像接口还是默认头像。不返回字节或路径。
- 备选：列表直接内联头像 data URL。否决——放大响应体，且与只会用 5 张静态默认图的现状不符。

### D6. 渲染面统一走 helper，侧栏/菜单改为 `img`
侧栏 `sidebar-account-avatar`、`account-menu-avatar` 由写文字首字改为写入 `userAvatarHTML(...)` 的 `<img>`；未登录/加载/错误仍用人物图标。新增 CSS 让容器内 `img` 铺满并 `object-fit: cover`。个人资料 hero 的 else 分支由首字改为默认头像。管理员两个列表把通用图标 `<div>` 换成头像 `<img>`。

## Risks / Trade-offs

- [哈希算法上线后不可改] → 固定算法并加前端测试锁定同一 id 的索引；如需换形象，新增资源而不是改取模。
- [`img` 加载失败出现破图] → helper 统一挂 `onerror` 回退默认头像。
- [把「同租户可见」误当授权依据] → 头像读取只是展示；`user-avatar` 明确头像不得作为服务端授权依据，handler 也不放宽任何业务接口。
- [管理员列表逐个请求头像造成 N+1] → 仅对 `avatar === 'image'` 的行发请求；浏览器缓存 `Cache-Control: private, max-age=86400`（沿用本人接口口径）。
- [SVG 在深色侧栏/浅色管理页对比度不一致] → 每张图使用不透明圆形底色 + 白色剪影，避免透明描边在深色下消失。

## Migration Plan

- 纯新增资源与前端口径调整，无数据库迁移、无数据回填。
- 回滚：移除默认头像资源与前端 helper 调用即可恢复首字/图标旧表现；新增路由为只读，删除注册项即可。
- 旧账号无需任何操作：`users.avatar` 保持 `NULL`，按哈希得到默认头像；已上传账号行为不变。
