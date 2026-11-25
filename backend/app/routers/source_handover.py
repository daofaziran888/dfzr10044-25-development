"""来源交接专用接口：详情页、接收/代送、截止点、窗口核对。

这些路径比通用 `/{module}` 更具体，必须挂在通用资源路由之前注册，
否则会被动态模块路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.handover_store import handover_store
from app.services import service_for

router = APIRouter(prefix="/api/source_handover", tags=["来源交接"])


def _service():
    service = service_for("source_handover")
    if service is None:  # pragma: no cover - 注册表缺项时才会发生
        raise HTTPException(status_code=500, detail="来源交接服务未注册")
    return service


class ReceivePayload(BaseModel):
    submitted_source: str
    window_start: str
    window_end: str
    batch_id: str = ""
    row_count: int = 0
    checksum: str = ""
    # 非空表示使用接续凭据代送；凭据必须是交接记录发放的 continuation_token
    credential: str | None = None
    operator: str = ""


class VerifyPayload(BaseModel):
    window_start: str
    window_end: str


class CutoffPayload(BaseModel):
    lake_table: str
    cutoff: str


@router.get("/{handover_id}/detail")
def handover_detail(handover_id: str) -> dict[str, Any]:
    """来源交接详情：窗口段、批次确认、待代送窗口与接续许可。"""
    detail = _service().detail(handover_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"来源交接记录 {handover_id} 不存在")
    return detail


@router.get("/entry/{entry_id}/detail")
def handover_detail_by_entry(entry_id: int) -> dict[str, Any]:
    detail = _service().detail_by_entry(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"来源交接记录 {entry_id} 不存在")
    return detail


@router.post("/{handover_id}/receive")
def receive_batch(handover_id: str, payload: ReceivePayload) -> dict[str, Any]:
    """接收批次或代送：按窗口归属确认，冲突时返回需要旧源补证的确切区间。"""
    result = _service().receive(handover_id, payload.model_dump())
    if result.get("ok"):
        return result
    error = result.get("error")
    if error == "handover_not_found":
        raise HTTPException(status_code=404, detail=result.get("message"))
    # 许可冲突、归属不符等业务拒绝统一 409，并带 intervals 供前端展示
    raise HTTPException(status_code=409, detail=result)


@router.get("/{handover_id}/proxy-permission")
def proxy_permission(handover_id: str) -> dict[str, Any]:
    if handover_store.get_handover(handover_id) is None:
        raise HTTPException(status_code=404, detail=f"来源交接记录 {handover_id} 不存在")
    return _service().proxy_permission(handover_id)


@router.get("/{handover_id}/pending-windows")
def pending_windows(handover_id: str) -> dict[str, Any]:
    if handover_store.get_handover(handover_id) is None:
        raise HTTPException(status_code=404, detail=f"来源交接记录 {handover_id} 不存在")
    return {
        "handover_id": handover_id,
        "windows": _service().proxy_permission(handover_id)["pending_intervals"],
    }


@router.post("/{handover_id}/verify")
def verify_window(handover_id: str, payload: VerifyPayload) -> dict[str, Any]:
    """按窗口核对：交接前窗口仍返回原来源。"""
    if handover_store.get_handover(handover_id) is None:
        raise HTTPException(status_code=404, detail=f"来源交接记录 {handover_id} 不存在")
    return _service().verify_window(handover_id, payload.window_start, payload.window_end)


@router.get("/table/{lake_table}/cutoffs")
def table_cutoffs(lake_table: str) -> dict[str, Any]:
    """表级与按来源的入湖采集截止点；不同湖表互相独立。"""
    return handover_store.cutoffs(lake_table)


@router.post("/table/cutoff")
def set_table_cutoff(payload: CutoffPayload) -> dict[str, Any]:
    """建立/抬高表级采集截止点；目录确认登记时会自动调用同样的入口。"""
    from app.handover_store import normalize_ts

    handover_store.ensure_table_cutoff(payload.lake_table, normalize_ts(payload.cutoff))
    return handover_store.cutoffs(payload.lake_table)
