# 2 站点地址解析模块 `channel/web/help_site.py`

新增模块只对外暴露三件事：配置路径、`site_url` 字面量抽取、最终可点击地址解析。

```startLine:1:1:channel/web/help_site.py
DEFAULT_HELP_SITE_URL = "http://localhost:8080/"
```

| 关注点 | 实现 |
| --- | --- |
| 配置路径 | `site_config_path()` 由模块位置推导仓库根，拼 `webhelp/includes/config.php` |
| 字面量抽取 | `read_declared_site_url()` 用正则取 `'site_url' => '...'`；文件缺失或键缺失返回空串 |
| 未配置判定 | 空值、缺失、主机命中 `your-site-domain`（大小写无关）→ 视为未配置 |
| 校验与归一化 | 仅收 http/https 绝对地址；必须有主机名；丢弃 query、fragment 与凭据；补尾斜杠并保留子目录路径 |
| 解析入口 | `resolve_help_site_url()` 任何异常或不可用取值一律返回缺省地址，且不抛异常 |

归一化示例（`tests/test_help_site_url.py` 断言）：

| 输入 | 结果 |
| --- | --- |
| `https://help.example.com/webhelp/?utm=1#top` | `https://help.example.com/webhelp/` |
| `http://10.0.0.9:9090/manual` | `http://10.0.0.9:9090/manual/` |
| `http://YOUR-SITE-DOMAIN` | 缺省 `http://localhost:8080/` |
| `http://` / `localhost:8080` / `javascript:alert(1)` / `not a url` | 缺省 `http://localhost:8080/` |

单测：`tests/test_help_site_url.py`（归一化、抽取、解析三组），与 `tests/test_branding.py` 合计 61 条通过：

```bash
$ .venv/bin/python -m unittest tests.test_branding tests.test_help_site_url
Ran 61 tests in 2.757s

OK
```
