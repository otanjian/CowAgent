#!/usr/bin/env python3
"""
企业信息查询脚本
Business Information Query Script

使用项目内置 web_search 工具（Bocha / Qianfan / Zhipu / LinkAI 多 provider 聚合）
收集企业相关信息，替代原 skillhub 版依赖的 ~/.workbuddy/skills/baidu-search 外部脚本。

用法:
    python business_query.py <公司名称> [--type TYPE] [--count COUNT] [--freshness F]

参数:
    公司名称    要查询的企业名称（必填）
    --type     查询类型：basic(默认)|shareholder|risk|finance|news|contact|all
    --count    返回结果数量，默认10条
    --freshness 时间范围过滤：noLimit(默认)|oneDay|oneWeek|oneMonth|oneYear
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

# 确保项目根目录在 sys.path 中，以便 import agent.tools.web_search
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from agent.tools.web_search import WebSearch
    from common.log import logger
except ImportError as e:
    print(f"[business_query] 导入项目模块失败：{e}", file=sys.stderr)
    print(f"[business_query] 请确保在项目根目录运行，或 PYTHONPATH 包含项目根", file=sys.stderr)
    raise


class BusinessSearch:
    """商业信息查询类 - 基于项目内置 WebSearch 工具"""

    # 预设的搜索模板（与 SKILL.md Step 2 保持一致）
    SEARCH_TEMPLATES = {
        "basic":      '"{name}" 工商信息 注册资本 成立时间 经营状态 统一社会信用代码',
        "shareholder": '"{name}" 股东 持股比例 大股东 法人代表 实际控制人',
        "risk":       '"{name}" 失信 被执行人 限制高消费 行政处罚 经营异常 诉讼',
        "finance":    '"{name}" 融资 A轮 B轮 IPO 上市 投资方 估值',
        "news":       '"{name}" 最新 新闻 动态 媒体报道',
        "contact":    '"{name}" 联系方式 地址 电话 官网',
    }

    def __init__(self):
        # 使用项目内置 WebSearch 工具，provider 由 config.json 的 tools.web_search 配置决定
        self._web_search = WebSearch()

    def search(
        self,
        query: str,
        count: int = 10,
        freshness: str = "noLimit",
    ) -> List[Dict]:
        """执行 Web 搜索

        Args:
            query:      搜索查询字符串
            count:      返回结果条数（1-50）
            freshness:  时间范围过滤
                        noLimit / oneDay / oneWeek / oneMonth / oneYear

        Returns:
            搜索结果列表，每项为 {title, url, snippet, siteName, datePublished}。
            失败时返回空列表（错误信息输出到 stderr）。
        """
        args = {"query": query, "count": count, "freshness": freshness}

        try:
            tool_result = self._web_search.execute(args)
        except Exception as e:
            logger.error(f"[business_query] WebSearch.execute 异常: {e}", exc_info=True)
            print(f"搜索异常: {e}", file=sys.stderr)
            return []

        if not tool_result or tool_result.status != "success":
            err = getattr(tool_result, "result", None) if tool_result else "no result"
            print(f"搜索出错: {err}", file=sys.stderr)
            return []

        # ToolResult.result 成功时为 dict：
        #   {query, backend, total, count, results: [{title,url,snippet,siteName,datePublished}]}
        data = tool_result.result or {}
        results = data.get("results", []) if isinstance(data, dict) else []
        backend = data.get("backend", "unknown") if isinstance(data, dict) else "unknown"
        logger.info(
            f"[business_query] query={query[:60]!r} backend={backend} "
            f"count={len(results)}"
        )
        return results

    def query_company(
        self,
        company_name: str,
        query_type: str = "all",
        count: int = 10,
        freshness: str = "noLimit",
    ) -> Dict[str, List]:
        """查询企业信息

        Args:
            company_name: 企业正式名称
            query_type:   basic|shareholder|risk|finance|news|contact|all
            count:        每个维度返回的结果条数
            freshness:    时间范围过滤（主要对 news 维度有意义）

        Returns:
            dict: {维度名: [搜索结果...]}
        """
        results: Dict[str, List] = {}

        if query_type == "all":
            # 查询所有维度
            for qtype, template in self.SEARCH_TEMPLATES.items():
                query = template.format(name=company_name)
                print(f"[{qtype}] 查询: {query}")
                # news 维度默认限近一年，更有时效性
                fr = "oneYear" if (qtype == "news" and freshness == "noLimit") else freshness
                results[qtype] = self.search(query, count, fr)
        else:
            # 查询指定类型
            template = self.SEARCH_TEMPLATES.get(query_type, self.SEARCH_TEMPLATES["basic"])
            query = template.format(name=company_name)
            print(f"[{query_type}] 查询: {query}")
            results[query_type] = self.search(query, count, freshness)

        return results

    def format_report(self, company_name: str, results: Dict) -> str:
        """生成结构化报告"""
        report = []
        report.append("=" * 60)
        report.append(f"企业信息查询报告")
        report.append("=" * 60)
        report.append(f"查询对象: {company_name}")
        report.append(f"查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")

        type_names = {
            "basic":      "【工商基本信息】",
            "shareholder": "【股东与法人信息】",
            "risk":       "【经营风险信息】",
            "finance":    "【融资上市信息】",
            "news":       "【新闻动态】",
            "contact":    "【联系方式】",
        }

        for qtype, data in results.items():
            report.append("")
            report.append(type_names.get(qtype, qtype))
            report.append("-" * 40)

            if not data:
                report.append("未找到相关信息")
                continue

            for item in data[:10]:
                if isinstance(item, dict):
                    title = item.get("title", "")
                    url = item.get("url", "")
                    snippet = item.get("snippet", "")
                    report.append(f"• {title}")
                    if snippet:
                        report.append(f"  {snippet[:100]}...")
                    report.append(f"  来源: {url}")
                else:
                    report.append(f"• {item}")
            report.append("")

        report.append("=" * 60)
        report.append("提示: 以上信息仅供参考，具体信息请以官方渠道为准")
        report.append("=" * 60)

        return "\n".join(report)


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    company_name = sys.argv[1]

    # 解析参数
    query_type = "all"
    count = 10
    freshness = "noLimit"

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--type" and i + 1 < len(sys.argv):
            query_type = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--count" and i + 1 < len(sys.argv):
            count = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--freshness" and i + 1 < len(sys.argv):
            freshness = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    # 执行查询
    search = BusinessSearch()
    results = search.query_company(company_name, query_type, count, freshness)

    # 生成报告
    report = search.format_report(company_name, results)
    print(report)


if __name__ == "__main__":
    main()
