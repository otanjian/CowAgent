# encoding:utf-8
"""Runs the personal-console frontend contract (node:test).

The assertions live in ``tests/test_personal_console_frontend.cjs``; this wrapper
exists so the JavaScript contract runs with the rest of the suite. Node is
optional in this repository, so the test is skipped rather than failing when it
is absent (same convention as the other ``*_frontend.py`` wrappers).
"""

import shutil
import subprocess
import unittest
from pathlib import Path


class PersonalConsoleFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for console behavior tests")
    def test_frontend_behavior(self):
        result = subprocess.run(
            [shutil.which("node"), "--test",
             str(Path(__file__).with_name("test_personal_console_frontend.cjs"))],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
