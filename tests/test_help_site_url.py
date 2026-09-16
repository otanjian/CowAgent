# encoding:utf-8
"""Tests for the 「帮助与关于」 target resolver (change help-about-project-site-link).

The resolver owns the whole "has the site declared its address?" decision, so
these tests pin both halves: the values that are usable as a link target, and
every shape that must read as *unconfigured* and answer the local default
instead of a placeholder or a malformed link.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from channel.web.help_site import (  # noqa: E402
    DEFAULT_HELP_SITE_URL,
    normalize_site_url,
    read_declared_site_url,
    resolve_help_site_url,
    site_config_path,
)

TEMPLATE = """<?php
return [
    'brand' => ['name' => '容大AI'],
    // 部署命令中的 {site_url} 占位符会在渲染时替换为该值。
    'site_url' => %s,
    'deployments' => [
        'unix' => "bash <(curl -fsSL {site_url}/assets/deploy/run.sh)",
    ],
];
"""


def _config_with(value_literal):
    handle, path = tempfile.mkstemp(suffix=".php")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(TEMPLATE % value_literal)
    return path


class NormalizeSiteUrlTests(unittest.TestCase):
    def test_accepts_absolute_http_and_https(self):
        self.assertEqual(normalize_site_url("http://localhost:8080/"), "http://localhost:8080/")
        self.assertEqual(normalize_site_url("https://help.example.com"), "https://help.example.com/")
        self.assertEqual(
            normalize_site_url("  http://127.0.0.1:8080  "), "http://127.0.0.1:8080/")

    def test_keeps_subdirectory_deployment_path(self):
        self.assertEqual(
            normalize_site_url("https://example.com/webhelp"), "https://example.com/webhelp/")
        self.assertEqual(
            normalize_site_url("https://example.com/webhelp/"), "https://example.com/webhelp/")

    def test_drops_query_fragment_and_credentials(self):
        self.assertEqual(
            normalize_site_url("https://user:pass@example.com:8443/a?b=1#c"),
            "https://example.com:8443/a/")

    def test_keeps_ipv6_literal_bracketed(self):
        self.assertEqual(normalize_site_url("http://[::1]:8080"), "http://[::1]:8080/")

    def test_rejects_unusable_values(self):
        for value in (
            "", "   ", None,
            "localhost:8080",                     # no scheme
            "ftp://example.com",                  # wrong scheme
            "javascript:alert(1)",                # never a link target
            "data:text/html,<b>x</b>",
            "://example.com",                     # unparsable
            "http://",                            # no host
            "http://example.com:notaport",        # malformed port
            "http://YOUR-SITE-DOMAIN",            # scaffold placeholder
            "https://YOUR-SITE-DOMAIN/webhelp",   # placeholder with a path
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_site_url(value), "")


class ReadDeclaredSiteUrlTests(unittest.TestCase):
    def _cleanup(self, *paths):
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_reads_single_and_double_quoted_literals(self):
        for literal in ("'https://help.example.com'", '"https://help.example.com"'):
            path = _config_with(literal)
            self.addCleanup(self._cleanup, path)
            self.assertEqual(read_declared_site_url(path), "https://help.example.com")

    def test_reads_the_declaration_not_its_uses(self):
        # The deployments block references "{site_url}" inside a double-quoted
        # string; only the keyed declaration may be picked up.
        path = _config_with("'https://help.example.com'")
        self.addCleanup(self._cleanup, path)
        self.assertEqual(read_declared_site_url(path), "https://help.example.com")

    def test_missing_file_or_key_answers_empty(self):
        self.assertEqual(read_declared_site_url("/nonexistent/config.php"), "")
        handle, path = tempfile.mkstemp(suffix=".php")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("<?php return ['brand' => ['name' => 'x']];\n")
        self.addCleanup(self._cleanup, path)
        self.assertEqual(read_declared_site_url(path), "")

    def test_shipped_site_config_declares_no_real_address(self):
        # The repo ships the scaffold placeholder, so the console must fall back
        # until an operator declares the real address.
        self.assertTrue(os.path.isfile(site_config_path()))
        self.assertEqual(resolve_help_site_url(), DEFAULT_HELP_SITE_URL)


class ResolveHelpSiteUrlTests(unittest.TestCase):
    def test_declared_address_wins(self):
        path = _config_with("'https://help.example.com'")
        self.addCleanup(os.remove, path)
        self.assertEqual(resolve_help_site_url(path), "https://help.example.com/")

    def test_unconfigured_shapes_answer_the_local_default(self):
        for literal in ("''", "'http://YOUR-SITE-DOMAIN'", "'localhost:8080'", "'javascript:alert(1)'"):
            path = _config_with(literal)
            self.addCleanup(os.remove, path)
            with self.subTest(literal=literal):
                self.assertEqual(resolve_help_site_url(path), DEFAULT_HELP_SITE_URL)

    def test_missing_file_answers_the_local_default(self):
        self.assertEqual(resolve_help_site_url("/nonexistent/config.php"), DEFAULT_HELP_SITE_URL)

    def test_never_raises_and_never_answers_empty(self):
        for path in (None, "", "/nonexistent/config.php", os.path.dirname(__file__)):
            with self.subTest(path=path):
                resolved = resolve_help_site_url(path)
                self.assertTrue(resolved.startswith("http://") or resolved.startswith("https://"))


if __name__ == "__main__":
    unittest.main()
