# 证据 1-2：校验结果与状态界定

对应任务：2.5、5.3。

## 1. 替换完整性

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 源文件中残留旧 URL 目标 | `rg --hidden -o 'https?://[A-Za-z0-9.-]*cowagent\.ai' . -g '!.git/**'` | 剩余仅 `run.log` 291 处与 `desktop/dist/**` 14 处，**源码 0 处** |
| 新增目标地址计数 | `git diff -U0` 删除行/新增行分别计数 | 359 / 359，一致 |
| 语言后缀派生 | `rg -n 'rsm\.global'` 中的 `currentLang === 'zh'` / `getLang() === 'zh'` / `lang === 'zh'` 三元表达式 | 三处派生分支（`console.js:1157`、`NavRail.tsx:55–56`、`UpdateBanner.tsx:14`）三元两侧现为同一地址 |
| 运行期拼接后缀 | `rg -n 'rsm\.global'` 后筛选拼接位置 | 6 处仍追加后缀，**已决定保持现状、不在本 change 范围**（见 1.1 节） |

### 1.1 运行期拼接：已决定不处理（范围外）

字面量已拉平，但以下 6 处在运行期仍把后缀拼到该地址之后：

| 位置 | 拼接 | 运行期实际目标 |
| --- | --- | --- |
| `desktop/src/main/updater.ts:52` | `+ (isLegacyWindows() ? 'legacy/' : '')` | `.../china/zh-hans/legacy/` |
| `desktop/src/main/updater.ts:55` | `` `${FEED_BASE}?lang=zh` `` | `.../china/zh-hans?lang=zh` |
| `desktop/src/renderer/src/components/UpdateBanner.tsx:15` | `` `${base}/releases/v${version}` `` | `.../china/zh-hans/releases/v<版本>` |
| `webhelp/tools/build-docs.php:279`、`:508` | `'…zh-hans' . $path` | `.../china/zh-hans/<页面路径>` |
| `webhelp/tools/build-docs.php:532` | `'…zh-hans' . substr($path, 3)` | `.../china/zh-hans/<英文页面路径>` |

**决策**：保持现状（已确认「拼接的不管」）。`upstream-link-retargeting` 规格只约束配置与源码中的目标取值，不声明运行期拼接约束。补齐见任务 3.6 的决策记录。

## 2. 改动后语法与格式校验

| 文件类型 | 文件 | 结果 |
| --- | --- | --- |
| JSON | `docs/docs.json`、`desktop/package.json` | 解析通过 |
| Python | `channel/feishu/lark_install.py`、`cli/commands/skill.py`、`cli/utils.py`、`models/linkai/link_ai_bot.py`、`plugins/cow_cli/cow_cli.py` | `py_compile` 通过 |
| YAML | `.github/ISSUE_TEMPLATE/config.yml`、`.github/workflows/release.yml`、`release-win7.yml` | 未做解析校验（环境无 `yaml` 模块）；逐行 diff 已复核，仅 `url:` 值与日志字符串的取值变化，缩进与结构未变 |
| TS/TSX | `desktop/src/**`（5 个文件） | 未做类型检查；改动均为字符串常量取值，未触碰类型或导入 |
| PHP | `webhelp/tools/build-docs.php` | 未做语法检查；改动为字面量取值 |

## 3. 既有测试影响

- `tests/` 下无任何断言上游 URL 的用例，`rg 'cowagent\.ai' tests/` 无匹配，因此本次替换**不产生既有测试失败**。
- 代价是**尚无回归保护**：没有任何测试会因目标地址被误改而失败，任务 5.1、5.2 用于补齐该缺口。
- `tests/test_sidebar_account_frontend.cjs` 仅以 fixture 形式赋值运营方地址，不构成断言。

## 4. 桌面端构建产物滞后

`desktop/dist/**` 为被 `.gitignore` 排除的构建产物，改动后仍含旧目标：

```
desktop/dist/main/menu.js
desktop/dist/main/updater.js
desktop/dist/renderer/assets/index-By5SpplF.js
desktop/dist/renderer/js/console.js
```

桌面端改动需重新构建打包后在运行产物中生效，该步骤列为任务 5.3，本 change 不提交构建产物。

## 5. 状态界定

| 状态 | 结论 |
| --- | --- |
| 替换已落地 | 是。86 个受版本控制文件、359 处 URL 目标已改指向运营方地址，源码中无旧目标残留 |
| 规格要求已满足 | 部分。用户可见外链目标收敛（第 1 条）已满足（按范围仅约束配置与源码中的取值，运行期拼接已明确排除）；功能端点收敛后果显式（第 2 条）**未满足**，更新源仍保留旧回退尝试逻辑；目标地址经可配置接缝承载（第 3 条）**未满足**；非目标域名与显示文字不变（第 4 条）已满足 |
| 功能验收通过 | 否。任务 3.4–3.5、4.1–4.4、5.1–5.5 未完成；无回归保护、未重新构建桌面产物、未做真实浏览器验证 |
| 生产启用 | 否。改动仅存在于本机工作区，未归档 |
