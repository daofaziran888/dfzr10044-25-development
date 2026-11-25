"""来源交接存储：交接记录、窗口归属段、批次确认与采集截止点。

来源换班时，旧源在途批次会跨过交接时刻，新源又从交接时刻开始接收。为了让两边
都能入湖且互不覆盖，这里把每条交接拆成按事件时间归属的窗口段（segment），并把
每次接收/代送落成批次确认（confirmation）：

- 窗口段：`window_owner` 表示该区间应由哪个来源确认；`original_source` 永远记录
  原批归属，不会因为代送而改变。
- 批次确认：同一湖表 + 原批号 + 窗口段只保留一条确认；重复提交只命中已有确认。
- 采集截止点：按（湖表, 来源）分别推进，另有表级截止点；不同湖表即使来源和窗口
  编号完全相同也互不影响。

窗口按半开区间 [start, end) 处理，交接时刻本身归新源：

    旧源在途 08:30 ~ 09:30，09:00 交接
    → 旧源 [08:30, 09:00) + 新源 [09:00, 09:30)

确认状态：
- confirmed：已入湖确认，推进归属来源和表级采集截止点；
- pending_confirmation：旧源迟到半批，暂记但不推进截止点，等新源窗口落地；
- pending_proof：新源接续凭据代送旧源窗口，已收下但须旧源按原批号补认；
- superseded：被交接后窗口的新源确认覆盖，不再生效。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


CONFIRMED = "confirmed"
PENDING_CONFIRMATION = "pending_confirmation"
PENDING_PROOF = "pending_proof"
SUPERSEDED = "superseded"


def parse_ts(value: str) -> datetime:
    """解析 ISO 时间；统一按秒精度比较，避免毫秒/时区写法干扰窗口归属。"""
    return datetime.fromisoformat(value)


def format_ts(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def normalize_ts(value: str, default_time: str = "00:00:00") -> str:
    """把 `2026-11-25` 这类日期补齐成秒级时间；已带时间的原样规范化。"""
    raw = str(value or "").strip()
    if not raw:
        return raw
    if len(raw) == 10:
        raw = f"{raw}T{default_time}"
    elif len(raw) == 16:
        raw = f"{raw}:00"
    return format_ts(parse_ts(raw))


def split_window(start: str, end: str, boundary: str) -> list[tuple[str, str, str]]:
    """按交接时刻把窗口切成 pre/post 两段，交接时刻本身归 post（新源）。"""
    start_dt, end_dt, boundary_dt = parse_ts(start), parse_ts(end), parse_ts(boundary)
    if end_dt <= start_dt:
        raise ValueError("窗口结束时间必须晚于开始时间")
    if end_dt <= boundary_dt:
        return [(format_ts(start_dt), format_ts(end_dt), "pre")]
    if start_dt >= boundary_dt:
        return [(format_ts(start_dt), format_ts(end_dt), "post")]
    return [
        (format_ts(start_dt), format_ts(boundary_dt), "pre"),
        (format_ts(boundary_dt), format_ts(end_dt), "post"),
    ]


def overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> tuple[str, str] | None:
    """返回两个半开区间的交集；无交集返回 None。"""
    start = max(parse_ts(start_a), parse_ts(start_b))
    end = min(parse_ts(end_a), parse_ts(end_b))
    if start >= end:
        return None
    return format_ts(start), format_ts(end)


# ----------------------------- 数据结构 -----------------------------

@dataclass
class HandoverSegment:
    """窗口归属段：原批可按事件时间拆开，原批归属始终是旧源。"""
    segment_id: str
    handover_id: str
    lake_table: str
    batch_id: str
    original_source: str
    window_owner: str
    window_start: str
    window_end: str
    state: str = "in_flight"  # in_flight | at_risk | pending_proof | confirmed | superseded
    confirmation_id: str | None = None
    row_count: int = 0
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "handover_id": self.handover_id,
            "lake_table": self.lake_table,
            "batch_id": self.batch_id,
            "original_source": self.original_source,
            "window_owner": self.window_owner,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "state": self.state,
            "confirmation_id": self.confirmation_id,
            "row_count": self.row_count,
        }


@dataclass
class BatchConfirmation:
    """批次确认：一条确认只对应一个窗口段，原批归属永远记在旧源。"""
    confirmation_id: str
    handover_id: str
    lake_table: str
    batch_id: str
    original_source: str
    submitted_source: str
    window_owner: str
    window_start: str
    window_end: str
    status: str
    auth_mode: str = "source"  # source | continuation_proxy
    credential_holder: str | None = None
    verification_source: str = ""
    row_count: int = 0
    checksum: str = ""
    superseded_by: str | None = None
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self, handover_at: str | None = None) -> dict[str, Any]:
        pre_handover = (
            handover_at is not None
            and parse_ts(self.window_start) < parse_ts(handover_at)
        )
        return {
            "confirmation_id": self.confirmation_id,
            "handover_id": self.handover_id,
            "lake_table": self.lake_table,
            "batch_id": self.batch_id,
            "original_source": self.original_source,
            "submitted_source": self.submitted_source,
            "window_owner": self.window_owner,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "status": self.status,
            "auth_mode": self.auth_mode,
            "credential_holder": self.credential_holder,
            "verification_source": self.verification_source,
            "pre_handover_window": pre_handover,
            "row_count": self.row_count,
            "checksum": self.checksum,
            "superseded_by": self.superseded_by,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class HandoverRecord:
    handover_id: str
    lake_table: str
    old_source: str
    new_source: str
    handover_at: str
    old_batch_id: str
    old_window_start: str
    old_window_end: str
    continuation_token: str
    row_count: int = 0
    status: str = "交接中"
    old_writable: bool = True
    restart_count: int = 0
    pending_intervals: list[dict[str, Any]] = field(default_factory=list)
    conflict_intervals: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "lake_table": self.lake_table,
            "old_source": self.old_source,
            "new_source": self.new_source,
            "handover_at": self.handover_at,
            "old_batch_id": self.old_batch_id,
            "old_window_start": self.old_window_start,
            "old_window_end": self.old_window_end,
            "continuation_token": self.continuation_token,
            "row_count": self.row_count,
            "status": self.status,
            "old_writable": self.old_writable,
            "restart_count": self.restart_count,
            "pending_intervals": list(self.pending_intervals),
            "conflict_intervals": list(self.conflict_intervals),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _clamp_rows(row_count: int, start: str, end: str,
                window_start: str, window_end: str) -> int:
    """按时间占比估算拆分后窗口的行数，仅用于展示。"""
    total = max(0, int(row_count))
    if total == 0:
        return 0
    full = (parse_ts(window_end) - parse_ts(window_start)).total_seconds()
    part = (parse_ts(end) - parse_ts(start)).total_seconds()
    if full <= 0:
        return total
    return int(round(total * part / full))


# ----------------------------- 存储引擎 -----------------------------

class HandoverStore:
    """来源交接内存存储：交接、窗口段、批次确认与截止点。"""

    def __init__(self) -> None:
        self._handovers: dict[str, HandoverRecord] = {}
        self._segments: dict[str, list[HandoverSegment]] = {}
        self._confirmations: dict[str, list[BatchConfirmation]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        # 采集截止点：按湖表 + 来源分开，保证不同湖表独立推进
        self._source_cutoffs: dict[tuple[str, str], str] = {}
        self._table_cutoffs: dict[str, str] = {}
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._handovers.clear()
            self._segments.clear()
            self._confirmations.clear()
            self._events.clear()
            self._source_cutoffs.clear()
            self._table_cutoffs.clear()

    # ---- 查询 ----

    def get_handover(self, handover_id: str) -> HandoverRecord | None:
        return self._handovers.get(handover_id)

    def list_handovers(self) -> list[HandoverRecord]:
        return list(self._handovers.values())

    def segments(self, handover_id: str) -> list[HandoverSegment]:
        return list(self._segments.get(handover_id, []))

    def confirmations(self, handover_id: str) -> list[BatchConfirmation]:
        return list(self._confirmations.get(handover_id, []))

    def confirmations_for_table(self, lake_table: str) -> list[BatchConfirmation]:
        result: list[BatchConfirmation] = []
        for items in self._confirmations.values():
            result.extend(c for c in items if c.lake_table == lake_table)
        return result

    def events(self, handover_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(handover_id, []))

    def _add_event(self, handover_id: str, kind: str, message: str,
                   **detail: Any) -> None:
        self._events.setdefault(handover_id, []).append({
            "at": time.time(),
            "kind": kind,
            "message": message,
            "detail": detail,
        })

    # ---- 截止点 ----

    def ensure_table_cutoff(self, lake_table: str, value: str) -> str:
        """创建或抬高表级采集截止点；目录确认登记时调用。"""
        value = normalize_ts(value) if value else ""
        current = self._table_cutoffs.get(lake_table)
        if current is None or (value and parse_ts(value) > parse_ts(current)):
            self._table_cutoffs[lake_table] = value
        return self._table_cutoffs[lake_table]

    def get_table_cutoff(self, lake_table: str) -> str | None:
        return self._table_cutoffs.get(lake_table)

    def get_source_cutoff(self, lake_table: str, source_system: str) -> str | None:
        return self._source_cutoffs.get((lake_table, source_system))

    def _ensure_source_cutoff(self, lake_table: str, source_system: str, value: str) -> None:
        key = (lake_table, source_system)
        current = self._source_cutoffs.get(key)
        if current is None:
            self._source_cutoffs[key] = value
        elif value and parse_ts(value) > parse_ts(current):
            self._source_cutoffs[key] = value

    def _advance_source_cutoff(self, lake_table: str, source_system: str, value: str) -> None:
        key = (lake_table, source_system)
        current = self._source_cutoffs.get(key)
        if current is None or parse_ts(value) > parse_ts(current):
            self._source_cutoffs[key] = value

    def _advance_table_cutoff(self, lake_table: str, value: str) -> None:
        current = self._table_cutoffs.get(lake_table)
        if not current or parse_ts(value) > parse_ts(current):
            self._table_cutoffs[lake_table] = value

    def cutoffs(self, lake_table: str) -> dict[str, Any]:
        by_source = {
            source: value
            for (table, source), value in self._source_cutoffs.items()
            if table == lake_table
        }
        return {
            "lake_table": lake_table,
            "table": self._table_cutoffs.get(lake_table),
            "by_source": by_source,
        }

    # ---- 交接创建 ----

    def create_handover(
        self,
        *,
        lake_table: str,
        old_source: str,
        new_source: str,
        handover_at: str,
        old_batch_id: str,
        old_window_start: str,
        old_window_end: str,
        row_count: int = 0,
        handover_id: str | None = None,
        continuation_token: str | None = None,
        prior_confirmed: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """登记一次来源交接，并把旧源在途批次拆成按窗口归属的段。

        交接时刻按约定归新源（例如新源从 09:00 开始接收）。
        """
        with self._lock:
            if not lake_table or not old_source or not new_source:
                raise ValueError("所属湖表、旧来源系统、新来源系统不能为空")
            if old_source == new_source:
                raise ValueError("旧来源系统与新来源系统不能相同")
            start = normalize_ts(old_window_start)
            end = normalize_ts(old_window_end)
            boundary = normalize_ts(handover_at, default_time="09:00:00")
            if parse_ts(start) >= parse_ts(end):
                raise ValueError("旧源在途窗口结束时间必须晚于开始时间")
            if parse_ts(boundary) < parse_ts(start):
                raise ValueError("交接时刻不能早于旧源在途窗口开始时间")

            hid = handover_id or f"HO-{lake_table}-{uuid.uuid4().hex[:6]}"
            if hid in self._handovers:
                hid = f"{hid}-{uuid.uuid4().hex[:4]}"
            token = continuation_token or f"cont-{lake_table}-{uuid.uuid4().hex[:8]}"
            record = HandoverRecord(
                handover_id=hid,
                lake_table=lake_table,
                old_source=old_source,
                new_source=new_source,
                handover_at=boundary,
                old_batch_id=old_batch_id or f"batch-{lake_table}-{uuid.uuid4().hex[:6]}",
                old_window_start=start,
                old_window_end=end,
                continuation_token=token,
                row_count=max(0, int(row_count)),
                status="交接中",
            )
            self._handovers[hid] = record
            self._segments[hid] = []
            self._confirmations[hid] = []
            self._events[hid] = []

            # 旧源 / 新源各自的采集截止点基线；已有表级截止点时不得因登记交接而抬高
            self._ensure_source_cutoff(lake_table, old_source, start)
            self._ensure_source_cutoff(lake_table, new_source, boundary)
            if self._table_cutoffs.get(lake_table) is None:
                self._table_cutoffs[lake_table] = start

            for part_start, part_end, phase in split_window(start, end, boundary):
                owner = old_source if phase == "pre" else new_source
                segment = HandoverSegment(
                    segment_id=f"SEG-{uuid.uuid4().hex[:8]}",
                    handover_id=hid,
                    lake_table=lake_table,
                    batch_id=record.old_batch_id,
                    original_source=old_source,
                    window_owner=owner,
                    window_start=part_start,
                    window_end=part_end,
                    state="in_flight" if phase == "pre" else "at_risk",
                    row_count=_clamp_rows(record.row_count, part_start, part_end, start, end),
                )
                self._segments[hid].append(segment)

            self._add_event(
                hid, "handover_created",
                f"来源交接登记：{old_source} → {new_source}，新源自 {boundary} 接收",
                old_window=f"{start} ~ {end}",
            )

            # 交接前已经确认的窗口：仍沿原来源核对，且计入旧源截止点
            for item in prior_confirmed or []:
                self._record_prior_confirmation(
                    record,
                    batch_id=str(item.get("batch_id") or record.old_batch_id),
                    window_start=normalize_ts(str(item.get("window_start") or start)),
                    window_end=normalize_ts(str(item.get("window_end") or boundary)),
                    row_count=int(item.get("row_count") or 0),
                )

            self._refresh_pending_intervals(record)
            record.updated_at = time.time()
            return record.as_dict()

    def _record_prior_confirmation(
        self, record: HandoverRecord, *, batch_id: str,
        window_start: str, window_end: str, row_count: int,
    ) -> BatchConfirmation:
        """登记交接前已确认的窗口：核对来源固定为原来源（旧源）。"""
        confirmation = BatchConfirmation(
            confirmation_id=f"CONF-{uuid.uuid4().hex[:8]}",
            handover_id=record.handover_id,
            lake_table=record.lake_table,
            batch_id=batch_id,
            original_source=record.old_source,
            submitted_source=record.old_source,
            window_owner=record.old_source,
            window_start=window_start,
            window_end=window_end,
            status=CONFIRMED,
            auth_mode="source",
            verification_source=record.old_source,
            row_count=row_count,
            checksum=f"prior-{uuid.uuid4().hex[:6]}",
            reason="交接前已确认，沿原来源核对",
        )
        self._confirmations[record.handover_id].append(confirmation)
        self._advance_source_cutoff(record.lake_table, record.old_source, window_end)
        self._advance_table_cutoff(record.lake_table, window_end)
        self._mark_segment(record, confirmation, "confirmed")
        self._add_event(
            record.handover_id, "prior_confirmed",
            f"交接前窗口已确认：{batch_id} {window_start} ~ {window_end}",
        )
        return confirmation

    # ---- 确认查找 / 去重 ----

    def _find_confirmation(
        self, handover_id: str, batch_id: str, window_start: str,
        window_end: str, window_owner: str,
    ) -> BatchConfirmation | None:
        for item in self._confirmations.get(handover_id, []):
            if (
                item.batch_id == batch_id
                and item.window_start == window_start
                and item.window_end == window_end
                and item.window_owner == window_owner
            ):
                return item
        return None

    def _find_overlap(
        self, handover_id: str, window_start: str, window_end: str,
        *, submitted_source: str | None = None,
        window_owner: str | None = None,
        statuses: tuple[str, ...] = (CONFIRMED, PENDING_CONFIRMATION),
    ) -> BatchConfirmation | None:
        for item in self._confirmations.get(handover_id, []):
            if item.status not in statuses:
                continue
            if submitted_source is not None and item.submitted_source != submitted_source:
                continue
            if window_owner is not None and item.window_owner != window_owner:
                continue
            if overlap(item.window_start, item.window_end, window_start, window_end):
                return item
        return None

    # ---- 待办区间 ----

    def _refresh_pending_intervals(self, record: HandoverRecord) -> None:
        """按窗口段和待证确认重算待办区间。"""
        pending: list[dict[str, Any]] = []
        for segment in self._segments.get(record.handover_id, []):
            if segment.state not in ("in_flight", "at_risk", "pending_proof"):
                continue
            if segment.state == "pending_proof":
                reason = "新源凭据已代送，等待旧源按原批号补认"
            elif segment.window_owner == record.old_source:
                reason = "旧源在途窗口尚未确认"
            else:
                reason = "旧源迟到半批归属新源窗口，待新源落地后覆盖"
            pending.append({
                "batch_id": segment.batch_id,
                "window_start": segment.window_start,
                "window_end": segment.window_end,
                "window_owner": segment.window_owner,
                "required_source": segment.window_owner,
                "segment_state": segment.state,
                "reason": reason,
            })
        for item in self._confirmations.get(record.handover_id, []):
            if item.status == PENDING_PROOF:
                pending.append({
                    "batch_id": item.batch_id,
                    "window_start": item.window_start,
                    "window_end": item.window_end,
                    "window_owner": item.window_owner,
                    "required_source": record.old_source,
                    "segment_state": "pending_proof",
                    "confirmation_id": item.confirmation_id,
                    "reason": "新源凭据已代送，等待旧源按原批号补认",
                })
            elif item.status == PENDING_CONFIRMATION:
                pending.append({
                    "batch_id": item.batch_id,
                    "window_start": item.window_start,
                    "window_end": item.window_end,
                    "window_owner": item.window_owner,
                    "required_source": record.new_source,
                    "segment_state": "late_half",
                    "confirmation_id": item.confirmation_id,
                    "reason": "旧源迟到半批暂记，等待新源窗口落地",
                })
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict[str, Any]] = []
        for item in pending:
            key = (item["batch_id"], item["window_start"], item["window_end"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        record.pending_intervals = unique

    def pending_proxy_windows(self, handover_id: str) -> list[dict[str, Any]]:
        """详情页/接收接口共用：待代送或待旧源补认的旧源归属窗口。"""
        record = self._handovers.get(handover_id)
        if record is None:
            return []
        self._refresh_pending_intervals(record)
        return [
            item for item in record.pending_intervals
            if item.get("required_source") == record.old_source
        ]

    # ---- 接收 / 代送 ----

    def receive_batch(
        self,
        handover_id: str,
        *,
        submitted_source: str,
        window_start: str,
        window_end: str,
        batch_id: str = "",
        row_count: int = 0,
        checksum: str = "",
        credential: str | None = None,
        operator: str = "",
    ) -> dict[str, Any]:
        """接收入湖批次：按窗口归属确认，必要时拒绝、暂存或要求旧源补证。

        返回结构里 `ok=False` 时带 `error` 与 `intervals`，路由层据此返回 409。
        """
        with self._lock:
            record = self._handovers.get(handover_id)
            if record is None:
                return {"ok": False, "error": "handover_not_found",
                        "message": f"交接记录 {handover_id} 不存在"}

            if submitted_source not in (record.old_source, record.new_source):
                return {"ok": False, "error": "invalid_source",
                        "message": "提交来源必须是交接记录里的旧源或新源"}

            start = normalize_ts(window_start)
            end = normalize_ts(window_end)
            if not start or not end or parse_ts(start) >= parse_ts(end):
                return {"ok": False, "error": "invalid_window",
                        "message": "接收窗口结束时间必须晚于开始时间"}
            batch_id = batch_id or record.old_batch_id

            proxy_mode = bool(credential)
            if proxy_mode and credential != record.continuation_token:
                return {
                    "ok": False,
                    "error": "invalid_continuation_credential",
                    "message": "接续凭据无效，不能代送旧源窗口",
                    "required_source": record.old_source,
                    "intervals": [{
                        "batch_id": batch_id,
                        "window_start": start,
                        "window_end": end,
                        "window_owner": record.old_source,
                    }],
                }

            parts = split_window(start, end, record.handover_at)
            old_parts = [p for p in parts if p[2] == "pre"]
            post_parts = [p for p in parts if p[2] == "post"]

            if proxy_mode:
                # 新源接续凭据只能代送旧源归属窗口。若同时带上新源窗口且该窗口
                # 已被新源确认，整单拒绝并返回需要旧源补证的确切区间。
                conflicts: list[dict[str, Any]] = []
                for part_start, part_end, _ in post_parts:
                    confirmed_new = self._find_overlap(
                        handover_id, part_start, part_end,
                        window_owner=record.new_source, statuses=(CONFIRMED,),
                    )
                    if confirmed_new is not None:
                        conflicts.append({
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": record.new_source,
                            "confirmed_by": confirmed_new.confirmation_id,
                        })
                if conflicts:
                    record.conflict_intervals = conflicts
                    record.updated_at = time.time()
                    self._add_event(
                        handover_id, "continuation_conflict",
                        "新源代送许可与已确认的交接后窗口冲突，需旧源按原批号补证",
                        intervals=conflicts,
                    )
                    intervals = [
                        {
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": record.old_source,
                        }
                        for part_start, part_end, _ in old_parts
                    ]
                    return {
                        "ok": False,
                        "error": "continuation_permission_conflict",
                        "message": "新源接续凭据与已确认窗口冲突，需旧源补证",
                        "required_source": record.old_source,
                        "intervals": intervals or [{
                            "batch_id": batch_id,
                            "window_start": conflicts[0]["window_start"],
                            "window_end": conflicts[0]["window_end"],
                            "window_owner": record.new_source,
                        }],
                        "conflict_intervals": conflicts,
                    }

            results: list[BatchConfirmation] = []
            duplicates: list[BatchConfirmation] = []
            rejected: list[dict[str, Any]] = []
            encountered: dict[str, Any] | None = None
            accepted_any = False

            for part_start, part_end, phase in parts:
                owner = record.old_source if phase == "pre" else record.new_source

                # ---------- 代送：只处理旧源归属窗口 ----------
                if proxy_mode:
                    if owner != record.old_source:
                        rejected.append({
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": owner,
                            "reason": "接续凭据只代送旧源窗口，新源窗口请由新源正常接收",
                            "required_source": owner,
                        })
                        continue
                    existing = self._find_confirmation(
                        handover_id, batch_id, part_start, part_end, owner)
                    if existing is not None:
                        duplicates.append(existing)
                        continue
                    confirmation = self._new_confirmation(
                        record, batch_id=batch_id,
                        submitted_source=record.new_source,
                        window_owner=owner, start=part_start, end=part_end,
                        status=PENDING_PROOF,
                        verification_source=record.old_source,
                        row_count=row_count, checksum=checksum,
                        auth_mode="continuation_proxy",
                        credential_holder=record.new_source,
                        reason="新源接续凭据代送，等待旧源按原批号补认",
                    )
                    self._mark_segment(record, confirmation, "pending_proof")
                    results.append(confirmation)
                    accepted_any = True
                    continue

                # ---------- 新源正常接收 ----------
                if submitted_source == record.new_source:
                    if owner == record.old_source:
                        rejected.append({
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": owner,
                            "reason": "交接前窗口归属旧源，新源不得确认",
                            "required_source": record.old_source,
                        })
                        continue
                    late = self._find_overlap(
                        handover_id, part_start, part_end,
                        submitted_source=record.old_source,
                        statuses=(PENDING_CONFIRMATION,),
                    )
                    existing = self._find_confirmation(
                        handover_id, batch_id, part_start, part_end, owner)
                    if late is not None:
                        confirmation = self._new_confirmation(
                            record, batch_id=batch_id,
                            submitted_source=record.new_source,
                            window_owner=owner, start=part_start, end=part_end,
                            status=CONFIRMED,
                            verification_source=record.new_source,
                            row_count=row_count, checksum=checksum,
                            reason="新源确认交接后窗口，旧源迟到半批被覆盖",
                        )
                        late.status = SUPERSEDED
                        late.superseded_by = confirmation.confirmation_id
                        late.updated_at = time.time()
                        late.reason = "旧源迟到半批被新源确认覆盖"
                        self._advance_cutoffs(record, owner, part_end)
                        self._mark_segment(record, confirmation, "confirmed")
                        encountered = {
                            "confirmation_id": late.confirmation_id,
                            "batch_id": late.batch_id,
                            "window_start": late.window_start,
                            "window_end": late.window_end,
                            "resolution": "新源确认生效，旧源迟到半批置为 superseded",
                            "restart_required": True,
                        }
                        results.append(confirmation)
                        accepted_any = True
                        self._add_event(
                            handover_id, "late_half_superseded",
                            "新源提交时遇到旧源迟到的半批确认，已按窗口归属覆盖",
                            late_confirmation=late.confirmation_id,
                            new_confirmation=confirmation.confirmation_id,
                        )
                        continue
                    if existing is not None:
                        duplicates.append(existing)
                        continue
                    confirmation = self._new_confirmation(
                        record, batch_id=batch_id,
                        submitted_source=record.new_source,
                        window_owner=owner, start=part_start, end=part_end,
                        status=CONFIRMED,
                        verification_source=record.new_source,
                        row_count=row_count, checksum=checksum,
                        reason="按窗口归属确认",
                    )
                    self._advance_cutoffs(record, owner, part_end)
                    self._mark_segment(record, confirmation, "confirmed")
                    results.append(confirmation)
                    accepted_any = True
                    continue

                # ---------- 旧源提交 ----------
                if owner == record.new_source:
                    if not record.old_writable:
                        rejected.append({
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": owner,
                            "reason": "作业重启后旧源暂时不可写，需用新源接续凭据代送或恢复旧源",
                            "required_source": record.old_source,
                        })
                        continue
                    protected = self._find_overlap(
                        handover_id, part_start, part_end,
                        window_owner=record.new_source, statuses=(CONFIRMED,),
                    )
                    if protected is not None:
                        rejected.append({
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": owner,
                            "reason": "新源已确认的交接后记录不可被旧源覆盖",
                            "protected_by": protected.confirmation_id,
                            "required_source": record.new_source,
                        })
                        continue
                    existing = self._find_confirmation(
                        handover_id, batch_id, part_start, part_end, owner)
                    if existing is not None:
                        duplicates.append(existing)
                        continue
                    late = self._new_confirmation(
                        record, batch_id=batch_id,
                        submitted_source=record.old_source,
                        window_owner=owner, start=part_start, end=part_end,
                        status=PENDING_CONFIRMATION,
                        verification_source=record.old_source,
                        row_count=row_count, checksum=checksum,
                        reason="旧源迟到半批暂记，等待新源窗口落地",
                    )
                    self._mark_segment(record, late, "at_risk")
                    results.append(late)
                    accepted_any = True
                    self._add_event(
                        handover_id, "late_half_pending",
                        f"旧源迟到半批暂记：{batch_id} {part_start} ~ {part_end}",
                    )
                    continue

                # owner == old_source
                if not record.old_writable:
                    rejected.append({
                        "batch_id": batch_id,
                        "window_start": part_start,
                        "window_end": part_end,
                        "window_owner": owner,
                        "reason": (
                            "作业重启后旧源暂时不可写；请用新源接续凭据代送，"
                            "待旧源恢复后再按原批号补认"
                        ),
                        "required_source": record.old_source,
                    })
                    continue

                existing = self._find_confirmation(
                    handover_id, batch_id, part_start, part_end, owner)
                if existing is not None:
                    if existing.status == PENDING_PROOF:
                        # 旧源按原批号补认：升级同一条确认，不新增第二条
                        existing.status = CONFIRMED
                        existing.verification_source = record.old_source
                        existing.updated_at = time.time()
                        existing.reason = "旧源按原批号补认，代送确认升级为已确认"
                        self._advance_cutoffs(record, owner, part_end)
                        self._mark_segment(record, existing, "confirmed")
                        results.append(existing)
                        accepted_any = True
                        self._add_event(
                            handover_id, "proof_completed",
                            f"旧源按原批号补认 {batch_id} {part_start} ~ {part_end}",
                            confirmation_id=existing.confirmation_id,
                        )
                        continue
                    if existing.status == SUPERSEDED:
                        rejected.append({
                            "batch_id": batch_id,
                            "window_start": part_start,
                            "window_end": part_end,
                            "window_owner": owner,
                            "reason": "该窗口已被新源确认，旧源确认不再生效",
                            "superseded_by": existing.superseded_by,
                            "required_source": record.new_source,
                        })
                        continue
                    duplicates.append(existing)
                    continue

                confirmation = self._new_confirmation(
                    record, batch_id=batch_id,
                    submitted_source=record.old_source,
                    window_owner=owner, start=part_start, end=part_end,
                    status=CONFIRMED,
                    verification_source=record.old_source,
                    row_count=row_count, checksum=checksum,
                    reason="旧源按原批号确认在途窗口",
                )
                self._advance_cutoffs(record, owner, part_end)
                self._mark_segment(record, confirmation, "confirmed")
                results.append(confirmation)
                accepted_any = True

            self._finalize_after_receive(record, results, duplicates, rejected)
            proxy_pending = [r for r in results if r.status == PENDING_PROOF]
            if proxy_pending:
                self._add_event(
                    handover_id, "proxy_submitted",
                    "新源接续凭据代送旧源窗口，等待旧源补证",
                    windows=[f"{c.window_start} ~ {c.window_end}" for c in proxy_pending],
                )
            elif accepted_any:
                self._add_event(
                    handover_id, "batch_received",
                    f"{submitted_source} 提交 {batch_id} {start} ~ {end}",
                    operator=operator,
                )

            payload: dict[str, Any] = {
                "ok": True,
                "handover_id": handover_id,
                "batch_id": batch_id,
                "submitted_source": submitted_source,
                "proxy": proxy_mode,
                "confirmations": [c.as_dict(record.handover_at) for c in results],
                "duplicates": [c.as_dict(record.handover_at) for c in duplicates],
                "rejected": rejected,
                "encountered_late_confirmation": encountered,
                "restart_required": bool(encountered),
                "proxy_permission": self.proxy_permission(handover_id),
                "cutoffs": self.cutoffs(record.lake_table),
                "proxy_pending_windows": self.pending_proxy_windows(handover_id),
            }
            if not results and not duplicates and rejected:
                # 整单没有一段被接受：按业务拒绝返回，让调用方看到确切窗口与原因
                payload.update({
                    "ok": False,
                    "error": "window_rejected",
                    "message": rejected[0]["reason"],
                    "required_source": rejected[0].get("required_source"),
                    "intervals": [
                        {
                            "batch_id": item["batch_id"],
                            "window_start": item["window_start"],
                            "window_end": item["window_end"],
                            "window_owner": item["window_owner"],
                            "reason": item["reason"],
                        }
                        for item in rejected
                    ],
                })
            return payload

    def _advance_cutoffs(self, record: HandoverRecord, owner: str, value: str) -> None:
        self._advance_source_cutoff(record.lake_table, owner, value)
        self._advance_table_cutoff(record.lake_table, value)

    def _new_confirmation(
        self, record: HandoverRecord, *, batch_id: str, submitted_source: str,
        window_owner: str, start: str, end: str, status: str,
        verification_source: str, row_count: int, checksum: str,
        auth_mode: str = "source", credential_holder: str | None = None,
        reason: str = "",
    ) -> BatchConfirmation:
        confirmation = BatchConfirmation(
            confirmation_id=f"CONF-{uuid.uuid4().hex[:8]}",
            handover_id=record.handover_id,
            lake_table=record.lake_table,
            batch_id=batch_id,
            original_source=record.old_source,
            submitted_source=submitted_source,
            window_owner=window_owner,
            window_start=start,
            window_end=end,
            status=status,
            auth_mode=auth_mode,
            credential_holder=credential_holder,
            verification_source=verification_source,
            row_count=row_count,
            checksum=checksum or f"md5-{uuid.uuid4().hex[:6]}",
            reason=reason,
        )
        self._confirmations[record.handover_id].append(confirmation)
        return confirmation

    def _mark_segment(self, record: HandoverRecord, confirmation: BatchConfirmation,
                      state: str) -> None:
        """按窗口归属落段状态：同一窗口段可能由不同批次确认（如新源接续接收），
        因此匹配窗口归属 + 区间重叠，而不是只认原批号。"""
        for segment in self._segments.get(record.handover_id, []):
            if segment.window_owner != confirmation.window_owner:
                continue
            if overlap(segment.window_start, segment.window_end,
                       confirmation.window_start, confirmation.window_end):
                segment.state = state
                segment.confirmation_id = confirmation.confirmation_id

    def _finalize_after_receive(
        self, record: HandoverRecord,
        results: list[BatchConfirmation],
        duplicates: list[BatchConfirmation],
        rejected: list[dict[str, Any]],
    ) -> None:
        record.updated_at = time.time()
        self._refresh_pending_intervals(record)
        has_pending_proof = any(
            c.status == PENDING_PROOF
            for c in self._confirmations.get(record.handover_id, [])
        )
        old_pending = any(
            item["required_source"] == record.old_source
            for item in record.pending_intervals
        )
        if has_pending_proof or (not record.old_writable and old_pending):
            if record.status != "已完成":
                record.status = "待旧源补证"
        elif record.status == "待旧源补证":
            record.status = "交接中"

    # ---- 许可 / 待办 ----

    def proxy_permission(self, handover_id: str) -> dict[str, Any]:
        record = self._handovers.get(handover_id)
        if record is None:
            return {"allowed": False, "reason": "交接记录不存在",
                    "pending_intervals": []}
        pending = self.pending_proxy_windows(handover_id)
        active = record.status not in ("已完成", "已取消")
        allowed = bool(record.continuation_token) and active
        if not active:
            summary = "交接已结束，接续凭据不再具备代送权限"
        elif record.old_writable:
            summary = "旧源当前可写；接续凭据仍可用于查询待办窗口，代送须旧源补认"
        else:
            summary = (
                "新源接续凭据可代送旧源归属窗口，但只记待证确认，"
                "须旧源按原批号补认后才推进采集截止点"
            )
        return {
            "allowed": allowed,
            "can_proxy": allowed and not record.old_writable,
            "credential_holder": record.new_source,
            "credential_effective": bool(record.continuation_token),
            "old_source_writable": record.old_writable,
            "requires_old_confirmation": True,
            "pending_intervals": pending,
            "conflict_intervals": list(record.conflict_intervals),
            "summary": summary,
        }

    # ---- 交接状态动作 ----

    def restart(self, handover_id: str, *, operator: str = "") -> dict[str, Any] | None:
        """作业重启：旧源暂时不可写，记下需要补证的窗口。"""
        with self._lock:
            record = self._handovers.get(handover_id)
            if record is None:
                return None
            record.old_writable = False
            record.restart_count += 1
            record.updated_at = time.time()
            self._refresh_pending_intervals(record)
            record.status = "待旧源补证" if record.pending_intervals else "交接中"
            self._add_event(
                handover_id, "job_restarted",
                f"作业重启，旧源暂时不可写；待办窗口 {len(record.pending_intervals)} 个",
                operator=operator,
            )
            return self.detail(handover_id)

    def resume_old_source(self, handover_id: str, *, operator: str = "") -> dict[str, Any] | None:
        with self._lock:
            record = self._handovers.get(handover_id)
            if record is None:
                return None
            record.old_writable = True
            record.updated_at = time.time()
            self._refresh_pending_intervals(record)
            if record.status == "待旧源补证" and not any(
                c.status == PENDING_PROOF
                for c in self._confirmations.get(handover_id, [])
            ):
                record.status = "交接中"
            self._add_event(
                handover_id, "old_source_resumed",
                "旧源恢复可写，可按原批号补认待办窗口",
                operator=operator,
            )
            return self.detail(handover_id)

    def confirm_continuation(self, handover_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._handovers.get(handover_id)
            if record is None:
                return None
            record.updated_at = time.time()
            self._add_event(handover_id, "continuation_confirmed", "新源接续已确认")
            return record.as_dict()

    def mark_proof_required(self, handover_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._handovers.get(handover_id)
            if record is None:
                return None
            self._refresh_pending_intervals(record)
            record.status = "待旧源补证"
            record.updated_at = time.time()
            self._add_event(
                handover_id, "proof_required",
                "标记待旧源补证", pending=record.pending_intervals,
            )
            return record.as_dict()

    def complete(self, handover_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._handovers.get(handover_id)
            if record is None:
                return None
            record.status = "已完成"
            record.updated_at = time.time()
            self._add_event(handover_id, "handover_completed", "交接完成")
            return record.as_dict()

    # ---- 汇总 / 详情 / 核对 ----

    def detail(self, handover_id: str) -> dict[str, Any] | None:
        record = self._handovers.get(handover_id)
        if record is None:
            return None
        with self._lock:
            self._refresh_pending_intervals(record)
            return {
                "handover": record.as_dict(),
                "segments": [s.as_dict() for s in self.segments(handover_id)],
                "confirmations": [
                    c.as_dict(record.handover_at)
                    for c in self.confirmations(handover_id)
                ],
                "pending_intervals": list(record.pending_intervals),
                "pending_proxy_windows": self.pending_proxy_windows(handover_id),
                "proxy_permission": self.proxy_permission(handover_id),
                "cutoffs": self.cutoffs(record.lake_table),
                "events": self.events(handover_id),
                "verification": {
                    "original_source": record.old_source,
                    "rule": "交接前窗口沿原来源核对，交接后窗口沿新来源核对",
                },
            }

    def verify_window(self, lake_table: str, window_start: str, window_end: str) -> dict[str, Any]:
        """按窗口核对确认：交接前窗口返回原来源，命中多条时按更新时间取最新。"""
        start, end = normalize_ts(window_start), normalize_ts(window_end)
        matches = [
            c for c in self.confirmations_for_table(lake_table)
            if c.status == CONFIRMED and overlap(c.window_start, c.window_end, start, end)
        ]
        if not matches:
            return {
                "found": False,
                "lake_table": lake_table,
                "window_start": start,
                "window_end": end,
            }
        latest = max(matches, key=lambda c: c.updated_at)
        handover = self._handovers.get(latest.handover_id)
        return {
            "found": True,
            "lake_table": lake_table,
            "window_start": start,
            "window_end": end,
            "confirmation": latest.as_dict(handover.handover_at if handover else None),
            "verified_by": latest.verification_source or latest.original_source,
            "original_source": latest.original_source,
        }


# 全局单例
handover_store = HandoverStore()
