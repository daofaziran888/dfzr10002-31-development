"""聚合查询配置业务规则：状态流转、字段校验与筛选口径都收在这里。

查询模板清单与审计工作区共用 ``audit_policy``：同一份脱敏结果、同一套部门归属判权，
只读副本看不到原始查询语句与参数定义，避免两个页面对敏感字段各说各话。
"""
from __future__ import annotations

from typing import Any

from app.services import audit_policy
from app.store import store

MODULE = "query"
REQUIRED_FIELDS = ["模板编号", "模板名称", "查询语句"]
STATUS_ORDER = ["草稿", "待审批", "已发布", "已停用"]
ACTION_RULES = {"提交审批": "待审批", "发布模板": "已发布", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]

# 只读副本不应出现的字段：脱敏后仍是原文可读的业务参数，直接隐藏。
HIDDEN_FOR_READONLY = ("参数定义", "查询语句")


class QueryService:
    def _visible_rows(
        self,
        context: audit_policy.AuditContext,
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        visible = [
            row for row in rows
            if audit_policy.resolve_access(row, context, None) != audit_policy.DENIED_MODE
        ]
        return visible

    def _present(self, row: dict[str, Any], context: audit_policy.AuditContext) -> dict[str, Any]:
        """统一出口：先判权、再脱敏；只读副本额外隐藏敏感列。"""
        mode = audit_policy.resolve_access(row, context, None)
        masked = audit_policy.mask_record(MODULE, row)
        masked["访问模式"] = mode
        masked["访问说明"] = audit_policy.access_label(mode)
        if mode == audit_policy.READONLY_MODE:
            for field in HIDDEN_FOR_READONLY:
                if field in masked:
                    masked[field] = "共享只读：已隐藏"
            masked["脱敏字段"] = [
                field for field in masked.get("脱敏字段", [])
                if field not in HIDDEN_FOR_READONLY
            ]
        return masked

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        context: audit_policy.AuditContext | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        context = context or audit_policy.AuditContext(
            audit_policy.DEFAULT_DEPARTMENT, audit_policy.DEFAULT_ROLE
        )
        rows = self._visible_rows(context)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._present(row, context) for row in rows[start:start + size]], total

    def get_entry(
        self,
        entry_id: int,
        context: audit_policy.AuditContext | None = None,
    ) -> dict[str, Any] | None:
        context = context or audit_policy.AuditContext(
            audit_policy.DEFAULT_DEPARTMENT, audit_policy.DEFAULT_ROLE
        )
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        audit_policy.ensure_access(
            audit_policy.resolve_access(entry, context, None),
            str(entry.get("模板编号", "")),
        )
        return self._present(entry, context)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["归属部门"] = str(values.get("归属部门") or audit_policy.DEFAULT_DEPARTMENT)
        entry["共享只读部门"] = list(values.get("共享只读部门") or [])
        rows.append(entry)
        # 登记结果同样走脱敏出口，避免回显刚写入的明文参数。
        return audit_policy.mask_record(MODULE, entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: audit_policy.AuditContext | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        context = context or audit_policy.AuditContext(
            audit_policy.DEFAULT_DEPARTMENT, audit_policy.DEFAULT_ROLE
        )
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"查询模板 {entry_id} 不存在或已归档"
        # 共享只读没有写权限；部门归属优先，只有 owner 能流转状态。
        if audit_policy.resolve_access(entry, context, None) != audit_policy.OWNER_MODE:
            return None, f"无权修改查询模板 {entry.get('模板编号', entry_id)}：当前部门只有只读权限"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于聚合查询配置可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._present(entry, context), f"查询模板已{action}"
