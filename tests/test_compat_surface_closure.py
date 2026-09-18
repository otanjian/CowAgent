# encoding:utf-8
"""8.2 调用观测：退役的个人入口现在还有谁在调用。

本 change 的兼容收口要求「确认正式页面不再使用旧入口，按迁移报告收口旧菜单、开关与
个人专属组件」。这不是一句可以在注释里断言的话——它要么在代码里成立，要么不成立——
所以这里把**调用面**本身钉成断言，作为可重复的调用观测：

1. **数据面**——旧个人端点（``/api/personal/*``、``/api/memory/personal``、
   ``/api/agents?view=personal``）只允许退役组件 ``personal-console.js`` 调用,任何正式
   模块（``console.js``、``identity-admin.js``、``todos.js``、i18n、``chat.html``）出现
   其一即失败。
2. **视图面**——``personal-*`` 视图 id 只允许退役组件注册；正式模块接触退役组件
   的方式只允许两个生命周期钩子（迟到响应失效 ``invalidatePersonalViews``、离页草稿
   守卫 ``__personalConsoleDirtyGuard__``），不允许任何读取/渲染/表单入口。
   ``personal-tools`` / ``personal-skills`` 更彻底：任务 5.5 把它们连同个人资源面一起
   移除，任何模块注册都失败（旧地址转发仍由 ``LEGACY_PERSONAL_FORWARD`` 承担）。
3. **开关面**——五个能力开关的读取方是一张台账（哪个文件读哪个开关）。新出现读取方
   即失败，必须连同台账和 8.2 证据一起更新。
4. **清点面**——退役命名空间仍在发行的部分（仍注册的三个视图 id、三语
   ``menu_personal_*`` / ``nav_group_personal`` 文案、仍转接的旧端点）逐项计数。
   这些正是 8.6 要删除的东西；删掉它们时本文件会失败，提醒同步 8.2 证据，而不是让
   证据悄悄过期。``/api/personal/resources`` 已经完成这一步：它现在是「零出现」断言。

只读观测：本文件不修改任何生产代码。

**随退役一并退休的测试文件**：``tests/test_personal_console_frontend.py`` 与其
``.cjs`` 契约在模块删除后仍去 ``require`` / ``readFileSync``
``channel/web/static/js/personal-console.js``，只能靠"把模块放回去"才通过，所以
在退役完成时一并删除（``unify-console-by-data-scope`` 任务 8.8 兑现的那一刻起，
那 40 余条断言的对象就不存在了）。它们仍有效的判据一条也没丢，只是换了归属：

* 模块的纯函数/视图注册/表单构造——对象已不存在，无判据可留。
* i18n 命名空间三语完整性与合并一致——``tests/test_console_i18n_parity.cjs``
  （"每个命名空间在它声明的每种语言里都完整"）。
* 五个 ``personal.*`` 页面 id 退役并迁移到正式页 id——``tests/test_console_menu_mapping.py``。
* 退役视图 id 无人注册、旧端点零正式调用方、组件本身已删除——本文件上面三条。
* ``chat.html`` 不再挂 ``data-view="personal-*"``、账户面板不再有「我的资源」组——
  ``tests/test_account_menu_no_personal_resources.cjs`` 与
  ``tests/test_personal_console_browser.cjs``（后者已按退役改写，断言"退役地址只作为
  地址转发，``personal-console.js`` 永不被拉取"）。
"""

import os
import re
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS_DIR = os.path.join(_REPO, "channel", "web", "static", "js")
_CHAT_HTML = os.path.join(_REPO, "channel", "web", "chat.html")

#: 退役的个人专属组件。它是唯一允许触达旧个人数据面的模块。
RETIRED_COMPONENT = "personal-console.js"

#: 旧个人数据面（兼容转接用的薄适配端点，见 `channel/web/route_registry.py`）。
RETIRED_ENDPOINTS = (
    "/api/personal/channels",
    "/api/memory/personal",
    "/api/agents?view=personal",
)

#: 已彻底移除的个人资源面（change ``unify-console-by-data-scope`` 任务 5.5）。
#: 它不再是一条「只准退役组件调用」的兼容转接，而是**没有任何调用方、也没有任何
#: 注册**：本人参数改在正式 工具与技能 页的资源详情组件里编辑，写的就是该页自己的
#: 端点（``/api/tools`` / ``/api/skills``）。所以这里断言的是零出现，而不是「只在
#: 退役组件里出现」——留一条只被旧组件调用的路由，等于把已经被取代的第二个写入口
#: 继续养着。
REMOVED_RESOURCE_ENDPOINT = "/api/personal/resources"

#: 旧个人页面 id（前端视图 id）。首页 id 与后端 ``personal.*`` 页面 id 一一对应。
#: 工具/技能两页随资源面一起退役（任务 5.5）：它们不再被任何模块注册，只在
#: ``console.js`` 的 ``LEGACY_PERSONAL_FORWARD`` 里作为**旧地址**保留一条转发。
RETIRED_VIEWS = (
    "personal-agents",
    "personal-channels",
    "personal-memory",
)

#: 退役得更彻底的两个视图 id：不是「只准退役组件注册」，而是谁都不许再注册。
#: 旧书签仍要走 ``LEGACY_PERSONAL_FORWARD``（键，不是 ``id:``），所以按 ``id:`` 形态
#: 全文搜索是准确的判据。
REMOVED_VIEWS = ("personal-tools", "personal-skills")

#: 正式模块接触退役组件时允许的两个生命周期钩子。
ALLOWED_HOOKS = ("invalidatePersonalViews", "__personalConsoleDirtyGuard__")

#: 五个能力开关的读取方台账（8.2 的「who reads which switch」观测结果）。
SWITCH_READERS = {
    "auth/service.py": {
        "user_private_agent_management",
        "member_personal_console",
        "personal_channel_onboarding",
    },
    "agent/private_agent.py": {
        "user_private_agent_management",
        "member_personal_console",
    },
    "agent/memory/personal.py": {"personal_memory_write"},
    "channel/channel_instances.py": {"personal_channel_runtime"},
    "channel/weixin_scan_adapter.py": {"personal_channel_onboarding"},
}

_SWITCH_NAMES = {
    "member_personal_console",
    "user_private_agent_management",
    "personal_memory_write",
    "personal_channel_onboarding",
    "personal_channel_runtime",
}

_READ_CALL = re.compile(
    r"(?:require_personal_capability|personal_capability_enabled"
    r"|personal_capability_open)\(\s*[\"']([a-z_]+)[\"']")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _frontend_files():
    """正式前端模块（不含退役组件）。"""
    paths = []
    for root, _dirs, names in os.walk(_JS_DIR):
        for name in sorted(names):
            if name.endswith(".js") and name != RETIRED_COMPONENT:
                paths.append(os.path.join(root, name))
    paths.append(_CHAT_HTML)
    return paths


def _production_python_files():
    for package in ("auth", "agent", "channel", "cli"):
        for root, _dirs, names in os.walk(os.path.join(_REPO, package)):
            if "__pycache__" in root:
                continue
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def _switch_readers():
    """{相对路径: 该文件读了哪些开关}。

    ``auth/policy.py`` 是声明处（``PERSONAL_CAPABILITY_SWITCHES`` 及三个读取助手），
    不算读取方,故排除;它之外的任何文件出现开关名字面量都会被记进来,包括绕过统一
    入口直接读配置的写法（字面量比对是刻意的粗粒度:宁可多报,不可漏报）。
    """
    found = {}
    for path in _production_python_files():
        rel = os.path.relpath(path, _REPO)
        if rel == os.path.join("auth", "policy.py"):
            continue
        text = _read(path)
        names = set()
        for match in _READ_CALL.finditer(text):
            if match.group(1) in _SWITCH_NAMES:
                names.add(match.group(1))
        for name in _SWITCH_NAMES:
            if re.search(r"[\"']%s[\"']" % name, text):
                names.add(name)
        if names:
            found[rel] = names
    return found


class RetiredDataPlaneTests(unittest.TestCase):

    def test_only_the_retired_component_calls_a_retired_endpoint(self):
        offenders = []
        for path in _frontend_files():
            text = _read(path)
            for endpoint in RETIRED_ENDPOINTS:
                if endpoint in text:
                    offenders.append("%s: %s" % (os.path.relpath(path, _REPO), endpoint))
        self.assertEqual(
            offenders, [],
            "正式前端模块仍在调用旧个人端点,兼容收口不算完成: %s" % offenders)

    def test_the_retired_endpoints_still_exist_so_zero_callers_means_something(self):
        """反向断言:端点仍在注册面上,所以上面的「零调用方」不是搜索写错了。

        原来这条断言读的是退役组件 ``personal-console.js``,用来证明端点确实还
        存在、只是没人正式调用。该组件已随个人控制台退役而删除
        (``unify-console-by-data-scope``),所以「端点还在」这件事现在由**注册面**
        证明:``route_registry.py`` 仍是它们的归属,``console.js`` 的
        ``LEGACY_PERSONAL_FORWARD`` 仍负责旧地址转发。

        换一个证据来源,判据不变:如果哪天端点被改名或删掉,扫描会因为找不到调用方
        而「通过」,这条断言必须在那种情况下失败——否则 8.2 的调用观测就变成了一句
        无法证伪的话。
        """
        routing = os.path.join(_REPO, "channel", "web", "route_registry.py")
        registry_text = _read(routing)
        for endpoint in RETIRED_ENDPOINTS:
            # ``/api/agents?view=personal`` is a path plus a query parameter, not
            # a pattern: what has to still exist is the path.
            path = endpoint.split("?", 1)[0]
            self.assertIn('"%s"' % path, registry_text,
                          "退役端点已从注册面消失: %s" % endpoint)
        # 退役组件本身已经不在了;上面的零调用方断言因此不是「文件被排除在外」。
        self.assertFalse(
            os.path.exists(os.path.join(_JS_DIR, RETIRED_COMPONENT)))

    def test_the_removed_resource_endpoint_has_no_caller_and_no_registration(self):
        """任务 5.5：资源面是**移除**，不是收敛到退役组件里。

        只断言前端不够——真正会让它复活的是注册面。所以这里同时扫前端全文与
        生产 Python（路由表、处理器），任一处出现即失败。
        """
        offenders = []
        for path in _frontend_files():
            if REMOVED_RESOURCE_ENDPOINT in _read(path):
                offenders.append(os.path.relpath(path, _REPO))
        for path in _production_python_files():
            if REMOVED_RESOURCE_ENDPOINT in _read(path):
                offenders.append(os.path.relpath(path, _REPO))
        self.assertEqual(
            offenders, [],
            "已移除的个人资源端点仍有调用方或注册: %s" % offenders)


class RetiredViewPlaneTests(unittest.TestCase):

    def test_no_formal_module_registers_a_retired_view(self):
        offenders = []
        for path in _frontend_files():
            text = _read(path)
            for view in RETIRED_VIEWS:
                if re.search(r"id:\s*[\"']%s[\"']" % re.escape(view), text):
                    offenders.append("%s: %s" % (os.path.relpath(path, _REPO), view))
        self.assertEqual(
            offenders, [],
            "正式模块注册了退役的 personal-* 视图(会重新打开独立个人页面): %s" % offenders)

    def test_the_retired_views_are_forwarded_by_the_console(self):
        """反向断言:三个 ``personal-*`` 视图 id 仍有旧地址转发。

        原来这条断言读退役组件 ``personal-console.js``,核对它注册的三个视图 id
        与 8.2 证据一致。组件删除后,「这三个 id 还在清点面上」由 ``console.js``
        的 ``LEGACY_PERSONAL_FORWARD`` 承担——那里保留的是旧**地址**到新页面的
        转发键。判据不变:清点面上的 id 少一个,这条断言就失败,提醒同步 8.2 证据。
        """
        text = _read(os.path.join(_JS_DIR, "console.js"))
        forward = re.search(r"LEGACY_PERSONAL_FORWARD\s*=\s*\{(.*?)\}",
                            text, re.S)
        self.assertIsNotNone(forward, "console.js 里找不到 LEGACY_PERSONAL_FORWARD")
        for view in RETIRED_VIEWS:
            self.assertIn(view, forward.group(1),
                          "旧地址转发里少了退役视图: %s" % view)

    def test_the_removed_views_are_registered_by_nobody(self):
        """任务 5.5：这两页连同它们的资源面一起退役。

        旧地址仍要能转发（``LEGACY_PERSONAL_FORWARD`` 是**键**，形如
        ``'personal-tools': 'skills'``），所以判据是 ``id: '...'`` 这种注册形态：
        转发表不受影响，任何一处注册都会被这里拦住。
        """
        offenders = []
        for path in _frontend_files():
            text = _read(path)
            for view in REMOVED_VIEWS:
                if re.search(r"id:\s*[\"']%s[\"']" % re.escape(view), text):
                    offenders.append("%s: %s" % (os.path.relpath(path, _REPO), view))
        self.assertEqual(
            offenders, [],
            "已退役的个人工具/技能视图仍被注册: %s" % offenders)
        # 反向断言：旧地址确实还留着转发，否则「零注册」可能是把转发表一起删了。
        console = _read(os.path.join(_JS_DIR, "console.js"))
        for view in REMOVED_VIEWS:
            self.assertIn("'%s':" % view, console,
                          "旧地址 %s 必须仍可转发到正式页面" % view)

    def test_formal_modules_touch_the_retired_component_only_through_lifecycle_hooks(self):
        offenders = []
        for path in _frontend_files():
            lines = _read(path).splitlines()
            for index, line in enumerate(lines):
                if "PersonalConsole" not in line:
                    continue
                window = "\n".join(lines[max(0, index - 2):index + 3])
                if not any(hook in window for hook in ALLOWED_HOOKS):
                    offenders.append("%s:%d" % (os.path.relpath(path, _REPO), index + 1))
        self.assertEqual(
            offenders, [],
            "正式模块在生命周期钩子之外使用退役组件: %s" % offenders)

    def test_no_formal_module_uses_the_retired_renderer(self):
        offenders = []
        for path in _frontend_files():
            text = _read(path)
            for token in ("loadPersonalView", "PERSONAL_VIEWS", "personalFormFields",
                          "personalActionRequest", "personalRowBadges"):
                if token in text:
                    offenders.append("%s: %s" % (os.path.relpath(path, _REPO), token))
        self.assertEqual(
            offenders, [],
            "正式模块在用退役组件的渲染/表单实现: %s" % offenders)


class CapabilitySwitchReaderTests(unittest.TestCase):

    def test_every_capability_switch_has_exactly_the_recorded_readers(self):
        self.assertEqual(
            _switch_readers(), SWITCH_READERS,
            "能力开关的读取方发生变化: 新读取方必须连同 8.2 台账与证据一起更新(旧开关的"
            "独立运行读取要在迁移完成后移除,以共用状态为唯一依据)")

    def test_the_five_switches_are_declared_exactly_once(self):
        policy = _read(os.path.join(_REPO, "auth", "policy.py"))
        for name in _SWITCH_NAMES:
            self.assertIn('"%s"' % name, policy)

    def test_config_ships_the_runtime_switch_off(self):
        config = _read(os.path.join(_REPO, "config.py"))
        self.assertRegex(config, r'"personal_channel_runtime":\s*False',
                         "运行开关的出厂值必须是关(未经真实验收不得启用)")


if __name__ == "__main__":
    unittest.main()
