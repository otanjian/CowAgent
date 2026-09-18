"""Optional offline-site importer. Network access occurs only when explicitly run.

Usage: python webhelp/tools/build_docs.py --source-url https://docs.example.com
The source must expose the paths registered in docs/manifest.json and #content.
All pages are validated in memory before replacing the existing corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
ALLOWED = {'h2', 'h3', 'h4', 'p', 'ul', 'ol', 'li', 'pre', 'code', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'blockquote', 'strong', 'em', 'b', 'i', 'hr', 'br'}
DROP = {'svg', 'button', 'script', 'style', 'iframe', 'video', 'audio', 'noscript', 'input'}
ALIASES = {'/zh/blog/self-evolution': '/zh/memory/self-evolution'}


@dataclass
class Node:
    tag: str = ''
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node): yield from child.walk()

    def text(self):
        return ''.join(child.text() if isinstance(child, Node) else child for child in self.children)


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID: self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID: self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)

    def content(self):
        return next((node for node in self.root.walk() if node.attrs.get('id') == 'content'), None)

    def title(self):
        for node in self.root.walk():
            if node.attrs.get('data-page-title'): return node.attrs['data-page-title']
        return next((node.text().replace('- CowAgent', '').strip() for node in self.root.walk() if node.tag == 'title'), '')


def fetch(url):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Only HTTP(S) source URLs are supported')
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'RongAI-Help-Importer/1.0'}), timeout=30) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code in (404, 410): return None
            if attempt == 2: raise
        except (URLError, TimeoutError):
            if attempt == 2: raise
        time.sleep(1)


def clean(node, paths, image):
    """Sanitize source markup, keeping only local links and localized images."""
    out = []
    for child in node.children:
        if isinstance(child, str):
            out.append(escape(child)); continue
        tag = child.tag
        if tag in DROP: continue
        if tag == 'img':
            source = child.attrs.get('src', '')
            if source:
                local = image(source)
                out.append(f'<img src="{escape(local, quote=True)}" alt="{escape(child.attrs.get("alt", ""), quote=True)}" loading="lazy">')
            continue
        inner = clean(child, paths, image)
        if tag == 'a':
            href = child.attrs.get('href', '')
            visible = re.sub(r'[\s\u200b-\u200f\ufeff]', '', child.text())
            if not visible and not any(n.tag == 'img' for n in child.walk()): continue
            parsed = urlsplit(href)
            slug = paths.get(parsed.path.rstrip('/'))
            if href.startswith('#'):
                out.append(f'<a href="{escape(href, quote=True)}">{inner}</a>')
            elif slug:
                out.append(f'<a href="doc?p={slug}">{inner}</a>')
            else:
                out.append(inner)
            continue
        if tag == 'span' and child.attrs.get('data-as') == 'p': tag = 'p'
        if tag not in ALLOWED:
            out.append(inner); continue
        attrs = ''
        keep = ['id'] if tag in ('h2', 'h3', 'h4') else ['colspan', 'rowspan'] if tag in ('td', 'th') else []
        for key in keep:
            value = child.attrs.get(key)
            if value: attrs += f' {key}="{escape(value, quote=True)}"'
        if tag in ('p', 'li', 'td', 'th') and not re.sub(r'[\s\u200b\ufeff]', '', child.text()): continue
        out.append(f'<{tag}{attrs}>' + ('' if tag in VOID else inner + f'</{tag}>'))
    return ''.join(out)


def build(source_url, root=ROOT):
    if urlsplit(source_url).scheme not in ('http', 'https') or not urlsplit(source_url).netloc:
        raise ValueError('--source-url must be an absolute HTTP(S) URL')
    old = json.loads((root / 'docs/manifest.json').read_text(encoding='utf-8'))['docs']
    paths = {meta['path']: slug for slug, meta in old.items()}
    for alias, target in ALIASES.items():
        if target in paths: paths[alias] = paths[target]
    queue = deque((meta['path'], 0) for meta in old.values())
    pages, visited = {}, set()
    while queue:
        path, depth = queue.popleft()
        path = ALIASES.get(path, path)
        if path in visited or depth > 4: continue
        visited.add(path)
        if len(visited) > 120: raise ValueError('Source exceeded the 120-page discovery limit')
        raw = fetch(source_url.rstrip('/') + path)
        if raw is None:
            if path in paths: raise ValueError('Registered source page is missing: ' + path)
            continue
        document = Document(raw.decode('utf-8'))
        content = document.content()
        if content is None: raise ValueError('Missing #content: ' + path)
        slug = paths.get(path) or path.removeprefix('/zh/').removesuffix('/index').replace('/', '-')
        if not re.fullmatch('[a-z0-9-]+', slug): raise ValueError('Unsupported document slug: ' + slug)
        if slug in pages: raise ValueError('Duplicate document slug: ' + slug)
        paths[path] = slug
        pages[slug] = (path, document, content)
        for node in content.walk():
            href = urlsplit(node.attrs.get('href', ''))
            if node.tag == 'a' and href.path.startswith('/zh/') and not href.path.endswith('.md'):
                queue.append((href.path.rstrip('/'), depth + 1))
        print('Read', slug)
    corpus, manifest, downloads = {}, {}, {}
    for order, (slug, (path, document, content)) in enumerate(pages.items()):
        def image(src):
            url = urljoin(source_url.rstrip('/') + path, src)
            suffix = Path(urlsplit(url).path).suffix.lower()
            if suffix not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                raise ValueError('Unsupported document image: ' + url)
            name = slug + '-' + hashlib.md5(src.encode()).hexdigest()[:8] + suffix
            relative = 'assets/img/docs/' + name
            if not (root / relative).is_file() and relative not in downloads:
                data = fetch(url)
                if not data: raise ValueError('Image download failed: ' + url)
                # Pillow already belongs to the project's dependencies.
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(data)) as img:
                    img.verify()
                downloads[relative] = data
            return relative
        body = clean(content, paths, image).strip()
        if not body: raise ValueError('Empty document body: ' + slug)
        headings = set(re.findall(r'<h[234]\b[^>]*\bid="([^"]+)"', body))
        anchors = set(re.findall(r'href="#([^"]+)"', body))
        lead = next((' '.join(n.text().split()) for n in content.walk() if (n.tag == 'p' or n.attrs.get('data-as') == 'p') and len(n.text().strip()) >= 8), '')
        en_title = old.get(slug, {}).get('title', {}).get('en', '')
        en_raw = fetch(source_url.rstrip('/') + path.removeprefix('/zh'))
        if en_raw: en_title = Document(en_raw.decode('utf-8')).title() or en_title
        manifest[slug] = {'path': path, 'section': path.removeprefix('/zh/').split('/')[0],
                          'title': {'zh': document.title(), 'en': en_title or document.title()},
                          'lead': lead if len(lead) <= 160 else lead[:157] + '…',
                          'anchor_ok': anchors <= headings, 'order': order}
        corpus[slug] = body + '\n'
    # A failed fetch/parse/validation above leaves the existing corpus untouched.
    for relative, data in downloads.items():
        target = root / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    for slug, body in corpus.items():
        (root / 'docs' / (slug + '.html')).write_text(body, encoding='utf-8')
    (root / 'docs/manifest.json').write_text(json.dumps({'docs': manifest}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {len(corpus)} documents and {len(downloads)} images')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-url', required=True, help='Documentation origin; no network requests occur at runtime')
    build(parser.parse_args().source_url)
