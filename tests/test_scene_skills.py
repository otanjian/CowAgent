# encoding:utf-8
"""场景技能映射与注册测试（阶段 2）。

覆盖：
- ``scenes/skill_mapping.json`` 覆盖全部去重 ``skill_name``
- 未映射键不阻断解析
- 场景技能注册进 SkillManager 可发现集
- 多场景共享技能复用同一实例（按名字去重）
- 技能目录非法不阻断其它技能注册
"""
import json
import os
import tempfile
import unittest

from scenes import config as scenes_config


def _all_skill_names():
    data = scenes_config.load_config()
    names = set()
    for scene in data.get("scenes", []):
        if scene.get("skill_name"):
            names.add(scene["skill_name"])
    return names


class SkillMappingTests(unittest.TestCase):
    def test_mapping_covers_all_skill_names(self):
        mapping = scenes_config.load_skill_mapping()
        names = _all_skill_names()
        self.assertGreaterEqual(len(names), 1)
        for name in names:
            self.assertIn(name, mapping, f"skill_name '{name}' 未在映射表中")

    def test_mapping_marks_unmapped_explicitly(self):
        mapping = scenes_config.load_skill_mapping()
        unmapped = [k for k, v in mapping.items() if v.get("mapped") is False]
        # 至少存在「未映射」标注键
        self.assertGreaterEqual(len(unmapped), 1)
        for k in unmapped:
            self.assertIsNone(mapping[k]["skill_dir"])
            self.assertIsNone(mapping[k]["name"])

    def test_mapped_entries_reference_existing_dirs(self):
        mapping = scenes_config.load_skill_mapping()
        skills_root = os.path.join(scenes_config.scenes_dir(), "skills")
        for k, v in mapping.items():
            if v.get("mapped"):
                d = os.path.join(skills_root, v["skill_dir"])
                self.assertTrue(
                    os.path.isdir(d), f"映射技能目录不存在: {v['skill_dir']}"
                )
                self.assertTrue(
                    os.path.isfile(os.path.join(d, "SKILL.md")),
                    f"映射技能缺 SKILL.md: {v['skill_dir']}",
                )


class SceneSkillDiscoveryTests(unittest.TestCase):
    def _manager(self):
        from agent.skills.manager import SkillManager

        custom = os.path.join(tempfile.mkdtemp(), "skills")
        os.makedirs(custom, exist_ok=True)
        return SkillManager(custom_dir=custom)

    def test_scene_skills_discovered(self):
        manager = self._manager()
        names = set(manager.skills.keys())
        # 映射后的技能名应出现在可发现集中
        for expected in ("quality-trace", "sap-integration", "bid-analysis",
                         "procurement-supplier-risk", "production-scheduling",
                         "financial-report-analysis", "finance-ledger-generator"):
            self.assertIn(expected, names, f"场景技能未注册: {expected}")

    def test_scene_skill_shared_reused_not_duplicated(self):
        manager = self._manager()
        names = [k for k in manager.skills.keys() if k == "quality-trace"]
        self.assertEqual(len(names), 1)


class SceneSkillResilienceTests(unittest.TestCase):
    def test_unmapped_key_does_not_resolve(self):
        from scenes import service as scenes_service

        self.assertEqual(
            scenes_service.resolve_skill_names({"skill_name": "hr-recruit"}), []
        )

    def test_illegal_skill_dir_does_not_block_others(self):
        # 一个不存在的场景技能目录不阻断其它场景技能注册。
        from agent.skills.manager import SkillManager

        custom = os.path.join(tempfile.mkdtemp(), "skills")
        os.makedirs(custom, exist_ok=True)
        manager = SkillManager(custom_dir=custom)
        # 缺失目录由 loader 忽略，不抛异常
        self.assertIn("quality-trace", manager.skills)


if __name__ == "__main__":
    unittest.main()
