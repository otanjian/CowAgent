# encoding:utf-8
"""入站渠道附件的落点按渠道实例绑定 Agent 解析
(scope-inbound-attachment-to-bound-agent)。

附件下载发生在消息解析阶段，早于 ``chat_channel._handle`` 建立身份作用域
(``use_identity(_identity_for(context))``)。因此 ``state_dir.tmp_dir()`` 读不到
ambient 身份，按下落规则回退到**进程全局默认智能体**的工作区。真实后果：绑定
租户智能体的企业微信机器人把客户资料下载进全局默认智能体的 ``tmp/``，而租户
智能体受执行隔离约束读不到该路径，只能回报「未投递到本工作区」。

这里钉住 ``wecom_bot`` 的**落点**：用真实 registry 配合真实 ``state_dir`` 解析，
断言附件落在绑定 Agent 的 workspace 之下、且**不**落在全局默认智能体之下。

``_build_context`` 是企业微信长连接与 webhook 回调共享的解析步骤（见
``test_wecom_bot_inbound_identity.py`` 的模块说明），故在此断言。
"""

import pytest

from agent.registry import AgentProfile, AgentRegistry, set_agent_registry

BOUND_AGENT = "agent-tax-health"
DEFAULT_AGENT = "agent-default"
BOT_ID = "bot_acme_health"
INSTANCE_ID = "chan_acme_health"
TENANT_ID = "tnt_acme"
SENDER = "zhangsan"

# 足够让 ``_guess_ext_from_bytes`` 判成 .docx / .png 的最小载荷。
DOCX_BYTES = b"PK\x03\x04" + b"word/document.xml" + b"\x00" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def workspaces(tmp_path):
    """绑定 Agent 与进程全局默认 Agent 两个**真实**工作区。

    默认 Agent 之所以重要：无身份时的下落目标就是它，测试必须能观测到
    「附件错误地落到那里」。
    """
    bound = tmp_path / BOUND_AGENT
    default = tmp_path / DEFAULT_AGENT
    set_agent_registry(AgentRegistry(
        [
            AgentProfile(id=BOUND_AGENT, name=BOUND_AGENT, workspace=str(bound)),
            AgentProfile(id=DEFAULT_AGENT, name=DEFAULT_AGENT, workspace=str(default)),
        ],
        DEFAULT_AGENT,
    ))
    yield bound, default
    set_agent_registry(None)


def _channel(*, bound_agent_id=""):
    """真实 WecomBotChannel，绕过进程级 singleton 缓存。

    ``bound_agent_id`` 有值时走多实例路径（``apply_instance``）；为空即传统
    单实例部署，保持既有语义。
    """
    from channel.wecom_bot.wecom_bot_channel import WecomBotChannel

    channel = WecomBotChannel.new_instance()
    channel.channel_type = "wecom_bot"
    channel.bot_id = BOT_ID
    if bound_agent_id:
        channel.apply_instance(
            instance_id=INSTANCE_ID, bound_agent_id=bound_agent_id,
            tenant_id=TENANT_ID)
    return channel


def _media_body(msgtype, msg_id):
    key = "file" if msgtype == "file" else "image"
    return {
        "msgid": msg_id,
        "msgtype": msgtype,
        "aibotid": BOT_ID,
        "chattype": "single",
        "from": {"userid": SENDER},
        key: {"url": "https://example.invalid/media", "aeskey": "x" * 43},
    }


def _stub_media(monkeypatch, payload):
    """媒体下载要走网络；换成固定字节以便观测落点。"""
    import channel.wecom_bot.wecom_bot_message as wm

    monkeypatch.setattr(wm, "_decrypt_media", lambda url, aeskey: payload)


def _landed(root):
    tmp = root / "tmp"
    return sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []


# --- 绑定 Agent 的实例：附件必须落在该 Agent 工作区 -------------------------

def test_file_lands_in_the_bound_agent_workspace(workspaces, monkeypatch):
    bound, default = workspaces
    _stub_media(monkeypatch, DOCX_BYTES)

    channel = _channel(bound_agent_id=BOUND_AGENT)
    # FILE 走缓存分支，消费掉消息属预期行为。
    assert channel._build_context(_media_body("file", "msg_file"), False) is None

    assert _landed(bound) == ["wecom_msg_file.docx"]
    assert _landed(default) == [], \
        "附件不得落到进程全局默认智能体工作区"


def test_image_lands_in_the_bound_agent_workspace(workspaces, monkeypatch):
    """图片与文件必须同一归属规则，不能一类落对、另一类落到全局默认。"""
    bound, default = workspaces
    _stub_media(monkeypatch, PNG_BYTES)

    channel = _channel(bound_agent_id=BOUND_AGENT)
    assert channel._build_context(_media_body("image", "msg_image"), False) is None

    assert _landed(bound) == ["wecom_msg_image.png"]
    assert _landed(default) == [], \
        "图片不得落到进程全局默认智能体工作区"


def test_both_receive_paths_agree(workspaces, monkeypatch):
    """长连接（无 default_aeskey）与回调（带 default_aeskey）落点一致。"""
    bound, default = workspaces
    _stub_media(monkeypatch, DOCX_BYTES)

    channel = _channel(bound_agent_id=BOUND_AGENT)
    channel._build_context(_media_body("file", "msg_ws"), False)
    channel._build_context(
        _media_body("file", "msg_hook"), False, default_aeskey="x" * 43)

    assert _landed(bound) == ["wecom_msg_hook.docx", "wecom_msg_ws.docx"]
    assert _landed(default) == []


def test_the_bound_agent_can_read_what_it_received(workspaces, monkeypatch):
    """落点必须是该租户智能体在自己隔离边界内可读的路径。"""
    bound, default = workspaces
    _stub_media(monkeypatch, DOCX_BYTES)

    channel = _channel(bound_agent_id=BOUND_AGENT)
    channel._build_context(_media_body("file", "msg_read"), False)

    landed = bound / "tmp" / "wecom_msg_read.docx"
    assert landed.read_bytes() == DOCX_BYTES


# --- 反向控制：传统单实例渠道保持既有语义 ---------------------------------

def test_an_unbound_instance_keeps_the_existing_fallback(workspaces, monkeypatch):
    """未登记绑定 Agent 的实例不得被本次修复改变行为。

    该部署合法地依赖无身份时的下落规则；把这一条钉住，防止「修复」退化成
    「一律拒绝」或把附件改落到别的 Agent。
    """
    bound, default = workspaces
    _stub_media(monkeypatch, DOCX_BYTES)

    channel = _channel()  # 不调用 apply_instance
    assert channel._build_context(_media_body("file", "msg_legacy"), False) is None

    assert _landed(default) == ["wecom_msg_legacy.docx"]
    assert _landed(bound) == [], \
        "无绑定的实例不得被改判到其他 Agent"


# --- 归属不可解析：失败关闭，不得回退全局默认 ------------------------------

def test_an_unresolvable_bound_agent_fails_closed(workspaces, monkeypatch):
    """绑定 Agent 不存在时必须拒绝，而不是静默落到全局默认智能体工作区。

    静默回退正是本次缺陷的形态：附件会落在需要读它的 Agent 够不到的地方。
    拒绝必须留下可诊断原因，且不得抛给调用方的收发循环。
    """
    bound, default = workspaces
    _stub_media(monkeypatch, DOCX_BYTES)

    channel = _channel(bound_agent_id="agent-that-no-longer-exists")
    result = channel._build_context(_media_body("file", "msg_ghost"), False)

    assert result is None, "归属不可解析时必须拒绝该消息"
    assert _landed(default) == [], "不得回退到全局默认智能体工作区"
    assert _landed(bound) == []


if __name__ == "__main__":
    import unittest

    unittest.main()
