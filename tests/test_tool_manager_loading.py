# encoding:utf-8
"""``ToolManager.load_tools`` stays quiet about tools it deliberately skips.

Tools whose ``__init__`` needs a dependency only an Agent boot can supply (a
``MemoryManager``, the caller's user id) are injected per Agent by
``bridge.agent_initializer``. They are not engine-loadable, so the loader skips
them — the way it already did for ``MemorySearchTool`` and ``MemoryGetTool``.

The skip was a hardcoded name list, and it missed ``MemoryAddTool`` when that
tool was added. Every ``load_tools()`` therefore logged

    [ERROR][...][tool_manager.py:193] - Error initializing tool class
    MemoryAddTool: MemoryAddTool.__init__() missing 1 required positional
    argument: 'memory_manager'

to **stdout**, which is the machine-readable channel of
``scripts/auth_preflight.py --json``: the report stopped parsing
(``json.decoder.JSONDecodeError``), and the same loader feeds the tool catalog
the 工具授权 tab is built from. Detecting the skip from the constructor
signature means a newly added dependency-injected tool cannot resurrect this.
"""

import logging

import pytest

from agent.tools.tool_manager import ToolManager
from common.log import logger


class _Capture(logging.Handler):
    """Collect messages logged at ERROR or above on the app logger."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.messages = []

    def emit(self, record):  # noqa: D102 - logging.Handler API
        self.messages.append(record.getMessage())


@pytest.fixture
def tool_manager():
    """A fresh ToolManager, so this module cannot ride another test's load."""
    ToolManager.reset_instances()
    try:
        yield ToolManager()
    finally:
        ToolManager.reset_instances()


def test_load_tools_skips_dependency_injected_tools_without_logging_errors(tool_manager):
    capture = _Capture()
    logger.addHandler(capture)
    try:
        tool_manager.load_tools()
    finally:
        logger.removeHandler(capture)

    assert capture.messages == []
    # Injected per Agent, so never in the engine-level class registry...
    assert "memory_add" not in tool_manager.tool_classes
    # ...while the tools the loader *can* construct are still registered.
    assert tool_manager.tool_classes


def test_no_registered_tool_class_needs_an_injected_dependency(tool_manager):
    """The invariant behind the skip: everything in ``tool_classes`` is built
    with ``cls()`` to read its name, so a class that needs arguments here would
    fail the load. This is what the hardcoded name list was standing in for."""
    import inspect

    tool_manager.load_tools()
    for name, cls in tool_manager.tool_classes.items():
        required = [
            parameter.name
            for parameter in inspect.signature(cls.__init__).parameters.values()
            if parameter.name != "self"
            and parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            and parameter.default is parameter.empty
        ]
        assert required == [], f"{name} needs injected arguments {required} but is registered"
