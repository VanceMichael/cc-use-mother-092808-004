"""县域试点与制度转化服务的业务规则测试。"""

import tempfile
import unittest
from pathlib import Path

from src.pilot_transfer_service import (
    Actor,
    AuthorizationError,
    EventJournal,
    ImmutableRecordError,
    MASKED,
    PILOT,
    PROVINCE,
    PARTNER,
    EVALUATOR,
    RECEIVER,
    SYSTEM,
    PilotTransferService,
    TamperDetected,
    canonical_hash,
    load_seed,
)

SEED = Path("fixtures/five_zones_seed.json")


def province():
    return Actor("u-province", PROVINCE, "省级发展改革部门")


def pilot_unit():
    return Actor("u-jiashan", PILOT, "嘉善县试点办")


def evaluator():
    return Actor("u-evaluator", EVALUATOR, "第三方独立评估组")


def receiver():
    return Actor("u-receiver", RECEIVER, "某接收县发改部门")


def system():
    return Actor("u-system", SYSTEM, "系统")


def partner():
    return Actor("u-partner", PARTNER, "跨域伙伴单位")


def register_demo_zone(service, **overrides):
    """登记一个要素齐全的引领区，供规则测试使用。"""
    data = {
        "code": "ZT",
        "name": "测试引领区",
        "objective": "验证试点到制度的转化链路",
        "baseline_metrics": [
            {
                "code": "MT1",
                "name": "示范指标",
                "calibre": {"scope": "全县", "source": "部门联审台账", "frequency": "季"},
                "baseline": 10,
            }
        ],
        "lead_unit": "县试点办",
        "cooperating_units": ["邻县对口部门"],
        "clauses": [{"id": "CT1", "text": "互认标准条款"}],
        "commitments": [
            {"id": "KC1", "bearer": "邻县对口部门", "content": "完成系统联调", "due": "2026-08-01"},
            {"id": "KC2", "bearer": "县试点办", "content": "落实配套资金", "due": "2026-09-01"},
        ],
        "milestones": [{"id": "ST1", "name": "制度文本出台", "due": "2026-10-01"}],
        "risks": [{"id": "RT1", "text": "口径分歧"}],
    }
    data.update(overrides)
    return service.register_zone(province(), at="2026-06-01T00:00:00+00:00", **data)


def confirm_demo_evidence(service, **overrides):
    evidence = {
        "id": "E1",
        "metric_code": "MT1",
        "period": "2026Q3",
        "value": 28,
        "commitment_refs": ["KC1"],
        "sensitive_fields": {"enterprise_code": "示例脱敏税号91330000XXXX"},
    }
    evidence.update(overrides)
    return service.confirm_evaluation(evaluator(), zone_code="ZT", evidence=evidence,
                                      at="2026-10-10T00:00:00+00:00")


def publish_demo(service, **overrides):
    payload = {
        "zone_code": "ZT",
        "preconditions": [
            {"id": "P1", "description": "两地已签署互认协议"},
            {"id": "P2", "description": "具备联审台账数据来源"},
        ],
        "non_applicability": ["无跨界相邻行政区的县域不适用", "以自然人现场办理为唯一渠道的事项不适用"],
        "result_summary": "示范指标由10提升至28",
        "evidence_refs": ["E1"],
    }
    payload.update(overrides)
    return service.publish_experience(province(), at="2026-11-01T00:00:00+00:00", **payload)


class ContinuousArchiveTest(unittest.TestCase):
    def test_five_zones_form_continuous_archives(self) -> None:
        service = PilotTransferService(EventJournal())
        load_seed(service, province(), SEED)
        codes = [f"Z0{i}" for i in range(1, 6)]
        self.assertEqual(set(service.zones), set(codes))
        for code in codes:
            archive = service.zone_archive(code)
            for key in (
                "objective", "baseline_metrics", "lead_unit", "cooperating_units",
                "clauses", "commitments", "milestones", "risks",
            ):
                self.assertTrue(archive[key], f"{code}的{key}不得为空")
        z01 = service.zone_archive("Z01")
        # 基线指标自带数据口径（范围、来源、频率）
        self.assertEqual(
            z01["baseline_metrics"]["M0101"]["calibre"]["source"], "政务服务平台办件台账"
        )

    def test_zone_cannot_be_overwritten(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        with self.assertRaisesRegex(ValueError, "已建档"):
            register_demo_zone(service)

    def test_baseline_metric_requires_calibre(self) -> None:
        service = PilotTransferService(EventJournal())
        with self.assertRaisesRegex(ValueError, "材料缺少必填项"):
            register_demo_zone(
                service,
                baseline_metrics=[{"code": "MX", "name": "无口径指标", "baseline": 1}],
            )


class PublicationSealTest(unittest.TestCase):
    def test_publish_seals_calibre_preconditions_and_limits(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)
        event = publish_demo(service)
        sealed = service.sealed_version("ZT", 1)
        self.assertEqual(sealed["version"], 1)
        self.assertEqual(sealed["sealed_hash"], event.payload["sealed_hash"])
        self.assertIn("P1", [c["id"] for c in sealed["preconditions"]])
        self.assertIn("无跨界相邻行政区的县域不适用", sealed["non_applicability"])
        self.assertEqual(sealed["data_calibres"]["MT1"]["source"], "部门联审台账")

    def test_sealed_version_immune_to_later_supplement(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)
        publish_demo(service)
        before = canonical_hash(service.sealed_version("ZT", 1))
        # 发布后申报方继续补充材料，封存版本不受影响
        service.supplement_material(
            pilot_unit(), "ZT", "materials", {"note": "后续补充的执行案例"},
            at="2026-11-05T00:00:00+00:00",
        )
        after = canonical_hash(service.sealed_version("ZT", 1))
        self.assertEqual(before, after)

    def test_publish_requires_preconditions_limits_and_confirmed_evidence(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        with self.assertRaisesRegex(ValueError, "前置条件"):
            publish_demo(service, preconditions=[])
        with self.assertRaisesRegex(ValueError, "不适用范围"):
            publish_demo(service, non_applicability=[])
        with self.assertRaisesRegex(ValueError, "未经独立评估确认"):
            publish_demo(service, evidence_refs=["E1"])


class CommitmentRearrangementTest(unittest.TestCase):
    def _ready(self):
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)  # E1 锁定 KC1
        publish_demo(service)
        return service

    def test_delay_reschedules_only_unfulfilled(self) -> None:
        service = self._ready()
        service.rearrange_plan(
            province(),
            zone_code="ZT",
            reason="delay",
            milestone_changes=[{"id": "ST1", "new_due": "2026-12-01"}],
            at="2026-10-20T00:00:00+00:00",
        )
        self.assertEqual(service.zones["ZT"].milestones["ST1"]["due"], "2026-12-01")
        self.assertEqual(service.zones["ZT"].milestones["ST1"]["status"], "rescheduled")
        # 历史保留调整前快照，可追溯偏差
        self.assertTrue(service.zones["ZT"].milestones["ST1"]["history"])

    def test_partner_exit_requires_successor(self) -> None:
        service = self._ready()
        with self.assertRaisesRegex(ValueError, "承接单位"):
            service.rearrange_plan(
                province(), zone_code="ZT", reason="partner_exit",
                commitment_changes=[{"id": "KC2", "new_due": "2026-10-01"}],
            )
        service.rearrange_plan(
            province(), zone_code="ZT", reason="partner_exit",
            commitment_changes=[{"id": "KC2", "new_bearer": "新承接县部门"}],
            at="2026-10-21T00:00:00+00:00",
        )
        self.assertEqual(service.zones["ZT"].commitments["KC2"]["bearer"], "新承接县部门")

    def test_completed_evaluation_cannot_be_rewritten(self) -> None:
        service = self._ready()
        # KC1 已纳入已完成评估 E1，任何重排都被拒绝
        with self.assertRaises(ImmutableRecordError):
            service.rearrange_plan(
                province(), zone_code="ZT", reason="partner_exit",
                commitment_changes=[{"id": "KC1", "new_bearer": "其他单位"}],
            )
        # 已履行承诺同样锁定
        service.fulfill_commitment(
            pilot_unit(), "ZT", "KC2", at="2026-10-22T00:00:00+00:00"
        )
        with self.assertRaises(ImmutableRecordError):
            service.rearrange_plan(
                province(), zone_code="ZT", reason="delay",
                commitment_changes=[{"id": "KC2", "new_due": "2027-01-01"}],
            )
        # 重排风波后，评估证据原值原样保留
        self.assertEqual(service.zones["ZT"].evidence["E1"]["value"], 28)


class RoleSeparationTest(unittest.TestCase):
    def test_applicant_can_only_supplement(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        with self.assertRaises(AuthorizationError):
            service.confirm_evaluation(
                pilot_unit(),
                zone_code="ZT",
                evidence={"id": "EX", "metric_code": "MT1", "period": "2026Q3", "value": 30},
            )
        with self.assertRaises(AuthorizationError):
            service.supplement_material(pilot_unit(), "ZT", "evidence", {"value": 30})
        service.supplement_material(
            pilot_unit(), "ZT", "risks", {"id": "RT2", "text": "新增风险"},
            at="2026-07-01T00:00:00+00:00",
        )

    def test_evaluator_cannot_publish(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)
        with self.assertRaises(AuthorizationError):
            service.publish_experience(
                evaluator(),
                zone_code="ZT",
                preconditions=[{"id": "P1", "description": "x"}],
                non_applicability=["x"],
                result_summary="x",
                evidence_refs=["E1"],
            )

    def test_sensitive_data_shown_by_duty(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)
        evaluator_view = service.view_evidence(evaluator(), "ZT")[0]
        province_view = service.view_evidence(province(), "ZT")[0]
        self.assertIn("91330000", evaluator_view["sensitive_fields"]["enterprise_code"])
        self.assertIn("91330000", province_view["sensitive_fields"]["enterprise_code"])
        for viewer in (pilot_unit(), partner(), receiver()):
            view = service.view_evidence(viewer, "ZT")[0]
            self.assertEqual(view["sensitive_fields"]["enterprise_code"], MASKED)
            self.assertIn("脱敏", view["_notice"])


class SubmissionAndConflictTest(unittest.TestCase):
    def test_duplicate_submission_reuses_original_acceptance(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        first, first_new = service.receive_submission(
            province(), zone_code="ZT", submitter_id="s-01",
            title="季度进展", content={"value": 28},
            at="2026-10-01T00:00:00+00:00",
        )
        second, second_new = service.receive_submission(
            province(), zone_code="ZT", submitter_id="s-01",
            title="季度进展", content={"value": 28},
            at="2026-10-02T00:00:00+00:00",
        )
        self.assertTrue(first_new)
        self.assertFalse(second_new)
        self.assertEqual(second.payload["original_event_seq"], first.seq)
        # 再次提交登记为去重事件，而非新受理记录
        self.assertEqual(second.type, "submission.deduplicated")

    def test_conflicting_metric_enters_review_without_overwriting(self) -> None:
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)  # 2026Q3 = 28
        with self.assertRaisesRegex(ValueError, "CF-001"):
            confirm_demo_evidence(service, id="E2", value=99)
        conflict = service.conflicts["CF-001"]
        self.assertEqual(conflict["status"], "open")
        self.assertEqual({v["value"] for v in conflict["values"]}, {28, 99})
        # 原确认结果不变
        self.assertEqual(service.zones["ZT"].evidence["E1"]["value"], 28)
        self.assertNotIn("E2", service.zones["ZT"].evidence)
        service.resolve_conflict(
            evaluator(), "CF-001", "经核验99为录入笔误，维持28",
            at="2026-10-15T00:00:00+00:00",
        )
        self.assertEqual(service.conflicts["CF-001"]["status"], "resolved")
        self.assertEqual(service.zones["ZT"].evidence["E1"]["value"], 28)
        # 同一条证据不能重复提交
        with self.assertRaises(ImmutableRecordError):
            confirm_demo_evidence(service)


class RecoveryAndTamperTest(unittest.TestCase):
    def _journal_path(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / "journal.jsonl"

    def test_replay_restores_state_and_interrupted_write_is_truncated(self) -> None:
        path = self._journal_path()
        service = PilotTransferService(EventJournal.open(path))
        register_demo_zone(service)
        service.open_supervision(
            province(), "ZT", item="督办配套资金落实", due="2026-07-15",
            at="2026-06-10T00:00:00+00:00",
        )
        event_count = len(service.journal.events)

        # 模拟中断：台账末尾出现半行残写
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 99, "at": "2026-10-31T00:00:00+00:00", "type": "zon')

        reopened = PilotTransferService(EventJournal.open(path))
        self.assertEqual(len(reopened.journal.events), event_count)

        # 恢复督办与到期承诺
        result = reopened.restore_after_interruption(
            system(), "2026-10-31T00:00:00+00:00", at="2026-10-31T01:00:00+00:00"
        )
        resumed = result["resumed"]
        due_ids = {c["id"] for c in resumed["due_commitments"]}
        self.assertEqual(due_ids, {"KC1", "KC2"})
        self.assertEqual(resumed["open_supervision"][0]["item"], "督办配套资金落实")
        self.assertEqual(resumed["overdue_milestones"][0]["id"], "ST1")

        # 同状态重复恢复不重复登记
        again = reopened.restore_after_interruption(system(), "2026-10-31T00:00:00+00:00")
        self.assertTrue(again["deduplicated"])

    def test_replay_is_deterministic(self) -> None:
        path = self._journal_path()
        service = PilotTransferService(EventJournal.open(path))
        register_demo_zone(service)
        confirm_demo_evidence(service)
        publish_demo(service)
        replay = PilotTransferService(EventJournal.open(path))
        self.assertEqual(
            canonical_hash(service.zone_archive("ZT")),
            canonical_hash(replay.zone_archive("ZT")),
        )
        self.assertEqual(replay.sealed_version("ZT", 1)["sealed_hash"],
                         service.sealed_version("ZT", 1)["sealed_hash"])

    def test_modified_record_breaks_chain(self) -> None:
        path = self._journal_path()
        service = PilotTransferService(EventJournal.open(path))
        register_demo_zone(service)
        lines = path.read_text(encoding="utf-8").splitlines()
        tampered = lines[0].replace("测试引领区", "被篡改的名称")
        path.write_text("\n".join([tampered] + lines[1:]) + "\n", encoding="utf-8")
        with self.assertRaises(TamperDetected):
            EventJournal.open(path)


class AdoptionTest(unittest.TestCase):
    def _published(self):
        service = PilotTransferService(EventJournal())
        register_demo_zone(service)
        confirm_demo_evidence(service)
        publish_demo(service)
        return service

    def _apply(self, service):
        return service.apply_adoption(
            receiver(),
            zone_code="ZT",
            version=1,
            receiver_region="某山区县",
            local_differences=[
                {"id": "D1", "description": "无同级邻县，改为市域内联动"},
                {"id": "D2", "description": "联审台账改为双月更新"},
            ],
            compensation_measures=[
                {"difference_id": "D1", "measure": "由市级平台承担跨域核验职责"}
            ],
            at="2026-11-10T00:00:00+00:00",
        )

    def test_application_requires_differences(self) -> None:
        service = self._published()
        with self.assertRaisesRegex(ValueError, "本地差异"):
            service.apply_adoption(
                receiver(), zone_code="ZT", version=1, receiver_region="x",
                local_differences=[], compensation_measures=[],
            )

    def test_approval_card_shows_version_preconditions_and_endorsers(self) -> None:
        service = self._published()
        applied = self._apply(service)
        sealed_hash = service.sealed_version("ZT", 1)["sealed_hash"]
        service.decide_adoption(
            province(),
            application_id=applied.payload["application_id"],
            approved=True,
            precondition_results={"P1": True, "P2": True},
            deviation_approvals=[{"difference_id": "D2", "approved_by": "省级督导组张组（脱敏职务）"}],
            note="同意按补偿与偏差方案采用",
            at="2026-11-20T00:00:00+00:00",
        )
        card = service.adoption_card("ADP-001")
        self.assertEqual(card["status"], "approved")
        self.assertEqual(card["adopted_experience"]["version"], 1)
        self.assertEqual(card["adopted_experience"]["sealed_hash"], sealed_hash)
        self.assertTrue(all(p["satisfied"] for p in card["preconditions"]))
        by_diff = {d["id"]: d for d in card["differences"]}
        self.assertEqual(by_diff["D1"]["compensation"], "由市级平台承担跨域核验职责")
        self.assertIsNone(by_diff["D1"]["approved_by"])
        self.assertIn("省级督导组", by_diff["D2"]["approved_by"])
        self.assertIsNone(by_diff["D2"]["compensation"])
        # 不适用范围随卡片展示，防止照搬
        self.assertTrue(card["adopted_experience"]["non_applicability"])

    def test_unsatisfied_precondition_blocks_approval(self) -> None:
        service = self._published()
        applied = self._apply(service)
        with self.assertRaisesRegex(ValueError, "前置条件尚未满足"):
            service.decide_adoption(
                province(),
                application_id=applied.payload["application_id"],
                approved=True,
                precondition_results={"P1": False, "P2": True},
                deviation_approvals=[{"difference_id": "D2", "approved_by": "省级督导组"}],
            )

    def test_unresolved_difference_blocks_approval(self) -> None:
        service = self._published()
        applied = self._apply(service)
        with self.assertRaisesRegex(ValueError, "D2"):
            service.decide_adoption(
                province(),
                application_id=applied.payload["application_id"],
                approved=True,
                precondition_results={"P1": True, "P2": True},
            )

    def test_precondition_coverage_must_match_sealed_version(self) -> None:
        service = self._published()
        applied = self._apply(service)
        with self.assertRaisesRegex(ValueError, "全部前置条件"):
            service.decide_adoption(
                province(),
                application_id=applied.payload["application_id"],
                approved=False,
                precondition_results={"P1": True},
            )

    def test_decision_is_immutable(self) -> None:
        service = self._published()
        applied = self._apply(service)
        service.decide_adoption(
            province(),
            application_id=applied.payload["application_id"],
            approved=True,
            precondition_results={"P1": True, "P2": True},
            deviation_approvals=[{"difference_id": "D2", "approved_by": "省级督导组"}],
        )
        with self.assertRaises(ImmutableRecordError):
            service.decide_adoption(
                province(),
                application_id=applied.payload["application_id"],
                approved=False,
                precondition_results={"P1": True, "P2": True},
            )


if __name__ == "__main__":
    unittest.main()
