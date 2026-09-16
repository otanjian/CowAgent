# 4 前端接线（`console.js` / `chat.html`）

## 4.1 模块级目标变量

```startLine:76:77:channel/web/static/js/console.js
const ACCOUNT_ABOUT_FALLBACK_URL = 'http://localhost:8080/';
let _accountAboutUrl = ACCOUNT_ABOUT_FALLBACK_URL;
```

## 4.2 从公开品牌快照取值，非法/缺失保留既有值

`fetchPublicBrand()` 在品牌应用流程里追加一行，取值与品牌字段相互独立：即使载荷没有可用的品牌名（`brandLoaded` 为假、`brandState` 区块整体跳过），这一行仍然执行；非法或缺失值由 `_applyAccountAboutUrl` 丢弃，不打断后续 `applyBrandToDocument()`。

```startLine:5006:5012:channel/web/static/js/console.js
        // The 「帮助与关于」 target travels in this projection. It is an
        // instance-level fact independent of the brand record, so it is adopted
        // even when the payload carries no usable brand name; an unusable or
        // absent value keeps the local-development default.
        if (data) _applyAccountAboutUrl(data.help_url);
        applyBrandToDocument();
        applyBrandToAgentAvatars();
    }).catch(() => { /* keep last known brand; never break the console */ });
```

前端与服务端同规则校验：只接受 http/https 绝对地址，`javascript:` 这类协议与相对值一律丢弃，避免相对值静默变成同源页面。

## 4.3 `openAccountAbout()` 不再读版本行

```startLine:19517:19543:channel/web/static/js/console.js
function _externalUrlOrEmpty(value) {
    const raw = String(value == null ? '' : value).trim();
    if (!raw) return '';
    try {
        const url = new URL(raw);
        return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : '';
    } catch (_) {
        return '';
    }
}

function _applyAccountAboutUrl(value) {
    const url = _externalUrlOrEmpty(value);
    if (url) _accountAboutUrl = url;
}

function openAccountAbout() {
    closeAccountMenu();
    if (_accountAboutUrl) window.open(_accountAboutUrl, '_blank', 'noopener');
    _setAccountPanel(null);
}
```

「先关闭账号菜单 → 新标签打开 → 复位面板」的顺序未变。

## 4.4 `chat.html` 未改动

- `#account-menu-about` 仍是同一按钮、同一 `onclick="openAccountAbout()"`、同一文案 `帮助与关于`。
- `#sidebar-version` 的 `href` 仍是 `https://www.rsm.global/china/zh-hans`。
- 账号菜单区域顺序未变（`tests/test_account_menu_no_personal_resources.cjs` 断言 `identity → settings → about → logout → sidebar-version`）。
