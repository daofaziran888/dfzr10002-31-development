"""分析操作日志接口：维护操作日志，并提供按操作对象串联的审计工作区。

- 列表 / 明细 / 导出都经过统一脱敏，且按部门归属过滤，越权对象不出现在结果里；
- ``/workspace/{object_code}`` 把同一业务对象在指标口径、查询模板、数据服务上的动作串成链路；
- 越权查询在工作区、明细、动作三处都直接 403，拒绝路径不写任何记录。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.audit_policy import AuditContext, get_audit_context
from app.services.log import LogService

router = APIRouter(prefix="/api/log", tags=["分析操作日志"])

service = LogService()

LIST_FIELDS = ["日志编号", "操作类型", "操作对象", "操作人", "操作时间", "查询耗时", "返回行数", "日志备注"]
STATUSES = ["已记录", "已复核", "已归档"]


# 注意：固定路径必须声明在 /{entry_id} 之前，否则会被路径参数吞掉。
@router.get("/objects")
def list_objects(context: AuditContext = Depends(get_audit_context)) -> dict[str, Any]:
    """可聚合为审计链路的主对象清单；当前部门无权访问的对象不返回。"""
    items = service.list_objects(context)
    return {
        "items": items,
        "total": len(items),
        "context": {
            "department": context.department,
            "department_label": context.department_label,
            "role": context.role,
            "role_label": context.role_label,
        },
    }


@router.get("/workspace/{object_code}")
def audit_workspace(
    object_code: str,
    operator: str | None = Query(default=None, description="按操作人筛选，支持模糊匹配"),
    action_type: str | None = Query(default=None, description="按操作类型筛选，支持模糊匹配"),
    date_from: str | None = Query(default=None, description="操作时间起始，格式 YYYY-MM-DD 或带时分秒"),
    date_to: str | None = Query(default=None, description="操作时间结束，格式 YYYY-MM-DD 或带时分秒"),
    context: AuditContext = Depends(get_audit_context),
) -> dict[str, Any]:
    """按操作对象串联审计链路：主对象 + 关联对象 + 时间线 + 缺失标记。

    越权直接 403，并在错误里说明原因；拒绝请求不会留下任何日志或缓存痕迹。
    """
    try:
        return service.workspace(
            object_code,
            context,
            operator=operator,
            action_type=action_type,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/export")
def export_entries(context: AuditContext = Depends(get_audit_context)) -> dict[str, Any]:
    """导出分析操作日志清单：脱敏后返回当前部门可见的全量数据。"""
    items, total = service.list_entries(page=1, size=10000, context=context)
    return {"module": "log", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按日志编号检索"),
    status: str | None = Query(default=None, description="已记录、已复核、已归档"),
    operator: str | None = Query(default=None, description="按操作人筛选"),
    action_type: str | None = Query(default=None, description="按操作类型筛选"),
    object_code: str | None = Query(default=None, description="按操作对象筛选"),
    date_from: str | None = Query(default=None, description="操作时间起始"),
    date_to: str | None = Query(default=None, description="操作时间结束"),
    page: int = 1,
    size: int = 20,
    context: AuditContext = Depends(get_audit_context),
) -> PageResult[dict]:
    """按日志编号、操作人、时间与动作类型过滤操作日志；敏感参数一律脱敏。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        operator=operator,
        action_type=action_type,
        object_code=object_code,
        date_from=date_from,
        date_to=date_to,
        page=page,
        size=size,
        context=context,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, context: AuditContext = Depends(get_audit_context)) -> dict:
    """读取单条操作日志明细；越权或无权限时给出可读的错误说明。"""
    entry = service.get_entry(entry_id, context)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"操作日志 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload, context: AuditContext = Depends(get_audit_context)) -> ActionResult:
    """登记一条操作日志，缺字段时说明原因而不是静默丢弃；返回内容同样脱敏。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="操作日志已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    context: AuditContext = Depends(get_audit_context),
) -> ActionResult:
    """对单条操作日志执行标记复核、归档记录；越权动作会被拦下且不修改记录。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, context)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
