"""The RULE.md layout section must describe the deployment it is written into.

A hardcoded ``~/cow`` tree is wrong for an Agent living under a shared root
(``<root>/agents/<id>``), and wrong again for a tenant whose shared root is
somewhere else entirely. These tests pin the section to the workspace path that
is actually being scaffolded, in both languages.
"""

from pathlib import Path
import re

import pytest

from agent.prompt import workspace as ws
from common import i18n
from common.i18n import EN, ZH


@pytest.fixture
def lang():
    """Restore the process language after each test; these tests flip it."""
    original = i18n.get_language()
    yield i18n.set_language
    i18n.set_language(original)


def _display(root: Path) -> str:
    """The ``~``-shortened form the layout section prints."""
    try:
        return "~/" + str(root.relative_to(Path.home()))
    except ValueError:
        return str(root)


def test_agent_layout_names_the_real_shared_root(lang, tmp_path):
    lang(ZH)
    root = tmp_path / "root"
    section = ws.workspace_layout_section(str(root / "agents" / "alpha"))

    assert _display(root) in section
    assert "agents/alpha" in section
    assert "~/cow" not in section


def test_agent_layout_covers_the_three_ownership_tiers(lang, tmp_path):
    lang(ZH)
    section = ws.workspace_layout_section(str(tmp_path / "root" / "agents" / "alpha"))

    assert "本智能体私有" in section
    assert "共享层" in section
    assert "每位用户私有" in section
    assert "users/<user_id>/" in section
    assert "long-term/index.db" in section
    assert "scheduler/" in section
    assert "tmp/" in section


def test_agent_layout_english(lang, tmp_path):
    lang(EN)
    root = tmp_path / "root"
    section = ws.workspace_layout_section(str(root / "agents" / "alpha"))

    assert _display(root) in section
    assert "## Workspace directory structure" in section
    assert "agents/alpha" in section
    assert "users/<user_id>/" in section
    assert "~/cow" not in section


def test_layout_when_workspace_is_the_shared_root(lang, tmp_path):
    lang(ZH)
    root = tmp_path / "root"
    section = ws.workspace_layout_section(str(root))

    assert _display(root) in section
    assert "你的工作区就是共享根" in section
    assert "agents/<id>/" in section
    assert "~/cow" not in section


def test_layout_when_workspace_is_the_shared_root_english(lang, tmp_path):
    lang(EN)
    root = tmp_path / "root"
    section = ws.workspace_layout_section(str(root))

    assert _display(root) in section
    assert "*is* the shared root" in section
    assert "agents/<id>/" in section
    assert "~/cow" not in section


def test_ensure_workspace_writes_the_agent_layout(lang, tmp_path):
    lang(ZH)
    root = tmp_path / "root"
    workspace = root / "agents" / "alpha"
    files = ws.ensure_workspace(str(workspace))

    rule = Path(files.rule_path).read_text(encoding="utf-8")
    assert _display(root) in rule
    assert "agents/alpha" in rule
    assert "{{WORKSPACE_LAYOUT}}" not in rule
    assert "~/cow" not in rule


def test_ensure_workspace_writes_the_root_layout(lang, tmp_path):
    lang(ZH)
    workspace = tmp_path / "root"
    files = ws.ensure_workspace(str(workspace))

    rule = Path(files.rule_path).read_text(encoding="utf-8")
    assert _display(workspace) in rule
    assert "你的工作区就是共享根" in rule
    assert "{{WORKSPACE_LAYOUT}}" not in rule
    assert "~/cow" not in rule


def test_tree_comments_share_one_column(lang, tmp_path):
    """A misaligned tree reads as sloppy; alignment must survive any id length."""
    lang(ZH)
    section = ws.workspace_layout_section(
        str(tmp_path / "root" / "agents" / "business-analysis"))

    tree = re.search(r"```\n(.*?)\n```", section, re.S).group(1)
    columns = {line.index("#") for line in tree.splitlines() if "#" in line}
    assert len(columns) == 1


def test_rendered_template_has_no_stray_blank_lines(lang, tmp_path):
    lang(ZH)
    rendered = ws._get_rule_template(str(tmp_path / "root" / "agents" / "alpha"))
    assert "\n\n\n" not in rendered


def test_templates_leave_no_unrendered_placeholder(lang, tmp_path):
    for language in (ZH, EN):
        lang(language)
        rendered = ws._get_rule_template(str(tmp_path / "root" / "agents" / "alpha"))
        assert ws._LAYOUT_PLACEHOLDER not in rendered
        assert rendered.count("## ") >= 2
