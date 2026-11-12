"""分析操作日志业务规则：状态流转、字段校验、按对象串联的审计工作区都收在这里。

审计工作区（workspace）按「操作对象」把同一业务对象的动作串成链路：
指标口径 INDI-0002 会带出绑定它的查询模板 QUER-0002 与数据服务 SERV-0002，
再把这些对象上的操作日志按时间排成一条时间线。

所有对外字段都经过 ``audit_policy.mask_record``：原始参数只留在仓库里，
接口永远不回明文。日志缺失、采集异常作为独立标记返回，不会被伪装成已完成记录，
也不会和真实日志混在一起重复展示。
"""
from __future__ import annotations

from typing import Any, Iterable

from app.services import audit_policy
from app.store import store

MODULE = "log"
REQUIRED_FIELDS = ["日志编号", "操作类型", "操作对象"]
STATUS_ORDER = ["已记录", "已复核", "已归档"]
ACTION_RULES = {"标记复核": "已记录", "归档记录": "已复核"}
NEGATIVE_ACTIONS = []

MISSING_MODULE = "audit_missing"

# 操作对象类型与「编号字段 / 名称字段」的对应关系；日志里能串起来的对象都从这里取。
OBJECT_TYPES: dict[str, dict[str, str]] = {
    "指标口径": {"module": "indicator", "code": "指标编号", "name": "指标名称"},
    "查询模板": {"module": "query", "code": "模板编号", "name": "模板名称"},
    "数据服务": {"module": "service", "code": "服务编号", "name": "服务名称"},
    "定时任务": {"module": "schedule", "code": "任务编号", "name": "任务名称"},
    "数据源连接": {"module": "datasource", "code": "连接编号", "name": "连接名称"},
    "数据集": {"module": "dataset", "code": "数据集编号", "name": "数据集名称"},
}

CODE_PREFIX_TYPES = {
    "INDI": "指标口径",
    "QUER": "查询模板",
    "SERV": "数据服务",
    "SCHE": "定时任务",
    "DATA": "数据源连接",
}

ACTION_FIELD = "操作类型"
OPERATOR_FIELD = "操作人"
TIME_FIELD = "操作时间"
OBJECT_FIELD = "操作对象"
TYPE_FIELD = "操作对象类型"


def object_type_for_code(code: str, explicit: str | None = None) -> str:
    if explicit and explicit in OBJECT_TYPES:
        return explicit
    prefix = code.split("-")[0].upper() if "-" in code else code[:4].upper()
    return CODE_PREFIX_TYPES.get(prefix, "其他对象")


def normalize_time_bound(value: str | None) -> str:
    """把前端 datetime-local 的 ``YYYY-MM-DDTHH:MM`` 归一成仓库里的空格分隔格式。

    只做格式对齐，不补默认时区；空值原样返回。归一后字符串比较才能和操作时间对上。
    """
    if not value:
        return ""
    return value.strip().replace("T", " ")


def find_object(code: str, object_type: str | None = None) -> dict[str, Any] | None:
    """按对象编号在对应业务模块里找到原始行（未脱敏，仅用于判权与取名）。"""
    resolved = object_type or object_type_for_code(code)
    config = OBJECT_TYPES.get(resolved)
    if config is None:
        return None
    for row in store.rows(config["module"]):
        if str(row.get(config["code"], "")).strip() == code:
            return row
    return None


def object_name(code: str, object_type: str | None = None) -> str:
    row = find_object(code, object_type)
    config = OBJECT_TYPES.get(object_type or object_type_for_code(code))
    if row is not None and config is not None:
        return str(row.get(config["name"], "") or code)
    return code


def related_object_codes(primary_code: str) -> list[tuple[str, str]]:
    """返回与主对象串联的其他对象 (编号, 类型)，按指标口径→查询模板→数据服务排序。

    规则：先找出主对象绑定的指标（查询模板 / 数据服务上的「关联指标」字段），
    再把所有绑定到同一指标的查询模板与数据服务一并纳入链路。
    """
    primary_type = object_type_for_code(primary_code)
    anchor = primary_code
    if primary_type != "指标口径":
        primary_row = find_object(primary_code, primary_type)
        anchor = str((primary_row or {}).get("关联指标", "") or "").strip()

    links: list[tuple[str, str]] = []
    if primary_type != "指标口径":
        links.append((primary_code, primary_type))
    if anchor:
        links.append((anchor, "指标口径"))
    if anchor:
        query_cfg = OBJECT_TYPES["查询模板"]
        service_cfg = OBJECT_TYPES["数据服务"]
        for row in store.rows(query_cfg["module"]):
            if str(row.get("关联指标", "") or "") == anchor:
                code = str(row.get(query_cfg["code"], "") or "")
                links.append((code, "查询模板"))
        for row in store.rows(service_cfg["module"]):
            if str(row.get("关联指标", "") or "") == anchor:
                code = str(row.get(service_cfg["code"], "") or "")
                links.append((code, "数据服务"))

    # 去重并保持顺序；主对象永远排在最前。
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = [(primary_code, primary_type)]
    for code, kind in links:
        if code and code not in seen and code != primary_code:
            seen.add(code)
            ordered.append((code, kind))
    return ordered


class LogService:
    # -- 基础能力（列表 / 明细 / 登记 / 流转） --------------------------------

    def _filter_rows(self, filters: dict[str, str | None]) -> list[dict[str, Any]]:
        rows = list(store.rows(MODULE))
        operator = (filters.get("operator") or "").strip()
        action_type = (filters.get("action_type") or "").strip()
        object_code = (filters.get("object") or "").strip()
        date_from = normalize_time_bound(filters.get("date_from"))
        date_to = normalize_time_bound(filters.get("date_to"))
        status = (filters.get("status") or "").strip()
        if operator:
            rows = [row for row in rows if operator in str(row.get(OPERATOR_FIELD, ""))]
        if action_type:
            rows = [row for row in rows if action_type in str(row.get(ACTION_FIELD, ""))]
        if object_code:
            rows = [row for row in rows if str(row.get(OBJECT_FIELD, "")) == object_code]
        if date_from:
            rows = [row for row in rows if str(row.get(TIME_FIELD, "")) >= date_from]
        if date_to:
            rows = [row for row in rows if str(row.get(TIME_FIELD, "")) <= date_to]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        context: audit_policy.AuditContext | None = None,
        operator: str | None = None,
        action_type: str | None = None,
        object_code: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """列表始终脱敏返回，并剔除当前部门无权查看的日志，保证与详情一致。"""
        context = context or audit_policy.AuditContext(audit_policy.DEFAULT_DEPARTMENT, audit_policy.DEFAULT_ROLE)
        rows = self._filter_rows({
            "operator": operator,
            "action_type": action_type,
            "object": object_code,
            "date_from": date_from,
            "date_to": date_to,
            "status": status,
        })
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("日志编号", ""))]

        visible: list[dict[str, Any]] = []
        for row in rows:
            code = str(row.get(OBJECT_FIELD, ""))
            obj = find_object(code, str(row.get(TYPE_FIELD, "") or None))
            mode = audit_policy.resolve_access(obj or row, context, str(row.get("归属部门", "") or ""))
            if mode == audit_policy.DENIED_MODE:
                continue
            masked = audit_policy.mask_record(MODULE, row)
            masked["访问模式"] = mode
            masked["访问说明"] = audit_policy.access_label(mode)
            if str(row.get("采集状态", "")) not in ("", "正常"):
                masked["采集标记"] = share_missing_marker(row)
            visible.append(masked)

        visible.sort(key=lambda item: str(item.get(TIME_FIELD, "")), reverse=True)
        total = len(visible)
        start = max(page - 1, 0) * size
        return visible[start:start + size], total

    def get_entry(
        self,
        entry_id: int,
        context: audit_policy.AuditContext | None = None,
    ) -> dict[str, Any] | None:
        context = context or audit_policy.AuditContext(audit_policy.DEFAULT_DEPARTMENT, audit_policy.DEFAULT_ROLE)
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        code = str(entry.get(OBJECT_FIELD, ""))
        obj = find_object(code, str(entry.get(TYPE_FIELD, "") or None))
        mode = audit_policy.resolve_access(obj or entry, context, str(entry.get("归属部门", "") or ""))
        audit_policy.ensure_access(mode, code)
        masked = audit_policy.mask_record(MODULE, entry)
        masked["访问模式"] = mode
        masked["访问说明"] = audit_policy.access_label(mode)
        if str(entry.get("采集状态", "")) not in ("", "正常"):
            masked["采集标记"] = share_missing_marker(entry)
        return masked

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        code = str(values.get(OBJECT_FIELD) or "")
        entry[TYPE_FIELD] = values.get(TYPE_FIELD) or object_type_for_code(code)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["采集状态"] = "正常"
        entry["敏感参数"] = ""
        rows.append(entry)
        return audit_policy.mask_record(MODULE, entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: audit_policy.AuditContext | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        context = context or audit_policy.AuditContext(audit_policy.DEFAULT_DEPARTMENT, audit_policy.DEFAULT_ROLE)
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"操作日志 {entry_id} 不存在或已归档"
        code = str(entry.get(OBJECT_FIELD, ""))
        obj = find_object(code, str(entry.get(TYPE_FIELD, "") or None))
        mode = audit_policy.resolve_access(obj or entry, context, str(entry.get("归属部门", "") or ""))
        if mode != audit_policy.OWNER_MODE:
            # 越权动作直接拒绝；拒绝路径不修改、不追加任何记录。
            return None, f"无权对操作对象 {code} 执行动作：当前部门没有归属权限"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分析操作日志可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return audit_policy.mask_record(MODULE, entry), f"操作日志已{action}"

    # -- 审计工作区 ---------------------------------------------------------

    def list_objects(self, context: audit_policy.AuditContext) -> list[dict[str, Any]]:
        """可聚合成链路的主对象清单；越权对象不返回。"""
        groups: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            code = str(row.get(OBJECT_FIELD, "") or "")
            if not code:
                continue
            kind = object_type_for_code(code, str(row.get(TYPE_FIELD, "") or None))
            obj = find_object(code, kind)
            mode = audit_policy.resolve_access(obj or row, context, str(row.get("归属部门", "") or ""))
            if mode == audit_policy.DENIED_MODE:
                continue
            group = groups.setdefault(code, {
                "object_code": code,
                "object_type": kind,
                "object_name": object_name(code, kind),
                "actions": 0,
                "missing": 0,
                "access_mode": mode,
            })
            group["actions"] += 1

        missing_rows = store.rows(MISSING_MODULE)
        for marker in missing_rows:
            code = str(marker.get("对象编号", ""))
            if not code:
                continue
            if code not in groups:
                # 对象只剩缺失标记、没有存活日志时也要能被审计员选中查看，
                # 否则“日志缺失”会被藏进不可见状态。
                kind = object_type_for_code(code)
                obj = find_object(code, kind)
                mode = audit_policy.resolve_access(obj, context, None)
                if mode == audit_policy.DENIED_MODE:
                    continue
                groups[code] = {
                    "object_code": code,
                    "object_type": kind,
                    "object_name": object_name(code, kind),
                    "actions": 0,
                    "missing": 0,
                    "access_mode": mode,
                }
            groups[code]["missing"] += 1

        return sorted(groups.values(), key=lambda item: str(item["object_code"]))

    def workspace(
        self,
        object_code: str,
        context: audit_policy.AuditContext,
        *,
        operator: str | None = None,
        action_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """按操作对象串联的审计视图。越权请求抛 403，且不产生任何记录。"""
        object_code = object_code.strip()
        if not object_code:
            raise ValueError("缺少操作对象编号")
        primary_type = object_type_for_code(object_code)
        primary_row = find_object(object_code, primary_type)
        mode = audit_policy.resolve_access(primary_row, context, None)
        audit_policy.ensure_access(mode, object_code)

        links = related_object_codes(object_code)
        # 关联对象同样判权：无权访问的对象从链路、时间线与汇总里一并剔除，
        # 避免通过链路结构泄露其他部门存在哪些对象。
        accessible_links = [
            (code, kind) for code, kind in links
            if audit_policy.resolve_access(find_object(code, kind), context, None) != audit_policy.DENIED_MODE
        ]
        link_codes = [code for code, _ in accessible_links]

        filtered = self._filter_rows({
            "operator": operator,
            "action_type": action_type,
            "date_from": date_from,
            "date_to": date_to,
        })
        # 只保留链路内对象，按 id 去重：缺失日志不会被复制成第二条“已完成”记录。
        chain_rows = [
            row for row in filtered
            if str(row.get(OBJECT_FIELD, "")) in link_codes
        ]
        chain_rows = _dedupe_by_id(chain_rows)
        chain_rows.sort(key=lambda item: str(item.get(TIME_FIELD, "")))

        chains: list[dict[str, Any]] = []
        for code, kind in accessible_links:
            actions = [
                _masked_action(row, kind)
                for row in chain_rows
                if str(row.get(OBJECT_FIELD, "")) == code
            ]
            obj = find_object(code, kind)
            obj_mode = audit_policy.resolve_access(obj, context, None)
            config = OBJECT_TYPES.get(kind, {})
            chains.append({
                "object_code": code,
                "object_type": kind,
                "object_name": str((obj or {}).get(config.get("name", ""), "") or object_name(code, kind)),
                "access_mode": obj_mode,
                "access_label": audit_policy.access_label(obj_mode),
                "actions": actions,
                "action_count": len(actions),
                "missing": _missing_for(code, kind),
            })

        missing = _missing_for_codes(link_codes)
        timeline = [
            dict(action, object_type=chain["object_type"], object_code=chain["object_code"], object_name=chain["object_name"])
            for chain in chains
            for action in chain["actions"]
        ]
        timeline.sort(key=lambda item: str(item.get("operation_time", "")), reverse=True)

        action_types = sorted({str(row.get(ACTION_FIELD, "")) for row in chain_rows})
        operators = sorted({str(row.get(OPERATOR_FIELD, "")) for row in chain_rows})
        # 下拉候选取自链路全量（不随筛选收缩），避免选完一个条件后其他候选人消失。
        all_chain_rows = _dedupe_by_id([
            row for row in store.rows(MODULE)
            if str(row.get(OBJECT_FIELD, "")) in link_codes
        ])
        operator_options = sorted({str(row.get(OPERATOR_FIELD, "")) for row in all_chain_rows})
        action_type_options = sorted({str(row.get(ACTION_FIELD, "")) for row in all_chain_rows})
        capture_breaks = [
            action for action in timeline
            if str(action.get("capture_status", "")) not in ("", "正常")
        ]

        primary_name = str((primary_row or {}).get(
            OBJECT_TYPES.get(primary_type, {}).get("name", ""), ""
        ) or object_name(object_code, primary_type))

        return {
            "object": {
                "object_code": object_code,
                "object_type": primary_type,
                "object_name": primary_name,
                "department": str((primary_row or {}).get("归属部门", "") or ""),
                "department_label": audit_policy.DEPARTMENTS.get(
                    str((primary_row or {}).get("归属部门", "") or ""), ""
                ),
            },
            "access": {
                "mode": mode,
                "label": audit_policy.access_label(mode),
                "department": context.department,
                "department_label": context.department_label,
                "role": context.role,
                "role_label": context.role_label,
            },
            "filters": {
                "operator": operator or "",
                "action_type": action_type or "",
                "date_from": normalize_time_bound(date_from),
                "date_to": normalize_time_bound(date_to),
                "operator_options": operator_options,
                "action_type_options": action_type_options,
            },
            "summary": {
                "action_count": len(chain_rows),
                "object_count": len(chains),
                "missing_count": len(missing) + len(capture_breaks),
                "operator_count": len(operators),
                "operators": operators,
                "action_types": action_types,
                "time_range": {
                    "start": str(chain_rows[0].get(TIME_FIELD, "")) if chain_rows else "",
                    "end": str(chain_rows[-1].get(TIME_FIELD, "")) if chain_rows else "",
                },
            },
            "links": [
                {
                    "object_code": code,
                    "object_type": kind,
                    "object_name": object_name(code, kind),
                    "relation": "主对象" if code == object_code else "关联对象",
                    "access_mode": audit_policy.resolve_access(find_object(code, kind), context, None),
                }
                for code, kind in accessible_links
            ],
            "chains": chains,
            "timeline": timeline,
            "missing": missing,
            "capture_breaks": capture_breaks,
        }


def _dedupe_by_id(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        entry_id = int(row.get("id", 0))
        if entry_id in seen:
            continue
        seen.add(entry_id)
        result.append(row)
    return result


def _masked_action(row: dict[str, Any], kind: str) -> dict[str, Any]:
    """单条动作的对外结构：命名稳定、全部脱敏，缺失采集显式标记。"""
    masked = audit_policy.mask_record(MODULE, row)
    capture_status = str(row.get("采集状态", "") or "正常")
    return {
        "id": int(row.get("id", 0)),
        "log_no": str(row.get("日志编号", "")),
        "action_type": str(row.get(ACTION_FIELD, "")),
        "action_label": str(row.get("对象动作", "") or row.get(ACTION_FIELD, "")),
        "operator": str(row.get(OPERATOR_FIELD, "")),
        "operation_time": str(row.get(TIME_FIELD, "")),
        "duration": str(row.get("查询耗时", "") or "—"),
        "rows_returned": str(row.get("返回行数", "") or "—"),
        "remark": masked.get("日志备注", ""),
        "sensitive_params": masked.get("敏感参数", ""),
        "masked_fields": masked.get("脱敏字段", []),
        "capture_status": capture_status,
        "capture_note": str(row.get("采集说明", "") or ""),
        "capture_ok": capture_status in ("", "正常"),
        "status": str(row.get("status", "")),
        "object_type": kind,
        "object_code": str(row.get(OBJECT_FIELD, "")),
    }


def share_missing_marker(row: dict[str, Any]) -> dict[str, Any]:
    """把采集异常整理成可展示的标记，缺失原因不省略。"""
    return {
        "status": str(row.get("采集状态", "") or "采集中断"),
        "reason": str(row.get("采集说明", "") or "采集侧未返回原因"),
        "from": str(row.get(TIME_FIELD, "")),
        "to": str(row.get(TIME_FIELD, "")),
        "source": "日志采集",
    }


def _missing_for(code: str, kind: str) -> list[dict[str, Any]]:
    entries = [
        marker for marker in store.rows(MISSING_MODULE)
        if str(marker.get("对象编号", "")) == code
    ]
    return [
        {
            "object_code": code,
            "object_type": kind,
            "from": str(marker.get("缺失区间开始", "")),
            "to": str(marker.get("缺失区间结束", "")),
            "reason": str(marker.get("缺失原因", "")),
            "status": str(marker.get("标记状态", "") or "日志缺失"),
        }
        for marker in entries
    ]


def _missing_for_codes(codes: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for code in codes:
        kind = object_type_for_code(code)
        result.extend(_missing_for(code, kind))
    return result
