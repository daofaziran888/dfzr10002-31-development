"""审计工作区回归测试：串联链路、脱敏、越权拒绝与缺失标记。

运行：cd backend && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store

ANALYTICS = {"X-Audit-Dept": "D-ANALYTICS", "X-Audit-Role": "auditor"}
DATA_DEPT = {"X-Audit-Dept": "D-DATA", "X-Audit-Role": "auditor"}
FINANCE = {"X-Audit-Dept": "D-FINANCE", "X-Audit-Role": "auditor"}
GUEST = {"X-Audit-Dept": "D-ANALYTICS", "X-Audit-Role": "guest"}

PLAINTEXT_PHONE = "13912345678"
PLAINTEXT_TOKEN = "tok_live_9f8a7b6c"
PLAINTEXT_MERCHANT = "M-8891"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _no_plaintext(payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    assert PLAINTEXT_PHONE not in text
    assert PLAINTEXT_TOKEN not in text
    assert PLAINTEXT_MERCHANT not in text


def test_workspace_chains_indicator_template_and_service(client: TestClient) -> None:
    response = client.get("/api/log/workspace/INDI-0002", headers=ANALYTICS)
    assert response.status_code == 200
    body = response.json()

    linked = {link["object_code"]: link["object_type"] for link in body["links"]}
    assert linked == {"INDI-0002": "指标口径", "QUER-0002": "查询模板", "SERV-0002": "数据服务"}

    action_types = {action["action_type"] for action in body["timeline"]}
    assert {"指标修改", "查询模板发布", "数据服务停用"} <= action_types

    # 时间线按时间倒序，且没有重复记录。
    ids = [action["id"] for action in body["timeline"]]
    assert len(ids) == len(set(ids))
    times = [action["operation_time"] for action in body["timeline"]]
    assert times == sorted(times, reverse=True)

    _no_plaintext(body)


def test_workspace_filters_by_operator_action_and_time(client: TestClient) -> None:
    response = client.get(
        "/api/log/workspace/INDI-0002",
        headers=ANALYTICS,
        params={"operator": "周敏", "action_type": "指标修改"},
    )
    assert response.status_code == 200
    body = response.json()
    assert {action["log_no"] for action in body["timeline"]} == {"LOG-0002", "LOG-0005"}

    ranged = client.get(
        "/api/log/workspace/INDI-0002",
        headers=ANALYTICS,
        params={"date_from": "2026-09-03", "date_to": "2026-09-04 23:59:59"},
    ).json()
    assert {action["log_no"] for action in ranged["timeline"]} == {"LOG-0003", "LOG-0004"}


def test_workspace_accepts_datetime_local_format(client: TestClient) -> None:
    """前端 datetime-local 用 T 分隔，后端要归一成空格分隔后再比较。"""
    body = client.get(
        "/api/log/workspace/INDI-0002",
        headers=ANALYTICS,
        params={"date_from": "2026-09-03T00:00", "date_to": "2026-09-04T23:59"},
    ).json()
    assert {action["log_no"] for action in body["timeline"]} == {"LOG-0003", "LOG-0004"}
    assert body["filters"]["date_from"] == "2026-09-03 00:00"


def test_shared_readonly_gets_masked_summary_only(client: TestClient) -> None:
    response = client.get("/api/log/workspace/INDI-0002", headers=DATA_DEPT)
    assert response.status_code == 200
    body = response.json()
    assert body["access"]["mode"] == "readonly"
    params = body["chains"][0]["actions"][0]["sensitive_params"]
    assert "139****5678" in params
    assert "M-****1" in params
    _no_plaintext(body)


def test_cross_department_workspace_is_denied(client: TestClient) -> None:
    response = client.get("/api/log/workspace/INDI-0002", headers=FINANCE)
    assert response.status_code == 403
    assert "无权查看操作对象" in response.json()["detail"]


def test_denied_query_leaves_no_trace(client: TestClient) -> None:
    before = len(store.rows("log"))
    for _ in range(3):
        client.get("/api/log/workspace/INDI-0002", headers=FINANCE)
        client.get("/api/log/2", headers=FINANCE)
    after = len(store.rows("log"))
    assert after == before


def test_denied_action_does_not_change_record(client: TestClient) -> None:
    entry = store.find("log", 2)
    assert entry is not None
    status_before = entry["status"]
    response = client.post("/api/log/2/actions", json={"values": {"action": "归档记录"}}, headers=FINANCE)
    assert response.json()["ok"] is False
    assert store.find("log", 2)["status"] == status_before


def test_missing_logs_are_marked_not_faked(client: TestClient) -> None:
    body = client.get("/api/log/workspace/INDI-0002", headers=ANALYTICS).json()
    marker_codes = {item["object_code"] for item in body["missing"]}
    assert marker_codes == {"INDI-0002", "QUER-0002"}
    assert all(item["reason"] for item in body["missing"])

    broken = [action for action in body["timeline"] if not action["capture_ok"]]
    assert [action["log_no"] for action in broken] == ["LOG-0005"]
    assert broken[0]["capture_status"] == "采集中断"
    assert broken[0]["duration"] == "—"

    # 中断日志只出现一次，不会被补成一条“已完成”记录。
    assert [action["log_no"] for action in body["timeline"]].count("LOG-0005") == 1


def test_object_with_only_missing_marker_stays_auditable(client: TestClient) -> None:
    """只剩缺失标记、没有存活日志的对象也要能被审计员选中，不能被藏起来。"""
    markers = store.rows("audit_missing")
    markers.append({
        "id": max((int(item.get("id", 0)) for item in markers), default=0) + 1,
        "对象编号": "INDI-0003",
        "对象类型": "指标口径",
        "缺失区间开始": "2026-09-08 00:00:00",
        "缺失区间结束": "2026-09-08 00:10:00",
        "缺失原因": "采集队列积压",
        "标记状态": "日志缺失",
    })
    try:
        objects = client.get("/api/log/objects", headers=ANALYTICS).json()["items"]
        entry = next(item for item in objects if item["object_code"] == "INDI-0003")
        assert entry["actions"] == 0
        assert entry["missing"] == 1

        body = client.get("/api/log/workspace/INDI-0003", headers=ANALYTICS).json()
        assert body["timeline"] == []
        assert body["chains"][0]["missing"][0]["status"] == "日志缺失"
    finally:
        markers.pop()


def test_log_list_and_workspace_agree(client: TestClient) -> None:
    listed = client.get("/api/log", headers=ANALYTICS, params={"object_code": "INDI-0002", "size": 200}).json()
    workspace = client.get("/api/log/workspace/INDI-0002", headers=ANALYTICS).json()
    listed_ids = {row["id"] for row in listed["items"]}
    chain_ids = {
        action["id"]
        for chain in workspace["chains"]
        if chain["object_code"] == "INDI-0002"
        for action in chain["actions"]
    }
    assert listed_ids == chain_ids


def test_query_list_reuses_masking_from_audit_policy(client: TestClient) -> None:
    owner_rows = client.get("/api/query/2", headers=ANALYTICS).json()
    assert "139****5678" in owner_rows["查询语句"]
    assert "tok_****6c" in owner_rows["参数定义"]
    _no_plaintext(owner_rows)

    readonly_rows = client.get("/api/query", headers=DATA_DEPT).json()
    assert [row["模板编号"] for row in readonly_rows["items"]] == ["QUER-0002"]
    assert readonly_rows["items"][0]["参数定义"] == "共享只读：已隐藏"
    _no_plaintext(readonly_rows)


def test_department_belongs_wins_over_shared_readonly(client: TestClient) -> None:
    # QUER-0003 的共享只读部门里同时写了归属部门；归属权限优先，仍是 owner。
    body = client.get("/api/query/3", headers=ANALYTICS).json()
    assert body["访问模式"] == "owner"


def test_readonly_cannot_write(client: TestClient) -> None:
    response = client.post("/api/query/2/actions", json={"values": {"action": "停用模板"}}, headers=DATA_DEPT).json()
    assert response["ok"] is False
    assert store.find("query", 2)["status"] == "待审批"


def test_log_list_hides_other_departments(client: TestClient) -> None:
    finance_rows = client.get("/api/log", headers=FINANCE, params={"size": 200}).json()
    assert {row["日志编号"] for row in finance_rows["items"]} == {"LOG-0007"}
    analytics_rows = client.get("/api/log", headers=ANALYTICS, params={"size": 200}).json()
    assert "LOG-0007" not in {row["日志编号"] for row in analytics_rows["items"]}


def test_unknown_department_is_rejected(client: TestClient) -> None:
    response = client.get("/api/log", headers={"X-Audit-Dept": "D-UNKNOWN", "X-Audit-Role": "auditor"})
    assert response.status_code == 400


def test_guest_role_cannot_get_owner_view(client: TestClient) -> None:
    body = client.get("/api/log/workspace/INDI-0002", headers=GUEST).json()
    assert body["access"]["mode"] == "readonly"
    assert body["object"]["object_code"] == "INDI-0002"
