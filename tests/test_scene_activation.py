# encoding:utf-8
"""场景提示词注入测试（阶段 1）。

覆盖 ``AgentBridge._apply_scene_context`` 的注入逻辑与
``scenes.service.resolve_skill_names`` 的技能解析：
- 激活场景后注入场景提示词到 ``extra_system_suffix``
- 未激活场景不注入
- 技能未安装/未映射不阻断（仅不注入对应技能）

场景上下文为模块级共享状态，测试在 setUp/tearDown 中隔离清理。
"""
import unittest

from bridge.agent_bridge import AgentBridge
from scenes import service as scenes_service


class _StubSkillManager:
    def __init__(self, selection=None):
        self.selection = selection


class _StubAgent:
    def __init__(self, skill_manager=None):
        self.extra_system_suffix = None
        self.skill_manager = skill_manager


class SceneActivationTests(unittest.TestCase):
    def setUp(self):
        scenes_service.clear_all_scene_context()
        # 裸实例：_apply_scene_context 不依赖 AgentBridge 初始化状态。
        self.bridge = object.__new__(AgentBridge)

    def tearDown(self):
        scenes_service.clear_all_scene_context()

    def _apply(self, agent, session_id="s1"):
        self.bridge._apply_scene_context(agent, session_id)
        return agent

    def test_activated_scene_injects_prompt(self):
        scenes_service.set_scene_context(
            "s1", {"id": "finance_voucher", "system_prompt": "你是凭证专家"}
        )
        agent = self._apply(_StubAgent())
        self.assertEqual(agent.extra_system_suffix, "你是凭证专家")

    def test_not_activated_does_not_inject(self):
        agent = self._apply(_StubAgent(), session_id="never_activated")
        self.assertIsNone(agent.extra_system_suffix)

    def test_scene_without_prompt_does_not_inject(self):
        scenes_service.set_scene_context("s1", {"id": "x"})
        agent = self._apply(_StubAgent())
        self.assertIsNone(agent.extra_system_suffix)

    def test_uninstalled_skill_does_not_block(self):
        # 未映射/未安装的技能不阻断：selection 保持 None（全部可用）。
        scenes_service.set_scene_context(
            "s1", {"id": "x", "system_prompt": "p", "skill_name": "finance-expert"}
        )
        agent = self._apply(_StubAgent(skill_manager=_StubSkillManager(None)))
        self.assertEqual(agent.extra_system_suffix, "p")
        self.assertIsNone(agent.skill_manager.selection)

    def test_mapped_skill_added_to_selection(self):
        # 已映射技能：selection 为受限集合时，加入解析后的技能名。
        scenes_service.set_scene_context(
            "s1", {"id": "x", "skill_name": "quality-trace"}
        )
        sm = _StubSkillManager(selection={"other-skill"})
        agent = self._apply(_StubAgent(skill_manager=sm))
        self.assertIn("quality-trace", agent.skill_manager.selection)
        self.assertIn("other-skill", agent.skill_manager.selection)

    def test_no_skill_manager_does_not_block(self):
        scenes_service.set_scene_context(
            "s1", {"id": "x", "system_prompt": "p", "skill_name": "quality-trace"}
        )
        agent = self._apply(_StubAgent(skill_manager=None))
        self.assertEqual(agent.extra_system_suffix, "p")


class ResolveSkillNamesTests(unittest.TestCase):
    def test_mapped_skill(self):
        self.assertEqual(
            scenes_service.resolve_skill_names({"skill_name": "quality-trace"}),
            ["quality-trace"],
        )

    def test_unmapped_skill(self):
        # chen-yiwei-perspective 标注「未映射」
        self.assertEqual(
            scenes_service.resolve_skill_names({"skill_name": "hr-recruit"}), []
        )

    def test_no_skill_name(self):
        self.assertEqual(scenes_service.resolve_skill_names({"id": "x"}), [])


if __name__ == "__main__":
    unittest.main()
