"""Exercise the integrated route, rendered navigation and public asset boundary."""
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from channel.web.web_channel import build_web_app
from channel.web.route_registry import check_route_coverage
from webhelp.site import HelpView, PAGES, resource_root
from webhelp.tools.check_manual import validate
from webhelp.tools.build_docs import Document, clean


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        for name in ['src', 'href']:
            if name in attrs: self.urls.append(attrs[name])


class HelpSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = build_web_app()

    def test_all_pages_and_documents_in_both_languages(self):
        docs = HelpView().docs
        checked = set()
        for lang in ['zh', 'en']:
            paths = ['/help/' + (page if page != 'index' else '') + '?lang=' + lang for page in PAGES if page != 'doc']
            paths += ['/help/doc?p=' + slug + '&lang=' + lang for slug in docs]
            for path in paths:
                with self.subTest(path=path):
                    response = self.app.request(path)
                    self.assertEqual(response.status, '200 OK')
                    html = response.data.decode()
                    self.assertIn('<html lang="' + ('zh-CN' if lang == 'zh' else 'en') + '">', html)
                    self.assertNotIn('<?php', html)
                    self.assertNotIn('YOUR-SITE-DOMAIN', html)
                    self.assertNotRegex(html, r'(?:href|src)="[^\"]*\.php')
                    links = Links(); links.feed(html)
                    for target in links.urls:
                        if target.startswith('#'):
                            self.assertIn(target[1:], links.ids)
                            continue
                        self.assertTrue(target.startswith('/help/'), target)
                        if target in checked: continue
                        checked.add(target)
                        linked = self.app.request(target)
                        self.assertEqual(linked.status, '200 OK', target)
        self.assertGreater(len(docs), 20)

    def test_language_cookie_and_switch_keep_document_slug(self):
        response = self.app.request('/help/doc?p=memory&lang=en')
        self.assertIn('webhelp_lang=en', response.headers['Set-Cookie'])
        self.assertIn('Path=/help', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('/help/doc?p=memory&amp;lang=zh', response.data.decode())
        response = self.app.request('/help/manual', headers={'Cookie': 'webhelp_lang=en'})
        self.assertIn('<html lang="en">', response.data.decode())
        response = self.app.request('/help/?lang=INVALID', headers={'Cookie': 'webhelp_lang=INVALID'})
        self.assertIn('<html lang="zh-CN">', response.data.decode())

    def test_redirects_preserve_query(self):
        for source, target in [('/help?lang=en', '/help/?lang=en'),
                               ('/help/index.php?lang=en', '/help/?lang=en'),
                               ('/help/doc.php?p=memory&lang=en', '/help/doc?p=memory&lang=en')]:
            response = self.app.request(source)
            self.assertEqual(response.status, '301 Moved Permanently')
            self.assertEqual(response.headers['Location'], target)

    def test_unknown_pages_and_private_files_are_not_served(self):
        for path in ['config.json', 'content.json', 'templates/index.html', 'site.py', 'docs/manifest.json',
                     'tools/build_docs.py', 'missing', 'doc?p=../config', 'doc?p=missing',
                     'assets/../config.json', 'assets/%2e%2e/config.json', 'assets/img/../../site.py',
                     'assets/.secret', 'assets/no-file.js']:
            self.assertEqual(self.app.request('/help/' + path).status, '404 Not Found', path)
        self.assertEqual(self.app.request('/help/', method='POST').status, '405 Method Not Allowed')

    def test_symlink_escape_is_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'site'; (root / 'assets').mkdir(parents=True)
            outside = Path(folder) / 'secret.js'; outside.write_text('secret')
            (root / 'assets/leak.js').symlink_to(outside)
            with patch('channel.web.help_site.resource_root', return_value=root):
                response = self.app.request('/help/assets/leak.js')
            self.assertEqual(response.status, '404 Not Found')

    def test_asset_mime_cache_and_ranges(self):
        path = '/help/assets/css/style.css'
        response = self.app.request(path)
        self.assertIn('text/css', response.headers['Content-Type'])
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        cached = self.app.request(path, headers={'If-None-Match': response.headers['ETag']})
        self.assertEqual(cached.status, '304 Not Modified')
        partial = self.app.request(path, headers={'Range': 'bytes=2-10'})
        self.assertEqual(partial.status, '206 Partial Content')
        self.assertEqual(partial.data, response.data[2:11])
        suffix = self.app.request(path, headers={'Range': 'bytes=-7'})
        self.assertEqual(suffix.data, response.data[-7:])
        for value in ['bytes=-0', 'bytes=9999999-', 'bytes=9-2', 'bytes=abc', 'bytes=0-1,4-5']:
            self.assertEqual(self.app.request(path, headers={'Range': value}).status, '416 Range Not Satisfiable')

    def test_command_urls_use_current_backend(self):
        response = self.app.request('/help/quickstart', host='help.example.test:9899')
        self.assertIn('http://help.example.test:9899/help/assets/deploy/run.sh', response.data.decode())

    def test_escaping_and_route_coverage(self):
        view = HelpView('manual')
        view.translations['manual']['title'] = '<script>alert(1)</script>'
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', view.render())
        self.assertNotIn('<script>alert(1)</script>', view.render())
        import channel.web.web_channel as module
        self.assertEqual(check_route_coverage(vars(module)), [])

    def test_manual_assets_and_translations(self):
        self.assertEqual(validate(), [])

    def test_document_importer_strips_active_and_external_content(self):
        doc = Document('<div id="content"><h2 id="x">Title</h2><script>alert(1)</script>'
                       '<p><a href="https://other.test">External</a> <a href="/zh/memory">Memory</a>'
                       '<a href="#x">Jump</a><img src="https://images.test/a.png" onerror="alert(1)"></p></div>')
        result = clean(doc.content(), {'/zh/memory': 'memory'}, lambda source: 'assets/img/docs/a.png')
        self.assertNotIn('script', result)
        self.assertNotIn('onerror', result)
        self.assertNotIn('https://', result)
        self.assertIn('href="doc?p=memory"', result)
        self.assertIn('href="#x"', result)
