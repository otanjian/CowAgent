#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
气袋生产排产引擎（重构版）

根据排产规则自动计算并生成新的排产表。与原脚本相比的关键变化：
  1. 日期驱动：排产2 表行3 的日期列动态推导早班/夜班列号，不再硬编码 7/31~8/10、8/3 等
  2. 规则5 数量追踪：逐日填入产能，剩余需排产量 < 当日产能时填剩余量，次日填 0
  3. 规则10 班组分配：按班组第一~第五产线偏好查找，支持替代班组，并强制执行拼线规则（备注结构化）
  4. 配置化：排产规则参数（rules.json）、产能（capacity.json）、班组产线（teams.json）、
     拼线规则（merge_rules.json）均可由外部 JSON 传入；未传入时回退读取 Excel 内 sheet 或内置默认值
  5. 双模式 CLI：parse（解析预览）/ run（执行排产输出新表），--json 输出结构化结果

使用方法:
    python schedule_production.py parse --excel <模板路径> [--config <rules.json>]
        [--capacity <capacity.json>] [--teams <teams.json>] [--merge <merge.json>] [--json]
    python schedule_production.py run --excel <模板路径> --output <输出路径>
        [--config <rules.json>] [--capacity <capacity.json>] [--teams <teams.json>]
        [--merge <merge.json>] [--material <SAP号>] [--json]

依赖: openpyxl
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl


# ---------------------------------------------------------------------------
# 内置默认排产规则参数（与 references/scheduling_rules.md 对应）
# ---------------------------------------------------------------------------
DEFAULT_RULES = {
    "version": 1,
    # 规则3：取前 N 个出货量非零期间
    "period_count": 2,
    # 规则4：取优先级前 N 名产线（优先级高于 N 不排）
    "max_priority": 2,
    # 规则7：备注(O列)含该关键字视为设变物料
    "shebian_keyword": "设变",
    # 规则8：备货(G列)周期关键字
    "stockup_keywords": ["2周", "3周", "4周"],
    # 规则8/9：备注(O列)提前备货触发关键字
    "stockup_trigger_keywords": ["提前备货"],
    # 规则8：备货产品从排产窗口内第一个周一开始提前排产（true），否则从窗口起点开始
    "stockup_start_on_monday": True,
    # 规则9：夜班判定分母（14=两周日历天数，10=两周工作日）
    "night_divisor": 14,
    # 规则6：基础夜班触发（早班总量 < 第一周出货量）开关
    "night_basic_enabled": True,
    # 全局：夜班总开关
    "night_enabled": True,
    # 全局：早班列奇偶（偶数列=夜班），预留模板变化
    "early_col_parity": "odd",
}

# 各 sheet 列定义
CAP_SHEET = "产能"           # A=SAP物料号 B=课别 C=生产线 D=产能 E=优先级
DEMAND_SHEET = "排产1"       # B=SAP物料号 G=备货 O=备注 AY=可用库存 AZ~CI=36周出货量
TEAM_SHEET = "班组产线"      # C=班组 D=人数 E~I=第一~第五产线 J=备注
S2_SHEET = "排产2"           # D=生产线 G=SAP物料号 K=班组 行3=日期 奇列早班/偶列夜班

# sheet 名候选（兼容新模板：去掉无用 sheet 后仅保留单个 sheet1）
S2_SHEET_CANDIDATES = ("排产2", "SHEET1", "Sheet1", "sheet1")
DEMAND_SHEET_CANDIDATES = ("排产1", "SHEET1", "Sheet1", "sheet1", "出货量")
CAP_SHEET_CANDIDATES = ("产能",)
TEAM_SHEET_CANDIDATES = ("班组产线", "班组")


def _find_sheet(wb, candidates):
    """在工作簿中按候选名查找 sheet，返回实际 sheet 名；未找到返回 None。"""
    norm = {str(n).strip().replace(" ", "").lower(): n for n in wb.sheetnames}
    for cand in candidates:
        key = str(cand).strip().replace(" ", "").lower()
        if key in norm:
            return norm[key]
    return None


def _find_sheet_or_single(wb, candidates):
    """按候选名查找 sheet；未找到但工作簿仅一个 sheet 时，无论该 sheet 叫什么都直接使用。

    客户提供的文件 sheet 名可能任意（如 sheet1(2)），单 sheet 文件按目标 sheet 处理。
    """
    name = _find_sheet(wb, candidates)
    if name is not None:
        return name
    if len(wb.sheetnames) == 1:
        return wb.sheetnames[0]
    return None


def _norm_sap(v):
    """SAP 物料号规范化：去掉前导零，统一为原始数字串（兼容 18 位补零格式）。

    如 000000005000001256 → 5000001256；8000004171 → 8000004171。
    """
    s = str(v).strip()
    return s.lstrip("0")


def _to_date(v):
    """将表头单元格转 date；支持 datetime、8位整数/字符串、YYYY/M/D、带时分秒日期串、Excel 日期序列号。

    客户文件周列表头日期格式不固定（可能是 '20260814'、'2026/8/14'、'2026-08-14 00:00:00'
    或 Excel 日期序列号），必须一并识别。
    """
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, (int, float)):
        n = int(v) if abs(float(v) - round(float(v))) < 1e-6 else None
        if n is None:
            return None
        s = str(n).strip()
        if len(s) == 8 and s.isdigit():
            try:
                return datetime.strptime(s, "%Y%m%d").date()
            except ValueError:
                return None
        # Excel 日期序列号（1900 日期系统，1900-01-01 序列号为 1）
        if 20000 <= n <= 80000:
            try:
                return (datetime(1899, 12, 30) + timedelta(days=n)).date()
            except (ValueError, OverflowError):
                return None
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        cleaned = re.sub(r"[^\d]", "", s)
        if len(cleaned) >= 8 and cleaned[:8].isdigit():
            try:
                return datetime.strptime(cleaned[:8], "%Y%m%d").date()
            except ValueError:
                return None
        m = re.match(r"^(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?$", s)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            except ValueError:
                return None
    return None


def _detect_ay_col(ws):
    """识别可用库存列号（1-based）：按表头字段名动态识别（1~3 行）。

    客户文件列布局/字段名不固定（如 63 列），识别顺序：
      1. 优先「差异量减排产量」（客户新版预估出货量表的库存字段，勿用列号硬编码）；
      2. 其次表头同时含「可用」「库存」的列（模板 AY 列）；
      3. 再取最后一个含「库存」且不含「出货量」的列（排除「预估减出货量减库存量」等派生列）；
      4. 全部失败回退固定 51。
    """
    # 1. 字段名「差异量减排产量」优先（客户新版文件）
    for r in range(1, 4):
        for c in range(1, ws.max_column + 1):
            v = ws.cell(row=r, column=c).value
            if v is not None and "差异量减排产量" in str(v):
                return c
    # 2/3. 回退：含「库存」的列（排除「预估减出货量减库存量」等含出货量的派生字段）
    last_stock = None
    for r in range(1, 4):
        for c in range(1, ws.max_column + 1):
            v = ws.cell(row=r, column=c).value
            if v is None:
                continue
            s = str(v).strip()
            if "库存" in s and "出货量" not in s:
                last_stock = c
                if "可用" in s:
                    return c
    return last_stock if last_stock else DEMAND_COLS["ay"]


def _detect_inventory_cols(ws):
    """动态识别排产1 库存相关列号（1-based）。

    返回 (差异量减排产量列, 预估减出货量减库存量列, 生产线量列)。
    按表头 1~3 行扫描字段名，未识别到完整三列时回退固定列：
    差异量减排产量=AY(51)、预估减出货量减库存量=AA(27)、生产线量=AB(28)。
    """
    diff_col = estimate_col = line_col = None
    for row in range(1, 4):
        for c in range(1, ws.max_column + 1):
            v = ws.cell(row=row, column=c).value
            if v is None:
                continue
            s = str(v)
            if diff_col is None and "差异量减排产量" in s:
                diff_col = c
            if estimate_col is None and "预估减出货量减库存量" in s:
                estimate_col = c
            if line_col is None and "生产线量" in s:
                line_col = c
        if diff_col and estimate_col and line_col:
            break
    return (
        diff_col or DEMAND_COLS["ay"],
        estimate_col or 27,
        line_col or 28,
    )


def _find_sheet_by_headers(wb, keywords):
    """按表头关键字查找 sheet（扫描每个 sheet 前 3 行），未找到返回 None。

    客户配置表 sheet 名可能随意（如 Sheet1），需按内容（含「班组」「产线」等）兜底识别。
    """
    for name in wb.sheetnames:
        ws = wb[name]
        for r in range(1, 4):
            joined = ""
            for c in range(1, min(ws.max_column, 40) + 1):
                v = ws.cell(row=r, column=c).value
                if v is not None:
                    joined += str(v)
            if joined and all(k in joined for k in keywords):
                return name
    return None


def _detect_week_cols(ws):
    """动态识别需求表周出货量列范围 (start, end)（1-based，end 不含）。

    客户文件不按模板结构：取表头 1~3 行中日期单元格最多的行，找「相邻两列日期相差恰为 7 天」
    的最长连续段作为周出货量区域；无 7 天节奏时回退最长连续日期段。识别失败返回 None。
    """
    # 多行表头：取 1~3 行中日期单元格最多的一行
    best_row = None
    best_n = 0
    for r in range(1, 4):
        row_vals = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
        n = sum(1 for v in row_vals if _to_date(v) is not None)
        if n > best_n:
            best_n = n
            best_row = row_vals
    if best_n < 2:
        return None
    cols = []  # (列号, 日期)
    for i, v in enumerate(best_row, 1):
        d = _to_date(v)
        if d is not None:
            cols.append((i, d))
    # 周节奏段：相邻列 + 日期差恰为 7 天
    weekly = None
    cur = []
    for item in cols:
        if cur and item[0] == cur[-1][0] + 1 and (item[1] - cur[-1][1]).days == 7:
            cur.append(item)
        else:
            if len(cur) > 1 and (weekly is None or len(cur) > len(weekly)):
                weekly = cur
            cur = [item]
    if len(cur) > 1 and (weekly is None or len(cur) > len(weekly)):
        weekly = cur
    if weekly:
        return (weekly[0][0], weekly[-1][0] + 1)
    # 回退：最长连续日期段
    longest, cur = [], []
    for item in cols:
        if cur and item[0] == cur[-1][0] + 1:
            cur.append(item)
        else:
            if len(cur) > len(longest):
                longest = cur
            cur = [item]
    if len(cur) > len(longest):
        longest = cur
    if len(longest) < 2:
        return None
    return (longest[0][0], longest[-1][0] + 1)


CAP_COLS = {"sap": 1, "dept": 2, "line": 3, "capacity": 4, "priority": 5}
DEMAND_COLS = {"sap": 2, "g": 7, "o": 15, "ay": 51, "weeks_start": 52, "weeks_end": 88}  # 52~87
# 班组产线表（客户新版）：A序号 B课别 C区域 D班组 E人数 F~K第一~第六产线 L备注（课别/区域为合并单元格）
TEAM_COLS = {"seq": 1, "dept": 2, "area": 3, "team": 4, "count": 5, "lines_start": 6, "lines_end": 12, "remark": 12}
# 排产2 表：B课别 C区域 D生产线 G=SAP物料号 K=班组 行3=日期 奇列早班/偶列夜班
S2_COLS = {"line": 4, "sap": 7, "team": 11, "dept": 2, "area": 3, "date_row": 3, "data_start": 6}


def norm_line(token):
    """产线代码规范化：'S3101'/'3101'/'S0302' 归一为 '3101'/'0302'，其余保持原样。"""
    s = str(token).strip().upper()
    if s.startswith("S") and len(s) > 1 and s[1:].isdigit():
        return s[1:]
    return s


def _num(value, default=0):
    """安全转数值。"""
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def _is_na(value):
    """判断单元格值是否为无效值（None、空串、#N/A）。"""
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip()
        return not s or s.upper() == "#N/A"
    return False


def _to_number(value):
    """尝试转数值；无效（None/#N/A/空串/非数字）返回 None。"""
    if _is_na(value):
        return None
    if isinstance(value, (int, float)):
        return value
    s = str(value).replace(",", "").strip()
    if s.upper() == "#N/A":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


class ProductionScheduler:
    """气袋生产排产调度器。"""

    def __init__(self, excel_path, rules=None, capacity=None, teams=None, merge=None, demands_path=None,
                 config_only=False):
        """config_only=True 时仅提取产能/班组/拼线配置，不要求排产2 结构与需求（供「从 Excel 同步配置」使用）。"""
        self.excel_path = str(excel_path)
        if not Path(self.excel_path).exists():
            raise FileNotFoundError(f"模板文件不存在: {self.excel_path}")
        # keep_links=False 避免解析外部链接（模板/客户文件中外部链接极多，会导致加载耗时数十秒）
        self.wb_data = openpyxl.load_workbook(self.excel_path, data_only=True, keep_links=False)
        self.wb_formula = openpyxl.load_workbook(self.excel_path, data_only=False, keep_links=False)
        self.warnings = []
        self._week_range_cache = None  # 周出货量列范围缓存（按表头日期动态识别）
        self._demands_cache = None      # 需求数据缓存，避免 _missing_capacity 重复读取

        if config_only:
            # 配置提取模式：跳过排产2 结构与需求，仅读取产能/班组/拼线
            self.s2_name = None
            self.demand_ws = None
        else:
            # 定位排产结果 sheet（排产2）：新模板仅保留单一 sheet（sheet1），客户 sheet 名可能任意
            self.s2_name = _find_sheet_or_single(self.wb_data, S2_SHEET_CANDIDATES)
            if self.s2_name is None:
                raise ValueError(
                    f"模板中未找到 {S2_SHEET_CANDIDATES[0]}/SHEET1 sheet（含多个 sheet），请检查模板"
                )

            # 预估出货量表（排产1）：外部提供时优先读取；识别不到需求 sheet 时直接报错，禁止静默回退
            self.demand_ws = None
            if demands_path:
                if not Path(demands_path).exists():
                    raise ValueError(f"预估出货量表文件不存在: {demands_path}")
                try:
                    wb_demand = openpyxl.load_workbook(str(demands_path), data_only=True, keep_links=False)
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"预估出货量表读取失败（{e}），请检查文件格式") from e
                name = _find_sheet_or_single(wb_demand, DEMAND_SHEET_CANDIDATES)
                if name is None:
                    raise ValueError(
                        f"预估出货量表 {Path(demands_path).name} 中未找到需求数据 sheet"
                        f"（{DEMAND_SHEET_CANDIDATES[0]}/SHEET1，且含多个 sheet），请检查文件"
                    )
                self.demand_ws = wb_demand[name]

        # 规则参数：外部配置 > 内置默认值
        self.rules = dict(DEFAULT_RULES)
        if rules:
            self.rules.update(rules)

        # 产能：外部配置 > Excel 产能 sheet（单 sheet 文件按该 sheet 处理，兼容独立配置表）
        self.capacity = capacity if capacity is not None else self._read_capacity_excel()
        # 班组产线：外部配置 > Excel 班组产线 sheet（单 sheet 文件按该 sheet 处理）
        self.teams = teams if teams is not None else self._read_teams_excel()
        # 拼线规则：外部配置 > 从班组备注解析
        if merge is None:
            self.merge = self._parse_merge_rules(self.teams)
        else:
            self.merge = merge
        self.merge_lookup = self._build_merge_lookup(self.merge)

        # 排产窗口（日期驱动）；配置提取模式不需要
        self.window = None if config_only else self._derive_window()

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------
    def _read_capacity_excel(self):
        """读取产能表，返回 {sap: [{line, capacity, priority, dept}]}，按优先级排序。

        优先「产能」sheet，文件仅一个 sheet 时按该 sheet 处理（兼容独立产能配置表）；
        均无时返回空并告警。
        """
        ws_name = _find_sheet_or_single(self.wb_data, CAP_SHEET_CANDIDATES)
        if ws_name is None:
            # sheet 名随意时按表头内容兜底识别
            ws_name = (_find_sheet_by_headers(self.wb_data, ("SAP物料号", "生产线"))
                       or _find_sheet_by_headers(self.wb_data, ("物料", "产线")))
        if ws_name is None:
            self.warnings.append("文件中未找到「产能」sheet，产能为空")
            return {}
        ws = self.wb_data[ws_name]
        capacity = {}
        for row in range(1, ws.max_row + 1):
            sap = ws.cell(row=row, column=CAP_COLS["sap"]).value
            if sap is None:
                continue
            sap_str = _norm_sap(sap)
            # 仅接收纯数字物料号（跳过表头/非物料行）；不再限定 800 开头，导入全部产能记录
            if not sap_str or not sap_str.isdigit():
                continue
            line = ws.cell(row=row, column=CAP_COLS["line"]).value
            if line is None:
                continue
            prio = ws.cell(row=row, column=CAP_COLS["priority"]).value
            # 优先级为空 = 该物料仅此产线，视为最高优先级 1
            priority = prio if isinstance(prio, (int, float)) else 1
            capacity.setdefault(sap_str, []).append({
                "line": str(line).strip(),
                "capacity": _num(ws.cell(row=row, column=CAP_COLS["capacity"]).value),
                "priority": priority,
                "dept": ws.cell(row=row, column=CAP_COLS["dept"]).value,
            })
        for sap in capacity:
            capacity[sap].sort(key=lambda x: (x["priority"], x["line"]))
        return capacity

    def _read_teams_excel(self):
        """读取班组产线表，返回 [{seq, dept, area, team, count, lines, remark}]。

        客户新版表结构：课别/区域为合并单元格（仅组首行有值），需向下前向填充；
        产线为第一~第六（F~K 列）。优先「班组产线」sheet，单 sheet 文件按该 sheet 处理。
        """
        ws_name = _find_sheet_or_single(self.wb_data, TEAM_SHEET_CANDIDATES)
        if ws_name is None:
            # sheet 名随意（如 Sheet1）时按表头内容兜底识别
            ws_name = _find_sheet_by_headers(self.wb_data, ("班组", "产线"))
        if ws_name is None:
            self.warnings.append("文件中未找到「班组产线」sheet，班组为空")
            return []
        ws = self.wb_data[ws_name]
        teams = []
        cur_dept = ""
        cur_area = ""
        for row in range(3, ws.max_row + 1):
            name = ws.cell(row=row, column=TEAM_COLS["team"]).value
            if name is None or not str(name).strip():
                continue
            # 合并单元格：课别/区域只有组首行有值，空则沿用上一行
            dept = ws.cell(row=row, column=TEAM_COLS["dept"]).value
            area = ws.cell(row=row, column=TEAM_COLS["area"]).value
            if dept is not None and str(dept).strip():
                cur_dept = str(dept).strip()
            if area is not None and str(area).strip():
                cur_area = str(area).strip()
            lines = []
            for col in range(TEAM_COLS["lines_start"], TEAM_COLS["lines_end"]):
                v = ws.cell(row=row, column=col).value
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    lines.append(s)
            teams.append({
                "seq": ws.cell(row=row, column=TEAM_COLS["seq"]).value,
                "dept": cur_dept,
                "area": cur_area,
                "team": str(name).strip(),
                "count": ws.cell(row=row, column=TEAM_COLS["count"]).value,
                "lines": lines,
                "remark": ws.cell(row=row, column=TEAM_COLS["remark"]).value,
            })
        return teams

    def _parse_merge_rules(self, teams):
        """从班组备注解析拼线规则。

        规则语义：当班组 T 开线 L 时，需与伙伴班组一起生产（拼线）。
        常见句式：'开S5291 线和黄根拼'、'S2604.S2603跟郑波班组合'、
                  'S2303跟朱志红班组合,S2604需要跟郑波班组合'、'与郑阵飞班组人员合开S0205线'。
        解析失败的原备注保留 parsed=false，供人工在界面补录。
        """
        team_names = [t["team"] for t in teams]
        rules = []
        for t in teams:
            remark = t.get("remark")
            if not remark or not str(remark).strip():
                continue
            remark = str(remark)
            for seg in re.split(r"[,，;；、]", remark):
                seg = seg.strip()
                if not seg:
                    continue
                lines = re.findall(r"S\d{3,5}", seg)
                partners = [n for n in team_names if n != t["team"] and n in seg]
                if lines and partners:
                    for line in lines:
                        rules.append({
                            "team": t["team"],
                            "line": norm_line(line),
                            "partners": partners,
                            "raw": seg,
                            "parsed": True,
                        })
                else:
                    rules.append({
                        "team": t["team"],
                        "line": None,
                        "partners": [],
                        "raw": seg,
                        "parsed": False,
                    })
        return rules

    def _build_merge_lookup(self, merge):
        """拼线规则索引：{(norm_line, team): [partners...]}，合并同键、去重。"""
        lookup = {}
        for rule in merge:
            if not rule.get("parsed") or not rule.get("line"):
                continue
            key = (rule["line"], rule["team"])
            lookup.setdefault(key, [])
            for p in rule.get("partners", []):
                if p not in lookup[key]:
                    lookup[key].append(p)
        return lookup

    def _demand_worksheet(self):
        """需求数据 worksheet：预估出货量表优先；未提供时从模板内排产1 sheet 读取，识别不到直接报错。"""
        if self.demand_ws is not None:
            return self.demand_ws
        # 模板内需求 sheet：排除已作为排产2 使用的 sheet（新模板单 sheet1 是排产2 结果表，不是需求）
        norm_cands = {c.strip().replace(" ", "").lower() for c in DEMAND_SHEET_CANDIDATES}
        for name in self.wb_data.sheetnames:
            if name == self.s2_name:
                continue
            if str(name).strip().replace(" ", "").lower() in norm_cands:
                return self.wb_data[name]
        raise ValueError("未找到需求数据 sheet（排产1/SHEET1），请先在「预估出货量」页导入出货量表")

    def _week_range(self):
        """需求表周出货量列范围 (start, end)（1-based，end 不含）。

        按表头日期动态识别（最长连续日期列段）；识别失败回退固定列 52~88。
        """
        if self._week_range_cache is None:
            ws = self._demand_worksheet()
            rng = _detect_week_cols(ws)
            self._week_range_cache = rng or (DEMAND_COLS["weeks_start"], DEMAND_COLS["weeks_end"])
        return self._week_range_cache

    def _read_az_date(self):
        """排产1 第一周列（AZ）表头日期：支持 YYYYMMDD 整数或 datetime。"""
        ws = self._demand_worksheet()
        v = ws.cell(row=1, column=self._week_range()[0]).value
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, (int, float)):
            s = str(int(v)).strip()
            if len(s) == 8 and s.isdigit():
                return datetime.strptime(s, "%Y%m%d").date()
        return None

    def _derive_window(self):
        """从排产2 行3 日期动态推导排产窗口。

        规则：窗口起点 = 排产1 AZ 日期（第一期），终点 = 排产2 最后一个日期；
              备货窗口起点 = 窗口内第一个周一（stockup_start_on_monday=true）。
        返回: {start, end, days, stockup_start, stockup_days, early, night,
              stockup_early, stockup_night}
        """
        ws = self.wb_data[self.s2_name]
        date_map = {}  # date -> (early_col, night_col)
        for col in range(1, ws.max_column + 1):
            v = ws.cell(row=S2_COLS["date_row"], column=col).value
            if isinstance(v, datetime):
                date_map[v.date()] = (col, col + 1)
        if not date_map:
            raise ValueError("排产2 表行3 未找到日期列，无法确定排产窗口")

        az = self._read_az_date()
        dates = sorted(date_map)
        if az:
            dates = [d for d in dates if d >= az]
        if not dates:
            dates = sorted(date_map)  # AZ 日期不在窗口内时回退全部日期
            self.warnings.append(f"排产1 AZ 日期({az})不在排产2 日期范围内，回退使用全部日期")

        early_cols = [date_map[d][0] for d in dates]
        night_cols = [date_map[d][1] for d in dates]

        stockup_start = dates[0]
        if self.rules.get("stockup_start_on_monday", True):
            monday = next((d for d in dates if d.weekday() == 0), None)
            if monday:
                stockup_start = monday
        idx = dates.index(stockup_start)
        stockup_early = early_cols[idx:]
        stockup_night = night_cols[idx:]

        return {
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "days": len(dates),
            "stockup_start": stockup_start.isoformat(),
            "stockup_days": len(stockup_early),
            "early": early_cols,
            "night": night_cols,
            "stockup_early": stockup_early,
            "stockup_night": stockup_night,
        }

    def _read_demand(self):
        """读取排产1 需求数据，返回 {sap: {row, g, o, ay, weeks}}。

        过滤规则：从第一周起连续 1~3 周出货量均为 0/空 的记录不纳入需求（导入时已过滤，此处兜底）。
        库存列：优先「差异量减排产量」；该单元格为 #N/A/空 时（因生产线量为 #N/A 导致公式结果失效），
        回退取「预估减出货量减库存量」列的值。
        """
        if self._demands_cache is not None:
            return self._demands_cache
        ws = self._demand_worksheet()
        week_start, week_end = self._week_range()
        diff_col, estimate_col, line_col = _detect_inventory_cols(ws)
        demands = {}
        zero_skipped = 0
        for row in range(1, ws.max_row + 1):
            sap = ws.cell(row=row, column=DEMAND_COLS["sap"]).value
            if sap is None:
                continue
            sap_str = _norm_sap(sap)
            if not sap_str.startswith("800"):
                continue
            weeks = []
            for col in range(week_start, week_end):
                weeks.append(_num(ws.cell(row=row, column=col).value))
            # 前3周均无出货 → 过滤（不足3周则按实际周数列判断）
            if weeks[:3] and not any(weeks[:3]):
                zero_skipped += 1
                continue

            # 库存：优先取「差异量减排产量」；当生产线量为 #N/A 时回退「预估减出货量减库存量」
            line_val = _to_number(ws.cell(row=row, column=line_col).value)
            estimate_val = _to_number(ws.cell(row=row, column=estimate_col).value)
            diff_val = _to_number(ws.cell(row=row, column=diff_col).value)
            if not _is_na(line_val) and line_val is not None:
                ay = (estimate_val - line_val) if estimate_val is not None else diff_val
            else:
                ay = estimate_val if estimate_val is not None else diff_val

            demands[sap_str] = {
                "row": row,
                "g": ws.cell(row=row, column=DEMAND_COLS["g"]).value,
                "o": ws.cell(row=row, column=DEMAND_COLS["o"]).value,
                "ay": _num(ay),
                "weeks": weeks,
            }
        if zero_skipped:
            self.warnings.append(f"已过滤前3周无出货的需求记录 {zero_skipped} 条")
        self._demands_cache = demands
        return demands

    # ------------------------------------------------------------------
    # 排产计算
    # ------------------------------------------------------------------
    def calculate_schedule(self, sap, demand):
        """计算单个物料排产计划（规则1/2/3/4/6/7/8/9）。

        返回: (schedule_dict, error_str)；error_str 非空表示该物料不排产。
        """
        g_val = str(demand.get("g") or "")
        o_val = str(demand.get("o") or "")
        ay = demand.get("ay") or 0
        weeks = demand.get("weeks") or []
        w1 = weeks[0] if weeks else 0
        w2 = weeks[1] if len(weeks) > 1 else 0

        # 规则2：需排产量 = W1 + W2 + 库存（差异量减排产量）。
        # 客户符号约定：该值为负表示有库存，为正表示缺货，因此直接相加。
        need = w1 + w2 + ay
        if need <= 0:
            return None, f"库存充足无需排产(需排产量={need:.0f}<=0)"

        # 规则8：是否备货提前排产（G列含周期关键字 或 O列含提前备货关键字）
        stockup_kws = self.rules.get("stockup_keywords", [])
        trigger_kws = self.rules.get("stockup_trigger_keywords", [])
        has_stockup = bool(g_val and any(k in g_val for k in stockup_kws)) or bool(
            o_val and any(k in o_val for k in trigger_kws))

        # 规则7：设变检查（设变且非备货 → 不排产）
        shebian_kw = self.rules.get("shebian_keyword", "")
        has_shebian = bool(shebian_kw and shebian_kw in o_val)
        if has_shebian and not has_stockup:
            return None, f"设变物料不排产(O={o_val})"

        # 产能检查
        lines = self.capacity.get(sap, [])
        if not lines:
            return None, "产能表中无此物料"

        # 规则4：取优先级前 N 名产线
        max_prio = self.rules.get("max_priority", 2)
        active = [l for l in lines if l["priority"] <= max_prio]
        if not active:
            return None, f"无可用产线(优先级均高于{max_prio})"

        # 窗口选择
        if has_stockup:
            early_cols = self.window["stockup_early"]
            night_cols = self.window["stockup_night"]
            reason = (f"备货{g_val}提前排产(从{self.window['stockup_start']}起,"
                      f"{len(early_cols)}天)")
        else:
            early_cols = self.window["early"]
            night_cols = self.window["night"]
            reason = f"正常排产(从{self.window['start']}起,{len(early_cols)}天)"

        # 规则9/6：夜班触发
        total_cap = sum(l["capacity"] for l in active)
        trigger_night = False
        night_why = ""
        if self.rules.get("night_enabled", True):
            if (has_stockup or (o_val and any(k in o_val for k in trigger_kws))) and w1 + w2 > 0:
                divisor = self.rules.get("night_divisor", 14)
                daily_need = (w1 + w2) / divisor
                if daily_need > total_cap:
                    trigger_night = True
                    night_why = (f"夜班触发((W1+W2)/{divisor}={daily_need:.0f} > 日产能{total_cap:.0f})")
            if not trigger_night and self.rules.get("night_basic_enabled", True) and w1 > 0:
                total_early = total_cap * len(early_cols)
                if total_early < w1:
                    trigger_night = True
                    night_why = f"夜班触发(早班总量{total_early:.0f}<W1={w1:.0f})"
        if trigger_night:
            reason += " + " + night_why

        return {
            "sap": sap,
            "need": need,
            "w1": w1,
            "w2": w2,
            "ay": ay,
            "g": g_val,
            "o": o_val,
            "stockup": has_stockup,
            "shebian": has_shebian,
            "reason": reason,
            "active": active,
            "early_cols": early_cols,
            "night_cols": night_cols,
            "trigger_night": trigger_night,
        }, None

    def fill_quantities(self, schedule):
        """规则5：逐日填入产能，剩余量 < 当日产能时填剩余量，次日填 0。

        返回 fill 列表：[{line, capacity, early: {col: qty}, night: {col: qty},
                         early_days, night_days}]
        """
        lines = schedule["active"]
        early_cols = schedule["early_cols"]
        night_cols = schedule["night_cols"] if schedule["trigger_night"] else []
        remaining = schedule["need"]

        fill = [{
            "line": l["line"],
            "capacity": l["capacity"],
            "early": {},
            "night": {},
            "early_days": 0,
            "night_days": 0,
        } for l in lines]

        # 早班逐日填充
        for col in early_cols:
            if remaining <= 0:
                break
            for item in fill:
                qty = min(item["capacity"], remaining)
                if qty > 0:
                    item["early"][col] = qty
                    item["early_days"] += 1
                    remaining -= qty
                if remaining <= 0:
                    break

        # 夜班逐日填充（仅当触发且早班仍未覆盖）
        for col in night_cols:
            if remaining <= 0:
                break
            for item in fill:
                qty = min(item["capacity"], remaining)
                if qty > 0:
                    item["night"][col] = qty
                    item["night_days"] += 1
                    remaining -= qty
                if remaining <= 0:
                    break

        return fill

    def _team_candidates(self, norm, line_dept=None, line_area=None):
        """返回可开该产线的班组；按 同区域 → 同课别 → 跨课 优先，同级内第一产线优先。

        无课别/区域信息（旧配置/旧模板）时保持原逻辑（仅按产线偏好）。
        """
        cands = []
        for team in self.teams:
            for i, line in enumerate(team.get("lines") or []):
                if norm_line(line) == norm:
                    if not line_dept and not line_area:
                        group = 0  # 无区域/课别信息 → 保持原逻辑
                    elif team.get("area") and line_area and str(team.get("area")) == str(line_area):
                        group = 0  # 同区域（最优先）
                    elif team.get("dept") and line_dept and str(team.get("dept")) == str(line_dept):
                        group = 1  # 同课别
                    else:
                        group = 2  # 跨课
                    cands.append((group, i, team["team"]))
                    break
        cands.sort(key=lambda x: (x[0], x[1]))
        return [t for _, _, t in cands]

    def _pick_team(self, norm, used, exclude=None, line_dept=None, line_area=None):
        for name in self._team_candidates(norm, line_dept, line_area):
            if name in used or (exclude and name == exclude):
                continue
            return name
        return None

    def _line_region(self, sap, line):
        """排产2 表中该 SAP+产线行的课别/区域（供 同区域→同课别→跨课 优先分配）。"""
        row = self._find_row_in_schedule2(sap, line)
        if row is None:
            return None, None
        ws = self.wb_data[self.s2_name]
        dept = ws.cell(row=row, column=S2_COLS["dept"]).value
        area = ws.cell(row=row, column=S2_COLS["area"]).value
        return (str(dept).strip() if dept is not None else None,
                str(area).strip() if area is not None else None)

    def assign_teams(self, schedules):
        """规则10：产线→班组分配。

        分配优先级：同区域 → 同课别 → 跨课（按排产2 行课别/区域），
        含拼线规则强约束与占用冲突处理。
        """
        used = set()
        for sch in schedules:
            for item in sch["fill"]:
                norm = norm_line(item["line"])
                line_dept, line_area = self._line_region(sch["sap"], item["line"])
                cands = self._team_candidates(norm, line_dept, line_area)  # 区域→课别→跨课 偏好
                preferred = cands[0] if cands else None  # 理想班组
                team = self._pick_team(norm, used, line_dept=line_dept, line_area=line_area)
                partners = (self.merge_lookup.get((norm, team), []) if team else [])
                team_list = list(dict.fromkeys([team] + [p for p in partners if p != team])) if team else []
                if team and all(t not in used for t in team_list):
                    item["team"] = team_list
                    used.update(team_list)
                    if team != preferred:
                        item["note"] = f"原班组{preferred}已用于其他物料/产线，改用替代班组{team}"
                else:
                    # 原班组被占用（或拼线伙伴被占用）→ 尝试替代班组
                    alt = self._pick_team(norm, used, exclude=team, line_dept=line_dept, line_area=line_area)
                    alt_partners = (self.merge_lookup.get((norm, alt), []) if alt else [])
                    alt_list = list(dict.fromkeys([alt] + [p for p in alt_partners if p != alt])) if alt else []
                    if alt and all(t not in used for t in alt_list):
                        item["team"] = alt_list
                        used.update(alt_list)
                        item["conflict"] = f"原班组{team or preferred}已占用，改用替代班组{alt}"
                    else:
                        item["team"] = []
                        item["conflict"] = (f"班组占用冲突(产线{item['line']}): "
                                            f"原班组{team or preferred or '?'}及其替代班组均不可用，待人工处理")

    # ------------------------------------------------------------------
    # 写表
    # ------------------------------------------------------------------
    def _find_row_in_schedule2(self, sap, line):
        """在排产2 表查找指定物料+产线的数据行。"""
        ws = self.wb_data[self.s2_name]
        line_norm = norm_line(line)
        for row in range(S2_COLS["data_start"], ws.max_row + 1):
            row_line = ws.cell(row=row, column=S2_COLS["line"]).value
            row_sap = ws.cell(row=row, column=S2_COLS["sap"]).value
            if row_line is None or row_sap is None:
                continue
            if norm_line(row_line) == line_norm and str(row_sap).strip().endswith(sap[-4:]):
                return row
        return None

    def write(self, output_path, schedules):
        """将排产结果写入输出文件（模板副本，保留公式），返回写入行数与告警。"""
        ws = self.wb_formula[self.s2_name]
        min_col = min(self.window["early"])
        max_col = max(self.window["night"])
        written = 0
        for sch in schedules:
            for item in sch["fill"]:
                row = self._find_row_in_schedule2(sch["sap"], item["line"])
                if row is None:
                    self.warnings.append(f"排产2 表未找到 {sch['sap']}/{item['line']} 行，已跳过")
                    continue
                # 清空本窗口列（不触碰窗口外的历史数据）
                for col in range(min_col, max_col + 1):
                    ws.cell(row=row, column=col).value = None
                for col, qty in item["early"].items():
                    ws.cell(row=row, column=col).value = qty
                for col, qty in item["night"].items():
                    ws.cell(row=row, column=col).value = qty
                if item.get("team"):
                    ws.cell(row=row, column=S2_COLS["team"]).value = "+".join(item["team"])
                written += 1

        # A~P 列（前 16 列）冻结，滚动时保持客户/产线/SAP 等元数据列可见
        ws.freeze_panes = "Q1"

        wb_out = self.wb_formula
        wb_out.save(output_path)
        return written

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def build(self, target_sap=None):
        """计算全部物料的排产计划（含班组分配）。

        返回: (schedules, skipped)
        """
        demands = self._read_demand()
        schedules = []
        skipped = []
        for sap in sorted(demands.keys()):
            if target_sap and sap != target_sap:
                continue
            demand = demands[sap]
            schedule, err = self.calculate_schedule(sap, demand)
            if err is not None:
                skipped.append({"sap": sap, "reason": err})
                continue
            schedule["fill"] = self.fill_quantities(schedule)
            schedules.append(schedule)
        self.assign_teams(schedules)
        return schedules, skipped

    def to_config_payload(self):
        """配置提取模式：仅返回产能/班组/拼线配置（不排产、不推导窗口/需求）。"""
        return {
            "status": "success",
            "capacity": self.capacity,
            "teams": self.teams,
            "merge_rules": self.merge,
            "warnings": self.warnings,
        }

    def to_parse_payload(self, target_sap=None):
        """解析预览模式：返回结构化 JSON 数据（不写文件）。"""
        schedules, skipped = self.build(target_sap)
        return {
            "status": "success",
            "window": self.window,
            "rules": self.rules,
            "capacity": self.capacity,
            "teams": self.teams,
            "merge_rules": self.merge,
            "demands": [self._schedule_to_dict(s) for s in schedules],
            "skipped": skipped,
            "missing_capacity": self._missing_capacity(schedules, skipped),
            "warnings": self.warnings,
        }

    def to_run_payload(self, output_path, target_sap=None):
        """执行排产模式：写文件并返回结构化 JSON。"""
        schedules, skipped = self.build(target_sap)
        written = self.write(output_path, schedules)
        return {
            "status": "success",
            "output": output_path,
            "written_rows": written,
            "window": self.window,
            "rules": self.rules,
            "results": [self._schedule_to_dict(s) for s in schedules],
            "skipped": skipped,
            "warnings": self.warnings,
        }

    def _schedule_to_dict(self, sch):
        """排产计划转可序列化 dict。"""
        fill = []
        for item in sch["fill"]:
            fill.append({
                "line": item["line"],
                "capacity": item["capacity"],
                "early_days": item["early_days"],
                "night_days": item["night_days"],
                "early_total": sum(item["early"].values()),
                "night_total": sum(item["night"].values()),
                "team": item.get("team", []),
                "conflict": item.get("conflict"),
                "note": item.get("note"),
            })
        return {
            "sap": sch["sap"],
            "g": sch["g"],
            "o": sch["o"],
            "ay": sch["ay"],
            "w1": sch["w1"],
            "w2": sch["w2"],
            "need": round(sch["need"], 2),
            "stockup": sch["stockup"],
            "shebian": sch["shebian"],
            "trigger_night": sch["trigger_night"],
            "reason": sch["reason"],
            "lines": fill,
        }

    def _missing_capacity(self, schedules, skipped):
        """产能表缺失物料的 SAP 号列表（含需求但无产能配置）。"""
        demands = self._demands_cache if self._demands_cache is not None else self._read_demand()
        missing = []
        for sap in sorted(demands.keys()):
            if sap not in self.capacity:
                missing.append(sap)
        return missing


def _load_json(path):
    """读取 JSON 文件，失败返回 None。"""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"WARNING: 配置文件不存在，已忽略: {path}", file=sys.stderr)
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="气袋生产排产引擎")
    sub = parser.add_subparsers(dest="mode", required=True)

    for name in ("parse", "run"):
        p = sub.add_parser(name, help=f"{'解析预览' if name == 'parse' else '执行排产'}")
        p.add_argument("--excel", required=True, help="生产排产模板 Excel 路径")
        p.add_argument("--config", help="排产规则配置 rules.json")
        p.add_argument("--capacity", help="产能配置 capacity.json")
        p.add_argument("--teams", help="班组产线配置 teams.json")
        p.add_argument("--merge", help="拼线规则配置 merge_rules.json")
        p.add_argument("--demands", help="预估出货量表 Excel 路径（含排产1 sheet），缺省回退模板内排产1")
        p.add_argument("--material", help="仅排产指定 SAP 物料号")
        p.add_argument("--config-only", action="store_true",
                       help="仅提取产能/班组/拼线配置（不排产），供「从 Excel 同步配置」使用")
        p.add_argument("--json", action="store_true", help="输出 JSON 结果")
        if name == "run":
            p.add_argument("--output", required=True, help="输出 Excel 路径")

    args = parser.parse_args()

    try:
        rules = _load_json(args.config)
        capacity = _load_json(args.capacity)
        teams = _load_json(args.teams)
        merge = _load_json(args.merge)

        scheduler = ProductionScheduler(
            args.excel,
            rules=rules,
            capacity=capacity,
            teams=teams,
            merge=merge,
            demands_path=args.demands,
            config_only=bool(getattr(args, "config_only", False)),
        )

        if args.mode == "parse":
            if getattr(args, "config_only", False):
                payload = scheduler.to_config_payload()
            else:
                payload = scheduler.to_parse_payload(target_sap=args.material)
        else:
            output = str(args.output)
            if Path(output).exists():
                Path(output).unlink()
            shutil.copy2(args.excel, output)  # 模板副本 → 输出文件，模板本身不修改
            payload = scheduler.to_run_payload(output, target_sap=args.material)

        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            # 人类可读摘要输出到 stdout（供直接运行查看）
            print(f"排产窗口: {payload['window']['start']} ~ {payload['window']['end']} "
                  f"({payload['window']['days']}天, 备货自{payload['window']['stockup_start']}起 "
                  f"{payload['window']['stockup_days']}天)")
            for r in payload.get("results", payload.get("demands", [])):
                for l in r.get("lines", []):
                    print(f"  {r['sap']}/{l['line']}: 早{l['early_days']}天+夜{l['night_days']}天 "
                          f"班组={'+'.join(l.get('team') or []) or '?'} 原因:{r['reason']}")
            for s in payload.get("skipped", []):
                print(f"  跳过 {s['sap']}: {s['reason']}")
            for w in payload.get("warnings", []):
                print(f"  WARNING: {w}")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
