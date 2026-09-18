"""SAP 数据分析洞察生成模块 - 纯统计分析，无需外部 API。

基于 sql-report-generator 的 ai_insights.py 改造，适配 SAP 查询结果。
依赖：pandas, numpy, scipy
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

import numpy as np
import pandas as pd
from scipy import stats


class InsightType:
    """洞察类型"""

    ANOMALY = "anomaly"
    TREND = "trend"
    CORRELATION = "correlation"
    TOP_N = "top_n"
    DISTRIBUTION = "distribution"
    SEASONALITY = "seasonality"
    COMPARISON = "comparison"


class SeverityLevel:
    """严重程度"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Insight:
    """单个洞察对象"""

    type: str
    severity: str
    title: str
    description: str
    metric: str
    value: Any
    confidence: float

    def to_dict(self) -> dict:
        """转换为字典（确保 JSON 可序列化）"""

        def make_serializable(val):
            if isinstance(val, (np.integer, np.int64, np.int32)):
                return int(val)
            elif isinstance(val, (np.floating, np.float64, np.float32)):
                return float(val)
            elif isinstance(val, np.ndarray):
                return val.tolist()
            return val

        return {
            "type": make_serializable(self.type),
            "severity": make_serializable(self.severity),
            "title": make_serializable(self.title),
            "description": make_serializable(self.description),
            "metric": make_serializable(self.metric),
            "value": make_serializable(self.value),
            "confidence": make_serializable(self.confidence),
        }


@dataclass
class InsightReport:
    """完整洞察报告"""

    insights: List[Insight] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    summary: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "insights": [i.to_dict() for i in self.insights],
            "recommendations": self.recommendations,
            "summary": self.summary,
            "generated_at": self.generated_at,
            "total_insights": len(self.insights),
        }

    def to_markdown(self) -> str:
        """转换为 Markdown 格式"""
        lines = []
        lines.append("## 数据洞察\n")

        if self.summary:
            lines.append(f"**摘要**: {self.summary}\n")

        for i, insight in enumerate(self.insights, 1):
            lines.append(f"{i}. **{insight.title}** ({insight.severity})\n")
            lines.append(f"   - 指标: {insight.metric}\n")
            lines.append(f"   - 描述: {insight.description}\n")

        if self.recommendations:
            lines.append("\n**建议**:\n")
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"{i}. {rec}\n")

        return "".join(lines)


class InsightGenerator:
    """AI 自动洞察生成器 - 纯统计分析，无需外部 API"""

    def __init__(self, df: pd.DataFrame, date_col=None, value_cols=None, dimension_cols=None):
        """
        初始化洞察生成器

        Args:
            df: 输入数据框
            date_col: 日期列名（用于时间序列分析）
            value_cols: 数值列列表（用于分析的指标列）
            dimension_cols: 维度列列表（用于分组分析）
        """
        self.df = df.copy()
        self.date_col = date_col
        self.value_cols = value_cols or self._infer_numeric_cols()
        self.dimension_cols = dimension_cols or []
        self.insights: List[Insight] = []

        # 数据预处理
        if self.date_col and self.date_col in self.df.columns:
            self.df[self.date_col] = pd.to_datetime(self.df[self.date_col], errors="coerce")

    def _infer_numeric_cols(self) -> List[str]:
        """自动推断数值列"""
        return self.df.select_dtypes(include=[np.number]).columns.tolist()

    def generate_all(self) -> InsightReport:
        """生成完整洞察报告"""
        self.insights = []

        # 执行各类分析
        self.insights.extend(self.detect_anomalies())
        self.insights.extend(self.detect_trends())
        self.insights.extend(self.detect_correlations())
        self.insights.extend(self.detect_top_items())
        self.insights.extend(self.detect_distribution())

        if self.date_col:
            self.insights.extend(self.detect_seasonality())

        if self.dimension_cols:
            self.insights.extend(self.detect_comparison())

        # 生成建议
        recommendations = self.generate_recommendations(self.insights)

        # 生成摘要
        summary = self._generate_summary()

        return InsightReport(
            insights=self.insights,
            recommendations=recommendations,
            summary=summary,
        )

    def detect_anomalies(self) -> List[Insight]:
        """异常检测：识别离群值、突变点"""
        insights = []

        for col in self.value_cols:
            if col not in self.df.columns:
                continue

            data = self.df[col].dropna()
            if len(data) < 3:
                continue

            # Z-score 方法
            z_scores = np.abs(stats.zscore(data))
            anomaly_mask = z_scores > 2.5
            anomaly_count = int(anomaly_mask.sum())

            if anomaly_count > 0:
                anomaly_rate = anomaly_count / len(data)
                insights.append(
                    Insight(
                        type=InsightType.ANOMALY,
                        severity=SeverityLevel.WARNING if anomaly_rate > 0.05 else SeverityLevel.INFO,
                        title=f"{col} 中检测到异常值",
                        description=f"检测到 {anomaly_count} 个异常值（占比 {anomaly_rate:.1%}），可能需要进一步调查。",
                        metric=col,
                        value=anomaly_count,
                        confidence=min(0.95, 0.7 + anomaly_rate),
                    )
                )

            # IQR 方法检测突变
            if len(data) > 1 and self.date_col:
                sorted_df = self.df.sort_values(self.date_col)
                values = sorted_df[col].dropna().values
                if len(values) > 1:
                    pct_change = np.abs(np.diff(values) / (np.abs(values[:-1]) + 1e-10))
                    max_change_idx = int(np.argmax(pct_change))
                    max_change = pct_change[max_change_idx]

                    if max_change > 0.5:
                        insights.append(
                            Insight(
                                type=InsightType.ANOMALY,
                                severity=SeverityLevel.CRITICAL if max_change > 1.0 else SeverityLevel.WARNING,
                                title=f"{col} 出现突变",
                                description=f"检测到 {max_change:.1%} 的环比变化，可能存在重大事件。",
                                metric=col,
                                value=f"{max_change:.1%}",
                                confidence=0.85,
                            )
                        )

        return insights

    def detect_trends(self) -> List[Insight]:
        """趋势检测：上升/下降/平稳"""
        insights = []

        if not self.date_col or self.date_col not in self.df.columns:
            return insights

        sorted_df = self.df.sort_values(self.date_col)

        for col in self.value_cols:
            if col not in sorted_df.columns:
                continue

            data = sorted_df[col].dropna()
            if len(data) < 3:
                continue

            # 线性回归计算趋势
            x = np.arange(len(data))
            y = data.values

            try:
                coeffs = np.polyfit(x, y, 1)
                slope = coeffs[0]
                mean_val = np.mean(y)

                # 标准化斜率
                normalized_slope = slope / (mean_val + 1e-10)

                if normalized_slope > 0.05:
                    trend_type = "上升"
                    severity = SeverityLevel.INFO
                    confidence = min(0.95, 0.6 + abs(normalized_slope))
                elif normalized_slope < -0.05:
                    trend_type = "下降"
                    severity = SeverityLevel.WARNING
                    confidence = min(0.95, 0.6 + abs(normalized_slope))
                else:
                    trend_type = "平稳"
                    severity = SeverityLevel.INFO
                    confidence = 0.7

                insights.append(
                    Insight(
                        type=InsightType.TREND,
                        severity=severity,
                        title=f"{col} 呈现{trend_type}趋势",
                        description=f"在分析周期内，{col} 显示{trend_type}趋势。",
                        metric=col,
                        value=f"{normalized_slope:.1%}",
                        confidence=confidence,
                    )
                )
            except Exception:
                pass

        return insights

    def detect_correlations(self) -> List[Insight]:
        """相关性检测：列间相关性"""
        insights = []

        if len(self.value_cols) < 2:
            return insights

        numeric_df = self.df[self.value_cols].select_dtypes(include=[np.number])
        if numeric_df.shape[1] < 2:
            return insights

        corr_matrix = numeric_df.corr()

        # 找出强相关对
        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                col1 = corr_matrix.columns[i]
                col2 = corr_matrix.columns[j]
                corr_val = corr_matrix.iloc[i, j]

                abs_corr = abs(corr_val)

                if abs_corr > 0.7:
                    corr_type = "正相关" if corr_val > 0 else "负相关"
                    insights.append(
                        Insight(
                            type=InsightType.CORRELATION,
                            severity=SeverityLevel.INFO,
                            title=f"{col1} 与 {col2} 高度{corr_type}",
                            description=f"相关系数为 {corr_val:.3f}，两个指标高度相关。",
                            metric=f"{col1} vs {col2}",
                            value=f"{corr_val:.3f}",
                            confidence=0.9,
                        )
                    )
                elif 0.4 < abs_corr < 0.7:
                    insights.append(
                        Insight(
                            type=InsightType.CORRELATION,
                            severity=SeverityLevel.INFO,
                            title=f"{col1} 与 {col2} 中等相关",
                            description=f"相关系数为 {corr_val:.3f}，两个指标存在中等程度的相关性。",
                            metric=f"{col1} vs {col2}",
                            value=f"{corr_val:.3f}",
                            confidence=0.75,
                        )
                    )

        return insights

    def detect_top_items(self) -> List[Insight]:
        """TOP N 分析：头部集中度、长尾"""
        insights = []

        for col in self.value_cols:
            if col not in self.df.columns:
                continue

            data = self.df[col].dropna()
            if len(data) < 5:
                continue

            # 帕累托分析
            sorted_data = np.sort(data)[::-1]
            cumsum = np.cumsum(sorted_data)
            total = cumsum[-1]

            if total <= 0:
                continue

            # 找出占比 80% 的项数
            threshold_80 = total * 0.8
            idx_80 = np.searchsorted(cumsum, threshold_80)
            concentration_20 = (idx_80 + 1) / len(data)

            if concentration_20 < 0.3:
                severity = SeverityLevel.WARNING
                title = f"{col} 集中度偏高"
                description = f"前 {concentration_20:.0%} 的项目贡献了 80% 的价值，集中度偏高，建议分散风险。"
            else:
                severity = SeverityLevel.INFO
                title = f"{col} 分布相对均衡"
                description = f"前 {concentration_20:.0%} 的项目贡献了 80% 的价值，分布相对均衡。"

            insights.append(
                Insight(
                    type=InsightType.TOP_N,
                    severity=severity,
                    title=title,
                    description=description,
                    metric=col,
                    value=f"{concentration_20:.1%}",
                    confidence=0.85,
                )
            )

        return insights

    def detect_distribution(self) -> List[Insight]:
        """分布检测：正态/偏态/均匀"""
        insights = []

        for col in self.value_cols:
            if col not in self.df.columns:
                continue

            data = self.df[col].dropna()
            if len(data) < 10:
                continue

            # 计算偏度和峰度
            skewness = stats.skew(data)
            kurtosis = stats.kurtosis(data)
            cv = np.std(data) / (np.mean(data) + 1e-10)

            # 判断分布类型
            if abs(skewness) < 0.5:
                dist_type = "近似正态分布"
            elif skewness > 0.5:
                dist_type = "右偏分布"
            else:
                dist_type = "左偏分布"

            insights.append(
                Insight(
                    type=InsightType.DISTRIBUTION,
                    severity=SeverityLevel.INFO,
                    title=f"{col} 呈现{dist_type}",
                    description=f"偏度: {skewness:.3f}, 峰度: {kurtosis:.3f}, 变异系数: {cv:.3f}。",
                    metric=col,
                    value=f"偏度={skewness:.3f}",
                    confidence=0.8,
                )
            )

        return insights

    def detect_seasonality(self) -> List[Insight]:
        """季节性检测：周期性模式"""
        insights = []

        if not self.date_col or self.date_col not in self.df.columns:
            return insights

        sorted_df = self.df.sort_values(self.date_col)

        for col in self.value_cols:
            if col not in sorted_df.columns:
                continue

            data = sorted_df[col].dropna()
            if len(data) < 14:
                continue

            # 简单的周期性检测：计算周间差异
            if len(data) >= 7:
                week1 = data.iloc[:7].mean()
                week2 = data.iloc[7:14].mean()

                if week1 > 0:
                    week_diff = abs(week2 - week1) / week1

                    if week_diff > 0.2:
                        insights.append(
                            Insight(
                                type=InsightType.SEASONALITY,
                                severity=SeverityLevel.INFO,
                                title=f"{col} 存在周期性波动",
                                description=f"检测到周间差异 {week_diff:.1%}，可能存在周期性模式。",
                                metric=col,
                                value=f"{week_diff:.1%}",
                                confidence=0.7,
                            )
                        )

        return insights

    def detect_comparison(self) -> List[Insight]:
        """对比分析：分组差异"""
        insights = []

        if not self.dimension_cols:
            return insights

        for dim_col in self.dimension_cols:
            if dim_col not in self.df.columns:
                continue

            for val_col in self.value_cols:
                if val_col not in self.df.columns:
                    continue

                grouped = self.df.groupby(dim_col)[val_col].agg(["mean", "std", "count"])
                grouped = grouped[grouped["count"] >= 2]

                if len(grouped) < 2:
                    continue

                # 计算组间差异
                max_mean = grouped["mean"].max()
                min_mean = grouped["mean"].min()

                if min_mean > 0:
                    diff_ratio = (max_mean - min_mean) / min_mean

                    if diff_ratio > 0.3:
                        max_group = grouped["mean"].idxmax()
                        min_group = grouped["mean"].idxmin()

                        insights.append(
                            Insight(
                                type=InsightType.COMPARISON,
                                severity=SeverityLevel.WARNING if diff_ratio > 0.5 else SeverityLevel.INFO,
                                title=f"{dim_col} 维度下 {val_col} 差异显著",
                                description=f"{max_group} 的 {val_col} 比 {min_group} 高 {diff_ratio:.1%}，存在显著差异。",
                                metric=f"{dim_col} - {val_col}",
                                value=f"{diff_ratio:.1%}",
                                confidence=0.8,
                            )
                        )

        return insights

    def generate_recommendations(self, insights: List[Insight]) -> List[str]:
        """基于洞察生成运营建议"""
        recommendations = []

        for insight in insights:
            if insight.type == InsightType.TREND:
                if "上升" in insight.title:
                    recommendations.append(f"{insight.metric} 持续增长，建议加大投入和资源配置。")
                elif "下降" in insight.title:
                    recommendations.append(f"{insight.metric} 出现下降趋势，建议排查原因并制定改进方案。")

            elif insight.type == InsightType.ANOMALY:
                if "突变" in insight.title:
                    recommendations.append(f"{insight.metric} 出现异常突变，需要立即调查根本原因。")
                else:
                    recommendations.append(f"{insight.metric} 中存在异常值，建议数据清洗和验证。")

            elif insight.type == InsightType.TOP_N:
                if "偏高" in insight.title:
                    recommendations.append(f"{insight.metric} 集中度过高，建议优化组合，分散风险。")
                else:
                    recommendations.append(f"{insight.metric} 分布均衡，继续保持现有策略。")

            elif insight.type == InsightType.CORRELATION:
                if "高度" in insight.title:
                    recommendations.append(f"{insight.metric} 高度相关，可作为预测指标，建议建立预测模型。")

            elif insight.type == InsightType.COMPARISON:
                if "差异显著" in insight.title:
                    recommendations.append(f"{insight.metric} 存在显著差异，建议分析差异原因并复制最佳实践。")

            elif insight.type == InsightType.SEASONALITY:
                recommendations.append(f"{insight.metric} 存在周期性模式，建议制定季节性运营策略。")

        # 去重
        recommendations = list(dict.fromkeys(recommendations))

        return recommendations[:10]  # 限制建议数量

    def _generate_summary(self) -> str:
        """生成报告摘要"""
        if not self.insights:
            return "未检测到显著洞察。"

        critical_count = sum(1 for i in self.insights if i.severity == SeverityLevel.CRITICAL)
        warning_count = sum(1 for i in self.insights if i.severity == SeverityLevel.WARNING)

        summary_parts = []

        if critical_count > 0:
            summary_parts.append(f"检测到 {critical_count} 个严重问题")

        if warning_count > 0:
            summary_parts.append(f"{warning_count} 个警告")

        if summary_parts:
            return "，".join(summary_parts) + "。建议立即采取行动。"
        else:
            return "数据整体表现良好，继续监控关键指标。"


def quick_insights(df: pd.DataFrame, date_col=None, value_cols=None, dimension_cols=None) -> InsightReport:
    """一键生成洞察报告"""
    gen = InsightGenerator(df, date_col=date_col, value_cols=value_cols, dimension_cols=dimension_cols)
    return gen.generate_all()


# 领域洞察偏好：帮助自动识别更有业务意义的数值列与维度列
DOMAIN_INSIGHT_PREFERENCES = {
    "finance": {
        "value_keywords": ["金额", "额", "value", "amount", "dmbtr", "wrbtr", "netwr", "brtwr", "total"],
        "dimension_keywords": ["科目", "account", "公司", "bukrs", "类型", "type", "凭证", "document"],
    },
    "sales": {
        "value_keywords": ["销售额", "金额", "净值", "netwr", "brtwr", "value", "amount", "menge"],
        "dimension_keywords": ["客户", "kunnr", "name1", "产品", "matnr", "销售组织", "vkorg", "物料组"],
    },
    "procurement": {
        "value_keywords": ["采购额", "金额", "净值", "netwr", "brtwr", "value", "amount", "menge"],
        "dimension_keywords": ["供应商", "lifnr", "name1", "物料", "matnr", "采购组织", "ekorg", "采购组"],
    },
    "inventory": {
        "value_keywords": ["库存金额", "金额", "value", "amount", "labst", "menge", "数量"],
        "dimension_keywords": ["物料", "matnr", "maktx", "仓库", "werks", "lgort", "库存地点", "物料组"],
    },
    "production": {
        "value_keywords": ["产量", "数量", "menge", "gamng", "金额", "value", "工时", "成本"],
        "dimension_keywords": ["工单", "aufnr", "物料", "matnr", "工厂", "werks", "工单类型", "auart"],
    },
    "master_data": {
        "value_keywords": ["数量", "count", "金额", "value"],
        "dimension_keywords": ["类型", "type", "mtart", "land1", "国家", "城市", "分类"],
    },
}


def _column_matches(col: str, keywords: List[str]) -> bool:
    col_lower = col.lower()
    return any(kw.lower() in col_lower for kw in keywords)


def _pick_value_cols_by_domain(df: pd.DataFrame, domain: str) -> Optional[List[str]]:
    """根据领域偏好挑选优先分析的数值列。"""
    prefs = DOMAIN_INSIGHT_PREFERENCES.get(domain)
    if not prefs:
        return None
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    preferred = [c for c in numeric_cols if _column_matches(c, prefs["value_keywords"])]
    return (preferred + numeric_cols)[:4] if preferred else numeric_cols[:4]


def _pick_dimension_cols_by_domain(df: pd.DataFrame, date_col: Optional[str], domain: str) -> Optional[List[str]]:
    """根据领域偏好挑选优先分析的维度列。"""
    prefs = DOMAIN_INSIGHT_PREFERENCES.get(domain)
    if not prefs:
        return None
    candidates = []
    for col in df.columns:
        if col == date_col:
            continue
        if df[col].dtype == object:
            unique_ratio = df[col].nunique() / len(df)
            if 0 < unique_ratio < 0.5:
                candidates.append(col)
    preferred = [c for c in candidates if _column_matches(c, prefs["dimension_keywords"])]
    return (preferred + candidates)[:3] if preferred else candidates[:3]


def generate_insights_for_sap_rows(rows: List[dict], domain: str = "") -> dict:
    """为 SAP 查询结果生成洞察，返回可直接序列化的字典。

    Args:
        rows: SAP 查询结果行列表。
        domain: 业务领域（sales/procurement/inventory/production/finance/master_data），
                用于调整数值列和维度列的偏好。
    """
    if not rows:
        return {"summary": "无数据，无法生成洞察。", "insights": [], "recommendations": []}

    df = pd.DataFrame(rows)

    # 自动识别日期列
    date_col = None
    for col in df.columns:
        if df[col].dtype == object:
            try:
                parsed = pd.to_datetime(df[col], errors="coerce")
                if parsed.notna().sum() > len(df) * 0.5:
                    date_col = col
                    break
            except Exception:
                pass

    # 根据领域偏好选择数值列与维度列；无领域偏好则使用通用逻辑
    value_cols = _pick_value_cols_by_domain(df, domain)
    dimension_cols = _pick_dimension_cols_by_domain(df, date_col, domain)

    if dimension_cols is None:
        dimension_cols = []
        for col in df.columns:
            if col == date_col:
                continue
            if df[col].dtype == object:
                unique_ratio = df[col].nunique() / len(df)
                if 0 < unique_ratio < 0.5:
                    dimension_cols.append(col)

    try:
        report = quick_insights(df, date_col=date_col, value_cols=value_cols, dimension_cols=dimension_cols)
        result = report.to_dict()
        # 领域化摘要补充
        if domain and result.get("summary"):
            domain_names = {
                "finance": "财务",
                "sales": "销售",
                "procurement": "采购",
                "inventory": "库存",
                "production": "生产",
                "master_data": "主数据",
            }
            domain_name = domain_names.get(domain, domain)
            result["summary"] = f"【{domain_name}】{result['summary']}"
        return result
    except Exception as e:
        return {"summary": f"洞察生成失败: {e}", "insights": [], "recommendations": []}
