"""审计访问策略：敏感字段脱敏与部门归属判权，供操作日志与查询模板清单共用。

脱敏口径（本文件是唯一实现，所有读取方都必须走这里，避免有的页面漏脱敏）：

- 手机号：保留前 3 位与后 4 位，其余打码，例如 ``139****5678``；
- 令牌/密钥/口令：保留前 4 位与后 2 位，例如 ``tok_****6c``；
- 商户号/客户号等业务编号：保留前 2 位与最后 1 位，例如 ``M-****1``；
- 邮箱：保留首字符与域名；
- 记录字段（查询语句、参数定义、筛选条件、日志备注等）里的上述内容按文本规则同样处理。

权限口径（部门归属优先于共享只读）：

1. 请求部门等于数据归属部门 → ``owner``，可看完整记录（敏感参数仍按上面的口径脱敏）；
2. 请求部门在共享只读部门名单内 → ``readonly``，只能看脱敏摘要；
3. 其余情况 → ``denied``，接口直接拒绝，且不写入任何审计记录。

共享只读与部门归属同时成立时（对象把自己的归属部门也写进共享名单），按归属权限处理，
不会被降级成只读；共享只读也永远不会被提升成 owner。
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException

# ---------------------------------------------------------------------------
# 部门与角色
# ---------------------------------------------------------------------------

DEPARTMENTS: dict[str, str] = {
    "D-ANALYTICS": "数据分析部",
    "D-DATA": "数据治理部",
    "D-FINANCE": "财务部",
    "D-OPS": "运营部",
}

DEFAULT_DEPARTMENT = "D-ANALYTICS"
DEFAULT_ROLE = "auditor"

# 角色只用于收窄权限：guest 永远拿不到 owner 视图，其余角色仍以部门归属为准。
ROLES: dict[str, str] = {
    "auditor": "审计员",
    "auditor_admin": "合规审计管理员",
    "guest": "只读访客",
}
OWNER_ROLES = {"auditor", "auditor_admin"}

OWNER_MODE = "owner"
READONLY_MODE = "readonly"
DENIED_MODE = "denied"

ACCESS_LABELS = {
    OWNER_MODE: "本部门归属：完整记录（敏感参数已脱敏）",
    READONLY_MODE: "共享只读：仅脱敏摘要，原始参数不可见",
    DENIED_MODE: "无本部门归属：拒绝访问",
}


@dataclass(frozen=True)
class AuditContext:
    department: str
    role: str

    @property
    def department_label(self) -> str:
        return DEPARTMENTS.get(self.department, self.department)

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role)


def get_audit_context(
    x_audit_dept: str | None = Header(default=None, alias="X-Audit-Dept"),
    x_audit_role: str | None = Header(default=None, alias="X-Audit-Role"),
) -> AuditContext:
    """从请求头解析审计视角；缺省按数据分析部的审计员处理。"""
    department = (x_audit_dept or DEFAULT_DEPARTMENT).strip() or DEFAULT_DEPARTMENT
    role = (x_audit_role or DEFAULT_ROLE).strip() or DEFAULT_ROLE
    if department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail=f"未知部门「{department}」，无法判定数据归属")
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"未知角色「{role}」，无法判定访问范围")
    return AuditContext(department=department, role=role)


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_TOKEN_RE = re.compile(r"(?i)\b(tok_[A-Za-z0-9_-]{4,}|ak_[A-Za-z0-9_-]{4,}|sk_[A-Za-z0-9_-]{4,})\b")
_BUSINESS_ID_RE = re.compile(r"\b([A-Z]{1,3}-)([A-Za-z0-9-]{2,})")

_TOKEN_KEYS = {"token", "access_token", "refresh_token", "secret", "password", "passwd", "api_key", "apikey"}
_PHONE_KEYS = {"phone", "mobile", "tel", "contact_phone"}
_ID_KEYS = {"merchant_id", "customer_id", "user_id", "member_id", "account_id"}


def _mask_phone(value: str) -> str:
    return _PHONE_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", value)


def _mask_email(value: str) -> str:
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}***{m.group(2)}", value)


def _mask_tokens(value: str) -> str:
    def rewrite(match: re.Match[str]) -> str:
        token = match.group(1)
        if len(token) <= 6:
            return token[:2] + "****"
        return token[:4] + "****" + token[-2:]

    return _TOKEN_RE.sub(rewrite, value)


def _mask_business_ids(value: str) -> str:
    def rewrite(match: re.Match[str]) -> str:
        prefix, tail = match.group(1), match.group(2)
        if len(tail) <= 2:
            return prefix + "****"
        return prefix + "****" + tail[-1]

    return _BUSINESS_ID_RE.sub(rewrite, value)


def mask_text(value: str) -> str:
    """按文本规则脱敏任意字符串（SQL 语句、备注、URI 参数等）。"""
    if not value:
        return value
    masked = _mask_phone(value)
    masked = _mask_email(masked)
    masked = _mask_tokens(masked)
    masked = _mask_business_ids(masked)
    return masked


def _mask_mapping(data: Any) -> Any:
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, item in data.items():
            lowered = str(key).lower()
            if lowered in _TOKEN_KEYS and isinstance(item, (str, int, float)):
                result[key] = _mask_tokens(str(item))
            elif lowered in _PHONE_KEYS and isinstance(item, (str, int, float)):
                result[key] = _mask_phone(str(item))
            elif lowered in _ID_KEYS and isinstance(item, (str, int, float)):
                result[key] = _mask_business_ids(str(item))
            else:
                result[key] = _mask_mapping(item)
        return result
    if isinstance(data, list):
        return [_mask_mapping(item) for item in data]
    if isinstance(data, str):
        return mask_text(data)
    return data


def mask_json_text(value: str) -> str:
    """脱敏 JSON 文本；不是合法 JSON 时退回文本规则，不会把原文透传出去。"""
    if not value:
        return value
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return mask_text(value)
    masked = _mask_mapping(payload)
    try:
        return json.dumps(masked, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "******"


# 每个模块里需要脱敏的字段；未列出的字段按原值返回。
SENSITIVE_FIELDS: dict[str, tuple[str, ...]] = {
    "log": ("敏感参数", "日志备注"),
    "query": ("查询语句", "参数定义"),
    "indicator": ("计算公式", "筛选条件"),
    "service": ("查询参数",),
}

_JSON_FIELDS: set[tuple[str, str]] = {
    ("log", "敏感参数"),
    ("query", "参数定义"),
    ("service", "查询参数"),
}


def mask_record(module: str, row: dict[str, Any]) -> dict[str, Any]:
    """返回脱敏后的副本，绝不修改仓库中的原始行，避免明文残留。"""
    result = copy.deepcopy(row)
    fields = SENSITIVE_FIELDS.get(module, ())
    masked_fields: list[str] = []
    for field in fields:
        if field not in result:
            continue
        value = result.get(field)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            if (module, field) in _JSON_FIELDS:
                result[field] = mask_json_text(value)
            else:
                result[field] = mask_text(value)
        else:
            result[field] = mask_text(str(value))
        masked_fields.append(field)
    if masked_fields:
        result["脱敏字段"] = masked_fields
        result["脱敏策略"] = "审计脱敏 v1"
    return result


# ---------------------------------------------------------------------------
# 部门归属判权
# ---------------------------------------------------------------------------

def resolve_access(obj_row: dict[str, Any] | None, context: AuditContext, fallback_department: str | None = None) -> str:
    """判定访问模式；数据归属优先于共享只读。"""
    owner = str((obj_row or {}).get("归属部门") or fallback_department or "").strip()
    shared = [str(item) for item in (obj_row or {}).get("共享只读部门") or []]
    if not owner:
        return DENIED_MODE
    if context.department == owner:
        # 归属部门即使也出现在共享名单里，仍按 owner 处理：归属权限优先。
        return OWNER_MODE if context.role in OWNER_ROLES else READONLY_MODE
    if context.department in shared:
        return READONLY_MODE
    return DENIED_MODE


def access_label(mode: str) -> str:
    return ACCESS_LABELS.get(mode, mode)


def ensure_access(mode: str, object_code: str) -> None:
    """越权直接拒绝；这里不写任何记录，调用方也不得在拒绝后留下审计痕迹。"""
    if mode == DENIED_MODE:
        raise HTTPException(
            status_code=403,
            detail=f"无权查看操作对象 {object_code}：本部门既不是数据归属部门，也不在共享只读名单内",
        )
