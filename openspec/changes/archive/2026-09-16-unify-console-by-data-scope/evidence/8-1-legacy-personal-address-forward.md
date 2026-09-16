# 8.1 旧 personal 地址的受权转接与旧个人 API 的薄适配

对应 `tasks.md` 8.1：「实现旧 personal-* 地址到正式页面的受权转接，保留合法对象和上下文、
离页保护及迟到响应隔离；旧个人 API 只转换参数/响应并委托统一业务服务。」

## 1. 缺陷形状：退役页面仍可直达

任务 3.3/3.4 摘掉了账号菜单里的五项目个人入口，但**页面本身**还在：
`VIEW_META` 仍有 `personal-agents` / `personal-channels` / `personal-memory` /
`personal-tools` / `personal-skills`，`personal-console.js` 仍注册这五个视图。

于是 `#view-personal-memory` 这类收藏或转发链接仍然打开**另一套页面**——它有自己的门禁、
自己的端点、自己的动词投影。这正是本次整合要消除的东西：一次收敛如果留下一个可直达的
第二界面，那么「成员的对象在正式页面上管理」这句话就只对不用书签的人成立。

## 2. 转接（`channel/web/static/js/console.js`）

`LEGACY_PERSONAL_FORWARD` 把旧 id 映到承载同一对象的正式页面：

| 旧地址 | 转接到 | 为什么 |
| --- | --- | --- |
| `personal-agents` | `agents`（智能体管理） | 任务 4.x 已把生命周期合流，范围由 `auth.object_scope` 判定 |
| `personal-channels` | `channels`（消息渠道） | 任务 6.1 已让同一页面对成员开本人范围 |
| `personal-memory` | `memory`（记忆管理） | 任务 5.1 的目标集合由服务端下发，含个人域 |
| `personal-tools` | `skills`（工具与技能） | 工具与技能在整合后是同一页面（`admin.skills`） |
| `personal-skills` | `skills` | 同上 |

`legacyPersonalForward(viewId)` 经 `VIEW_META` 解析目标，所以映射表**不可能指向一个控制台
没有的视图**——否则 `navigateTo` 会静默落空，用户停在旧页面上而没有任何提示。

### 受权，不是豁免

转接块放在 `navigateTo` 的 `scenarios → scenes` 旧 id 重定向之后、**可用性门禁
（`_viewNavDenied`）与离页检查（`_viewLeaveCheck`）之前**。三件事因此成立：

1. **判定的是目标页面**。门禁按 `admin.memory` 等目标的真实投影裁决；一个成员进不去的
   正式页面，其拒绝由该页面自己如实呈现，而不是被旧地址绕过。
2. **离页保护仍然生效**。未保存草稿的询问发生在转接之后，所以问的是「要不要离开到目标
   页」，而不是先转接再丢弃——顺序反了就会静默丢稿。
3. **迟到响应被隔离**。转接同一步调用 `PersonalConsole.invalidatePersonalViews()`，把五个
   个人视图的 generation 全部前推；正在途中的个人响应回来时 generation 已不匹配，不能覆盖
   即将打开的页面。

地址也随转接改写（`history.replaceState`，不是 `pushState`）——URL 显示的是**实际呈现的
页面**，而不是它取代的那个；用 replace 则是因为后退键不应该在重定向上来回弹。

### 合法对象与上下文

`cow_memory_agent` 这类目标偏好由控制台按租户+用户持久化，转接到 `memory` 后仍然生效，
所以记忆页保留的是调用者原来选的那个合法目标。其余四页原本就没有对象参数（它们是列表页），
上下文即选中租户，租户由 `X-Tenant-ID` 与 `_invalidateAuthContext` 的既有机制维持。

## 3. 旧个人 API 已是薄适配

四个旧接口都已经只做参数/响应转换，委托统一业务服务，没有第二份实现：

| 旧接口 | 委托到 | 证据 |
| --- | --- | --- |
| `GET/POST /api/memory/personal` | `PersonalMemoryService(identity=to_runtime_identity(ctx))` | `web_channel.py:_personal_memory_service`（9074） |
| `GET /api/memory?scope=personal` | **同一个**构造函数 | `memory_console.py:_personal_service`（649）显式调用 `_web_channel()._personal_memory_service(ctx)`，docstring 写明「Same binding the ``/api/memory/personal`` handlers use」 |
| `GET/POST /api/personal/channels` | `IdentityService.list_personal_channel_instances` / `create_personal_channel_instance`（`allow_owner`，scope/owner 由 ctx 强制） | `web_channel.py:PersonalChannelHandler`（9106） |
| `GET/POST /api/personal/resources` | `IdentityService.list_personal_resource_configs` / `save_personal_resource_config` | `web_channel.py:PersonalResourceHandler`（9268） |
| `GET /api/agents?view=personal` | `_personal_agents_projection` 建立在 `_tenant_agents_admin_projection` 之上，用 `private_agent_ids` 过滤 | `web_channel.py`（10080） |

记忆这一条尤其要看清：统一路径与旧路径不是「碰巧行为一致的两个实现」，而是**同一个
服务构造入口**。旧的个人实现已经不存在了。

## 4. 证据

```
node --test tests/test_personal_address_forward_frontend.cjs
→ 6 passed
```

`tests/test_personal_address_forward_frontend.cjs`（新建）：

* `every retired personal page declares where it forwards` —— 成员集合由 `VIEW_META`
  **推导**而非写死：新增一个 `personal-*` 视图而不做决定，这条会失败，而不是留下一个
  可以顺着书签走进去的退役页面；
* `a retired address forwards to the page carrying the same objects` —— 五个映射逐个钉住，
  含 `personal-tools` 与 `personal-skills` 归到同一页面；
* `a forward never names a view the console does not have` —— 经 `VIEW_META` 解析；
* `an ordinary view is left alone` —— 正式页面 id 不被误转；
* `the forward runs before the availability gate, not around it` —— 用**源码顺序**断言
  `legacyPersonalForward` 出现在 `_viewNavDenied` 与 `_viewLeaveCheck` 之前。这是本文件里
  唯一能表达「受权而非豁免」的方式：门禁后置就会先判退役页面再放行目标，「受权」只剩名义；
* `an in-flight personal load is invalidated when the address forwards` —— 失效调用落在转接
  步骤内、门禁之前。

```
node --test tests/test_personal_console_frontend.cjs tests/test_nav_area_frontend.cjs \
  tests/test_channel_scope_nav_frontend.cjs tests/test_admin_area_group_gating.cjs \
  tests/test_account_menu_no_personal_resources.cjs tests/test_recovered_pages_frontend.cjs
→ 全部通过
```

`test_sidebar_account_frontend.cjs` 有 5 条失败（`node('login-form').onsubmit is not a
function`）。**与本改动无关**：该文件在 170 行用 `navigateTo(view) { ctx.currentView = view; }`
桩掉了导航，根本不执行本次改动的函数；失败属于既有环境问题（登录表单壳），改动前后一致。

## 5. 边界

* 浏览器内实操（真实点击书签、跨区转接、后退键）属任务 3.6，需 Playwright，本机不可运行；
* 旧页面模块 `personal-console.js` 本体未删除：它的纯函数仍被其它测试使用，且删除它属于
  「退役不删除业务数据」之外的组件退役，应在 8.2 的兼容周期观测后收口，而不是在转接刚
  落地时就删——先证明没有调用，再删。
