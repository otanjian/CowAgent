#!/usr/bin/env python3
"""五维度风险评分规则（适配天机商查免费 Web 搜索的精度）。

权重（与 scenes_config.json supplier_risk.indicators 对齐）：
- 司法风险 judicial     40%
- 股东与关联 shareholder 20%
- SAP 内部 sap_internal 20%
- 新闻舆情 news         10%
- 工商信息 business     10%

每个维度返回 0-100 分（越高越危险），总分 = 加权和。
风险等级：低(<25) / 中(25-50) / 高(50-75) / 严重(≥75)
"""
from datetime import datetime
from typing import Any, Dict, List, Tuple

DIMENSION_WEIGHTS: Dict[str, float] = {
    "judicial":     0.40,
    "shareholder":  0.20,
    "sap_internal": 0.20,
    "news":         0.10,
    "business":     0.10,
}

RISK_LEVEL_THRESHOLDS = [
    (75, "严重"),
    (50, "高"),
    (25, "中"),
    (0, "低"),
]


def score_judicial(tianji_risk: Dict[str, Any]) -> int:
    """司法风险评分 0-100。"""
    risk = tianji_risk or {}
    score = 0
    score += min(risk.get("executed_count", 0) * 15, 60)    # 被执行人
    score += min(risk.get("dishonest_count", 0) * 25, 50)   # 失信（硬指标，一次加25）
    score += risk.get("lawsuit_count", 0) * 5                # 诉讼
    score += min(risk.get("penalty_count", 0) * 10, 30)     # 行政处罚
    score += risk.get("abnormal_count", 0) * 10             # 经营异常
    return min(score, 100)


def score_shareholder(tianji_shareholder: List[Dict], tianji_basic: Dict[str, Any]) -> int:
    """股东与关联风险：股东过少（一人公司）+ 注册资本过小 + 经营状态异常。"""
    score = 0
    # 一人有限责任公司风险：独担风险
    if not tianji_shareholder or len(tianji_shareholder) <= 1:
        score += 25
    # 注册资本极小
    cap = (tianji_basic or {}).get("registered_capital", "")
    if isinstance(cap, str):
        # 提取数字部分（如 "5000万人民币" → 5000）
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*万", cap)
        if m:
            num = float(m.group(1))
            if num < 50:
                score += 30
            elif num < 100:
                score += 15
    # 经营状态：注销/吊销直接满分
    status = (tianji_basic or {}).get("business_status", "")
    if status in ("注销", "吊销", "撤销"):
        return 100
    if status and status not in ("存续", "在营", "在业", "开业"):
        score += 40
    return min(score, 100)


def score_sap_internal(sap_row: Dict[str, Any]) -> int:
    """SAP LFA1 内部标记：SPERR/LOEVM/成立年份。"""
    score = 0
    # 字段兼容：SAP 原始大写名 / 中文名 / ADT SQL 别名
    sperr = (
        sap_row.get("过账冻结") or sap_row.get("SPERR")
        or sap_row.get("posting_block") or ""
    )
    loevm = (
        sap_row.get("删除标记") or sap_row.get("LOEVM")
        or sap_row.get("delete_flag") or ""
    )
    erdat = (
        sap_row.get("创建日期") or sap_row.get("ERDAT")
        or sap_row.get("created_date") or ""
    )
    # 过账冻结
    if sperr and str(sperr).strip() and str(sperr).strip() not in ("", " "):
        score += 60
    # 删除标记（最严重）
    if loevm and str(loevm).strip() and str(loevm).strip() not in ("", " "):
        score += 80
    # 新供应商（LFA1 创建时间 < 1 年）
    if erdat and len(str(erdat)) == 8:
        try:
            created = datetime.strptime(str(erdat), "%Y%m%d")
            years = (datetime.now() - created).days / 365
            if years < 1:
                score += 20
        except Exception:
            pass
    return min(score, 100)


def score_news(tianji_news: List[Any]) -> int:
    """舆情评分：负面关键词命中则加分。"""
    if not tianji_news:
        return 0
    negative_keywords = [
        "投诉", "诈骗", "跑路", "破产", "倒闭", "资金链",
        "违约", "拖欠", "诉讼", "处罚", "强制", "清算",
    ]
    hit = 0
    for n in tianji_news:
        text = str(n)
        for kw in negative_keywords:
            if kw in text:
                hit += 1
                break
    return min(hit * 15, 100)


def score_business(tianji_basic: Dict[str, Any]) -> int:
    """工商核对：经营状态+成立时长。"""
    score = 0
    basic = tianji_basic or {}
    status = basic.get("business_status", "")
    if status in ("注销", "吊销"):
        return 100
    if not status:
        score += 25  # 没查到，不确定性加分
    # 成立 < 3 年
    est = basic.get("establish_date", "")
    if est:
        try:
            d = datetime.strptime(str(est).split("T")[0][:10], "%Y-%m-%d")
            years = (datetime.now() - d).days / 365
            if years < 3:
                score += 15
        except Exception:
            pass
    return min(score, 100)


def compute_total(dimension_scores: Dict[str, int]) -> Tuple[int, str]:
    """返回 (total_score, risk_level)。"""
    total = round(sum(dimension_scores[k] * w for k, w in DIMENSION_WEIGHTS.items()))
    for threshold, level in RISK_LEVEL_THRESHOLDS:
        if total >= threshold:
            return total, level
    return 0, "低"
