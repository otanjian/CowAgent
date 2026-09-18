#!/usr/bin/env python3
"""天机商查核心脚本：按企业名 + 维度 执行 Web 搜索 + 抓取，输出原始搜索结果。

设计原则：本脚本只负责「搜索 + 抓取」两件确定性的事，不调用 LLM。
所有理解/提取/建议交给对话 LLM 在 Agent Harness 上下文里完成——避免双重 LLM 调用，
保持上下文连贯，降低成本与延迟。

与 business_query.py 的关系：
- business_query.py 是 skillhub 原版的轻量迁移（subprocess → 内置 WebSearch），保留命令行接口
- search_business.py 专为 procurement-supplier-risk 调用，输出结构化搜索结果供评分脚本消费

用法：
    python skills/tianji-business-search/scripts/search_business.py \\
        --company "XX化工有限公司" \\
        --dimensions "basic,risk,shareholder,news" \\
        --output tmp/tianji_xx_company.json

说明：
- --output 为相对路径时自动锚定到**租户工作空间**（如 <工作空间>/tmp/xxx.json），
  不会写入项目根目录或调用方 cwd。

依赖：
- 项目内置 WebSearch（agent.tools.web_search）+ WebFetch（agent.tools.web_fetch）
- 无需 LLM 配置
"""
import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

# 确保项目根在 sys.path
# 注意：bash 工具的 cwd 可能是租户工作空间（如 ~/one），不是项目根
# 工作空间下有 skills/ 副本，但没有 agent/ 和 common/ 包
# 方案：从 __file__ 向上找，优先找包含 agent/ + common/ 的目录（项目根），
#       而不是只包含 skills/ 的目录（工作空间副本）
def _find_project_root() -> str:
    """从当前文件位置向上查找项目根（包含 agent/ 和 common/ 的目录）。

    当脚本从工作空间副本运行时（~/one/skills/.../script.py），
    __file__ 指向副本位置，向上找到的 ~/one 没有 agent/ 包。
    此时需要从已知的项目根路径列表中查找。
    """
    # 0. 优先使用平台注入的项目根环境变量（父进程 config.load_config 注入，
    #    子进程继承）。脚本从租户工作空间副本运行时，__file__ 向上永远
    #    找不到项目根（工作空间与项目根是两棵不同的目录树），必须靠它。
    env_root = os.environ.get("ONEAGENT_PROJECT_ROOT", "").strip()
    if env_root and os.path.isdir(os.path.join(env_root, "agent", "tools")) and \
       os.path.isdir(os.path.join(env_root, "common")):
        return os.path.abspath(env_root)

    # 1. 从 __file__ 向上找包含 agent/ + common/ 的目录
    for candidate in [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.realpath(__file__)),
    ]:
        cur = candidate
        for _ in range(10):
            if os.path.isdir(os.path.join(cur, "agent", "tools")) and \
               os.path.isdir(os.path.join(cur, "common")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    # 2. 从已知路径列表中查找（处理工作空间副本场景）
    known_roots = [
        os.path.expanduser("~/xmhmtai_agent"),           # 用户目录
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),                # __file__ 上 3 层
        os.getcwd(),                                     # 当前工作目录
    ]
    for root in known_roots:
        if root and os.path.isdir(os.path.join(root, "agent", "tools")) and \
           os.path.isdir(os.path.join(root, "common")):
            return os.path.abspath(root)

    # 3. 兜底：__file__ 上 3 层（适用于 cwd=项目根 的情况）
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_PROJECT_ROOT = _find_project_root()
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common.log import logger


# =============================================================================
# 租户工作空间解析（数据落盘锚定）
# =============================================================================
def _resolve_tenant_workspace() -> str:
    """解析当前租户工作空间根目录（租户环境）。

    天机商查产生的中间数据一律锚定在租户工作空间，避免因调用方 cwd 不同
    而把文件写进项目根目录的 tmp/（违反多租户数据隔离规范）。
    优先级：
    1. 当前工作目录本身位于 tenants/<tenant_id> 下（Agent bash 工具 / 工作台
       都以租户工作空间为 cwd）→ 直接用 cwd；
    2. 进程内请求上下文（web handler 直接调用）→ auth.tenant_context；
    3. 兜底：config.json 的 agent_workspace（单租户模式/无法识别租户时的全局工作空间）。
    """
    cwd = os.path.abspath(os.getcwd())
    parts = [p for p in cwd.split(os.sep) if p]
    if "tenants" in parts:
        return cwd

    try:
        from auth.tenant_context import get_tenant_workspace_root
        root = get_tenant_workspace_root()
        if root:
            return os.path.abspath(root)
    except Exception:
        pass

    try:
        from common.utils import expand_path
        from config import conf
        root = expand_path(conf().get("agent_workspace", "~/one"))
        if root:
            return os.path.abspath(root)
    except Exception:
        pass

    return os.path.abspath(os.path.expanduser("~/one"))


def _anchor_to_workspace(path: str, workspace: Optional[str] = None) -> str:
    """把相对路径锚定到租户工作空间；绝对路径原样返回。"""
    if os.path.isabs(path):
        return path
    workspace = workspace or _resolve_tenant_workspace()
    return os.path.normpath(os.path.join(workspace, path))


# =============================================================================
# 维度查询模板（与 SKILL.md Step 2 对齐）
# =============================================================================
DIMENSION_QUERIES: Dict[str, List[str]] = {
    "basic": [
        '"{name}" 工商信息 注册资本 成立时间 经营状态 统一社会信用代码',
        '"{name}" site:gsxt.gov.cn',
    ],
    "shareholder": [
        '"{name}" 股东 持股比例 大股东 法人代表 实际控制人',
    ],
    "risk": [
        '"{name}" 失信 被执行人 限制高消费 行政处罚 经营异常 诉讼',
        '"{name}" site:tianyancha.com 失信被执行人',
    ],
    "finance": [
        '"{name}" 融资 A轮 B轮 IPO 上市 投资方 估值',
    ],
    "news": [
        '"{name}" 最新 新闻 动态 舆情',
    ],
    "contact": [
        '"{name}" 联系方式 地址 电话 官网',
    ],
}


# =============================================================================
# WebSearch 调用
# =============================================================================
def _search_one_query(query: str, count: int = 8) -> List[Dict[str, Any]]:
    """调项目内置 WebSearch 工具，返回 [{title, url, snippet, siteName, datePublished}]。"""
    try:
        from agent.tools.web_search import WebSearch
        ws = WebSearch()
        if not ws.is_available():
            logger.warning("[tianji-search] WebSearch 未配置任何 provider，跳过")
            return []
        result = ws.execute({"query": query, "count": count})
        if not result or result.status != "success":
            err = getattr(result, "result", "no result") if result else "no result"
            logger.warning(f"[tianji-search] query failed: {query[:60]!r} - {err}")
            return []
        data = result.result or {}
        return data.get("results", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning(f"[tianji-search] query exception: {query[:60]!r} - {e}")
        return []


# =============================================================================
# WebFetch 调用（对含关键信息的 URL 抓取详情）
# =============================================================================
def _fetch_detail(url: str, max_chars: int = 2000) -> str:
    """调项目内置 WebFetch 工具抓取页面正文，截断到 max_chars。"""
    try:
        from agent.tools.web_fetch import WebFetch
        wf = WebFetch()
        result = wf.execute({"url": url})
        if not result or result.status != "success":
            return ""
        data = result.result
        if isinstance(data, dict):
            content = data.get("content") or data.get("text") or ""
        else:
            content = str(data)
        return content[:max_chars]
    except Exception as e:
        logger.debug(f"[tianji-search] fetch failed: {url} - {e}")
        return ""


# =============================================================================
# 关键词提取（规则法，供评分脚本快速估算司法/舆情风险量级）
# =============================================================================
_RISK_KEYWORDS = [
    "失信", "被执行人", "限制高消费", "行政处罚", "经营异常",
    "诉讼", "起诉", "强制执行", "清算", "破产", "倒闭",
]
_NEWS_NEGATIVE_KEYWORDS = [
    "投诉", "诈骗", "跑路", "破产", "倒闭", "资金链",
    "违约", "拖欠", "诉讼", "处罚", "强制", "清算",
]


def _count_keyword_hits(text: str, keywords: List[str]) -> int:
    """统计 keywords 在 text 中的命中次数（去重，每个关键词最多计 1 次）。"""
    return sum(1 for kw in keywords if kw in text)


def _build_basic_from_snippets(snippets: List[str]) -> Dict[str, Any]:
    """从搜索片段中用规则提取基本工商信息（无需 LLM）。

    提取项：注册资金、成立日期、经营状态、统一社会信用代码。
    规则：用正则匹配 snippet 里的「注册资本：XXX」「成立日期：XXX」等模式。
    """
    text = "\n".join(snippets)
    basic: Dict[str, Any] = {}

    # 注册资本
    m = re.search(r"注册资本[：:\s]*([0-9]+(?:\.[0-9]+)?\s*(?:万|亿元)?)", text)
    if m:
        basic["registered_capital"] = m.group(1).strip()

    # 成立日期
    m = re.search(r"成立日期[：:\s]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})", text)
    if m:
        basic["establish_date"] = m.group(1).replace("年", "-").replace("月", "-").replace("/", "-")

    # 经营状态
    for status in ("存续", "在营", "在业", "开业", "注销", "吊销", "撤销"):
        if status in text:
            basic["business_status"] = status
            break

    # 统一社会信用代码
    m = re.search(r"统一社会信用代码[：:\s]*([0-9A-Z]{18})", text)
    if m:
        basic["credit_code"] = m.group(1)

    # 法人代表
    m = re.search(r"法定代表人[：:\s]*([\u4e00-\u9fa5]{2,4})", text)
    if m:
        basic["legal_person"] = m.group(1)

    return basic


def _build_risk_from_snippets(snippets: List[str]) -> Dict[str, Any]:
    """从搜索片段中用规则统计风险指标（无需 LLM）。

    注意：这是基于关键词命中次数的估算，实际数量可能偏高（一条新闻命中多个关键词）。
    评分脚本（scoring_rules.py）使用 min() 截断，对量级不敏感，够用。
    对话 LLM 在看到原始片段后可以做更准确判断。
    """
    text = "\n".join(snippets)
    return {
        "lawsuit_count": _count_keyword_hits(text, ["诉讼", "起诉"]),
        "dishonest_count": _count_keyword_hits(text, ["失信"]),
        "executed_count": _count_keyword_hits(text, ["被执行人", "强制执行"]),
        "penalty_count": _count_keyword_hits(text, ["行政处罚"]),
        "abnormal_count": _count_keyword_hits(text, ["经营异常"]),
        "details": [s for s in snippets if any(kw in s for kw in _RISK_KEYWORDS)][:10],
    }


def _build_news_from_snippets(news_snippets: List[str]) -> List[str]:
    """从 news 维度 snippet 中筛出负面新闻（无需 LLM）。"""
    return [
        s for s in news_snippets
        if any(kw in s for kw in _NEWS_NEGATIVE_KEYWORDS)
    ][:5]


# =============================================================================
# 主流程
# =============================================================================
def search_and_extract(
    company: str,
    dimensions: List[str],
    count: int = 8,
    fetch_top_n: int = 2,
) -> Dict[str, Any]:
    """按维度搜索 + 抓取，返回结构化搜索结果（不调 LLM）。

    Args:
        company:       企业名称
        dimensions:    维度列表（basic/risk/shareholder/finance/news/contact）
        count:         每次搜索返回条数
        fetch_top_n:   每次搜索结果中抓取详情的条数（0 表示不抓取）

    Returns:
        {
            "basic": {...},         # 规则提取的工商信息
            "shareholder": [],      # 原始搜索片段（供对话 LLM 提取）
            "risk": {...},          # 规则统计的风险指标 + 原始片段
            "finance": [],          # 原始搜索片段
            "news": [...],          # 筛选后的负面新闻片段
            "contact": [],          # 原始搜索片段
            "raw_search_results": {
                "basic": [...], "shareholder": [...], ...
            },
            "fetched_contents": [...],
            "meta": {...}
        }
    """
    # 按维度分组的原始搜索结果
    raw_results: Dict[str, List[Dict[str, Any]]] = {dim: [] for dim in dimensions}
    # 按维度分组的 snippet 文本（用于规则提取）
    snippets_by_dim: Dict[str, List[str]] = {dim: [] for dim in dimensions}
    search_log: List[Dict[str, Any]] = []
    fetched_contents: List[str] = []

    for dim in dimensions:
        for q_template in DIMENSION_QUERIES.get(dim, []):
            q = q_template.format(name=company)
            results = _search_one_query(q, count=count)
            raw_results[dim].extend(results)
            for r in results:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                snippets_by_dim[dim].append(f"[{title}] {snippet}")
            search_log.append({"dimension": dim, "query": q, "num_results": len(results)})

            # 对前 N 条抓详情（仅对 risk 和 basic 维度抓，节省 API 调用）
            if fetch_top_n > 0 and dim in ("risk", "basic"):
                for r in results[:fetch_top_n]:
                    url = r.get("url", "")
                    if not url:
                        continue
                    content = _fetch_detail(url, max_chars=1500)
                    if content:
                        fetched_contents.append(f"[{dim}] {url}\n{content}")

    # 规则提取（不调 LLM）
    basic = _build_basic_from_snippets(snippets_by_dim.get("basic", []))
    risk = _build_risk_from_snippets(
        snippets_by_dim.get("risk", []) + snippets_by_dim.get("basic", [])
    )
    news = _build_news_from_snippets(snippets_by_dim.get("news", []))

    result = {
        "basic": basic,
        "shareholder": snippets_by_dim.get("shareholder", []),
        "risk": risk,
        "finance": snippets_by_dim.get("finance", []),
        "news": news,
        "contact": snippets_by_dim.get("contact", []),
        # 原始搜索结果（供对话 LLM 或下游进一步提取）
        "raw_search_results": raw_results,
        "fetched_contents": fetched_contents,
        "meta": {
            "company": company,
            "dimensions": dimensions,
            "search_log": search_log,
            "total_snippets": sum(len(v) for v in snippets_by_dim.values()),
            "total_fetched": len(fetched_contents),
            "extracted_via_llm": False,  # 标识：本脚本未调 LLM，纯规则提取
        },
    }
    return result


def main():
    # subprocess 独立进程，必须自己加载 config.json，否则 WebSearch 读不到 API key。
    # load_config() 用相对路径 "./config.json" 定位配置，临时切到项目根再加载，完成后还原。
    old_cwd = os.getcwd()
    try:
        os.chdir(_PROJECT_ROOT)
        from config import load_config
        load_config()
    except Exception as _e:
        logger.warning(f"[tianji-search] load_config 失败: {_e}")
    finally:
        os.chdir(old_cwd)

    ap = argparse.ArgumentParser(description="天机商查：按企业名 + 维度搜索（不调 LLM）")
    ap.add_argument("--company", required=True, help="企业名称")
    ap.add_argument(
        "--dimensions", default="basic,risk,shareholder,news",
        help="逗号分隔的维度：basic/shareholder/risk/finance/news/contact",
    )
    ap.add_argument("--count", type=int, default=8, help="每次搜索返回条数")
    ap.add_argument("--fetch-top-n", type=int, default=2, help="每次搜索结果中抓详情条数（0=不抓）")
    ap.add_argument("--output", required=True, help="输出 JSON 路径（相对路径自动锚定到租户工作空间）")
    args = ap.parse_args()

    # 输出路径锚定到租户工作空间：相对路径不再依赖调用方 cwd，防止写入项目根 tmp/
    args.output = _anchor_to_workspace(args.output)

    dims = [d.strip() for d in args.dimensions.split(",") if d.strip()]
    result = search_and_extract(args.company, dims, count=args.count, fetch_top_n=args.fetch_top_n)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Wrote {args.output} (snippets={result['meta']['total_snippets']}, "
          f"fetched={result['meta']['total_fetched']}, llm_extracted={result['meta']['extracted_via_llm']})")


if __name__ == "__main__":
    main()
