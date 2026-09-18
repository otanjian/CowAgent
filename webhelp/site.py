"""Request-local help views; HTML templates and JSON content have no PHP dependency."""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from functools import lru_cache
from html import escape, unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import web

PAGES = frozenset({'index', 'features', 'enterprise', 'quickstart', 'manual', 'architecture', 'about', 'doc'})


def resource_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'webhelp'
    return Path(__file__).resolve().parent


def read_json(relative: str):
    return json.loads((resource_root() / relative).read_text(encoding='utf-8'))


def lookup(value, key, default=None):
    for part in key.split('.'):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def e(value):
    return escape(str(value), quote=True)


@lru_cache(maxsize=32)
def _template(root: str, name: str):
    path = Path(root) / 'templates' / (name + '.html')
    return web.template.Template(path.read_text(encoding='utf-8'), filename=str(path))


class HelpView:
    """Each request owns its language, query, metadata and content helpers."""

    def __init__(self, page='index', lang='zh', query=None, base='/help', origin=''):
        self.page = page
        self.lang = lang if lang in ('zh', 'en') else 'zh'
        self.query = dict(query or {})
        self.base = base.rstrip('/')
        self.origin = origin.rstrip('/')
        self.config = read_json('config.json')
        self.contents = read_json('content.json')
        self.translations = read_json('lang/' + self.lang + '.json')
        self.defaults = self.translations if self.lang == 'zh' else read_json('lang/zh.json')
        self.icons = read_json('icons.json')
        docs = read_json('docs/manifest.json')['docs']
        self.docs = dict(sorted(docs.items(), key=lambda entry: entry[1].get('order', 0)))
        self.slug = self.query.get('p', '').strip()
        self.ok = self.page != 'doc' or self.doc_exists(self.slug)
        self.brand = self.cfg('brand', {})
        title_key = self.cfg('page_titles', {}).get(page, 'meta.title_home')
        self.title = self.t(title_key) + ' · ' + self.brand['name']
        lead_keys = {'features': 'capabilities.subtitle', 'enterprise': 'enterprise.banner_desc',
                     'quickstart': 'quickstart.subtitle', 'manual': 'manual.lead',
                     'architecture': 'architecture.subtitle', 'about': 'about.lead'}
        self.description = self.t(lead_keys.get(page, 'meta.description'))
        if page == 'doc':
            self.title = ((self.doc_title(self.slug) + ' · ' + self.t('doc.breadcrumb'))
                          if self.ok else self.t('doc.not_found')) + ' · ' + self.brand['name']
            self.description = self.doc_lead(self.slug) if self.ok else self.t('doc.not_found_desc')

    def render(self, name=None):
        return str(_template(str(resource_root()), name or self.page)(self))

    def cfg(self, key, default=None):
        return lookup(self.config, key, default)

    def content(self, key, default=None):
        return self.contents.get(key, [] if default is None else default)

    def translated(self, key, default=None):
        return lookup(self.translations, key, lookup(self.defaults, key, default))

    def t(self, key, fallback=''):
        value = self.translated(key)
        return str(value) if value and not isinstance(value, (list, dict)) else (fallback or key)

    def t_opt(self, key):
        value = self.translated(key)
        return str(value) if value and not isinstance(value, (list, dict)) else ''

    def t_list(self, key):
        value = self.translated(key, [])
        return list(value.values()) if isinstance(value, dict) else value if isinstance(value, list) else []

    def t_map(self, key):
        value = self.translated(key, {})
        return value if isinstance(value, dict) else {}

    def url(self, page='index', params=None):
        parsed = urlsplit(page)
        name = parsed.path.removesuffix('.php').strip('/')
        name = '' if name == 'index' else name
        values = dict(parse_qsl(parsed.query))
        values.update(params or {})
        if self.lang != 'zh':
            values.setdefault('lang', self.lang)
        path = self.base + '/' + name
        return path + ('?' + urlencode(values) if values else '') + ('#' + parsed.fragment if parsed.fragment else '')

    def asset(self, path):
        return self.base + '/' + path.lstrip('/') + '?v=' + str(self.cfg('version', '1'))

    def lang_url(self, lang):
        return self.url(self.page, {**self.query, 'lang': lang})

    def canonical(self):
        params = {k: v for k, v in self.query.items() if k != 'lang'}
        path = self.base + '/' + ('' if self.page == 'index' else self.page)
        return path + ('?' + urlencode(params) if params else '')

    def demo_image(self):
        return self.asset(self.cfg('demo_image', ''))

    def icon(self, name, css='icon'):
        return (f'<svg class="{e(css)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
                'aria-hidden="true" focusable="false">' + self.icons.get(name, self.icons['sparkles']) + '</svg>')

    def tag(self, label, modifier=''):
        return f'<span class="tag{e(" tag--" + modifier) if modifier else ""}">{e(label)}</span>'

    def section_heading(self, title, subtitle='', left=False):
        result = f'<h2 class="section-title{" section-title--left" if left else ""}">{e(self.t(title))}</h2>'
        if subtitle:
            result += f'<p class="section-subtitle{" section-subtitle--left" if left else ""}">{e(self.t(subtitle))}</p>'
        return result

    def page_hero(self, title, lead, opts=None):
        opts = opts or {}
        extra = opts.get('extra', '')
        result = f'<header class="page-hero{" page-hero--rich" if extra else ""}"><div class="section-container">'
        breadcrumb = opts.get('breadcrumb', title)
        if breadcrumb:
            result += (f'<nav class="breadcrumb" aria-label="breadcrumb"><a href="{e(self.url())}">{e(self.t("nav.home"))}</a>'
                       f'<span aria-hidden="true">/</span><span>{e(self.t(breadcrumb))}</span></nav>')
        if opts.get('eyebrow'):
            result += '<span class="perm-eyebrow">' + self.icon(opts.get('eyebrow_icon', 'sparkles')) + e(self.t(opts['eyebrow'])) + '</span>'
        accent = self.t(opts['title_accent']) if opts.get('title_accent') else ''
        lines = []
        for line in self.t(title).split('\n'):
            lines.append(e(line).replace(e(accent), '<span class="hero-accent">' + e(accent) + '</span>', 1) if accent else e(line))
        return result + '<h1>' + '<br>'.join(lines) + '</h1><p>' + e(self.t(lead)) + '</p>' + extra + '</div></header>'

    def capability_card(self, item):
        key = 'capabilities.items.' + item['id']
        inner = ('<span class="feature-icon">' + self.icon(item['icon']) + '</span>'
                 + '<h3 class="feature-title">' + e(self.t(key + '.title')) + '</h3>'
                 + '<p class="feature-desc">' + e(self.t(key + '.desc')) + '</p>')
        if not item.get('doc'):
            return '<article class="feature-card reveal">' + inner + '</article>'
        return (f'<a class="feature-card feature-card--link reveal" href="{e(self.doc_url(item["doc"]))}">' + inner
                + '<span class="feature-more">' + e(self.t('common.read_doc')) + self.icon('arrow') + '</span></a>')

    def enterprise_card(self, item):
        key = 'enterprise.items.' + item['id']
        points = self.t_list(key + '.points')
        items = ''.join('<li>' + self.icon('check') + '<span>' + e(point) + '</span></li>' for point in points)
        return ('<article class="feature-card enterprise-card reveal"><span class="feature-icon">' + self.icon(item['icon'])
                + '</span><h3 class="feature-title">' + e(self.t(key + '.title')) + '</h3><p class="feature-desc">'
                + e(self.t(key + '.desc')) + '</p>' + ('<ul class="feature-points">' + items + '</ul>' if points else '') + '</article>')

    def copy_button(self):
        return (f'<button class="copy-btn" type="button" data-copy-label="{e(self.t("common.copy"))}" '
                f'data-copy-done="{e(self.t("common.copied"))}">{e(self.t("common.copy"))}</button>')

    def code_block(self, label, code):
        return ('<div class="code-block code-block--wide"><div class="code-header">'
                '<span class="code-dot red"></span><span class="code-dot yellow"></span><span class="code-dot green"></span>'
                f'<span class="code-label">{e(label)}</span>' + self.copy_button()
                + f'</div><pre class="code-content is-active"><code>{e(code)}</code></pre></div>')

    def deploy_block(self):
        tabs, panels = [], []
        declared = self.cfg('site_url', '').strip().rstrip('/')
        from channel.web.help_site import normalize_site_url
        base = normalize_site_url(declared).rstrip('/') or self.origin + self.base
        for index, item in enumerate(self.content('deployments')):
            ident = item['id']
            active = ' is-active' if index == 0 else ''
            command = self.cfg('deployments', {}).get(ident, '').replace('{site_url}', base)
            prompt = '>' if ident == 'win' else '$'
            tabs.append(f'<button class="code-tab{active}" type="button" data-code-tab="{e(ident)}">{e(self.t("quickstart.tab_" + ident))}</button>')
            code = '\n'.join(f'<span class="code-prompt">{e(prompt)}</span> {e(line)}' for line in command.split('\n'))
            panels.append(f'<pre class="code-content{active}" data-code-panel="{e(ident)}"><code><span class="code-comment">'
                          + e('# ' + self.t('quickstart.comment_' + ident)) + '</span>\n' + code + '</code></pre>')
        return ('<div class="code-block"><div class="code-header"><span class="code-dot red"></span>'
                '<span class="code-dot yellow"></span><span class="code-dot green"></span><div class="code-tabs">'
                + ''.join(tabs) + '</div>' + self.copy_button() + '</div>' + ''.join(panels) + '</div>')

    def perm_stats(self):
        return '<div class="perm-stats">' + ''.join(f'<div class="perm-stat reveal"><strong>{e(stat.get("value", ""))}</strong><span>{e(stat.get("label", ""))}</span></div>' for stat in self.t_list('perm.stats')) + '</div>'

    def perm_tiers(self):
        out = []
        for item in self.content('perm_tiers'):
            ident = item['id']; key = 'perm.tiers.' + ident
            actions = ''.join('<li>' + e(a) + '</li>' for a in self.t_list(key + '.actions'))
            out.append(f'<article class="perm-tier perm-tier--{e(ident)} reveal"><span class="perm-tier-badge">'
                       + e(self.t(key + '.name')) + '</span><span class="perm-tier-icon">' + self.icon(item['icon'])
                       + '</span><h3 class="perm-tier-actor">' + e(self.t(key + '.actor')) + '</h3><p class="perm-tier-actordesc">'
                       + e(self.t(key + '.actor_desc')) + '</p><div class="perm-tier-field"><span class="perm-tier-label">'
                       + e(self.t('perm.labels.scope')) + '</span><p class="perm-tier-scope">' + e(self.t(key + '.scope'))
                       + '</p></div><div class="perm-tier-field"><span class="perm-tier-label">' + e(self.t('perm.labels.actions'))
                       + '</span><ul class="perm-tier-actions">' + actions + '</ul></div></article>')
        return '<div class="perm-tiers">' + ''.join(out) + '</div>'

    def perm_flow_panel(self):
        graph = self.t_map('perm.flow_graph'); desc = graph.get('actor_desc', {})
        icons = {'platform': 'tenant', 'tenant': 'rbac', 'user': 'access'}
        def node(tier):
            return (f'<div class="perm-node perm-node--{tier}"><span class="perm-node-icon">' + self.icon(icons[tier])
                    + '</span><span class="perm-node-name">' + e(self.t('perm.tiers.' + tier + '.actor'))
                    + '</span><span class="perm-node-desc">' + e(desc.get(tier, '')) + '</span></div>')
        def arrow(direction, key):
            return f'<div class="perm-arrow perm-arrow--{direction}"><span class="perm-arrow-label">{e(graph.get(key, ""))}</span><span class="perm-arrow-line" aria-hidden="true"></span></div>'
        return ('<div class="perm-flow reveal"><div class="perm-flow-grid">' + node('platform') + arrow('right', 'grant_platform')
                + node('tenant') + arrow('down', 'grant_tenant') + node('user') + '</div><div class="perm-return">'
                + '<span class="perm-return-text">' + e(graph.get('share_out', '')) + '</span><span class="perm-return-arrow" aria-hidden="true"></span>'
                + '<span class="perm-return-text">' + e(graph.get('share_in', '')) + '</span></div></div>')

    def perm_roles(self):
        out = []
        for item in self.content('perm_roles'):
            ident = item['id']; key = 'perm.roles.' + ident
            groups = ''.join('<div class="perm-role-group"><h4 class="perm-role-grouptitle">' + e(group.get('title', ''))
                             + '</h4><ul class="perm-role-list">' + ''.join('<li>' + e(entry) + '</li>' for entry in group.get('items', []))
                             + '</ul></div>' for group in self.t_list(key + '.groups'))
            out.append(f'<article class="perm-role perm-role--{e(ident)} reveal"><header class="perm-role-head"><span class="perm-role-icon">'
                       + self.icon(item['icon']) + '</span><div class="perm-role-id"><h3 class="perm-role-name">' + e(self.t(key + '.name'))
                       + '</h3><p class="perm-role-desc">' + e(self.t(key + '.desc')) + '</p></div><span class="perm-role-tag">'
                       + e(self.t(key + '.tag')) + '</span></header><div class="perm-role-groups">' + groups + '</div></article>')
        return '<div class="perm-roles">' + ''.join(out) + '</div>'

    def doc_exists(self, slug):
        return bool(re.fullmatch(r'[a-z0-9-]+', slug) and slug in self.docs and (resource_root() / 'docs' / (slug + '.html')).is_file())

    def doc_title(self, slug):
        titles = self.docs.get(slug, {}).get('title', {})
        return titles.get(self.lang, titles.get('zh', slug))

    def doc_lead(self, slug):
        return self.docs.get(slug, {}).get('lead', '')

    def doc_url(self, slug):
        return self.url('doc', {'p': slug})

    def doc_grouped(self):
        groups = {}
        for slug, meta in self.docs.items():
            groups.setdefault(meta.get('section', 'intro'), {})[slug] = meta
        return groups

    def doc_neighbor(self, slug, offset):
        ids = list(self.docs)
        if slug not in ids: return ''
        index = ids.index(slug) + offset
        return ids[index] if 0 <= index < len(ids) else ''

    def doc_body(self, slug):
        if not self.doc_exists(slug): return ''
        body = (resource_root() / 'docs' / (slug + '.html')).read_text(encoding='utf-8')
        body = re.sub(r'src="(assets/[^\"]*)"', lambda m: 'src="' + e(self.base + '/' + unescape(m[1])) + '"', body)
        body = re.sub(r'href="(doc(?:\.php)?\?[^\"]*)"', lambda m: 'href="' + e(self.url(unescape(m[1]))) + '"', body)
        if body.count('<table>') == body.count('</table>'):
            body = body.replace('<table>', '<div class="doc-table-wrap"><table>').replace('</table>', '</table></div>')
        return body

    def doc_toc(self, body):
        return [{'level': int(level), 'id': unescape(ident), 'text': unescape(re.sub(r'<[^>]*>', '', text)).strip()}
                for level, ident, text in re.findall(r'<h([23])\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</h[23]>', body, re.S)]

    def manual_nav(self):
        out = '<p class="doc-aside-title">' + e(self.t('manual.toc')) + '</p>'
        for part in self.content('manual_parts'):
            topics = [(n, topic) for n, topic in enumerate(self.content('manual_topics')) if topic.get('part', 'conversation') == part['id']]
            if not topics: continue
            out += '<p class="doc-aside-section">' + e(self.t('manual.parts.' + part['id'])) + '</p><ul class="doc-aside-list">'
            for n, topic in topics:
                out += f'<li><a class="doc-aside-link" href="#manual-{e(topic["id"])}">{n + 1}. {e(self.t("manual.topics." + topic["id"] + ".nav"))}</a></li>'
            out += '</ul>'
        return out

    def manual_step(self, topic, number, step):
        key = 'manual.topics.' + topic + '.steps.' + step['id']
        title = self.t_opt(key + '.title').strip()
        body = self.t_opt(key + '.body').strip()
        out = (f'<figure class="manual-step" id="manual-{e(topic)}-{e(step["id"])}"><figcaption class="manual-step-head">'
               f'<span class="manual-step-no" aria-hidden="true">{number}</span><span class="manual-step-copy">'
               f'<span class="manual-step-title">{e(title)}</span><span class="manual-step-body">{e(body)}</span></span></figcaption>')
        if step.get('shot'):
            path = e(self.asset(step['shot']))
            out += f'<a class="manual-shot" href="{path}" target="_blank" rel="noopener"><img class="manual-shot-img" src="{path}" alt="{e(title)}" loading="lazy" decoding="async"></a>'
        return out + '</figure>'

    def manual_refs(self, item):
        groups = [('manual.docs_label', item.get('docs', [])), ('manual.pages_label', item.get('links', []))]
        result = ''
        for label, refs in groups:
            links = ''
            for ref in refs:
                if label == 'manual.docs_label':
                    if not self.doc_exists(ref): continue
                    href, title = self.doc_url(ref), self.doc_title(ref)
                else:
                    pages = self.content('manual_page_links', {})
                    if ref not in pages: continue
                    href, title = self.url(pages[ref]), self.t('manual.page_links.' + ref, ref)
                links += f'<a class="manual-ref" href="{e(href)}">{e(title)}' + self.icon('arrow') + '</a>'
            if links:
                result += '<div class="manual-refs"><span class="manual-refs-label">' + e(self.t(label)) + '</span><div class="manual-refs-list">' + links + '</div></div>'
        return result

    def year(self):
        return date.today().year

    def escape(self, value):
        return e(value)
