"""来源交接领域测试：窗口归属、迟到半批、接续代送、补证与截止点隔离。"""
from __future__ import annotations

import pytest

from app.handover_store import HandoverStore, split_window, overlap


@pytest.fixture
def ho() -> HandoverStore:
    return HandoverStore()


def _create(ho: HandoverStore, lake_table: str = "tbl", old_batch: str = "B-OLD"):
    return ho.create_handover(
        lake_table=lake_table,
        old_source="OLD",
        new_source="NEW",
        handover_at="2026-11-25T09:00:00",
        old_batch_id=old_batch,
        old_window_start="2026-11-25T08:30:00",
        old_window_end="2026-11-25T09:30:00",
        row_count=1200,
    )


class TestWindowSplit:
    def test_split_mid_window(self):
        assert split_window("2026-11-25T08:30:00", "2026-11-25T09:30:00",
                            "2026-11-25T09:00:00") == [
            ("2026-11-25T08:30:00", "2026-11-25T09:00:00", "pre"),
            ("2026-11-25T09:00:00", "2026-11-25T09:30:00", "post"),
        ]

    def test_split_boundary_belongs_to_new_source(self):
        # 交接时刻本身归新源：只覆盖交接点的窗口整段是 post
        assert split_window("2026-11-25T09:00:00", "2026-11-25T10:00:00",
                            "2026-11-25T09:00:00") == [
            ("2026-11-25T09:00:00", "2026-11-25T10:00:00", "post"),
        ]

    def test_split_wholly_before(self):
        assert split_window("2026-11-25T08:00:00", "2026-11-25T08:30:00",
                            "2026-11-25T09:00:00")[0][2] == "pre"

    def test_half_open_overlap(self):
        assert overlap("2026-11-25T08:00:00", "2026-11-25T09:00:00",
                       "2026-11-25T09:00:00", "2026-11-25T10:00:00") is None
        assert overlap("2026-11-25T08:30:00", "2026-11-25T09:30:00",
                       "2026-11-25T09:00:00", "2026-11-25T10:00:00") is not None


class TestHandoverCreate:
    def test_segments_keep_original_source(self, ho):
        _create(ho)
        detail = ho.detail("HO-tbl-" + "") if False else ho.detail(
            ho.list_handovers()[0].handover_id)
        owners = [(s["window_owner"], s["window_start"], s["window_end"])
                  for s in detail["segments"]]
        assert owners == [
            ("OLD", "2026-11-25T08:30:00", "2026-11-25T09:00:00"),
            ("NEW", "2026-11-25T09:00:00", "2026-11-25T09:30:00"),
        ]
        assert all(s["original_source"] == "OLD" for s in detail["segments"])

    def test_rejects_same_source(self, ho):
        with pytest.raises(ValueError):
            ho.create_handover(
                lake_table="tbl", old_source="S", new_source="S",
                handover_at="2026-11-25T09:00:00", old_batch_id="b",
                old_window_start="2026-11-25T08:30:00",
                old_window_end="2026-11-25T09:30:00")

    def test_prior_confirmed_kept_under_original_source(self, ho):
        ho.create_handover(
            lake_table="tbl", old_source="OLD", new_source="NEW",
            handover_at="2026-11-25T09:00:00", old_batch_id="B-OLD",
            old_window_start="2026-11-25T08:30:00",
            old_window_end="2026-11-25T09:30:00",
            prior_confirmed=[{
                "batch_id": "B-PRE", "window_start": "2026-11-25T08:00:00",
                "window_end": "2026-11-25T08:30:00", "row_count": 50,
            }])
        check = ho.verify_window("tbl", "2026-11-25T08:00:00", "2026-11-25T08:30:00")
        assert check["found"]
        assert check["verified_by"] == "OLD"


class TestLateHalf:
    def test_old_full_window_then_new_supersedes_late_half(self, ho):
        h = _create(ho)
        hid = h["handover_id"]
        # 旧源把 8:30~9:30 整批投出：旧源段确认，新源段先暂记
        res = ho.receive_batch(hid, submitted_source="OLD",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:30:00",
                               batch_id="B-OLD")
        statuses = {c["window_owner"]: c["status"] for c in res["confirmations"]}
        assert statuses == {"OLD": "confirmed", "NEW": "pending_confirmation"}
        # 新源提交 9~10：遇到旧源迟到的半批，覆盖并要求重启
        res = ho.receive_batch(hid, submitted_source="NEW",
                               window_start="2026-11-25T09:00:00",
                               window_end="2026-11-25T10:00:00",
                               batch_id="B-NEW")
        assert res["restart_required"]
        assert res["encountered_late_confirmation"]["window_start"] == "2026-11-25T09:00:00"
        late = [c for c in ho.confirmations(hid) if c.status == "superseded"]
        assert len(late) == 1
        # 重启后旧源暂时不可写：直接补认会被拒
        ho.restart(hid)
        blocked = ho.receive_batch(hid, submitted_source="OLD",
                                   window_start="2026-11-25T08:30:00",
                                   window_end="2026-11-25T09:00:00",
                                   batch_id="B-OLD")
        assert not blocked["ok"]
        assert blocked["error"] == "window_rejected"
        # 旧源恢复后，8:30~9:00 仍按原批号补认（命中已有确认，不新增）
        ho.resume_old_source(hid)
        res = ho.receive_batch(hid, submitted_source="OLD",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:00:00",
                               batch_id="B-OLD")
        assert res["ok"]
        assert len(res["duplicates"]) == 1


class TestProtection:
    def test_new_records_after_midnight_not_overwritten(self, ho):
        h = _create(ho)
        hid = h["handover_id"]
        ho.receive_batch(hid, submitted_source="NEW",
                         window_start="2026-11-25T09:00:00",
                         window_end="2026-11-25T09:30:00", batch_id="B-NEW")
        res = ho.receive_batch(hid, submitted_source="OLD",
                               window_start="2026-11-25T09:00:00",
                               window_end="2026-11-25T09:30:00",
                               batch_id="B-OLD")
        assert not res["ok"]
        assert res["rejected"][0]["protected_by"]


class TestProxy:
    def test_proxy_requires_old_confirmation(self, ho):
        h = _create(ho)
        hid = h["handover_id"]
        token = h["continuation_token"]
        ho.restart(hid)
        res = ho.receive_batch(hid, submitted_source="NEW",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:00:00",
                               batch_id="B-OLD", credential=token)
        assert res["ok"]
        assert res["confirmations"][0]["status"] == "pending_proof"
        assert res["confirmations"][0]["original_source"] == "OLD"
        # 未补认前旧源截止点不前进
        assert ho.get_source_cutoff("tbl", "OLD") == "2026-11-25T08:30:00"
        permission = ho.proxy_permission(hid)
        assert permission["requires_old_confirmation"]
        assert permission["pending_intervals"][0]["window_start"] == "2026-11-25T08:30:00"

        ho.resume_old_source(hid)
        res = ho.receive_batch(hid, submitted_source="OLD",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:00:00",
                               batch_id="B-OLD")
        assert res["ok"]
        assert res["confirmations"][0]["status"] == "confirmed"
        assert ho.get_source_cutoff("tbl", "OLD") == "2026-11-25T09:00:00"
        assert ho.get_source_cutoff("tbl", "NEW") == "2026-11-25T09:00:00"

    def test_proxy_conflict_returns_old_intervals(self, ho):
        h = _create(ho)
        hid = h["handover_id"]
        token = h["continuation_token"]
        ho.receive_batch(hid, submitted_source="NEW",
                         window_start="2026-11-25T09:00:00",
                         window_end="2026-11-25T09:30:00", batch_id="B-NEW")
        ho.restart(hid)
        # 代送窗口跨到已被新源确认的 9~9:30
        res = ho.receive_batch(hid, submitted_source="NEW",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:30:00",
                               batch_id="B-OLD", credential=token)
        assert not res["ok"]
        assert res["error"] == "continuation_permission_conflict"
        assert res["required_source"] == "OLD"
        assert res["intervals"] == [{
            "batch_id": "B-OLD",
            "window_start": "2026-11-25T08:30:00",
            "window_end": "2026-11-25T09:00:00",
            "window_owner": "OLD",
        }]

    def test_invalid_credential_rejected(self, ho):
        h = _create(ho)
        res = ho.receive_batch(h["handover_id"], submitted_source="NEW",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:00:00",
                               batch_id="B-OLD", credential="wrong")
        assert res["error"] == "invalid_continuation_credential"


class TestIdempotency:
    def test_duplicate_resend_confirms_once(self, ho):
        h = _create(ho)
        hid = h["handover_id"]
        token = h["continuation_token"]
        ho.restart(hid)
        ho.receive_batch(hid, submitted_source="NEW",
                         window_start="2026-11-25T08:30:00",
                         window_end="2026-11-25T09:00:00",
                         batch_id="B-OLD", credential=token)
        ho.resume_old_source(hid)
        ho.receive_batch(hid, submitted_source="OLD",
                         window_start="2026-11-25T08:30:00",
                         window_end="2026-11-25T09:00:00", batch_id="B-OLD")
        # 两边接续后重发其中一批
        res = ho.receive_batch(hid, submitted_source="OLD",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:00:00", batch_id="B-OLD")
        assert res["ok"]
        assert res["confirmations"] == []
        assert len(res["duplicates"]) == 1
        confirmed = [c for c in ho.confirmations(hid)
                     if c.batch_id == "B-OLD"
                     and c.window_start == "2026-11-25T08:30:00"
                     and c.status == "confirmed"]
        assert len(confirmed) == 1

    def test_pre_handover_window_verifies_by_original_source(self, ho):
        h = _create(ho)
        hid = h["handover_id"]
        ho.receive_batch(hid, submitted_source="OLD",
                         window_start="2026-11-25T08:30:00",
                         window_end="2026-11-25T09:00:00", batch_id="B-OLD")
        check = ho.verify_window("tbl", "2026-11-25T08:45:00", "2026-11-25T08:50:00")
        assert check["found"]
        assert check["verified_by"] == "OLD"
        assert check["original_source"] == "OLD"


class TestCutoffIsolation:
    def test_same_source_and_windows_advance_per_table(self, ho):
        h1 = _create(ho, lake_table="t1", old_batch="B1")
        h2 = _create(ho, lake_table="t2", old_batch="B2")
        ho.receive_batch(h1["handover_id"], submitted_source="NEW",
                         window_start="2026-11-25T09:00:00",
                         window_end="2026-11-25T10:00:00", batch_id="B1")
        assert ho.get_table_cutoff("t1") == "2026-11-25T10:00:00"
        # t2 同来源同窗口编号未提交，截止点仍是登记基线
        assert ho.get_table_cutoff("t2") == "2026-11-25T08:30:00"
        assert ho.get_source_cutoff("t2", "NEW") == "2026-11-25T09:00:00"

    def test_new_source_cannot_confirm_pre_handover(self, ho):
        h = _create(ho)
        res = ho.receive_batch(h["handover_id"], submitted_source="NEW",
                               window_start="2026-11-25T08:30:00",
                               window_end="2026-11-25T09:00:00", batch_id="X")
        assert not res["ok"]
        assert res["required_source"] == "OLD"


class TestSeed:
    def test_seed_handovers_build_details(self):
        from app.services.source_handover import SourceHandoverService

        svc = SourceHandoverService()
        svc.ensure_seed_handovers()
        detail = svc.detail("HO-ODS-001")
        assert detail is not None
        assert detail["handover"]["old_source"] == "OLTP_DB"
        assert len(detail["segments"]) == 2
        assert detail["proxy_permission"]["requires_old_confirmation"]
        done = svc.detail("HO-INV-001")
        assert done["handover"]["status"] == "已完成"
        assert len(done["confirmations"]) == 2
