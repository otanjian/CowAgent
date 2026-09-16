# 1 基线（改动前）

## 1.1 「帮助与关于」与版本行共用同一目标

改动前 `channel/web/static/js/console.js`（`HEAD`）：

```js
function openAccountAbout() {
    closeAccountMenu();
    // Reuse the version link row: navigate to the release changelog, which is
    // the existing "original update log" entry.
    const version = document.getElementById('sidebar-version');
    if (version && version.href) window.open(version.href, '_blank', 'noopener');
    _setAccountPanel(null);
}
```

`channel/web/chat.html`（`HEAD`）两处入口：

- `#account-menu-about`（`onclick="openAccountAbout()"`，文案 `帮助与关于`）
- `#sidebar-version` → `href="https://www.rsm.global/china/zh-hans"`

因此「帮助与关于」实际打开的是版本行所属的运营方对外地址，两个入口只有一个目标。

## 1.2 站点配置仍是脚手架占位值

```bash
$ .venv/bin/python -c "from channel.web.help_site import *; ..."
config path            : /Users/jiantan/ai_assistant/cowagent/webhelp/includes/config.php
declared (unconfigured): 'http://YOUR-SITE-DOMAIN'
```

`webhelp/includes/config.php` 的 `site_url` 仍是占位值 `http://YOUR-SITE-DOMAIN`，因此改动后在未配置的生产环境里必须回退到 `http://localhost:8080/`，而不是把占位主机渲染成可点击目标。

## 1.3 站点自身对 `site_url` 的既有消费不变

`webhelp/includes/bootstrap.php` 依旧按原样取值：

```php
function site_url(): string
{
    return rtrim((string) cfg('site_url', ''), '/');
}

function resolve_site_url(string $command): string
{
    return str_replace('{site_url}', site_url(), $command);
}
```

本 change 只**读**该声明，不改站点渲染：`webhelp/` 下所有文件都未被本次改动触及（`git status` 中的 `webhelp/` 修改属于站点自身在途工作，与本 change 无关）。站点在占位值下的可用性由 6.1 的实机验收覆盖。

## 1.4 本 change 不新增路由

`/api/branding/public` 既有行未变（`scripts/route-baseline.txt:24`：`GET public`），本 change 只在该响应上追加字段。`scripts/route-baseline.txt` 的工作区改动来自在途 change `unify-console-by-data-scope`，与本 change 无关。
