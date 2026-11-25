"""来源交接领域服务：交接登记、作业重启、接续代送与补证待办。

业务差异都在这里：
- 列表行与 `handover_store` 的交接记录保持同步；
- 登记交接时按窗口归属拆分旧源在途批次；
- 动作（作业重启 / 恢复旧源 / 确认接续 / 标记待补证 / 完成交接）驱动记录状态；
- 接收与代送走 `receive_batch`，按窗口归属确认并推进各自采集截止点。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.handover_store import handover_store, normalize_ts
from app.services.base import BaseModuleService
from app.store import store


ACTION_STATUS = {
    "作业重启": "待旧源补证",
    "恢复旧源": "交接中",
    "确认接续": "交接中",
    "标记待补证": "待旧源补证",
    "完成交接": "已完成",
}


def _as_ts(value: Any, default_time: str = "00:00:00") -> str:
    raw = str(value or "").strip()
    return normalize_ts(raw, default_time=default_time) if raw else ""


class SourceHandoverService(BaseModuleService):
    MODULE = "source_handover"
    ENTITY = "来源交接记录"
    OPERATION_TERM = "交接"
    LIST_FIELDS = [
        "交接编号", "所属湖表", "旧来源系统", "新来源系统", "交接时刻",
        "在途批号", "在途窗口起始", "在途窗口结束", "采集截止点",
    ]
    REQUIRED_FIELDS = ["所属湖表", "旧来源系统", "新来源系统", "交接时刻"]
    STATUS_ORDER = ["交接中", "待旧源补证", "已完成", "已取消"]
    ACTION_RULES = ACTION_STATUS
    NEGATIVE_ACTIONS = ["标记待补证"]

    # ---- 初始化 / 行同步 ----

    def ensure_seed_handovers(self) -> None:
        """把示例登记行补成真正的交接记录，重复调用无副作用。"""
        for row in store.rows(self.MODULE):
            handover_id = str(row.get("handover_id") or "")
            if not handover_id or handover_store.get_handover(handover_id) is not None:
                continue
            start = _as_ts(row.get("在途窗口起始"), default_time="08:30:00")
            end = _as_ts(row.get("在途窗口结束"), default_time="09:30:00")
            boundary = _as_ts(row.get("交接时刻"), default_time="09:00:00")
            if not start or not end:
                continue
            try:
                record = handover_store.create_handover(
                    lake_table=str(row.get("所属湖表", "")),
                    old_source=str(row.get("旧来源系统", "")),
                    new_source=str(row.get("新来源系统", "")),
                    handover_at=boundary,
                    old_batch_id=str(row.get("在途批号", "")),
                    old_window_start=start,
                    old_window_end=end,
                    row_count=int(row.get("在途行数") or 0),
                    handover_id=handover_id,
                    continuation_token=str(row.get("接续凭据") or "") or None,
                    prior_confirmed=list(row.get("_prior_confirmed") or []),
                )
            except ValueError:
                continue
            self._replay_seed_state(record, row)

    def _replay_seed_state(self, record: dict[str, Any], row: dict[str, Any]) -> None:
        """按示例行的既有状态补做接收/重启，确保详情页有可核对的确认链。"""
        handover_id = record["handover_id"]
        old_source = record["old_source"]
        new_source = record["new_source"]
        boundary = record["handover_at"]
        start = record["old_window_start"]
        end = record["old_window_end"]
        status = str(row.get("status") or "交接中")

        if status == "已完成":
            handover_store.receive_batch(
                handover_id, submitted_source=old_source,
                window_start=start, window_end=boundary,
                batch_id=record["old_batch_id"], row_count=record["row_count"],
                checksum="seed-old-pre",
            )
            handover_store.receive_batch(
                handover_id, submitted_source=new_source,
                window_start=boundary, window_end=end,
                batch_id=record["old_batch_id"], row_count=record["row_count"],
                checksum="seed-new-post",
            )
            handover_store.complete(handover_id)
        elif status == "待旧源补证" or row.get("old_writable") is False:
            handover_store.restart(handover_id, operator="seed")
        elif status == "已取消":
            handover_store.complete(handover_id)

    def _row_from_record(self, row: dict[str, Any]) -> dict[str, Any]:
        record = handover_store.get_handover(str(row.get("handover_id") or ""))
        if record is None:
            return dict(row)
        cutoffs = handover_store.cutoffs(record.lake_table)
        updated = dict(row)
        updated.update({
            "交接编号": record.handover_id,
            "所属湖表": record.lake_table,
            "旧来源系统": record.old_source,
            "新来源系统": record.new_source,
            "交接时刻": record.handover_at,
            "在途批号": record.old_batch_id,
            "在途窗口起始": record.old_window_start,
            "在途窗口结束": record.old_window_end,
            "采集截止点": cutoffs.get("table") or "",
            "status": record.status,
            "pending": record.status not in ("已完成", "已取消"),
            "abnormal": record.status == "待旧源补证",
        })
        return updated

    # ---- 通用列表 / 详情 / 登记 ----

    def list_entries(self, *, filters=None, keyword=None, status=None,
                     page=1, size=20) -> tuple[list[dict[str, Any]], int]:
        self.ensure_seed_handovers()
        rows = [self._row_from_record(row) for row in store.rows(self.MODULE)]
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword:
            rows = [
                row for row in rows
                if any(keyword.casefold() in str(row.get(field, "")).casefold()
                       for field in self.LIST_FIELDS)
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        self.ensure_seed_handovers()
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None
        return self._row_from_record(row)

    def find_row_by_handover(self, handover_id: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("handover_id") == handover_id:
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing

        boundary = _as_ts(values.get("交接时刻"), default_time="09:00:00")
        boundary_dt = parse_ts(boundary)
        start = _as_ts(values.get("在途窗口起始")) or format_ts(boundary_dt - timedelta(minutes=30))
        end = _as_ts(values.get("在途窗口结束")) or format_ts(boundary_dt + timedelta(minutes=30))
        row_count = int(values.get("在途行数") or 0)
        batch_id = str(values.get("在途批号") or "").strip()

        try:
            record = handover_store.create_handover(
                lake_table=str(values.get("所属湖表", "")).strip(),
                old_source=str(values.get("旧来源系统", "")).strip(),
                new_source=str(values.get("新来源系统", "")).strip(),
                handover_at=boundary,
                old_batch_id=batch_id,
                old_window_start=start,
                old_window_end=end,
                row_count=row_count,
                continuation_token=str(values.get("接续凭据") or "").strip() or None,
            )
        except ValueError as exc:
            return None, [str(exc)]

        rows = store.rows(self.MODULE)
        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "交接编号": record.handover_id,
            "所属湖表": record.lake_table,
            "旧来源系统": record.old_source,
            "新来源系统": record.new_source,
            "交接时刻": record.handover_at,
            "在途批号": record.old_batch_id,
            "在途窗口起始": record.old_window_start,
            "在途窗口结束": record.old_window_end,
            "在途行数": record.row_count,
            "接续凭据": record.continuation_token,
            "handover_id": record.handover_id,
            "采集截止点": handover_store.cutoffs(record.lake_table).get("table") or "",
            "status": record.status,
            "pending": True,
            "abnormal": False,
        }
        rows.append(entry)
        return entry, []

    # ---- 动作 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        self.ensure_seed_handovers()
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        handover_id = str(row.get("handover_id") or "")
        record = handover_store.get_handover(handover_id)
        if record is None:
            return None, f"交接记录 {handover_id} 不存在"

        if action == "作业重启":
            handover_store.restart(handover_id)
        elif action == "恢复旧源":
            handover_store.resume_old_source(handover_id)
        elif action == "确认接续":
            handover_store.confirm_continuation(handover_id)
        elif action == "标记待补证":
            handover_store.mark_proof_required(handover_id)
        elif action == "完成交接":
            handover_store.complete(handover_id)
        return self._row_from_record(row), f"{self.ENTITY}已{action}"

    # ---- 接收 / 代送 / 详情 / 核对 ----

    def receive(self, handover_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_seed_handovers()
        credential = payload.get("credential")
        if credential is not None and not str(credential).strip():
            credential = None
        result = handover_store.receive_batch(
            handover_id,
            submitted_source=str(payload.get("submitted_source") or "").strip(),
            window_start=_as_ts(payload.get("window_start")),
            window_end=_as_ts(payload.get("window_end")),
            batch_id=str(payload.get("batch_id") or "").strip(),
            row_count=int(payload.get("row_count") or 0),
            checksum=str(payload.get("checksum") or ""),
            credential=str(credential) if credential else None,
            operator=str(payload.get("operator") or ""),
        )
        # 新源提交时撞上旧源迟到的半批，按交接约定随后重启作业：
        # 旧源转入暂时不可写，值班员改用接续凭据代送。
        if result.get("restart_required"):
            handover_store.restart(
                handover_id, operator=str(payload.get("operator") or "system"))
        self._refresh_row_status(handover_id)
        return result

    def _refresh_row_status(self, handover_id: str) -> None:
        row = self.find_row_by_handover(handover_id)
        if row is None:
            return
        updated = self._row_from_record(row)
        row.update(updated)

    def detail(self, handover_id: str) -> dict[str, Any] | None:
        self.ensure_seed_handovers()
        detail = handover_store.detail(handover_id)
        if detail is None:
            return None
        row = self.find_row_by_handover(handover_id)
        detail["entry"] = self._row_from_record(row) if row else None
        return detail

    def detail_by_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = self.get_entry(entry_id)
        if row is None:
            return None
        return self.detail(str(row.get("handover_id") or ""))

    def proxy_permission(self, handover_id: str) -> dict[str, Any]:
        self.ensure_seed_handovers()
        return handover_store.proxy_permission(handover_id)

    def verify_window(self, handover_id: str, window_start: str, window_end: str) -> dict[str, Any]:
        self.ensure_seed_handovers()
        record = handover_store.get_handover(handover_id)
        if record is None:
            return {"found": False, "error": "handover_not_found"}
        return handover_store.verify_window(
            record.lake_table,
            _as_ts(window_start),
            _as_ts(window_end),
        )

    def stats_snapshot(self) -> dict[str, Any]:
        self.ensure_seed_handovers()
        rows = [self._row_from_record(row) for row in store.rows(self.MODULE)]
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(rows),
            "pending_proof": sum(1 for row in rows if row.get("status") == "待旧源补证"),
            "abnormal": sum(1 for row in rows if row.get("abnormal")),
        }
