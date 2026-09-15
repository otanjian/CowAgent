## 1. 基线与范围固化（G0）

- [x] 1.1 盘点全部上游 `cowagent.ai` 引用：按文件、出现次数与 URL 形态建立清单，区分「完整 URL 目标」与「仅作正文/注释的域名提及」，并登记改动前已指向运营方地址的反向先例（`sidebar-version`、`openAccountAbout()` 复用入口、前端测试 fixture）。（见 `evidence/1-1-inventory-and-execution.md` 第 1–2 节）
- [x] 1.2 确认替换范围为用户已决定的「含功能端点」口径：外链与功能端点一并收敛为 `https://www.rsm.global/china/zh-hans`，目标一律不保留路径、查询串、锚点与语言后缀。（见 `evidence/1-1-inventory-and-execution.md` 第 1 节）
- [x] 1.3 固化非目标边界：`api.link-ai.tech`、`cdn.link-ai.tech`、`link-ai.tech` 与 `github.com/zhayujie/CowAgent` 不改动；Markdown 链接文字与来源说明注释不改写。列出应保持不变的提及清单作为回归基线。（见 `evidence/1-1-inventory-and-execution.md` 第 3 节）

## 2. 用户可见外链目标收敛（G0 通过后）

- [x] 2.1 Web 控制台：技能管理「探索技能广场」入口与侧栏文档入口改指运营方对外地址，取消按 `currentLang === 'zh'` 派生 `/zh`。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [x] 2.2 桌面端：侧栏官网、文档、技能广场入口，原生菜单入口与更新提示中的文档入口改指运营方对外地址，取消按 `getLang() === 'zh'` 派生 `/zh`。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [x] 2.3 文档站点：`docs/docs.json` 导航项与简繁英三套发布说明、技能/安装/桌面/升级页中的对外引用改指运营方对外地址。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [x] 2.4 CLI 与插件输出提示、`.github` 议题模板文档入口与发布工作流下载日志中的地址改指运营方对外地址。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [x] 2.5 校验全部产品面在简体、繁体、英文三种语言下得到同一目标，且不再存在语言后缀派生分支；确认技术性位置（代码注释、`models/linkai` 文档注释）随目标同步更新且不改变代码语义。（见 `evidence/1-2-verification.md` 第 1 节）

## 3. 功能端点收敛与失败显式化

- [x] 3.1 技能广场接口端点（`cli/utils.py` 的 `SKILL_HUB_API`）改指运营方地址；确认第一个可用来源的渠道依赖镜像（`cdn.link-ai.tech`）未被改动，飞书依赖安装路径不受影响。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [x] 3.2 桌面端更新源与发布目标（`desktop/src/main/updater.ts` 的 `FEED_BASE`、`desktop/package.json` 的 `publish.url`）改指运营方地址。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [x] 3.3 文档站点构建抓取基址（`webhelp/tools/build-docs.php`）与演示素材地址（`docs*/intro/index.mdx` 的 `<video src>`）改指运营方地址。（见 `evidence/1-1-inventory-and-execution.md` 第 2 节）
- [ ] 3.4 按要求「功能端点收敛后果显式」实现失败可见：技能广场在线安装与远程列表在端点不可用时报告明确失败（不以空列表伪装成功）；桌面端更新检查报告更新源不可用并移除对旧更新域名的静默回退尝试；文档站点构建在抓取失败时报告失败而不把同一份页面写入各路径。逐项给出失败路径的实测证据。
- [ ] 3.5 为 3.4 的失败路径补充回归：断言端点不可用时得到明确失败状态与可诊断信息，且不存在指向旧上游域名的回退分支。
- [x] 3.6 判定运行期在目标取值之后追加后缀的 6 处拼接**不在本 change 范围**（已确认「拼接的不管」）：保留 `desktop/src/main/updater.ts` 的 `legacy/` 段与 `?lang=zh`、`desktop/src/renderer/src/components/UpdateBanner.tsx` 的 `/releases/v<版本>`、`webhelp/tools/build-docs.php` 的 `$path` 与 `substr($path, 3)` 拼接现状；`upstream-link-retargeting` 规格相应只约束配置与源码中的目标取值，不声明运行期拼接约束。（决策记录见 `design.md` D1 与 `evidence/1-2-verification.md` 第 1.1 节）

## 4. 接缝化、冲突基线与未决决策

- [ ] 4.1 决定功能端点是否恢复可用（保留运营方站点外链并为端点改指可用服务 / 运营方托管服务）：给出结论、理由与生效范围；结论为「保持已收敛状态」时才以任务 3.4 的失败显式化为交付前提。
- [ ] 4.2 将对外目标地址收敛为单一可配置来源（优先复用 `desktop/src/main/updater.ts` 已有的 `loadAppConfig()?.updateFeedUrl` 配置项入口），字面量仅作缺省值；验证更换该来源后各产品面入口同步更新，且未引入与该接缝并列的第二套配置。
- [ ] 4.3 评估是否需要按主题把外链映射到运营方站点的对应页面（D1 记录的「丢失按主题定位能力」代价）：给出结论与理由；结论为「需要」时另立 change 承载映射表，不在本 change 内扩围。
- [ ] 4.4 按 `fork-upstream-decoupling` 的「删除/修改类冲突有显式决策并记录」要求，把本改动在上游核心文件中的 URL 字面量改写登记进 `scripts/conflict-baseline.txt`（文件内改写，使用 `keep-fork` 语义的处置），并确认 `scripts/sync_report.py` 的 `DELIBERATE_REMOVALS` 不被误用。

## 5. 验证、构建与交付

- [ ] 5.1 补充外链目标的回归断言：至少覆盖 Web 控制台技能广场入口与侧栏文档入口、三种语言下目标一致、桌面端侧栏与原生菜单入口、文档站点导航项；断言目标为运营方对外地址且不出现上游域名。
- [ ] 5.2 补充非目标不变的回归断言：LinkAI 相关功能域名与上游仓库地址保持原值；Markdown 链接文字与来源说明注释未被改写。
- [ ] 5.3 重新构建桌面端产物并确认产物中不再含旧目标；验证 Web 控制台改动经静态资源版本机制生效。（见 `evidence/1-2-verification.md` 第 3 节：`desktop/dist/**` 4 个文件仍含旧目标）
- [ ] 5.4 在真实浏览器覆盖技能管理入口、侧栏文档入口、账号菜单版本入口与三种语言，记录点击后目标地址与页面呈现；确认桌面端外观与更新提示文案未被本次改动破坏。
- [ ] 5.5 完成针对本 change 的严格 OpenSpec 校验与差异审查，逐项记录第 2、3 节的事实性结果与 3.4、4.1–4.4 的未完成状态；区分「替换已落地」「规格要求已满足」「功能验收通过」三种状态，不用任务勾选替代功能验收。
