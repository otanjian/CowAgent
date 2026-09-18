"""
BOM树结构模块
支持多级BOM展开、订单树构建、倒排运算
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from common.log import logger


@dataclass
class BOMItem:
    """BOM物料项"""
    material_name: str
    quantity: float
    item_type: str = "raw_material"
    unit: str = "个"
    material_code: str = ""  # 料号，用于匹配库存和工序
    base_qty: float = 1.0  # 底数


@dataclass
class OrderTreeNode:
    """订单树节点"""
    order_id: str
    product: str
    quantity: int
    level: int = 0
    parent_id: Optional[str] = None  # BOM结构中的父级（料号层级）
    children: List['OrderTreeNode'] = field(default_factory=list)
    process_route: List[Any] = field(default_factory=list)
    due_date: datetime = field(default_factory=datetime.now)
    schedule_start: Optional[datetime] = None
    schedule_end: Optional[datetime] = None
    status: str = "pending"
    material_check: Dict = field(default_factory=dict)
    bom_ratio: float = 1.0
    # 新模板增强字段
    explicit_parent_id: Optional[str] = None  # 显式父级生产订单号（v3模板）
    sales_order_no: Optional[str] = None
    batch_no: Optional[str] = None
    product_code: Optional[str] = None
    mold_id: Optional[str] = None  # 当前订单/产品主要模具

    def is_leaf(self) -> bool:
        """是否为叶子节点（原材料）"""
        return len(self.children) == 0 and self.level > 0

    def is_root(self) -> bool:
        """是否为根节点（成品）"""
        return self.level == 0

    def get_all_nodes(self) -> List['OrderTreeNode']:
        """获取所有节点（包含自身）"""
        nodes = [self]
        for child in self.children:
            nodes.extend(child.get_all_nodes())
        return nodes

    def get_leaf_nodes(self) -> List['OrderTreeNode']:
        """获取所有叶子节点（原材料）"""
        if self.is_leaf():
            return [self]
        leaves = []
        for child in self.children:
            leaves.extend(child.get_leaf_nodes())
        return leaves

    def get_semi_finished_nodes(self) -> List['OrderTreeNode']:
        """获取所有半成品节点"""
        nodes = []
        if self.level > 0 and not self.is_leaf():
            nodes.append(self)
        for child in self.children:
            nodes.extend(child.get_semi_finished_nodes())
        return nodes


class BOMTreeBuilder:
    """BOM树构建器"""

    def __init__(self, bom_db: Dict[str, List[BOMItem]], 
                 process_db: Optional[Dict[str, List[Any]]] = None):
        """
        初始化BOM树构建器
        
        Args:
            bom_db: BOM数据库 {产品名: [BOMItem, ...]}
            process_db: 工序数据库 {产品名: [ProcessStep, ...]}
        """
        self.bom_db = bom_db
        self.process_db = process_db or {}
        self._visited = set()

    def build_order_tree(self, root_order_id: str, product: str, 
                         quantity: int, due_date: datetime,
                         process_route: Optional[List[Any]] = None) -> OrderTreeNode:
        """
        从根订单构建完整的订单树
        
        Args:
            root_order_id: 根订单ID
            product: 产品名称
            quantity: 数量
            due_date: 交期
            process_route: 工序路线
            
        Returns:
            OrderTreeNode: 根节点
        """
        self._visited.clear()
        
        root = OrderTreeNode(
            order_id=root_order_id,
            product=product,
            quantity=quantity,
            level=0,
            process_route=process_route or [],
            due_date=due_date
        )
        
        self._build_children(root)
        return root

    def _build_children(self, node: OrderTreeNode):
        """递归构建子节点"""
        if node.product in self._visited:
            return
        
        self._visited.add(node.product)
        
        if node.product not in self.bom_db:
            return
        
        # 只给半成品子件创建虚拟订单，按顺序编号
        semi_items = [
            item for item in self.bom_db[node.product]
            if item.item_type in ("semi_finished", "半成品", "subassembly")
        ]
        for idx, item in enumerate(semi_items, start=1):
            # 数量 = 父订单数量 / 底数 * 用量
            child_quantity = int(node.quantity / item.base_qty * item.quantity)
            
            # 子件的产品标识：优先使用料号，否则使用品名
            child_product = item.material_code or item.material_name
            
            child_id = f"{node.order_id}-{idx:02d}"
            child = OrderTreeNode(
                order_id=child_id,
                product=child_product,
                quantity=child_quantity,
                level=node.level + 1,
                parent_id=node.order_id,
                process_route=(
                    self.process_db.get(child_product, []) or
                    self.process_db.get(item.material_name, [])
                ),
                bom_ratio=item.quantity
            )
            node.children.append(child)
            self._build_children(child)
        
        self._visited.discard(node.product)

    def get_material_demand(self, tree: OrderTreeNode) -> Dict[str, Dict]:
        """
        获取原材料需求汇总
        
        Returns:
            {material_name: {"total_need": float, "nodes": [OrderTreeNode]}}
        """
        demand = {}
        
        def collect(node: OrderTreeNode):
            if node.is_leaf():
                name = node.product
                if name not in demand:
                    demand[name] = {"total_need": 0, "nodes": []}
                demand[name]["total_need"] += node.quantity
                demand[name]["nodes"].append(node)
            for child in node.children:
                collect(child)
        
        collect(tree)
        return demand


class ExplicitParentTreeBuilder:
    """
    显式父级订单树构建器（v3模板）
    根据生产订单信息中的'父级'字段构建订单树
    父级为'0'的是成品订单（根节点）
    """

    def __init__(self, process_db: Optional[Dict[str, List[Any]]] = None):
        self.process_db = process_db or {}

    def build_trees(self, orders: List[Any]) -> List[OrderTreeNode]:
        """
        从带parent_id的订单列表构建多棵树

        Args:
            orders: WorkOrder列表，需要包含parent_id字段

        Returns:
            List[OrderTreeNode]: 根节点列表
        """
        # 按order_id索引
        order_map = {o.order_id: o for o in orders}

        # 构建父子关系
        children_map: Dict[str, List[Any]] = {}  # parent_order_id -> [child_orders]
        root_orders = []

        for order in orders:
            parent_id = getattr(order, 'parent_id', None)
            if parent_id == '0' or parent_id == 0 or not parent_id:
                root_orders.append(order)
            else:
                parent_id_str = str(parent_id).strip()
                if parent_id_str not in children_map:
                    children_map[parent_id_str] = []
                children_map[parent_id_str].append(order)

        trees = []
        for root_order in root_orders:
            tree = self._build_node(root_order, children_map, order_map, level=0)
            trees.append(tree)

        return trees

    def _build_node(self, order: Any, children_map: Dict[str, List[Any]],
                    order_map: Dict[str, Any], level: int = 0) -> OrderTreeNode:
        """递归构建节点"""
        # 确定模具：从工序路线第一道工序取模具
        mold_id = None
        if order.process_route:
            mold_id = getattr(order.process_route[0], 'mold_id', None)

        node = OrderTreeNode(
            order_id=order.order_id,
            product=order.product,
            quantity=order.quantity,
            level=level,
            due_date=order.due_date,
            process_route=order.process_route,
            explicit_parent_id=getattr(order, 'parent_id', None),
            sales_order_no=getattr(order, 'sales_order_no', None),
            batch_no=getattr(order, 'batch_no', None),
            product_code=getattr(order, 'product_code', None),
            mold_id=mold_id
        )

        # 递归构建子节点
        children = children_map.get(order.order_id, [])
        for child_order in children:
            child_node = self._build_node(child_order, children_map, order_map, level + 1)
            child_node.parent_id = order.order_id  # BOM层级parent
            node.children.append(child_node)

        return node


class WorkTimeHelper:
    """
    工作时间辅助器
    正常班：08:00-12:00, 13:00-17:00（8小时）
    加班：18:00之后
    """

    MORNING_START = 8
    MORNING_END = 12
    LUNCH_HOURS = 1
    AFTERNOON_START = 13
    AFTERNOON_END = 17
    OVERTIME_START = 18

    @classmethod
    def get_day_capacity_hours(cls, machine: Any, date: datetime) -> float:
        """获取某天工作中心的可用小时数"""
        if machine and hasattr(machine, 'daily_capacity') and machine.daily_capacity:
            date_key = date.strftime("%Y-%m-%d")
            return float(machine.daily_capacity.get(date_key, 8.0))
        return 8.0

    @classmethod
    def is_overtime(cls, dt: datetime) -> bool:
        """判断时间是否属于加班时间"""
        return dt.hour >= cls.OVERTIME_START

    @classmethod
    def get_work_hours_detail(cls, dt_start: datetime, dt_end: datetime) -> Dict[str, float]:
        """计算一段工作时间的正常工时和加班工时"""
        total_hours = (dt_end - dt_start).total_seconds() / 3600
        # 简化计算：17:00前为正常班，18:00后为加班，17:00-18:00为休息
        normal_hours = 0.0
        overtime_hours = 0.0

        current = dt_start
        while current < dt_end:
            hour = current.hour
            if 8 <= hour < 17:
                normal_hours += 1
            elif hour >= 18:
                overtime_hours += 1
            current += timedelta(hours=1)

        return {
            "total_hours": total_hours,
            "normal_hours": normal_hours,
            "overtime_hours": overtime_hours
        }


class BackwardScheduler:
    """倒排运算器（支持无限产能和有限产能）"""

    def __init__(self, machines: Optional[Dict[str, Any]] = None,
                 transfer_time_hours: float = 5.0):
        """
        初始化倒排运算器

        Args:
            machines: 设备/工作中心字典 {machine_id: Machine}
            transfer_time_hours: 工序间转移时间（小时）
        """
        self.machines = machines or {}
        self.transfer_time = timedelta(hours=transfer_time_hours)
        self.machine_schedules: Dict[str, List[Dict]] = {}
        # 按天缓存工作中心已用分钟数，避免每次遍历所有任务
        self._day_usage_cache: Dict[str, Dict[str, float]] = {}
        self._day_usage_by_order: Dict[str, Dict[str, Dict[str, float]]] = {}

    def schedule_infinite(self, tree: OrderTreeNode) -> Dict[str, Any]:
        """
        无限产能倒排运算
        只考虑工序工时约束和转移时间，不考虑设备负荷

        Args:
            tree: 订单树根节点

        Returns:
            {
                "schedule": List[Dict],  # 工序排程明细
                "node_schedules": Dict[str, Dict],  # 每个节点的排程
                "is_feasible": bool  # 是否可行
            }
        """
        schedule = []
        node_schedules = {}

        def schedule_node(node: OrderTreeNode, latest_end: datetime):
            """递归倒排节点"""
            current_end = latest_end
            process_schedule = []

            # 倒排工序（从最后一道工序往前）
            for i in range(len(node.process_route) - 1, -1, -1):
                step = node.process_route[i]
                processing_time = timedelta(minutes=step.time_per_unit * node.quantity)
                processing_hours = processing_time.total_seconds() / 3600

                step_end = current_end
                step_start = step_end - processing_time

                process_schedule.insert(0, {
                    "process_name": step.process_name,
                    "start_time": step_start,
                    "end_time": step_end,
                    "machine_id": getattr(step, 'machine_id', None),
                    "processing_time_minutes": processing_time.total_seconds() / 60,
                    "sequence": getattr(step, 'sequence', 0) or (i + 1),
                    "mold_id": getattr(step, 'mold_id', None)
                })

                # 流转时长规则：前道生产时间 < 5小时时，流转时长取生产时间
                transfer_hours = min(self.transfer_time.total_seconds() / 3600, processing_hours) if processing_hours < self.transfer_time.total_seconds() / 3600 else self.transfer_time.total_seconds() / 3600
                current_end = step_start - timedelta(hours=transfer_hours)

            # 更新节点排程
            if process_schedule:
                node.schedule_end = process_schedule[-1]["end_time"]
                node.schedule_start = process_schedule[0]["start_time"]
            else:
                node.schedule_end = latest_end
                node.schedule_start = latest_end

            node_schedules[node.order_id] = {
                "order_id": node.order_id,
                "product": node.product,
                "quantity": node.quantity,
                "level": node.level,
                "start_time": node.schedule_start,
                "end_time": node.schedule_end,
                "processes": process_schedule
            }

            schedule.extend([
                {
                    "order_id": node.order_id,
                    "product": node.product,
                    "process_name": p["process_name"],
                    "machine_id": p["machine_id"],
                    "start_time": p["start_time"],
                    "end_time": p["end_time"],
                    "quantity": node.quantity,
                    "level": node.level,
                    "schedule_type": "infinite",
                    "sequence": p.get("sequence", 0),
                    "mold_id": p.get("mold_id"),
                    "processing_time_minutes": p.get("processing_time_minutes", 0)
                }
                for p in process_schedule
            ])

            # 递归处理子节点（子节点必须在父节点开始时间前完成）
            for child in node.children:
                schedule_node(child, node.schedule_start)

        schedule_node(tree, tree.due_date)

        # 检查是否可行（是否有排程早于当前时间）
        now = datetime.now()
        is_feasible = all(
            item["start_time"] >= now for item in schedule
        ) if schedule else True

        return {
            "schedule": schedule,
            "node_schedules": node_schedules,
            "is_feasible": is_feasible
        }

    def schedule_finite(self, tree: OrderTreeNode) -> Dict[str, Any]:
        """
        有限产能倒排运算
        考虑工作中心负荷、换模时间、每天容量限制

        Args:
            tree: 订单树根节点

        Returns:
            同schedule_infinite，但schedule_type为"finite"
        """
        # 初始化设备排程表（仅在首次调用时清空，以支持跨订单冲突检测）
        if not self.machine_schedules:
            self.machine_schedules = {mid: [] for mid in self.machines}

        schedule = []
        node_schedules = {}

        def schedule_node_finite(node: OrderTreeNode, latest_end: datetime):
            """递归有限产能倒排节点"""
            current_end = latest_end
            process_schedule = []

            # 倒排工序（从最后一道工序往前）
            for i in range(len(node.process_route) - 1, -1, -1):
                step = node.process_route[i]
                processing_time = timedelta(minutes=step.time_per_unit * node.quantity)
                processing_hours = processing_time.total_seconds() / 3600

                machine_id = getattr(step, 'machine_id', None)
                step_mold_id = getattr(step, 'mold_id', None)

                if machine_id and machine_id in self.machines:
                    # 计算换模时间
                    setup_time = self._get_setup_time(
                        machine_id, node.order_id, node.product, step_mold_id
                    )
                    total_duration = processing_time + timedelta(minutes=setup_time)

                    # 有限产能：查找可用时段
                    step_end, step_start = self._find_latest_slot(
                        machine_id, current_end, total_duration,
                        node.order_id, node.product, step_mold_id
                    )
                else:
                    # 无限产能
                    step_end = current_end
                    step_start = step_end - processing_time
                    setup_time = 0.0

                process_schedule.insert(0, {
                    "process_name": step.process_name,
                    "start_time": step_start,
                    "end_time": step_end,
                    "machine_id": machine_id,
                    "processing_time_minutes": processing_time.total_seconds() / 60,
                    "sequence": getattr(step, 'sequence', 0) or (i + 1),
                    "mold_id": step_mold_id,
                    "setup_time_minutes": setup_time
                })

                # 记录设备排程
                if machine_id:
                    self.machine_schedules[machine_id].append({
                        "start": step_start,
                        "end": step_end,
                        "order_id": node.order_id,
                        "product": node.product,
                        "mold_id": step_mold_id
                    })
                    # 更新按天缓存（跨天任务按天拆分）
                    check_date = step_start.date()
                    end_date = step_end.date()
                    while check_date <= end_date:
                        day_start_dt = datetime.combine(check_date, datetime.min.time())
                        day_end_dt = day_start_dt + timedelta(days=1)
                        overlap_start = max(step_start, day_start_dt)
                        overlap_end = min(step_end, day_end_dt)
                        if overlap_end > overlap_start:
                            day_min = (overlap_end - overlap_start).total_seconds() / 60
                            day_key = check_date.strftime("%Y-%m-%d")
                            self._day_usage_cache.setdefault(machine_id, {}).setdefault(day_key, 0.0)
                            self._day_usage_cache[machine_id][day_key] += day_min
                            self._day_usage_by_order.setdefault(machine_id, {}).setdefault(day_key, {}).setdefault(node.order_id, 0.0)
                            self._day_usage_by_order[machine_id][day_key][node.order_id] += day_min
                        check_date += timedelta(days=1)

                # 流转时长规则：前道生产时间 < 5小时时，流转时长取生产时间
                transfer_hours = min(self.transfer_time.total_seconds() / 3600, processing_hours) if processing_hours < self.transfer_time.total_seconds() / 3600 else self.transfer_time.total_seconds() / 3600
                current_end = step_start - timedelta(hours=transfer_hours)

            if process_schedule:
                node.schedule_end = process_schedule[-1]["end_time"]
                node.schedule_start = process_schedule[0]["start_time"]
            else:
                node.schedule_end = latest_end
                node.schedule_start = latest_end

            node_schedules[node.order_id] = {
                "order_id": node.order_id,
                "product": node.product,
                "quantity": node.quantity,
                "level": node.level,
                "start_time": node.schedule_start,
                "end_time": node.schedule_end,
                "processes": process_schedule
            }

            schedule.extend([
                {
                    "order_id": node.order_id,
                    "product": node.product,
                    "process_name": p["process_name"],
                    "machine_id": p["machine_id"],
                    "start_time": p["start_time"],
                    "end_time": p["end_time"],
                    "quantity": node.quantity,
                    "level": node.level,
                    "schedule_type": "finite",
                    "sequence": p.get("sequence", 0),
                    "mold_id": p.get("mold_id"),
                    "setup_time_minutes": p.get("setup_time_minutes", 0),
                    "processing_time_minutes": p.get("processing_time_minutes", 0)
                }
                for p in process_schedule
            ])

            for child in node.children:
                schedule_node_finite(child, node.schedule_start)

        schedule_node_finite(tree, tree.due_date)

        now = datetime.now()
        is_feasible = all(
            item["start_time"] >= now for item in schedule
        ) if schedule else True

        return {
            "schedule": schedule,
            "node_schedules": node_schedules,
            "is_feasible": is_feasible
        }

    def _get_setup_time(self, machine_id: str, order_id: str,
                        product: str, mold_id: Optional[str]) -> float:
        """
        计算换模时间（分钟）
        规则：
        - 同工作中心前后单不同模具：1工作日 = 480分钟
        - 同工作中心前后单同模具、不同产品：2小时 = 120分钟
        - 同模具同产品：0分钟
        """
        machine = self.machines.get(machine_id)
        if not machine:
            return 0.0

        scheduled = self.machine_schedules.get(machine_id, [])
        if not scheduled:
            return 0.0

        # 找同一工作中心最后一个已排程的订单（排除当前订单自身）
        last_item = None
        for item in reversed(scheduled):
            if item.get("order_id") != order_id:
                last_item = item
                break

        if not last_item:
            return 0.0

        last_mold = last_item.get("mold_id")
        last_product = last_item.get("product")

        # 不同模具 -> 1工作日 = 480分钟
        if mold_id and last_mold and str(mold_id) != str(last_mold):
            return getattr(machine, 'setup_time_diff_mold', 480.0)

        # 同模具不同产品 -> 2小时 = 120分钟
        if mold_id and last_mold and str(mold_id) == str(last_mold) and product != last_product:
            return getattr(machine, 'setup_time_same_mold_diff_product', 120.0)

        # 同模具同产品 -> 0
        return 0.0

    def _get_day_used_minutes(self, machine_id: str, date_obj: Any,
                               exclude_order_id: Optional[str] = None) -> float:
        """获取某天某工作中心已用分钟数（不含换模），使用缓存加速"""
        day_key = date_obj.strftime("%Y-%m-%d")
        if not exclude_order_id:
            # 无排除订单时直接使用缓存
            return self._day_usage_cache.get(machine_id, {}).get(day_key, 0.0)
        # 需要排除特定订单时，从总缓存中减去该订单的占用
        total = self._day_usage_cache.get(machine_id, {}).get(day_key, 0.0)
        excluded = self._day_usage_by_order.get(machine_id, {}).get(day_key, {}).get(exclude_order_id, 0.0)
        return max(0.0, total - excluded)

    def _find_latest_slot(self, machine_id: str, latest_end: datetime,
                          duration: timedelta, order_id: str,
                          product: str, mold_id: Optional[str]) -> tuple:
        """
        查找工作中心最晚可用时段（考虑每天容量限制）

        Args:
            machine_id: 设备ID
            latest_end: 最晚结束时间
            duration: 加工时长（含换模）
            order_id: 订单ID
            product: 产品名称
            mold_id: 模具编号

        Returns:
            (step_end, step_start): 实际结束时间和开始时间
        """
        scheduled = self.machine_schedules.get(machine_id, [])
        machine = self.machines.get(machine_id)

        # 尝试在latest_end前完成
        candidate_end = latest_end
        candidate_start = candidate_end - duration

        max_attempts = 200
        for _ in range(max_attempts):
            conflict = False
            for item in scheduled:
                if item.get("order_id") == order_id:
                    continue
                if not (candidate_end <= item["start"] or candidate_start >= item["end"]):
                    # 有冲突，往前找
                    candidate_end = item["start"] - timedelta(minutes=1)
                    candidate_start = candidate_end - duration
                    conflict = True
                    break

            if not conflict:
                # 检查每天容量限制（放宽：允许大任务跨多天，只避开已完全占满的天）
                if machine:
                    day_cap = WorkTimeHelper.get_day_capacity_hours(machine, candidate_end)
                    day_cap_min = day_cap * 60
                    check_date = candidate_end.date()
                    start_date = candidate_start.date()
                    while check_date >= start_date:
                        used = self._get_day_used_minutes(machine_id, check_date, exclude_order_id=order_id)
                        # 如果当天已被其他任务完全占满，才往前找
                        if used >= day_cap_min - 1:
                            candidate_end = datetime.combine(check_date, datetime.min.time()) - timedelta(minutes=1)
                            candidate_start = candidate_end - duration
                            conflict = True
                            break
                        check_date -= timedelta(days=1)

                if not conflict:
                    return candidate_end, candidate_start

        # 找不到合适时段，强制安排
        return candidate_end, candidate_start


class MaterialKitChecker:
    """齐套检查器（针对BOM树）"""

    def __init__(self, materials: Dict[str, Any]):
        """
        初始化齐套检查器
        
        Args:
            materials: 物料库存字典 {material_name: Material}
        """
        self.materials = materials

    def check_tree_kits(self, tree: OrderTreeNode) -> Dict[str, Any]:
        """
        检查订单树的原材料齐套情况
        只看原材料，不看半成品齐套
        
        Args:
            tree: 订单树根节点
            
        Returns:
            {
                "all_ready": bool,
                "materials": List[Dict],
                "ready_time": datetime,
                "shortages": List[Dict]
            }
        """
        result = {
            "all_ready": True,
            "materials": [],
            "ready_time": datetime.now(),
            "shortages": []
        }
        
        # 获取所有叶子节点（原材料）
        leaf_nodes = tree.get_leaf_nodes()
        
        # 汇总需求
        demand = {}
        for node in leaf_nodes:
            name = node.product
            if name not in demand:
                demand[name] = {"total_need": 0, "nodes": []}
            demand[name]["total_need"] += node.quantity
            demand[name]["nodes"].append(node)
        
        # 检查每种物料（同时支持料号和品名匹配）
        for name, info in demand.items():
            total_need = info["total_need"]
            mat = self.materials.get(name)
            
            # 如果直接匹配不到，尝试用品名匹配（料号可能在库存中不存在）
            if not mat:
                for m in self.materials.values():
                    if getattr(m, 'material_name', '') == name or getattr(m, 'material_code', '') == name:
                        mat = m
                        break
            
            if not mat:
                result["all_ready"] = False
                result["shortages"].append({
                    "material": name,
                    "need": total_need,
                    "available": 0,
                    "gap": total_need,
                    "ready_time": None
                })
                result["materials"].append({
                    "material": name,
                    "need": total_need,
                    "stock": 0,
                    "on_the_way": 0,
                    "gap": total_need
                })
                continue
            
            # 先看库存，再看在途
            stock = getattr(mat, 'stock', 0)
            on_the_way = getattr(mat, 'on_the_way', 0)
            lead_time = getattr(mat, 'lead_time', 0)
            safety_stock = getattr(mat, 'safety_stock', 0)
            
            available = stock + on_the_way - safety_stock
            gap = max(0, total_need - available)
            
            mat_result = {
                "material": name,
                "need": total_need,
                "stock": stock,
                "on_the_way": on_the_way,
                "safety_stock": safety_stock,
                "available": available,
                "gap": gap
            }
            result["materials"].append(mat_result)
            
            if gap > 0:
                result["all_ready"] = False
                ready_time = datetime.now() + timedelta(days=lead_time)
                result["shortages"].append({
                    "material": name,
                    "need": total_need,
                    "available": available,
                    "gap": gap,
                    "lead_time": lead_time,
                    "ready_time": ready_time
                })
                if ready_time > result["ready_time"]:
                    result["ready_time"] = ready_time
            else:
                # 齐套，但考虑在途物料到达时间
                if stock < total_need and on_the_way > 0:
                    ready_time = datetime.now() + timedelta(days=lead_time)
                    if ready_time > result["ready_time"]:
                        result["ready_time"] = ready_time
        
        return result
