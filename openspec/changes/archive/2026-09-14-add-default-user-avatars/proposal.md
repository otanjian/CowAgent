## Why

用户账号虽然已经支持上传头像，但没有上传时前端只能显示展示名称首字，缺少一组固定的默认形象；管理员侧栏的成员/平台账号列表更是完全没有头像。这使大量无头像账号难以辨认，也让账号界面在「首字 / 通用图标 / 上传图」之间口径不一。需要一个稳定的默认头像集合与统一呈现，且不依赖每个用户各自上传。

## What Changes

- 新增 5 个内置默认用户头像（SVG 静态资源），按 `user.id` 稳定哈希分配：同一用户在任何会话、租户、请求顺序或刷新下映射不变，不新增数据库列、不改注册/建号流程。
- 无上传头像的账号以默认头像兜底；已上传头像始终优先，沿用既有 `users.avatar` 标记与 `shared_root()/avatars/user-<id><ext>` 文件，不新增第二处头像来源。
- 新增只读接口 `GET /api/users/<user_id>/avatar`：有上传返回图片字节，无上传返回 404（默认头像为静态资源，由前端直接引用，不经过后端）。仅本人、平台管理员或与目标存在共同有效租户成员关系者（即现有成员名录可见范围）可读；未认证 401、越权 403；接口不修改任何身份或成员状态。
- 账号界面统一呈现：侧栏账号按钮、账号菜单、个人资料 hero、管理员租户成员列表与平台账号列表均显示「上传头像或稳定默认头像」。
- 同步修订既有规范中与现状冲突的旧口径：侧栏账号头像不再限定为「展示名首字」；个人资料取消「不新增头像」约束；成员/平台账号列表投影与页面补充头像呈现。

## Capabilities

### New Capabilities
- `user-avatar`: 内置默认用户头像集合、按用户标识的稳定分配、上传优先规则，以及只读头像读取接口的鉴权与边界契约。

### Modified Capabilities
- `self-account-context`: `/auth/me` 的 `user` 投影包含头像标记，供前端在上传头像与默认头像之间选择。
- `sidebar-account-menu`: 侧栏账号按钮头像由「展示名首字」改为「上传头像或稳定默认头像」，并允许消费账号头像读取来源。
- `account-menu-actions`: 个人资料取消「不新增头像」的旧约束，头像默认值/上传/呈现统一由 `user-avatar` 能力规定。
- `identity-management-workbench`: 租户成员列表与平台账号列表按账号呈现实头像（上传或默认），不改变既有授权与分页语义。

## Impact

- 前端：`channel/web/static/js/console.js`（默认头像哈希与 `userAvatarHTML` helper、侧栏/账号菜单/个人资料 hero 渲染）、`channel/web/static/js/identity-admin.js`（租户成员列表、平台账号列表行头像）、`channel/web/static/css/console.css`（账号头像容器与图片样式）、`channel/web/chat.html`（如标记需要）。
- 静态资源：新增 `channel/web/static/avatars/default-1.svg` ~ `default-5.svg`。
- 后端：`auth/service.py`（`list_members` / `list_platform_users_paged` 投影补 `avatar`，新增头像读取授权服务方法）、`channel/web/auth_handlers.py`（新增头像读取 handler）、`channel/web/route_registry.py`（注册 `GET /api/users/([^/]+)/avatar`）、`channel/web/web_channel.py`（handler 装配）。
- 测试：新增 `tests/test_user_avatar.py`（路由鉴权、200/404、越权拒绝）并更新 `tests/test_sidebar_account_frontend.cjs`、`tests/test_auth_profile_edit.py` 等相关断言。
- 不改动：上传存储位置与 `users.avatar` 语义、Agent 头像体系、品牌默认头像、身份/租户/权限授权规则、i18n 键集合。
