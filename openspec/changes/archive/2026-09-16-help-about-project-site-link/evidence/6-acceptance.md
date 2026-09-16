# 6 验收证据

## 6.1 站点自身可用 + 未配置时回退缺省 + 改地址后下一次读取跟随

站点按仓库文档的方式本机起服务（`php -S 127.0.0.1:8080 -t webhelp`），并且在 `site_url` 仍是占位值时页面照常渲染（对应 spec 的「站点地址缺失不影响站点自身」）：

```bash
$ php -S 127.0.0.1:8080 -t webhelp &    # PHP 8.5.8
$ curl -s -o /dev/null -w "GET / -> %{http_code}\n" http://127.0.0.1:8080/
GET / -> 200
$ curl -s http://127.0.0.1:8080/ | grep -o "<title>[^<]*</title>"
<title>企业级 AI 平台 · 多租户与权限管控 · 容大AI</title>
```

同一次运行里用产品侧的解析入口读真实配置与一份站点副本：

```bash
$ .venv/bin/python /tmp/help_site_probe.py
config path            : /Users/jiantan/ai_assistant/cowagent/webhelp/includes/config.php
declared (unconfigured): 'http://YOUR-SITE-DOMAIN'
resolved (unconfigured): http://localhost:8080/          # 占位值 => 缺省地址
resolved (configured)  : https://help.example.com/webhelp/   # 声明 https 且带子目录
resolved (re-declared) : http://10.0.0.9:9090/manual/    # 同一路径原地改成另一个地址，下一次读取即跟随
resolved (placeholder) : http://localhost:8080/          # 改回占位值 => 回到缺省
php bootstrap untouched: True
```

- 未配置（含占位值）时「帮助与关于」的目标是 `http://localhost:8080/`，正是本机站点。
- 声明合法地址后取值跟随声明值（协议、主机、端口、子目录一致），query 与 fragment 被丢弃并补尾斜杠。
- 同一路径原地修改后下一次解析即跟随，说明没有缓存，无需改代码或重建前端。

## 6.2 版本行仍指向运营方地址 / 读取失败不阻断账号菜单

版本行目标未被本 change 触碰（`chat.html` 未改动）：

```bash
$ grep -n "sidebar-version" -A 2 channel/web/chat.html
475:                        <a id="sidebar-version"
476:                           href="https://www.rsm.global/china/zh-hans"
```

前端断言直接覆盖两个场景（`tests/test_sidebar_account_frontend.cjs`，均已通过）：

- `帮助与关于 opens the project site address, not the brand version row`：改掉 `#sidebar-version.href` 后重开，帮助入口仍打开站点地址。
- `a failed public brand read leaves the help target usable`：`/api/branding/public` 抛错时帮助入口仍打开缺省地址，账号菜单流程不中断。

服务端在解析失败时不改变响应形状：`test_public_endpoint_survives_a_failing_help_site_read` 与 `test_failing_brand_read_still_carries_the_help_site_target` 断言两种失败下 `help_url` 都是缺省地址，且既有字段集合不减少。

## 6.3 规范校验

```bash
$ openspec validate help-about-project-site-link --strict
Change 'help-about-project-site-link' is valid
```

delta 中引用的 requirement 名称与 `openspec/specs/` 既有条目一致：`console-entry-consistency` 为对「关于产品和更新日志含义一致」的 MODIFIED（含原有全部场景），`product-manual-site` 为 ADDED 新 requirement。
