"""Validate bilingual manual copy, screenshots and internal references offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(root=ROOT):
    def read(name):
        return json.loads((root / name).read_text(encoding='utf-8'))
    content = read('content.json')
    packs = {lang: read('lang/' + lang + '.json')['manual'] for lang in ('zh', 'en')}
    docs = read('docs/manifest.json')['docs']
    errors = []

    def keys(node, prefix='manual'):
        if isinstance(node, dict):
            return {path for key, value in node.items() for path in keys(value, prefix + '.' + key)}
        if isinstance(node, list):
            return {path for value in node for path in keys(value, prefix + '.[]')}
        return {prefix}
    for lang, other in [('zh', 'en'), ('en', 'zh')]:
        for key in sorted(keys(packs[other]) - keys(packs[lang])):
            errors.append(lang + ' 缺少键 ' + key)
    seen, hashes = set(), {}
    parts = {p['id'] for p in content['manual_parts']}
    for topic in content['manual_topics']:
        ident = topic['id']
        if topic.get('part') not in parts:
            errors.append(ident + ' 分组未声明')
        if not topic.get('steps'):
            errors.append(ident + ' 没有步骤')
        for lang, pack in packs.items():
            entry = pack['topics'].get(ident, {})
            for field in ('nav', 'title', 'lead'):
                if not entry.get(field, '').strip(): errors.append(f'{lang} {ident}.{field} 为空')
            for step in topic['steps']:
                for field in ('title', 'body'):
                    if not entry.get('steps', {}).get(step['id'], {}).get(field, '').strip():
                        errors.append(f'{lang} {ident}.{step["id"]}.{field} 为空')
        for step in topic['steps']:
            shot = step.get('shot', '')
            path = root / shot
            if not shot.startswith('assets/img/manual/') or '..' in Path(shot).parts or not path.resolve().is_relative_to(root.resolve()):
                errors.append('截图路径无效: ' + shot); continue
            if shot in seen: errors.append('重复引用截图: ' + shot)
            seen.add(shot)
            if not path.is_file() or not path.stat().st_size:
                errors.append('截图缺失或为空: ' + shot); continue
            if not path.name.startswith(ident + '-'):
                errors.append('截图前缀与主题不符: ' + shot)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in hashes: errors.append('截图内容重复: ' + shot + ' / ' + hashes[digest])
            hashes[digest] = shot
        for slug in topic.get('docs', []):
            if slug not in docs or not (root / 'docs' / (slug + '.html')).is_file():
                errors.append('文档不存在: ' + slug)
        for page in topic.get('links', []):
            if page not in content['manual_page_links']: errors.append('页面不存在: ' + page)
    on_disk = {str(p.relative_to(root)) for p in (root / 'assets/img/manual').glob('*') if p.suffix in ('.png', '.jpg', '.jpeg', '.webp')}
    for shot in sorted(on_disk - seen): errors.append('未引用截图: ' + shot)
    for lang, pack in packs.items():
        for field in ('title', 'title_accent', 'lead', 'toc', 'shot_hint', 'docs_label', 'pages_label'):
            if not pack.get(field, '').strip(): errors.append(f'{lang} manual.{field} 为空')
        for part in parts:
            if not pack['parts'].get(part, '').strip(): errors.append(f'{lang} 分组文案缺失: {part}')
    return errors


if __name__ == '__main__':
    failures = validate()
    print('\n'.join('FAIL ' + message for message in failures) if failures else 'OK 手册结构、双语文案、截图与引用一致')
    raise SystemExit(bool(failures))
