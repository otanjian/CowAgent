## 1. 默认头像资源

- [x] 1.1 新增 `channel/web/static/avatars/default-1.svg` ~ `default-5.svg`：统一 `viewBox 0 0 64 64`，圆形底 + 扁平人物剪影，5 套品牌配色与 5 个小配件区分，不透明底色以适配深浅主题
- [x] 1.2 确认经既有 `/assets/(.*)` 可访问（`/assets/avatars/default-1.svg` 返回 200 与 `image/svg+xml`）

## 2. 前端统一呈现

- [x] 2.1 在 `channel/web/static/js/console.js` 新增 `USER_AVATAR_DEFAULTS = 5`、`userDefaultAvatarIndex(userId)`（固定 `hash*31 >>> 0` 取模）、`userDefaultAvatarURL(userId)` 与 `userAvatarHTML({id, avatar, self, version})`，非本人上传头像走 `/api/users/<id>/avatar`、本人走 `/auth/profile/avatar`，并对 `img` 挂 `onerror` 回退默认头像
- [x] 2.2 侧栏账号按钮 `sidebar-account-avatar` 由写展示名首字改为写入 `userAvatarHTML(...)`，未登录/加载/错误仍用人物图标；账号菜单 `account-menu-avatar` 同步
- [x] 2.3 个人资料 hero `renderAccountProfileAvatar` 的 else 分支由首字改为默认头像（有上传仍优先上传头像）
- [x] 2.4 `channel/web/static/css/console.css` 补账号头像容器内 `img` 的铺满与 `object-fit: cover`，样式与既有圆角一致
- [x] 2.5 `channel/web/static/js/identity-admin.js` 的租户成员列表与平台账号列表把通用 `fa-user` / `fa-user-cog` 图标换成 `userAvatarHTML(...)`，`avatar === 'image'` 时请求只读头像接口

## 3. 后端头像读取

- [x] 3.1 `auth/service.py`：`list_members` 与 `list_platform_users_paged` 投影补 `avatar` 字段（不返回字节或路径）
- [x] 3.2 `auth/service.py`：新增头像读取授权服务方法，单一入口判定「本人 / 有效平台管理员 / 与目标有共同 active 租户成员关系」，返回目标账号 id 与 `avatar` 标记，越权抛 403、无会话抛 401
- [x] 3.3 `channel/web/auth_handlers.py`：新增用户头像只读 handler，复用 `shared_root()/avatars/user-<id>` 与既有类型映射，有上传返回字节与 Content-Type、无上传返回 404
- [x] 3.4 `channel/web/route_registry.py` 注册 `GET /api/users/([^/]+)/avatar` 为 `personal` 策略，并在 `channel/web/web_channel.py` 装配 handler

## 4. 测试与校验

- [x] 4.1 新增 `tests/test_user_avatar.py`：覆盖本人/平台管理员/同租户成员读取 200、无上传 404、未认证 401、无资格 403、接口不改状态、不泄漏路径
- [x] 4.2 更新 `tests/test_sidebar_account_frontend.cjs`：断言无上传时渲染默认头像 `img`、`avatar === 'image'` 时走上传路径、映射对同一 id 稳定
- [x] 4.3 更新 `tests/test_auth_profile_edit.py` 与管理员列表相关前端断言：列表投影含 `avatar`、列表行渲染头像
- [x] 4.4 运行相关 Python 与 `.cjs` 测试并确认通过
- [x] 4.5 `openspec validate add-default-user-avatars --strict` 通过
