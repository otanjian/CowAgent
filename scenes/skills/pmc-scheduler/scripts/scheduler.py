"""
生产排产引擎 (Production Scheduling Engine)
基于有限能力排程算法 (Finite Capacity Scheduling)
"""

import json
import csv
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from io import StringIO
import sys


@dataclass
class WorkOrder:
    """工单"""
    order_id: str
    product: str
    quantity: int
    due_date: str
    priority: int = 5
    bom: Dict[str, float] = field(default_factory=dict)
    status: str = "pending"

    def __post_init__(self):
        if isinstance(self.due_date, str):
            self.due_date = datetime.strptime(self.due_date, "%Y-%m-%d")


@dataclass
class Material:
    """物料"""
    name: str
    stock: float = 0
    on_the_way: float = 0
    lead_time: int = 5

    @property
    def available(self) -> float:
        return self.stock + self.on_the_way


@dataclass
class Process:
    """工序"""
    name: str
    capacity: int = 100  # 日产能
    time_per_unit: float = 60  # 分钟/件


@dataclass
class ScheduleItem:
    """排产明细"""
    order_id: str
    product: str
    quantity: int
    process: str
    start_time: datetime
    end_time: datetime
    status: str
    material_status: str = "ok"


class ProductionScheduler:
    """生产排产器"""

    def __init__(self):
        self.orders: List[WorkOrder] = []
        self.materials: Dict[str, Material] = {}
        self.processes: List[Process] = []
        self.schedule: List[ScheduleItem] = []
        self.start_date: Optional[datetime] = None
        self._bom_cache: Dict[str, Dict[str, float]] = {}  # 产品BOM缓存

    def load_orders_from_csv(self, csv_text: str):
        """从CSV加载工单数据"""
        reader = csv.DictReader(StringIO(csv_text))
        for row in reader:
            bom_str = row.get('bom', '{}')
            bom = json.loads(bom_str) if bom_str and bom_str != '{}' else {}
            self.orders.append(WorkOrder(
                order_id=row['order_id'],
                product=row['product'],
                quantity=int(row['quantity']),
                due_date=row['due_date'],
                priority=int(row.get('priority', 5)),
                bom=bom
            ))

    def load_orders_from_text(self, text: str):
        """从文本描述解析工单"""
        lines = text.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or '工单' in line or 'order' in line.lower():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                order_id = parts[0]
                product = parts[1]
                quantity = int(parts[2])
                due_date = parts[3]
                priority = int(parts[4]) if len(parts) > 4 else 5
                self.orders.append(WorkOrder(
                    order_id=order_id,
                    product=product,
                    quantity=quantity,
                    due_date=due_date,
                    priority=priority
                ))

    def load_materials_from_csv(self, csv_text: str):
        """从CSV加载物料数据"""
        reader = csv.DictReader(StringIO(csv_text))
        for row in reader:
            name = row['material'] or row['物料名称']
            self.materials[name] = Material(
                name=name,
                stock=float(row.get('stock', row.get('库存数量', 0))),
                on_the_way=float(row.get('on_the_way', row.get('在途数量', 0))),
                lead_time=int(row.get('lead_time', row.get('采购周期(天)', 5)))
            )

    def load_materials_from_text(self, text: str):
        """从文本描述解析物料"""
        lines = text.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or '物料' in line or 'material' in line.lower():
                continue
            # 支持格式: "原材料A: 库存100, 在途200"
            if ':' in line or ',' in line:
                parts = line.replace(':', ',').split(',')
                if len(parts) >= 2:
                    name = parts[0].strip()
                    stock = 0
                    on_the_way = 0
                    for p in parts[1:]:
                        p = p.strip().lower()
                        if '库存' in p:
                            stock = float(''.join(filter(str.isdigit, p)))
                        elif '在途' in p:
                            on_the_way = float(''.join(filter(str.isdigit, p)))
                    self.materials[name] = Material(
                        name=name,
                        stock=stock,
                        on_the_way=on_the_way
                    )

    def load_processes_from_csv(self, csv_text: str):
        """从CSV加载工序数据"""
        reader = csv.DictReader(StringIO(csv_text))
        for row in reader:
            self.processes.append(Process(
                name=row['process'] or row['工序名称'],
                capacity=int(row.get('capacity', row.get('日产能', 100))),
                time_per_unit=float(row.get('time_per_unit', row.get('单位耗时(分钟)', 60)))
            ))

    def load_processes_from_text(self, text: str):
        """从文本描述解析工序"""
        lines = text.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or '工序' in line or 'process' in line.lower():
                continue
            parts = line.replace(':', ',').split(',')
            if len(parts) >= 2:
                name = parts[0].strip()
                capacity = 100
                time_per_unit = 60
                for p in parts[1:]:
                    p = p.strip().lower()
                    if '日产能' in p or '产能' in p:
                        capacity = int(''.join(filter(str.isdigit, p)))
                    elif '分钟' in p or '耗时' in p:
                        time_per_unit = float(''.join(filter(str.isdigit, p)))
                self.processes.append(Process(
                    name=name,
                    capacity=capacity,
                    time_per_unit=time_per_unit
                ))

    def add_order(self, order: WorkOrder):
        """添加工单"""
        self.orders.append(order)

    def add_material(self, material: Material):
        """添加物料"""
        self.materials[material.name] = material

    def add_process(self, process: Process):
        """添加工序"""
        self.processes.append(process)

    def load_bom_from_csv(self, csv_text: str):
        """从CSV加载BOM配置"""
        reader = csv.DictReader(StringIO(csv_text))
        for row in reader:
            product = row.get('product', row.get('产品名称', ''))
            material = row.get('material', row.get('原材料', ''))
            qty = float(row.get('qty', row.get('单件用量', 0)))
            
            if product and material:
                if product not in self._bom_cache:
                    self._bom_cache[product] = {}
                self._bom_cache[product][material] = qty

    def get_bom_for_product(self, product: str) -> Dict[str, float]:
        """获取产品的BOM"""
        return self._bom_cache.get(product, {})

    def apply_bom_to_orders(self):
        """将BOM配置应用到所有工单"""
        for order in self.orders:
            if not order.bom and order.product in self._bom_cache:
                order.bom = self._bom_cache[order.product]

    def check_material_availability(self, order: WorkOrder) -> Dict[str, Dict]:
        """检查物料齐套情况"""
        result = {}
        for material_name, qty_per_unit in order.bom.items():
            total_needed = qty_per_unit * order.quantity
            if material_name in self.materials:
                available = self.materials[material_name].available
                shortage = max(0, total_needed - available)
                result[material_name] = {
                    'needed': total_needed,
                    'available': available,
                    'shortage': shortage,
                    'status': 'ok' if shortage == 0 else 'shortage'
                }
            else:
                result[material_name] = {
                    'needed': total_needed,
                    'available': 0,
                    'shortage': total_needed,
                    'status': 'missing'
                }
        return result

    def calculate_production_time(self, quantity: int, process: Process) -> float:
        """计算生产耗时（分钟）"""
        return quantity * process.time_per_unit

    def calculate_processing_days(self, quantity: int, process: Process) -> float:
        """计算加工天数"""
        total_minutes = self.calculate_production_time(quantity, process)
        hours_per_day = 10  # 假设每天工作10小时
        return total_minutes / (hours_per_day * 60)

    def schedule_order_backward(self, order: WorkOrder, processes: List[Process]) -> List[ScheduleItem]:
        """反向排程（从交期倒推）"""
        items = []
        current_time = datetime.combine(order.due_date, datetime.strptime("17:00", "%H:%M").time())

        for process in reversed(processes):
            days_needed = self.calculate_processing_days(order.quantity, process)
            end_time = current_time
            start_time = end_time - timedelta(minutes=days_needed * 10 * 60)
            current_time = start_time - timedelta(hours=1)  # 换模时间

            material_check = self.check_material_availability(order)
            material_status = "ok"
            for mat_result in material_check.values():
                if mat_result['status'] != 'ok':
                    material_status = "shortage"
                    break

            items.append(ScheduleItem(
                order_id=order.order_id,
                product=order.product,
                quantity=order.quantity,
                process=process.name,
                start_time=start_time,
                end_time=end_time,
                status="scheduled",
                material_status=material_status
            ))

        return list(reversed(items))

    def schedule_order_forward(self, order: WorkOrder, processes: List[Process]) -> List[ScheduleItem]:
        """正向排程（从可用时间开始）"""
        items = []
        current_time = self.start_date or datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)

        for process in processes:
            days_needed = self.calculate_processing_days(order.quantity, process)
            start_time = current_time
            end_time = start_time + timedelta(minutes=days_needed * 10 * 60)
            current_time = end_time + timedelta(hours=1)  # 换模时间

            material_check = self.check_material_availability(order)
            material_status = "ok"
            for mat_result in material_check.values():
                if mat_result['status'] != 'ok':
                    material_status = "shortage"
                    break

            items.append(ScheduleItem(
                order_id=order.order_id,
                product=order.product,
                quantity=order.quantity,
                process=process.name,
                start_time=start_time,
                end_time=end_time,
                status="scheduled",
                material_status=material_status
            ))

        return items

    def run_scheduler(self, mode: str = "backward"):
        """执行排程"""
        # 默认工序
        if not self.processes:
            self.processes = [
                Process(name="生产", capacity=100, time_per_unit=60)
            ]

        # 排序工单：先按优先级，再按交期
        sorted_orders = sorted(self.orders, key=lambda x: (x.priority, x.due_date))

        # 设置排程开始时间
        self.start_date = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)

        self.schedule = []
        for order in sorted_orders:
            if mode == "backward":
                items = self.schedule_order_backward(order, self.processes)
            else:
                items = self.schedule_order_forward(order, self.processes)
            self.schedule.extend(items)

        return self.schedule

    def get_material_requirements(self) -> List[Dict]:
        """获取物料需求汇总"""
        requirements = {}

        for order in self.orders:
            for material_name, qty_per_unit in order.bom.items():
                total = qty_per_unit * order.quantity
                if material_name in requirements:
                    requirements[material_name] += total
                else:
                    requirements[material_name] = total

        result = []
        for name, total_needed in requirements.items():
            if name in self.materials:
                mat = self.materials[name]
                shortage = max(0, total_needed - mat.available)
                result.append({
                    'material': name,
                    'total_needed': total_needed,
                    'available': mat.available,
                    'shortage': shortage,
                    'lead_time': mat.lead_time,
                    'suggested_order': shortage,
                    'suggested_date': (datetime.now() + timedelta(days=mat.lead_time)).strftime("%Y-%m-%d") if shortage > 0 else "-"
                })
            else:
                result.append({
                    'material': name,
                    'total_needed': total_needed,
                    'available': 0,
                    'shortage': total_needed,
                    'lead_time': 7,
                    'suggested_order': total_needed,
                    'suggested_date': (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                })

        return result

    def generate_gantt_data(self) -> List[Dict]:
        """生成甘特图数据（兼容D3.js设备和表格订单两种视图）"""
        gantt_data = []
        for item in self.schedule:
            gantt_data.append({
                'order_id': item.order_id,
                'product': item.product,
                'process': item.process,
                'process_name': item.process,
                'start': item.start_time.strftime("%Y-%m-%d %H:%M"),
                'end': item.end_time.strftime("%Y-%m-%d %H:%M"),
                'start_time': item.start_time.isoformat(),
                'end_time': item.end_time.isoformat(),
                'quantity': item.quantity,
                'status': item.status,
                'material_status': item.material_status,
                'machine_id': item.process,  # 默认以工序作为设备标识
                'machine': item.process,
                'delay_hours': 0
            })
        return gantt_data

    def generate_schedule_csv(self) -> str:
        """生成排产明细CSV"""
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['工单号', '产品', '数量', '工序', '开始时间', '结束时间', '物料状态'])

        for item in self.schedule:
            writer.writerow([
                item.order_id,
                item.product,
                item.quantity,
                item.process,
                item.start_time.strftime("%Y-%m-%d %H:%M"),
                item.end_time.strftime("%Y-%m-%d %H:%M"),
                item.material_status
            ])

        return output.getvalue()

    def generate_material_csv(self) -> str:
        """生成物料需求CSV"""
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['物料名称', '需求总量', '可用量', '缺口', '建议采购量', '建议到货日期'])

        for req in self.get_material_requirements():
            writer.writerow([
                req['material'],
                req['total_needed'],
                req['available'],
                req['shortage'],
                req['suggested_order'] if req['suggested_order'] > 0 else '-',
                req['suggested_date']
            ])

        return output.getvalue()

    def get_summary(self) -> Dict:
        """获取排产摘要"""
        total_orders = len(self.orders)
        scheduled_orders = len(set(item.order_id for item in self.schedule))
        shortage_count = sum(1 for item in self.schedule if item.material_status == "shortage")

        return {
            'total_orders': total_orders,
            'scheduled_orders': scheduled_orders,
            'total_items': len(self.schedule),
            'shortage_items': shortage_count,
            'start_date': self.start_date.strftime("%Y-%m-%d") if self.start_date else "-",
            'end_date': max(item.end_time for item in self.schedule).strftime("%Y-%m-%d") if self.schedule else "-"
        }


def parse_natural_language_orders(text: str) -> List[WorkOrder]:
    """从自然语言解析工单"""
    orders = []
    lines = text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 跳过表头
        if any(keyword in line.lower() for keyword in ['工单号', 'order_id', '产品名称', '序号']):
            continue

        # 解析序号格式: "1. WO001 电机壳体 100件 4月20日交 优先级1"
        if line[0].isdigit() and '.' in line[:3]:
            parts = line.split()
            if len(parts) >= 3:
                order_id = parts[1] if parts[1].startswith('WO') else f"WO{parts[0].replace('.', '')}"
                product = parts[2] if len(parts) > 2 else "Unknown"

                # 解析数量
                qty = 0
                for p in parts:
                    if '件' in p or '个' in p:
                        qty = int(''.join(filter(str.isdigit, p)))
                        break

                # 解析交期
                due_date = None
                for p in parts:
                    if '月' in p and '日' in p:
                        due_date = parse_chinese_date(p)
                        break

                # 解析优先级
                priority = 5
                for p in parts:
                    if '优先' in p:
                        priority = int(''.join(filter(str.isdigit, p)) or 5)
                        break

                if order_id and qty > 0 and due_date:
                    orders.append(WorkOrder(
                        order_id=order_id,
                        product=product,
                        quantity=qty,
                        due_date=due_date,
                        priority=priority
                    ))

    return orders


def parse_natural_language_materials(text: str) -> Dict[str, Material]:
    """从自然语言解析物料"""
    materials = {}
    lines = text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if '库存' in line or '原材料' in line or '物料' in line:
            # 格式: "原材料A: 库存100, 在途200" 或 "当前库存：原材料A 500kg, 原材料B 300kg"
            parts = line.replace(':', ',').replace('：', ',').split(',')

            current_name = None
            stock = 0
            on_the_way = 0

            for p in parts:
                p = p.strip()
                if 'kg' in p or '件' in p or '个' in p:
                    # 这是数量
                    nums = ''.join(filter(str.isdigit, p))
                    if nums:
                        qty = float(nums)
                        if stock == 0:
                            stock = qty
                        else:
                            on_the_way = qty
                elif '库存' in p or '在途' in p:
                    continue
                elif p and not p[0].isdigit():
                    # 这是名称
                    if current_name and stock > 0:
                        materials[current_name] = Material(name=current_name, stock=stock, on_the_way=on_the_way)
                    current_name = p
                    stock = 0
                    on_the_way = 0

            if current_name and stock > 0:
                materials[current_name] = Material(name=current_name, stock=stock, on_the_way=on_the_way)

    return materials


def parse_natural_language_bom(text: str, orders: List[WorkOrder]) -> List[WorkOrder]:
    """从自然语言解析BOM"""
    lines = text.strip().split('\n')

    # 查找BOM相关段落
    bom_text = ""
    in_bom_section = False

    for line in lines:
        if '每个' in line or 'BOM' in line or '物料清单' in line:
            in_bom_section = True
        if in_bom_section:
            bom_text += line + "\n"
        if in_bom_section and line.strip() == "":
            break

    if not bom_text:
        return orders

    # 解析每个产品的物料需求
    for order in orders:
        for line in bom_text.split('\n'):
            if order.product in line:
                # 解析物料和数量
                parts = line.replace('需要', ',').split(',')
                bom = {}
                for p in parts:
                    for mat_name in ['原材料A', '原材料B', '原材料C', '包装材料', '半成品']:
                        if mat_name in p:
                            qty = float(''.join(filter(str.isdigit, p.replace('.', ''))))
                            bom[mat_name] = qty
                if bom:
                    order.bom = bom

    return orders


def parse_chinese_date(date_str: str) -> str:
    """解析中文日期为标准格式"""
    # 例如: "4月20日" -> "2026-04-20"
    year = datetime.now().year
    month = int(''.join(filter(str.isdigit, date_str.split('月')[0]))) if '月' in date_str else 1
    day = int(''.join(filter(str.isdigit, date_str.split('日')[0].split('月')[1]))) if '日' in date_str else 1

    return f"{year}-{month:02d}-{day:02d}"


def main():
    """主函数 - 命令行入口"""
    if len(sys.argv) < 2:
        print("用法: python scheduler.py <命令> [参数]")
        print("命令: parse, schedule, gantt, material")
        sys.exit(1)

    command = sys.argv[1]

    if command == "parse":
        # 解析输入数据
        input_text = sys.stdin.read() if len(sys.argv) == 2 else sys.argv[2]
        print(json.dumps({"status": "ready", "message": "数据已接收，请提供完整数据"}))
    elif command == "demo":
        # 运行演示
        scheduler = ProductionScheduler()

        # 添加示例工单
        scheduler.add_order(WorkOrder(
            order_id="WO001",
            product="电机壳体",
            quantity=100,
            due_date="2026-04-20",
            priority=1,
            bom={"原材料A": 2.0, "原材料B": 1.0}
        ))
        scheduler.add_order(WorkOrder(
            order_id="WO002",
            product="连接支架",
            quantity=200,
            due_date="2026-04-22",
            priority=2,
            bom={"原材料A": 1.0, "原材料B": 0.5}
        ))

        # 添加示例物料
        scheduler.add_material(Material(name="原材料A", stock=500, on_the_way=200, lead_time=5))
        scheduler.add_material(Material(name="原材料B", stock=300, on_the_way=100, lead_time=3))

        # 添加示例工序
        scheduler.add_process(Process(name="CNC加工", capacity=200, time_per_unit=30))
        scheduler.add_process(Process(name="装配", capacity=150, time_per_unit=20))

        # 执行排程
        scheduler.run_scheduler(mode="backward")

        # 输出结果
        result = {
            "summary": scheduler.get_summary(),
            "schedule": scheduler.generate_gantt_data(),
            "materials": scheduler.get_material_requirements()
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
