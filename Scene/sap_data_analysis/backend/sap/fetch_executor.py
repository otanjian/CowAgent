"""执行 QueryPlan 并从 SAP 读取数据。

支持三种来源：
- table: 单表查询，直接调用 provider.fetch_by_plan 或 RFC_READ_TABLE；
- multi_table: 多表关联。若 provider 支持 ADT SQL 则直接执行 JOIN；
  否则主表用 RFC_READ_TABLE，关联表按关联字段批量读取后在内存 merge；
- bapi: 调用指定 BAPI/RFC，从返回结果中提取指定内表。

本模块是纯粹的 SAP 数据读取执行器，不包含任何错误修正或重试逻辑。
错误修正由上层 sap_data_analysis.py 通过 LLM 驱动完成。
"""

import re
from collections import OrderedDict
from typing import Any, Dict, List

from common.log import logger

from .provider import SAPDataProvider
from .query_planner import JoinPlan, QueryPlan


class FetchExecutor:
    """SAP 数据查询执行器。"""

    def __init__(self, provider: SAPDataProvider):
        self.provider = provider

    def execute(self, plan: QueryPlan) -> List[Dict[str, Any]]:
        """根据 QueryPlan 执行查询并返回结构化数据。"""
        logger.info(
            f"[FetchExecutor] intent={plan.intent} source={plan.source} "
            f"table={plan.table} max_rows={plan.max_rows}"
        )

        if plan.pre_query:
            keys = self._execute_pre_query(plan.pre_query)
            plan = self._render_pre_query_keys(plan, keys)

        if plan.source == "table":
            return self._execute_table(plan)
        if plan.source == "multi_table":
            return self._execute_multi_table(plan)
        if plan.source == "bapi":
            return self._execute_bapi(plan)

        raise ValueError(f"不支持的查询来源: {plan.source}")

    def _execute_table(self, plan: QueryPlan) -> List[Dict[str, Any]]:
        if hasattr(self.provider, "fetch_by_plan"):
            rows = self.provider.fetch_by_plan(plan)
        else:
            # 兼容旧版 provider（理论上不应走到这里）
            raise RuntimeError("当前 SAP Provider 不支持 fetch_by_plan")

        # RFC_READ_TABLE 不支持 SQL 聚合，统一在应用层按 plan 聚合
        rows = self._apply_aggregation(rows, plan)
        return self._apply_order_and_limit(rows, plan)

    def _execute_multi_table(self, plan: QueryPlan) -> List[Dict[str, Any]]:
        # 优先使用 ADT SQL 直接执行 JOIN
        if hasattr(self.provider, "execute_sql"):
            sql = self._build_join_sql(plan)
            logger.debug(f"[FetchExecutor] ADT SQL: {sql}")
            rows = self.provider.execute_sql(sql, max_rows=plan.max_rows)
            rows = self._apply_aggregation(rows, plan)
            return self._apply_order_and_limit(rows, plan)

        # RFC 方式：主表读取 + 关联表批量读取 + 内存 merge
        rows = self._execute_multi_table_rfc(plan)
        rows = self._apply_aggregation(rows, plan)
        return self._apply_order_and_limit(rows, plan)

    def _execute_multi_table_rfc(self, plan: QueryPlan) -> List[Dict[str, Any]]:
        if not hasattr(self.provider, "fetch_by_plan") or not hasattr(
            self.provider, "_fetch_table"
        ):
            raise RuntimeError("当前 SAP Provider 不支持多表 RFC 查询")

        # 直接调用底层 _fetch_table，避免 RfcProvider.fetch_by_plan 对 source=multi_table 的限制
        main_fields = self._extract_main_fields(plan)
        main_where = self._filter_main_where(plan)
        logger.info(
            f"[FetchExecutor] RFC multi_table main_table={plan.table} "
            f"fields={main_fields} where={main_where}"
        )
        main_rows = self.provider._fetch_table(
            table=plan.table,
            fields=main_fields,
            where=main_where,
            max_rows=plan.max_rows,
            batch_size=5000,
        )
        if not main_rows or not plan.joins:
            return self._apply_field_mapping(main_rows, plan.field_mapping, plan.table)

        # 计算每个关联表必须读取的最小字段集合，避免读取全表字段导致 DATA_BUFFER_EXCEEDED
        join_fields_list = self._compute_join_fields(plan)
        for join, join_fields in zip(plan.joins, join_fields_list):
            main_rows = self._merge_join_rfc(
                main_rows, join, plan.max_rows, join_fields
            )

        return self._apply_field_mapping(main_rows, plan.field_mapping, plan.table)

    def _parse_join_on(
        self, on: str, join_table: str
    ) -> List[tuple[str, str, str, str]]:
        """解析 ON 条件为 (left_table, left_field, right_table, right_field) 列表。

        支持复合关联键，例如：
            BKPF.BUKRS = BSEG.BUKRS AND BKPF.BELNR = BSEG.BELNR AND BKPF.GJAHR = BSEG.GJAHR
        根据 join_table 判断等式右侧；无法判断时默认等号左侧为 left。
        """
        join_table_upper = join_table.upper()
        conditions = re.split(r"\s+AND\s+", on, flags=re.IGNORECASE)
        pairs: List[tuple[str, str, str, str]] = []

        for cond in conditions:
            parts = [p.strip() for p in cond.split("=")]
            if len(parts) != 2:
                continue
            left_raw, right_raw = parts

            def _parse(expr: str) -> tuple[str, str]:
                expr = expr.strip()
                if "." in expr:
                    table, field = expr.rsplit(".", 1)
                    return table.upper(), field.upper()
                return "", expr.upper()

            left_table, left_field = _parse(left_raw)
            right_table, right_field = _parse(right_raw)

            # 等式右侧属于 join_table 时，保持原方向
            if right_table == join_table_upper and left_table != join_table_upper:
                pairs.append((left_table, left_field, right_table, right_field))
            # 等式左侧属于 join_table 时，交换方向
            elif left_table == join_table_upper and right_table != join_table_upper:
                pairs.append((right_table, right_field, left_table, left_field))
            else:
                # 无法判断时默认等号左侧为 left
                pairs.append((left_table, left_field, right_table, right_field))

        return pairs

    def _compute_join_fields(self, plan: QueryPlan) -> List[List[str]]:
        """根据 plan.fields 和下游关联条件，计算每个 join 必须读取的最小字段。"""
        result: List[List[str]] = []
        for i, join in enumerate(plan.joins):
            required: set[str] = set()
            join_table = join.table.upper()

            # 1. 本表在 plan.fields 中出现的字段
            for f in plan.fields:
                parts = f.split(".")
                if len(parts) == 2 and parts[0].upper() == join_table:
                    required.add(parts[1].upper())

            # 2. 本表关联键（用于和左侧表匹配）
            for _, _, _, right_field in self._parse_join_on(join.on, join.table):
                required.add(right_field)

            # 3. 下游关联需要本表提供的字段
            for downstream in plan.joins[i + 1 :]:
                for left_table, left_field, _, _ in self._parse_join_on(
                    downstream.on, downstream.table
                ):
                    if left_table == join_table:
                        required.add(left_field)

            result.append(list(required))
        return result

    def _extract_main_fields(self, plan: QueryPlan) -> List[str]:
        """从 plan.fields 中提取属于主表的字段（去掉主表前缀）。"""
        main_table = plan.table.upper()
        main_fields = []
        for f in plan.fields:
            parts = f.split(".")
            if len(parts) == 2 and parts[0].upper() == main_table:
                main_fields.append(parts[1])
            elif len(parts) == 1:
                main_fields.append(parts[0])
        if not main_fields:
            # 主表字段为空时，从 joins 中找主表关联字段兜底
            for join in plan.joins:
                for left_table, left_field, _, _ in self._parse_join_on(
                    join.on, join.table
                ):
                    if left_table == main_table:
                        main_fields.append(left_field)
        return main_fields

    def _filter_main_where(self, plan: QueryPlan) -> str:
        """过滤 WHERE，只保留涉及主表字段的条件，并去掉主表前缀。"""
        where = (plan.where or "").strip()
        if not where:
            return ""
        main_table = plan.table.upper()
        refs = re.findall(r"\b([A-Z][A-Z0-9_]*)\.", where.upper())
        if all(ref == main_table for ref in refs):
            return self._strip_table_prefix(where, main_table)
        logger.warning(
            f"[FetchExecutor] WHERE 包含非主表引用，清空以避免 RFC_READ_TABLE 报错: {where}"
        )
        return ""

    def _strip_table_prefix(self, where: str, table: str) -> str:
        """去掉 WHERE 中的表名前缀，使其适配 RFC_READ_TABLE。"""
        if not where:
            return ""
        pattern = re.compile(rf"\b{re.escape(table)}\.", re.IGNORECASE)
        return pattern.sub("", where)

    def _apply_field_mapping(
        self,
        rows: List[Dict[str, Any]],
        mapping: Dict[str, str],
        main_table: str = "",
    ) -> List[Dict[str, Any]]:
        """根据 field_mapping 把字段名映射为中文表头。

        兼容 RFC 模式下主表字段不带前缀、关联表字段带表前缀的情况。
        """
        if not mapping or not rows:
            return rows
        result = []
        main_table_upper = (main_table or "").upper()
        for row in rows:
            new_row: Dict[str, Any] = {}
            # 先确定哪些无前缀字段存在带前缀版本，避免输出重复列
            prefixed_keys = {k.split(".")[-1] for k in row if "." in k}
            for k, v in row.items():
                # 若该字段有无前缀的重复版本，优先保留带前缀版本
                if "." not in k and k in prefixed_keys:
                    continue
                new_k = mapping.get(k)
                if new_k is None and main_table_upper and "." not in k:
                    new_k = mapping.get(f"{main_table_upper}.{k}")
                if new_k is None and "." in k:
                    new_k = mapping.get(k.split(".")[-1])
                new_row[new_k if new_k is not None else k] = v
            result.append(new_row)
        return result

    def _merge_join_rfc(
        self,
        main_rows: List[Dict[str, Any]],
        join: JoinPlan,
        max_rows: int,
        join_fields: List[str],
    ) -> List[Dict[str, Any]]:
        """在 RFC 模式下按关联字段批量读取关联表并做连接。

        支持单字段或多字段（AND 连接）关联条件。
        尊重 join.join_type：left 保留所有主表行；inner 只保留匹配行。
        """
        join_conditions = self._parse_join_on(join.on, join.table)
        if not join_conditions:
            raise ValueError(f"无法解析关联条件: {join.on}")

        join_table_upper = join.table.upper()

        def _get_value(row: Dict[str, Any], field: str, table: str = "") -> Any:
            """优先读取无前缀字段，再尝试 table 前缀。"""
            if field in row:
                return row[field]
            if table and f"{table}.{field}" in row:
                return row[f"{table}.{field}"]
            return None

        def _make_key(row: Dict[str, Any], right_side: bool = False) -> tuple:
            """构造复合关联键元组。right_side=True 表示关联表行。"""
            parts = []
            for left_table, left_field, _, right_field in join_conditions:
                field = right_field if right_side else left_field
                table = join_table_upper if right_side else left_table
                value = _get_value(row, field, table)
                parts.append(str(value).strip() if value is not None else "")
            return tuple(parts)

        # 收集主表复合关联键，去重并过滤空值
        join_keys: List[tuple] = []
        seen: set[tuple] = set()
        for row in main_rows:
            key = _make_key(row, right_side=False)
            if not any(key):
                continue
            if key not in seen:
                seen.add(key)
                join_keys.append(key)
        if not join_keys:
            return main_rows

        # 分批构造关联表过滤条件，避免 OPTIONS 超长
        batch_size = 100
        join_map: Dict[tuple, Dict[str, Any]] = {}
        for i in range(0, len(join_keys), batch_size):
            batch = join_keys[i : i + batch_size]
            or_clauses: List[str] = []
            for key in batch:
                and_clauses: List[str] = []
                for idx, (_, _, _, right_field) in enumerate(join_conditions):
                    escaped = key[idx].replace("'", "''")
                    and_clauses.append(f"{right_field} = '{escaped}'")
                if len(and_clauses) == 1:
                    or_clauses.append(and_clauses[0])
                else:
                    # SAP Open SQL 中 AND 优先级高于 OR，无需括号即可分组
                    or_clauses.append(" AND ".join(and_clauses))

            if len(or_clauses) == 1:
                in_clause = or_clauses[0]
            else:
                in_clause = " OR ".join(or_clauses)

            join_where = self._strip_table_prefix(join.where, join.table)
            if join_where:
                # 不用括号包裹，SAP 解析器对 AND (expr) 形式的括号会报错
                where = f"{join_where} AND {in_clause}"
            else:
                where = in_clause

            rows = self.provider._fetch_table(
                table=join.table,
                fields=join_fields,
                where=where,
                max_rows=max_rows,
                # 关联表批量读取时缩小批次，避免 DATA_BUFFER_EXCEEDED
                batch_size=1000,
            )
            for row in rows:
                key = _make_key(row, right_side=True)
                if any(key):
                    join_map[key] = row

        is_inner = join.join_type.upper() == "INNER"
        merged = []
        for row in main_rows:
            key = _make_key(row, right_side=False)
            matched = any(key) and key in join_map
            if is_inner and not matched:
                # INNER JOIN：未命中关联表则丢弃该行
                continue
            joined_row = dict(row)
            if matched:
                for k, v in join_map[key].items():
                    if k not in joined_row:
                        # 同时保留无前缀（方便后续 join）和带表前缀（与 field_mapping 对齐）
                        joined_row[k] = v
                        joined_row[f"{join.table}.{k}"] = v
            merged.append(joined_row)

        return merged

    def _execute_bapi(self, plan: QueryPlan) -> List[Dict[str, Any]]:
        if not hasattr(self.provider, "call_bapi"):
            raise RuntimeError("当前 SAP Provider 不支持 BAPI 调用")

        logger.info(f"[FetchExecutor] call_bapi {plan.bapi_name}")
        result = self.provider.call_bapi(plan.bapi_name, plan.bapi_parameters)

        if not plan.bapi_output_table_path:
            # 未指定输出内表路径时，尝试自动发现第一个内表
            rows = self._extract_first_table(result)
        else:
            data = result
            for key in plan.bapi_output_table_path.split("."):
                data = data.get(key, {})
                if isinstance(data, list):
                    break
            if not isinstance(data, list):
                raise ValueError(
                    f"BAPI {plan.bapi_name} 返回结果中未找到内表路径 {plan.bapi_output_table_path}"
                )
            rows = data

        # 按 plan.fields 过滤，只返回需要的字段
        rows = self._filter_rows_by_fields(rows, plan.fields)
        rows = self._apply_aggregation(rows, plan)
        return self._apply_order_and_limit(rows, plan)

    def _filter_rows_by_fields(
        self, rows: List[Dict[str, Any]], fields: List[str]
    ) -> List[Dict[str, Any]]:
        """只保留 rows 中出现在 fields 里的字段（支持带表前缀）。"""
        if not rows or not fields:
            return rows
        wanted = {f.upper() for f in fields}
        wanted_no_prefix = {f.split(".")[-1].upper() for f in fields if "." in f}
        result = []
        for row in rows:
            new_row: Dict[str, Any] = {}
            for k, v in row.items():
                kupper = k.upper()
                if kupper in wanted or kupper in wanted_no_prefix:
                    new_row[k] = v
            result.append(new_row)
        return result

    def _extract_first_table(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        for value in result.values():
            if isinstance(value, list) and value:
                return value
        return []

    def _execute_pre_query(self, pre_query: Dict[str, Any]) -> List[str]:
        """执行前置 BAPI 查询，提取关键字段值列表用于主查询 IN 条件。"""
        source = pre_query.get("source", "bapi")
        if source != "bapi":
            raise ValueError("当前仅支持 BAPI 作为前置查询")

        temp_plan = QueryPlan(
            intent="pre_query",
            domain=pre_query.get("domain", "master_data"),
            source="bapi",
            bapi_name=pre_query.get("bapi_name", ""),
            bapi_parameters=pre_query.get("bapi_parameters", {}),
            bapi_output_table_path=pre_query.get("bapi_output_table_path", ""),
            max_rows=pre_query.get("max_rows", 5000),
        )
        rows = self._execute_bapi(temp_plan)
        key_field = pre_query.get("output_key_field", "")
        if not key_field:
            raise ValueError("pre_query 必须指定 output_key_field")

        keys: List[str] = []
        seen: set[str] = set()
        for row in rows:
            val = row.get(key_field)
            if val is None:
                continue
            s = str(val).strip()
            if s and s not in seen:
                seen.add(s)
                keys.append(s)
        logger.info(f"[FetchExecutor] pre_query returned {len(keys)} unique keys")
        return keys

    @staticmethod
    def _escape(value: str) -> str:
        """转义 SQL 字符串中的单引号。"""
        return value.replace("'", "''")

    def _render_pre_query_keys(self, plan: QueryPlan, keys: List[str]) -> QueryPlan:
        """将主查询 WHERE 中的 {PRE_QUERY_KEYS} 替换为前置查询得到的 key 列表。"""
        if not keys:
            # 无 key 时让 IN 条件恒假，避免全表扫描
            plan.where = re.sub(
                r"\b([A-Za-z0-9_]+\.)?[A-Za-z0-9_]+\s+IN\s*\(\s*\{PRE_QUERY_KEYS\}\s*\)",
                "1 = 0",
                plan.where,
                flags=re.IGNORECASE,
            )
            return plan

        if len(keys) > 1000:
            logger.warning(f"[FetchExecutor] pre_query keys truncated from {len(keys)} to 1000")
            keys = keys[:1000]

        escaped = [self._escape(k) for k in keys]
        placeholder = ",".join(f"'{k}'" for k in escaped)
        plan.where = plan.where.replace("{PRE_QUERY_KEYS}", placeholder)
        return plan

    def _apply_aggregation(
        self, rows: List[Dict[str, Any]], plan: QueryPlan
    ) -> List[Dict[str, Any]]:
        """RFC_READ_TABLE 不支持 SQL 聚合，按 plan.aggregation 在应用层汇总。"""
        if not rows or not plan.aggregation:
            return rows

        agg_type = plan.aggregation.upper()
        mapping = plan.field_mapping or {}

        # 判断数据是否已经过字段映射（存在中文 key）
        sample_row = rows[0]
        is_mapped = any(k in sample_row for k in mapping.values())

        def _resolve_key(field: str) -> str:
            """返回在结果行中应使用的 key：若已映射则优先用中文，否则用英文。"""
            base = field.split(".")[-1].upper()
            if is_mapped:
                return mapping.get(base, base)
            return base

        def _resolve_group_field(field: str) -> tuple[str, str]:
            """返回 (英文 key, 结果行中使用的 key)。"""
            base = field.split(".")[-1].upper()
            out = mapping.get(base, base) if is_mapped else base
            return base, out

        # 识别需要聚合的字段：显式 SUM(field) 或金额/数量字段
        agg_specs: List[tuple[str, str, str]] = []  # (读取 key, 聚合方式, 输出 key)
        for f in plan.fields:
            clean = f.strip()
            m = re.match(
                r"^(SUM|COUNT|AVG|MIN|MAX)\s*\(\s*([A-Za-z0-9_\.]+)\s*\)$",
                clean,
                re.IGNORECASE,
            )
            if m:
                base = m.group(2).split(".")[-1].upper()
                agg_specs.append((base, m.group(1).upper(), _resolve_key(base)))
                continue
            base = clean.split(".")[-1].upper()
            if base in {
                "NETWR",
                "BRTWR",
                "NETPR",
                "DMBTR",
                "WRBTR",
                "MENGE",
                "KWMENG",
                "KWMENGE",
                "GAMNG",
            }:
                agg_specs.append((base, agg_type, _resolve_key(base)))

        if not agg_specs:
            return rows

        group_specs = [_resolve_group_field(g) for g in plan.group_by]

        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for row in rows:
            key = tuple(str(row.get(out, row.get(base, ""))) for base, out in group_specs)
            groups.setdefault(key, []).append(row)

        result_rows: List[Dict[str, Any]] = []
        for key, group_rows in groups.items():
            new_row: Dict[str, Any] = {}
            for base, out in group_specs:
                new_row[out] = group_rows[0].get(out, group_rows[0].get(base, ""))
            for base, op, out in agg_specs:
                values = []
                for r in group_rows:
                    # 支持已映射和未映射两种 key
                    val = r.get(out, r.get(base, "0")) or "0"
                    try:
                        values.append(float(val))
                    except (ValueError, TypeError):
                        pass
                if op == "SUM":
                    new_row[out] = sum(values)
                elif op == "COUNT":
                    new_row[out] = len(values)
                elif op == "AVG":
                    new_row[out] = sum(values) / len(values) if values else 0
                elif op == "MIN":
                    new_row[out] = min(values) if values else 0
                elif op == "MAX":
                    new_row[out] = max(values) if values else 0
                else:
                    new_row[out] = sum(values)
            result_rows.append(new_row)

        # 保持原始排序语义
        if plan.order_by:
            sort_base = plan.order_by[0].split()[0].split(".")[-1].upper()
            sort_out = _resolve_key(sort_base)
            reverse = "DESC" in plan.order_by[0].upper()
            try:
                result_rows.sort(key=lambda r: r.get(sort_out, 0), reverse=reverse)
            except Exception:
                pass

        return result_rows

    def _apply_order_and_limit(
        self, rows: List[Dict[str, Any]], plan: QueryPlan
    ) -> List[Dict[str, Any]]:
        """对原始或聚合后的结果应用排序与 Top-N 截断。

        RFC_READ_TABLE / ADT SQL 不保证按数值大小排序，且 Open SQL 中字符型数字
        按字符串排序会出现 "9" > "10" 的问题，因此在应用层统一做一次排序修正。
        """
        if not rows:
            return rows

        if plan.order_by:
            rows = self._sort_rows(rows, plan.order_by, plan.field_mapping)
        elif plan.top_n and plan.top_n > 0:
            # Top N 但没有 order_by 时，自动按第一个数值金额字段降序
            auto_field = self._detect_numeric_field(rows, plan.fields)
            if auto_field:
                rows = self._sort_rows(rows, [f"{auto_field} DESC"], plan.field_mapping)

        if plan.top_n and plan.top_n > 0:
            rows = rows[: plan.top_n]

        return rows

    def _detect_numeric_field(
        self, rows: List[Dict[str, Any]], fields: List[str]
    ) -> str:
        """从 fields 中找一个可转为数字的字段名，优先金额/数量字段。"""
        priority_names = {"NETWR", "BRTWR", "DMBTR", "WRBTR", "NETPR", "MENGE", "KWMENG", "GAMNG"}
        candidates = [f.split(".")[-1].upper() for f in fields] if fields else list(rows[0].keys())
        for c in candidates:
            if c in priority_names and self._is_numeric_column(rows, c):
                return c
        for c in candidates:
            if self._is_numeric_column(rows, c):
                return c
        return ""

    def _is_numeric_column(self, rows: List[Dict[str, Any]], field: str) -> bool:
        field_upper = field.upper()
        for row in rows:
            for k, v in row.items():
                if k.upper() == field_upper and v is not None and v != "":
                    try:
                        float(v)
                    except (ValueError, TypeError):
                        return False
        return True

    def _sort_rows(
        self,
        rows: List[Dict[str, Any]],
        order_by: List[str],
        field_mapping: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """按 order_by 对行排序；若排序字段可全部转为数字，则按数值大小排序。"""
        if not order_by:
            return rows

        sort_expr = order_by[0].strip()
        parts = sort_expr.split()
        field_raw = parts[0]
        reverse = len(parts) > 1 and parts[1].upper() == "DESC"

        base_field = field_raw.split(".")[-1].upper()
        sort_key = field_mapping.get(base_field, base_field) if field_mapping else base_field

        # 判断是否能按数字排序
        numeric = True
        for row in rows:
            val = row.get(sort_key)
            if val is not None and val != "":
                try:
                    float(val)
                except (ValueError, TypeError):
                    numeric = False
                    break

        def _sort_key(row: Dict[str, Any]):
            val = row.get(sort_key)
            if val is None or val == "":
                # 空值统一放在最后
                return float("-inf") if reverse else float("inf")
            if numeric:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return 0.0
            return str(val)

        try:
            rows.sort(key=_sort_key, reverse=reverse)
        except Exception as e:
            logger.warning(f"[FetchExecutor] 应用层排序失败: {e}")

        return rows

    def _build_join_sql(self, plan: QueryPlan) -> str:
        """为 ADT SQL provider 构造 Open SQL JOIN 语句。

        Open SQL 要求：
        - 别名与字段之间使用 ~（如 a~carrid）
        - LEFT JOIN 必须写为 LEFT OUTER JOIN
        - ORDER BY 使用 DESCENDING / ASCENDING
        """

        def _strip_prefix(field: str) -> str:
            return field.split(".")[-1]

        main_alias = plan.table[0].lower()
        aliases = {plan.table.upper(): main_alias}
        aliases.update(
            {join.table.upper(): f"j{i}" for i, join in enumerate(plan.joins)}
        )

        def _table_dot_to_tilde(expr: str) -> str:
            """把表达式中的 TABLE.FIELD 转换为 alias~FIELD（Open SQL 语法）。"""
            result = expr
            for table, alias in aliases.items():
                result = re.sub(
                    rf"\b{re.escape(table)}\.",
                    f"{alias}~",
                    result,
                    flags=re.IGNORECASE,
                )
            return result

        fields = []
        for f in plan.fields:
            parts = f.split(".")
            if len(parts) == 2:
                table = parts[0].upper()
                field = parts[1]
                alias = aliases.get(table, main_alias)
            else:
                alias = main_alias
                field = f
            fields.append(
                f"{alias}~{_strip_prefix(field)} AS {_strip_prefix(field)}"
            )

        joins_sql = []
        for i, join in enumerate(plan.joins):
            alias = f"j{i}"
            raw_type = join.join_type.upper()
            if raw_type == "LEFT":
                join_type = "LEFT OUTER"
            elif raw_type == "RIGHT":
                join_type = "RIGHT OUTER"
            else:
                join_type = raw_type
            joins_sql.append(
                f"{join_type} JOIN {join.table} AS {alias} ON {_table_dot_to_tilde(join.on)}"
            )
            if join.where:
                joins_sql.append(f"AND {_table_dot_to_tilde(join.where)}")

        sql = f"SELECT {', '.join(fields)} FROM {plan.table} AS {main_alias}"
        if joins_sql:
            sql += " " + " ".join(joins_sql)
        if plan.where:
            sql += f" WHERE {_table_dot_to_tilde(plan.where)}"
        if plan.order_by:
            order_parts = []
            for ob in plan.order_by:
                ob = _table_dot_to_tilde(ob)
                ob = re.sub(r"\bDESC\b", "DESCENDING", ob, flags=re.IGNORECASE)
                ob = re.sub(r"\bASC\b", "ASCENDING", ob, flags=re.IGNORECASE)
                order_parts.append(ob)
            sql += f" ORDER BY {', '.join(order_parts)}"
        sql += f" UP TO {plan.max_rows} ROWS"
        return sql

