# 3 公开品牌投影追加 `help_url`

`channel/web/web_channel.py`：`BrandingPublicHandler.GET` 在成功路径与异常回退路径上都追加同一个 `help_url`，取值来自 `_help_site_url()`；后者自身吞掉解析异常并返回缺省地址，因此回退分支不会因为解析失败再抛一次。

```startLine:4917:4931:channel/web/web_channel.py
        except Exception as e:
            logger.exception(f"[BrandingPublicHandler] failed: {e}")
            # Fall back to the built-in default so login/nav never breaks.
            payload = {
                "enabled": False,
                "revision": 0,
                "brand_name": "容大AI",
                "logo_description": "工作台",
                "logo_url": "/assets/rongda-ai-mark.svg",
                "favicon_url": "/assets/favicon.ico",
            }
        # The 「帮助与关于」 target: the site declares its own address, so a read
        # failure lands on the local-development default instead of a dead link.
        payload["help_url"] = _help_site_url()
        return json.dumps(payload, ensure_ascii=False)
```

原字段集合 `enabled`、`revision`、`brand_name`、`logo_description`、`logo_url`、`favicon_url` 与取值均未变，本次是纯追加。

单测（`tests/test_branding.py`）：

- `test_public_endpoint_returns_minimal_payload`：公开投影键集合为原六项 + `help_url`。
- `test_public_endpoint_carries_the_help_site_target`：声明了合法地址时 `help_url` 跟随声明值。
- `test_public_endpoint_survives_a_failing_help_site_read`、`test_failing_brand_read_still_carries_the_help_site_target`：解析失败与品牌读取失败两种情况仍返回缺省地址，响应形状一致。
