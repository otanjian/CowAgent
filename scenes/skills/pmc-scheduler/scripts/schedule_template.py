# -*- coding: utf-8 -*-
"""
PMC智能排程 - 标准化脚本模板
========================================
使用说明：
1. 将排产数据写入 JSON 文件（格式见下方 INPUT_SCHEMA）
2. 修改本文件底部的 INPUT_FILE 路径
3. 运行 python schedule_template.py
4. 输出：排程结果JSON + 甘特图HTML（使用技能包模板）

AI生成排程脚本时，应优先基于此模板修改，而非从零编写。
这样可以保证：数据结构统一、班次/换线逻辑正确、datetime序列化安全、
HTML输出使用技能包双视图模板。
"""

import json
import math
import random
import os
from datetime import datetime, timedelta, time
from collections import defaultdict
from copy import deepcopy
from typing import List, Dict, Any, Tuple

# ============================================================
# 0. 输入数据结构 (INPUT_SCHEMA)
# ============================================================
# {
#   "version": "1.0",
#   "params": {
#     "mode": "forward" | "backward",
#     "objective": "tardiness" | "makespan" | "cost" | "balanced",
#     "iterations": 500
#   },
#   "orders": [
#     {
#       "工单号": "WO001",
#       "产品名称": "电机壳体",
#       "数量": 100,
#       "交期": "2026-06-20",
#       "优先级": "紧急",          // 紧急 | 高 | 中 | 低
#       "设备需求": "注塑机",
#       "换线时间": 60,             // 分钟
#       "班组需求": "白班",         // 白班 | 夜班 | 两班倒
#       "备注": "",
#       "工序路线": [
#         {"process_name": "注塑", "time_per_unit": 3, "machine_id": "M001", "sequence": 1}
#       ],
#       "BOM物料清单": [
#         {"material_name": "原材料A", "quantity": 2.5, "unit": "kg"}
#       ]
#     }
#   ],
#   "machines": [
#     {"machine_id": "M001", "machine_name": "注塑机-1", "capacity_per_day": 500}
#   ],
#   "teams": [
#     {"team_name": "白班A组", "headcount": 12, "shift": "day"}
#   ],
#   "materials": [
#     {"material_name": "原材料A", "stock": 1000, "on_the_way": 500, "lead_time": 3}
#   ]
# }

# ============================================================
# 1. 配置与工具函数
# ============================================================

PRIORITY_MAP = {'紧急': 0, '高': 1, '中': 2, '低': 3}

SHIFT_RANGES = {
    '白班': [
        (time(8, 0), time(12, 0)),
        (time(13, 0), time(17, 0))
    ],
    '夜班': [
        (time(20, 0), time(23, 59)),
        (time(0, 0), time(5, 0))
    ],
    '两班倒': [
        (time(8, 0), time(12, 0)),
        (time(13, 0), time(17, 0)),
        (time(18, 0), time(22, 0)),
        (time(23, 0), time(3, 0))
    ]
}

START_TIME = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)


def _to_iso(dt_obj) -> str:
    """安全地将datetime转为ISO格式字符串"""
    if isinstance(dt_obj, datetime):
        return dt_obj.isoformat()
    return str(dt_obj)


def _parse_date(s: str) -> datetime:
    """解析日期字符串为datetime"""
    s = str(s).strip()
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y/%m/%d %H:%M', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # 纯日期默认到当天23:59
    try:
        return datetime.strptime(s, '%Y-%m-%d').replace(hour=23, minute=59)
    except ValueError:
        return START_TIME + timedelta(days=7)


def align_to_working_time(dt: datetime, shift_type: str = '白班') -> datetime:
    """将时间对齐到下一个可用工作时段"""
    current = dt
    ranges = SHIFT_RANGES.get(shift_type, SHIFT_RANGES['白班'])
    for _ in range(30):  # 最多找30天
        current_date = current.date()
        candidates = []
        for day_offset in [0, 1]:
            d = current_date + timedelta(days=day_offset)
            for st, et in ranges:
                seg_start = datetime.combine(d, st)
                seg_end = datetime.combine(d, et)
                if et <= st:
                    seg_end += timedelta(days=1)
                if seg_start <= current < seg_end:
                    return current
                if current < seg_start:
                    candidates.append(seg_start)
        if candidates:
            return min(candidates)
        current = datetime.combine(current_date + timedelta(days=1), time(0, 0))
    return dt


def add_working_minutes(dt: datetime, minutes: float, shift_type: str = '白班') -> datetime:
    """从给定时间开始，累加工作分钟数，返回结束时间"""
    remaining = float(minutes)
    current = align_to_working_time(dt, shift_type)
    max_iter = 5000
    iter_count = 0
    while remaining > 1e-6 and iter_count < max_iter:
        iter_count += 1
        ranges = SHIFT_RANGES.get(shift_type, SHIFT_RANGES['白班'])
        current_date = current.date()
        found = False
        for st, et in ranges:
            seg_start = datetime.combine(current_date, st)
            seg_end = datetime.combine(current_date, et)
            if et <= st:
                seg_end += timedelta(days=1)
            if current < seg_start:
                current = seg_start
            if seg_start <= current < seg_end:
                avail = (seg_end - current).total_seconds() / 60.0
                if remaining <= avail:
                    current = current + timedelta(minutes=remaining)
                    remaining = 0
                    found = True
                    break
                else:
                    remaining -= avail
                    current = seg_end
                    found = True
                    break
        if not found:
            current = datetime.combine(current_date + timedelta(days=1), time(0, 0))
    return current


# ============================================================
# 2. 排程核心算法
# ============================================================

def dispatch_orders(orders: List[Dict], start_time: datetime = None) -> Dict:
    """
    按工单顺序分配到设备，考虑：
    - 工序先后依赖（前序完成后才能开始下一工序）
    - 设备独占（同一设备同一时间只能做一个工单）
    - 换线时间（不同产品切换时需要换线）
    - 班组工作时段（白班/夜班/两班倒）
    """
    if start_time is None:
        start_time = START_TIME

    machine_avail = {}      # machine_id -> 下次可用时间
    machine_last_product = {}  # machine_id -> 上次加工的产品
    schedule_items = []

    for order in orders:
        order_id = order.get('工单号') or order.get('order_id') or order.get('id') or order.get('wo')
        product = order.get('产品名称') or order.get('product')
        qty = float(order.get('数量') or order.get('qty') or 0)
        shift = order.get('班组需求') or order.get('shift') or '白班'
        changeover = float(order.get('换线时间') or order.get('setup') or order.get('changeover') or 60)
        due = _parse_date(order.get('交期') or order.get('due'))
        priority = order.get('优先级') or order.get('priority') or '中'
        priority_val = PRIORITY_MAP.get(priority, 2)

        processes = order.get('工序路线') or order.get('processes') or order.get('route') or []
        processes = sorted(processes, key=lambda p: p.get('sequence', 0) or p.get('seq', 0) or 0)

        prev_end = None
        for proc in processes:
            proc_name = proc.get('process_name') or proc.get('process')
            machine = proc.get('machine_id') or proc.get('machine')
            tpu = float(proc.get('time_per_unit') or proc.get('tpu') or proc.get('per_unit', 0))

            # 批量炉时处理：热处理炉等设备按炉次计算，不按单件
            if machine and '热处理' in str(machine):
                batch_size = float(proc.get('batch_size', 8))  # 默认一炉8件
                batches = math.ceil(qty / batch_size)
                duration = tpu * batches  # tpu 在此表示每炉时间
            else:
                duration = tpu * qty

            # 最早可开始 = 前序结束（如有）vs 设备可用
            if prev_end is None:
                earliest = start_time
            else:
                earliest = prev_end

            # 设备可用时间（含换线）
            if machine in machine_avail:
                co = changeover if machine_last_product.get(machine) != product else 0
                avail = add_working_minutes(machine_avail[machine], co, shift)
                actual_start = max(earliest, avail)
            else:
                actual_start = earliest

            actual_start = align_to_working_time(actual_start, shift)
            end_time = add_working_minutes(actual_start, duration, shift)

            item = {
                'order_id': order_id,
                'product': product,
                'process': proc_name,
                'process_name': proc_name,
                'machine': machine,
                'machine_id': machine,
                'qty': qty,
                'shift': shift,
                'priority': priority,
                'priority_val': priority_val,
                'due': due,
                'start_time': actual_start,
                'end_time': end_time,
                'duration_min': duration,
                'changeover_min': changeover if machine in machine_avail else 0,
                'is_delayed': end_time > due,
                'status': 'delayed' if end_time > due else 'ok'
            }
            schedule_items.append(item)
            machine_avail[machine] = end_time
            machine_last_product[machine] = product
            prev_end = end_time

    # 按开始时间排序
    schedule_items.sort(key=lambda x: x['start_time'])

    # 工单级统计
    order_finish = defaultdict(lambda: start_time)
    for item in schedule_items:
        order_finish[item['order_id']] = max(order_finish[item['order_id']], item['end_time'])

    order_delays = {}
    for order in orders:
        oid = order.get('工单号') or order.get('order_id') or order.get('id') or order.get('wo')
        due = _parse_date(order.get('交期') or order.get('due'))
        finish = order_finish[oid]
        delay_min = max(0, (finish - due).total_seconds() / 60.0)
        order_delays[oid] = {
            'order_id': oid,
            'product': order.get('产品名称') or order.get('product'),
            'qty': float(order.get('数量') or order.get('qty') or 0),
            'due': _to_iso(due),
            'finish': _to_iso(finish),
            'delay_min': delay_min,
            'delay_hours': delay_min / 60.0,
            'status': '延期' if delay_min > 0 else '准时'
        }

    # 设备负载
    machine_stats = defaultdict(lambda: {'total_min': 0, 'items': 0})
    for item in schedule_items:
        m = item['machine']
        machine_stats[m]['total_min'] += item['duration_min']
        machine_stats[m]['items'] += 1

    return {
        'schedule': schedule_items,
        'machine_stats': dict(machine_stats),
        'order_delays': order_delays,
        'start_time': start_time,
        'total_items': len(schedule_items)
    }


def evaluate_schedule(result: Dict, objective: str = 'tardiness') -> float:
    """评估排程结果，返回惩罚值（越小越好）"""
    penalty = 0.0
    schedule = result['schedule']
    order_delays = result['order_delays']

    # 延期惩罚
    for oid, info in order_delays.items():
        if info['delay_min'] > 0:
            # 优先级越高，惩罚越大
            priority_items = [s for s in schedule if s['order_id'] == oid]
            priority_val = priority_items[0]['priority_val'] if priority_items else 2
            weight = (4 - priority_val) * 10
            penalty += info['delay_min'] * weight

    if objective == 'makespan':
        # 最小化总工期
        if schedule:
            makespan = max(s['end_time'] for s in schedule) - result['start_time']
            penalty += makespan.total_seconds() / 60.0

    if objective in ('cost', 'balanced'):
        # 负载均衡惩罚
        loads = [v['total_min'] for v in result['machine_stats'].values()]
        if loads:
            avg = sum(loads) / len(loads)
            imbalance = sum((l - avg) ** 2 for l in loads) / len(loads)
            penalty += imbalance * 0.1

    return penalty


def optimize_schedule(orders: List[Dict], objective: str = 'tardiness',
                      iterations: int = 500, start_time: datetime = None) -> Tuple[List[Dict], Dict]:
    """
    使用禁忌搜索优化工单排序
    返回：(最优工单顺序, 排程结果)
    """
    if start_time is None:
        start_time = START_TIME

    # 初始解：按优先级 + EDD（最早交期优先）
    base = sorted(orders, key=lambda o: (
        PRIORITY_MAP.get(o.get('优先级') or o.get('priority') or '中', 2),
        _parse_date(o.get('交期') or o.get('due') or '2099-12-31')
    ))

    n = len(base)
    if n == 0:
        return [], {}
    if n == 1 or iterations <= 0:
        result = dispatch_orders(base, start_time)
        return base, result

    best_order = deepcopy(base)
    best_result = dispatch_orders(best_order, start_time)
    best_score = evaluate_schedule(best_result, objective)

    random.seed(42)
    cur_order = deepcopy(base)
    tabu = set()

    for it in range(iterations):
        i, j = random.sample(range(n), 2)
        cand = deepcopy(cur_order)
        cand[i], cand[j] = cand[j], cand[i]

        move_key = f"{min(i,j)}-{max(i,j)}"
        if move_key in tabu:
            continue

        cand_result = dispatch_orders(cand, start_time)
        cand_score = evaluate_schedule(cand_result, objective)

        if cand_score < best_score:
            best_score = cand_score
            best_order = deepcopy(cand)
            best_result = cand_result
            cur_order = deepcopy(cand)
        elif random.random() < 0.05:  # 5%概率接受差解，跳出局部最优
            cur_order = deepcopy(cand)

        tabu.add(move_key)
        if len(tabu) > 30:
            tabu = set(list(tabu)[-30:])

    return best_order, best_result


# ============================================================
# 3. 物料需求计算
# ============================================================

def calculate_material_plan(orders: List[Dict], materials: List[Dict],
                            schedule: List[Dict]) -> List[Dict]:
    """
    计算物料需求计划：
    - 汇总每个物料的总需求
    - 结合库存、在途、提前期计算缺口
    - 给出建议采购量和建议到货日期
    """
    # 建立物料库存索引
    mat_inventory = {}
    for m in materials:
        name = m.get('material_name') or m.get('物料名称')
        if name:
            mat_inventory[name] = {
                'stock': float(m.get('stock') or m.get('当前库存') or m.get('库存') or 0),
                'on_the_way': float(m.get('on_the_way') or m.get('在途数量') or m.get('在途') or 0),
                'lead_time': int(m.get('lead_time') or m.get('提前期') or 7),
                'safety_stock': float(m.get('safety_stock') or m.get('安全库存') or 0),
                'unit': m.get('unit') or m.get('单位') or ''
            }

    # 汇总需求
    demand = defaultdict(float)
    demand_first_need = {}
    order_map = {o.get('工单号') or o.get('order_id') or o.get('id') or o.get('wo'): o for o in orders}

    for item in schedule:
        oid = item['order_id']
        order = order_map.get(oid)
        if not order:
            continue
        bom = order.get('BOM物料清单') or order.get('bom') or []
        first_start = min(
            (s['start_time'] for s in schedule if s['order_id'] == oid),
            default=START_TIME
        )
        for b in bom:
            name = b.get('material_name') or b.get('物料名称')
            qty = float(b.get('quantity') or b.get('用量') or b.get('qty') or 0)
            unit = b.get('unit') or b.get('单位') or ''
            if name:
                demand[name] += qty * item['qty']
                if name not in demand_first_need or first_start < demand_first_need[name]:
                    demand_first_need[name] = first_start

    # 生成物料计划行
    plan = []
    for name, total_demand in sorted(demand.items()):
        info = mat_inventory.get(name, {})
        stock = info.get('stock', 0)
        otw = info.get('on_the_way', 0)
        lead = info.get('lead_time', 7)
        safety = info.get('safety_stock', 0)
        unit = info.get('unit', '')
        avail = stock + otw
        gap = max(0, total_demand - avail)
        first_need = demand_first_need.get(name, START_TIME)

        # 建议到货日期 = 最早需求 - 提前期（至少今天）
        suggest_arrival = max(
            START_TIME,
            first_need - timedelta(days=lead)
        ) if gap > 0 else first_need

        # 生成预警信息
        warnings = []
        if gap > 0:
            if avail == 0:
                warnings.append('缺料')
            else:
                warnings.append('库存不足')
        if safety > 0 and avail < safety:
            warnings.append(f'低于安全库存({safety})')
        
        plan.append({
            'material': name,
            'unit': unit,
            'demand': round(total_demand, 2),
            'stock': round(stock, 2),
            'on_the_way': round(otw, 2),
            'available': round(avail, 2),
            'gap': round(gap, 2),
            'safety_stock': round(safety, 2),
            'lead_time': lead,
            'first_need': _to_iso(first_need),
            'suggest_purchase': round(gap, 2),
            'suggest_arrival': _to_iso(suggest_arrival),
            'warning': '; '.join(warnings)
        })

    return plan


# ============================================================
# 4. 输出与HTML生成
# ============================================================

def build_summary(result: Dict, orders: List[Dict]) -> Dict:
    """构建排产摘要"""
    order_delays = result.get('order_delays', {})
    total = len(order_delays)
    on_time = sum(1 for v in order_delays.values() if v['delay_min'] == 0)
    delayed = total - on_time
    schedule = result.get('schedule', [])
    start = result.get('start_time', START_TIME)
    finish = max((s['end_time'] for s in schedule), default=start) if schedule else start
    makespan = finish - start

    # 瓶颈设备
    machine_stats = result.get('machine_stats', {})
    bottleneck = max(machine_stats.items(), key=lambda x: x[1]['total_min']) if machine_stats else ('', {'total_min': 0})

    return {
        'total_orders': total,
        'on_time': on_time,
        'on_time_rate': on_time / total if total else 0,
        'delayed_orders': delayed,
        'makespan_days': makespan.total_seconds() / 86400.0,
        'total_items': len(schedule),
        'start': _to_iso(start),
        'finish': _to_iso(finish),
        'bottleneck': bottleneck[0],
        'bottleneck_hours': bottleneck[1]['total_min'] / 60.0
    }


def serialize_for_json(obj: Any) -> Any:
    """递归地将对象中的datetime转为字符串"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_for_json(v) for v in obj]
    return obj


def generate_outputs(result: Dict, material_plan: List[Dict],
                     orders: List[Dict], out_dir: str = 'tmp') -> Dict:
    """
    生成标准输出：
    - result.json: 完整排程结果（JSON序列化安全）
    - summary.json: 摘要
    - gantt.html: 双视图甘特图（使用技能包模板）
    - 返回结果字典供调用方使用
    """
    os.makedirs(out_dir, exist_ok=True)

    summary = build_summary(result, orders)
    schedule = result.get('schedule', [])

    # 安全序列化排程明细
    schedule_safe = []
    for s in schedule:
        schedule_safe.append({
            'order_id': s['order_id'],
            'product': s['product'],
            'process': s['process'],
            'process_name': s['process_name'],
            'machine': s['machine'],
            'machine_id': s['machine_id'],
            'qty': s['qty'],
            'priority': s['priority'],
            'shift': s['shift'],
            'start': _to_iso(s['start_time']),
            'end': _to_iso(s['end_time']),
            'duration_min': s['duration_min'],
            'is_delayed': s['is_delayed'],
            'status': s['status']
        })

    output = {
        'summary': summary,
        'schedule': schedule_safe,
        'order_delays': serialize_for_json(result.get('order_delays', {})),
        'machine_stats': serialize_for_json(result.get('machine_stats', {})),
        'material_plan': serialize_for_json(material_plan)
    }

    result_path = os.path.join(out_dir, 'schedule_result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    summary_path = os.path.join(out_dir, 'schedule_summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 生成甘特图 HTML
    html_path = generate_gantt_html(schedule_safe, material_plan, summary, out_dir)
    if html_path:
        output['html_path'] = html_path

    return output


# ============================================================
# 4.5 甘特图 HTML 生成（基于技能包模板）
# ============================================================

def _load_gantt_template() -> str:
    """加载技能包甘特图模板"""
    possible_paths = [
        os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'gantt_template.html'),
        os.path.join(os.path.dirname(__file__), '..', '..', 'gantt_template.html'),
        os.path.join(os.getcwd(), 'skills', 'pmc-scheduler', 'assets', 'gantt_template.html'),
        'skills/pmc-scheduler/assets/gantt_template.html',
    ]
    for path in possible_paths:
        path = os.path.normpath(path)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
    return None


def generate_gantt_html(schedule: List[Dict], material_plan: List[Dict],
                        summary: Dict, out_dir: str = 'tmp') -> str:
    """
    使用技能包模板生成双视图甘特图 HTML
    返回生成的 HTML 文件路径
    """
    template = _load_gantt_template()
    if not template:
        print('[WARN] 找不到技能包甘特图模板，跳过 HTML 生成')
        return None

    # 生成订单视图表格行
    gantt_rows = []
    for s in schedule:
        start_dt = datetime.fromisoformat(s['start'].replace('Z', '+00:00')) if isinstance(s['start'], str) else s['start']
        end_dt = datetime.fromisoformat(s['end'].replace('Z', '+00:00')) if isinstance(s['end'], str) else s['end']
        start_str = start_dt.strftime('%m-%d %H:%M') if isinstance(start_dt, datetime) else str(start_dt)
        end_str = end_dt.strftime('%m-%d %H:%M') if isinstance(end_dt, datetime) else str(end_dt)
        status_class = 'status-shortage' if s.get('status') == 'material_shortage' else ('status-ok' if not s.get('is_delayed') else 'status-shortage')
        status_text = '正常' if not s.get('is_delayed') else '延期'
        bar_class = _get_process_class(s.get('process_name', s.get('process', '')))
        gantt_rows.append(
            f'<tr>'
            f'<td>{s["order_id"]}</td>'
            f'<td>{s["product"]}</td>'
            f'<td>{s["qty"]}</td>'
            f'<td>{s.get("process_name", s.get("process", ""))}</td>'
            f'<td>{s.get("machine_id", s.get("machine", ""))}</td>'
            f'<td>{start_str}</td>'
            f'<td>{end_str}</td>'
            f'<td><span class="status-badge {status_class}">{status_text}</span></td>'
            f'<td><div class="gantt-bar {bar_class}">{s["order_id"]}<div class="tooltip">{s["product"]} {s["qty"]}件</div></div></td>'
            f'</tr>'
        )

    # 生成物料需求行
    material_rows = []
    if material_plan:
        for m in material_plan:
            gap_class = 'ok' if m.get('gap', 0) == 0 else 'mat'
            progress_pct = min(100, int((m.get('available', 0) / max(m.get('demand', 1), 1)) * 100))
            progress_class = 'ok' if progress_pct >= 100 else ('warning' if progress_pct >= 50 else 'danger')
            suggest = m.get('suggest_purchase', 0)
            first_need = m.get('first_need', '')
            suggest_arrival = m.get('suggest_arrival', '')
            available = m.get('available', m.get('stock', 0) + m.get('on_the_way', 0))
            material_rows.append(
                f'<tr>'
                f'<td>{m["material"]}</td>'
                f'<td>{m.get("demand", 0):.2f} {m.get("unit", "")}</td>'
                f'<td>{m.get("stock", 0):.2f}</td>'
                f'<td>{m.get("on_the_way", 0):.2f}</td>'
                f'<td>{available:.2f}</td>'
                f'<td class="{gap_class}">{m.get("gap", 0):.2f}</td>'
                f'<td><div class="progress-bar"><div class="progress-fill {progress_class}" style="width:{progress_pct}%"></div></div></td>'
                f'<td>{suggest:.2f}</td>'
                f'<td>{first_need[:10] if first_need else ""}</td>'
                f'</tr>'
            )
    else:
        material_rows.append('<tr><td colspan="9" style="text-align:center; color:#909399; padding:30px;">未提供库存数据，无法计算物料缺口</td></tr>')

    # 准备 D3.js 数据
    schedule_json = json.dumps(schedule, ensure_ascii=False, default=str)

    # 替换模板变量
    html = template
    html = html.replace('{{generation_time}}', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    html = html.replace('{{total_orders}}', str(summary.get('total_orders', 0)))
    html = html.replace('{{scheduled_orders}}', str(summary.get('on_time', 0)))
    html = html.replace('{{total_items}}', str(summary.get('total_items', 0)))
    shortage_count = sum(1 for m in material_plan if m.get('gap', 0) > 0)
    html = html.replace('{{shortage_count}}', str(shortage_count))
    html = html.replace('{{shortage_class}}', 'danger' if shortage_count > 0 else 'success')
    html = html.replace('{{start_date}}', str(summary.get('start', ''))[:10])
    html = html.replace('{{end_date}}', str(summary.get('finish', ''))[:10])
    html = html.replace('{{gantt_rows}}', '\n'.join(gantt_rows))
    html = html.replace('{{material_rows}}', '\n'.join(material_rows))
    html = html.replace('{{schedule_json}}', schedule_json)

    html_path = os.path.join(out_dir, 'schedule_gantt.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'[PMC Scheduler] 甘特图已生成: {html_path}')
    return html_path


def _get_process_class(process_name: str) -> str:
    """根据工序名返回CSS类"""
    name = str(process_name).lower()
    if 'cnc' in name or '加工' in name:
        return 'cnc'
    elif '装配' in name or '组装' in name:
        return 'assembly'
    elif '包装' in name or '打包' in name:
        return 'pack'
    return 'default'


def print_report(result: Dict, material_plan: List[Dict], orders: List[Dict]):
    """在控制台打印排产报告"""
    summary = build_summary(result, orders)
    schedule = result.get('schedule', [])

    print("=" * 80)
    print("智能排产结果摘要")
    print("=" * 80)
    print(f"工单总数: {summary['total_orders']}")
    print(f"准时交付: {summary['on_time']} ({summary['on_time_rate']:.1%})")
    print(f"延期工单: {summary['delayed_orders']}")
    print(f"总工期: {summary['makespan_days']:.2f} 天")
    print(f"瓶颈设备: {summary['bottleneck']} ({summary['bottleneck_hours']:.1f} 小时)")
    print()

    print("工单交期情况:")
    for oid, info in sorted(result.get('order_delays', {}).items()):
        status = "准时" if info['delay_min'] == 0 else f"延期 {info['delay_hours']:.1f}h"
        print(f"  {oid} {info['product']} -> {status}")
    print()

    if material_plan:
        print("物料需求预警:")
        shortages = [m for m in material_plan if m['gap'] > 0]
        if shortages:
            for m in shortages[:10]:
                print(f"  {m['material']}: 需求{m['demand']}{m['unit']}, 缺口{m['gap']}{m['unit']}")
        else:
            print("  无物料缺口")
        print()

    print("排产明细 (前20条):")
    for s in schedule[:20]:
        start_str = s['start_time'].strftime('%m-%d %H:%M')
        end_str = s['end_time'].strftime('%m-%d %H:%M')
        status = "延期" if s['is_delayed'] else "正常"
        print(f"  {s['order_id']} | {s['product']} | {s['process']} | {s['machine']} | {start_str} ~ {end_str} | {status}")


# ============================================================
# 5. 主程序入口
# ============================================================

def main(input_file: str = None, out_dir: str = 'tmp'):
    """
    主入口函数。
    使用方式：
      result = main('data/scheduling_data.json')
    """
    if input_file is None:
        # 默认从常见位置查找输入文件
        candidates = [
            'tmp/scheduling_data.json',
            'tmp/web_*.json',
            'scheduling_data.json'
        ]
        import glob as _glob
        for c in candidates:
            matches = _glob.glob(c)
            if matches:
                input_file = matches[0]
                break

    if not input_file or not os.path.exists(input_file):
        raise FileNotFoundError(f"找不到排产数据文件。请确认文件路径: {input_file}")

    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    orders = data.get('orders', [])
    materials = data.get('materials', [])
    params = data.get('params', {})
    objective = params.get('objective', 'tardiness')
    iterations = int(params.get('iterations', 500))

    print(f"[PMC Scheduler] 读取 {len(orders)} 个工单, {len(materials)} 种物料")
    print(f"[PMC Scheduler] 优化目标: {objective}, 迭代次数: {iterations}")

    # 执行排程
    best_order, result = optimize_schedule(orders, objective=objective, iterations=iterations)

    # 物料需求计划
    material_plan = calculate_material_plan(orders, materials, result.get('schedule', []))

    # 生成输出
    output = generate_outputs(result, material_plan, orders, out_dir)

    # 打印报告
    print_report(result, material_plan, orders)

    return output


if __name__ == '__main__':
    import sys
    input_file = sys.argv[1] if len(sys.argv) > 1 else None
    out_dir = sys.argv[2] if len(sys.argv) > 2 else 'tmp'
    main(input_file, out_dir)
