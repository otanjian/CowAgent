# encoding:utf-8
"""Runs the Desktop external-connection broker contract (node:test).

断言在 ``tests/test_desktop_external_broker.cjs``：外部连接与 ERP 连接走与其它
``/api/**`` 相同的 broker 路径（同源路径守卫、``withTenant``、``withToken``），
控制台页 ``ecRequest`` 只用相对 ``same-origin`` fetch，且没有 ``desktop-external``
旁路 IPC。

该合同是纯静态断言（读 ``auth-broker.ts`` / ``preload.ts`` 与连接页 JS 的源码），
只要求 Node，不需要 TypeScript 转译。缺少 Node 时跳过而不是失败。
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


class DesktopExternalBrokerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the desktop broker contract")
    def test_external_broker_contract(self):
        result = subprocess.run(
            [shutil.which("node"), "--test",
             str(Path(__file__).with_name("test_desktop_external_broker.cjs"))],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
