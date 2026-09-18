"""Help is hosted on the current service; public config only controls commands."""
import json
import tempfile
import unittest
from pathlib import Path

from channel.web.help_site import (
    DEFAULT_HELP_SITE_URL, normalize_site_url, read_declared_site_url,
    resolve_help_site_url, site_config_path,
)


class HelpSiteUrlTests(unittest.TestCase):
    def test_help_always_points_to_current_service(self):
        self.assertEqual(DEFAULT_HELP_SITE_URL, '/help/')
        self.assertEqual(resolve_help_site_url(), '/help/')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'site_url': 'https://example.com'}))
            self.assertEqual(read_declared_site_url(str(path)), 'https://example.com')
            self.assertEqual(resolve_help_site_url(str(path)), '/help/')

    def test_public_config_is_json(self):
        self.assertTrue(Path(site_config_path()).is_file())
        self.assertEqual(Path(site_config_path()).suffix, '.json')
        self.assertEqual(read_declared_site_url(), '')

    def test_config_failure_does_not_break_help(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            for content in ['broken', '[]', 'null', '{"site_url":12}', '{"brand":{}}']:
                path.write_text(content)
                self.assertEqual(read_declared_site_url(str(path)), '')
                self.assertEqual(resolve_help_site_url(str(path)), '/help/')
        self.assertEqual(read_declared_site_url('/missing/config.json'), '')

    def test_command_base_normalization(self):
        for raw, expected in [
            ('https://example.com', 'https://example.com/'),
            (' https://example.com/webhelp ', 'https://example.com/webhelp/'),
            ('https://user:pass@example.com:8443/a?x=1#c', 'https://example.com:8443/a/'),
            ('http://[::1]:8080', 'http://[::1]:8080/'),
        ]:
            self.assertEqual(normalize_site_url(raw), expected)
        for raw in ['', None, 'localhost:8080', 'javascript:alert(1)', 'ftp://example.com',
                    'http://', 'http://example.com:bad', 'http://YOUR-SITE-DOMAIN']:
            self.assertEqual(normalize_site_url(raw), '')
