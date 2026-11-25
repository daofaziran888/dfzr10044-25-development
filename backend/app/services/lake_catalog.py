"""湖表目录领域服务：登记、冻结、下线湖表条目，关联存储引擎文件注册。"""
from __future__ import annotations

import time
from typing import Any

from app.handover_store import handover_store, normalize_ts
from app.services.base import BaseModuleService
from app.store import store
from app.lake_store import lake_store


class LakeCatalogService(BaseModuleService):
    MODULE = "lake_catalog"
    ENTITY = "湖表条目"
    OPERATION_TERM = "入湖"
    LIST_FIELDS = ["表编号", "表名全称", "所属库域", "表格式", "存储路径", "负责人标识", "登记时间", "当前版本"]
    REQUIRED_FIELDS = ["表编号", "表名全称", "所属库域"]
    STATUS_ORDER = ["待登记", "已登记", "已冻结", "已下线"]
    ACTION_RULES = {"确认登记": "已登记", "冻结条目": "已冻结", "下线条目": "已下线"}
    NEGATIVE_ACTIONS = ["下线条目"]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记新湖表条目，同时注册存储引擎文件和初始化快照。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        lake_table = str(values.get("表名全称", ""))
        if lake_table:
            pf = lake_store.register_file(lake_table, "init", rows=0, bytes_=0)
            lake_store.create_snapshot(
                lake_table, file_ids=[pf.file_id],
                parent_snapshot=None, added_rows=0, deleted_rows=0,
            )
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """湖表操作：确认登记时触发水位初始化，下线时释放锁。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "确认登记":
            lake_table = str(entry.get("表名全称", ""))
            if lake_table:
                lake_store.set_watermark(lake_table, "登记时间", str(entry.get("登记时间", "2026-01-01")), "append")
                # 确认登记即建立表级采集截止点，后续来源交接以它为归属基线。
                registered_at = str(entry.get("登记时间") or "").strip()
                if registered_at:
                    handover_store.ensure_table_cutoff(
                        lake_table, normalize_ts(registered_at, default_time="00:00:00"))
        return super().run_action(entry_id, action)

    def query_by_keyword(self, keyword: str, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        return self.list_entries(keyword=keyword, page=page, size=size)

    def stats_snapshot(self) -> dict[str, Any]:
        """输出目录级统计数据，供跨模块聚合消费。"""
        rows = store.rows(self.MODULE)
        registered = sum(1 for r in rows if r.get("status") == "已登记")
        frozen = sum(1 for r in rows if r.get("status") == "已冻结")
        offline = sum(1 for r in rows if r.get("status") == "已下线")
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "total": len(rows),
            "registered": registered,
            "frozen": frozen,
            "offline": offline,
            "registration_rate": registered / len(rows) if rows else 0,
        }