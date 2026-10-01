"""县域试点与制度转化服务的规则测试。"""

import dataclasses
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.pilot_transfer import (
    MASKED,
    AdoptionStatus,
    CommitmentStatus,
    EvaluationStatus,
    PermissionDenied,
    PilotTransferService,
    RescheduleReason,
    ReviewStatus,
    Role,
    RuleViolation,
    Store,
)

TODAY = date(2026, 10, 1)


def full_sections(**overrides):
    sections = {
        "goals": ["建成跨省通办示范窗口"],
        "baseline_indicators": {"跨省通办事项数": 128},
        "units": {"牵头": "县政务服务办", "协同": ["毗邻县区审批局"]},
        "clauses": ["跨省通办事项清单动态调整条款"],
        "funding_commitments": ["省级专项资金三百万元", "毗邻县区配套一百万元"],
        "milestones": [{"name": "事项清单发布", "due": "2026-06-30"}],
        "risks": ["毗邻地区系统接口不稳定"],
        "evidence_refs": [],
    }
    sections.update(overrides)
    return sections


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store_path = Path(self.temporary.name) / "store.json"
        self.service = PilotTransferService(Store(self.store_path), clock=lambda: TODAY)
        self.zone = self.service.register_zone("跨省通办引领区", "跨省通办")

    def reopen(self) -> PilotTransferService:
        """模拟进程中断后重启：从同一份落盘快照恢复。"""
        return PilotTransferService(Store(self.store_path), clock=lambda: TODAY)

    def confirmed_evaluation(self, subject: str = "跨省通办"):
        evaluation = self.service.open_evaluation(self.zone.zone_id, subject, actor_name="嘉善试点办")
        self.service.submit_evidence(
            evaluation.evaluation_id,
            "办件量月报",
            "月均办结一万两千件",
            sensitive=False,
            actor_name="嘉善试点办",
            actor_role=Role.DECLARANT,
        )
        return self.service.confirm_evaluation(
            evaluation.evaluation_id,
            "成效稳定，具备复制推广条件",
            actor_name="独立评估员甲",
            actor_role=Role.EVALUATOR,
        )

    def published_version(self):
        evaluation = self.confirmed_evaluation()
        return self.service.publish_experience(
            self.zone.zone_id,
            "跨省通办",
            evaluation.evaluation_id,
            data_caliber={"办件量": "按办结口径统计，含跨省联办件"},
            preconditions=["与毗邻地区签订通办协议", "完成事项清单对齐"],
            non_applicable=["涉密事项", "涉外审批事项"],
            actor_name="省级发展改革部门",
            actor_role=Role.SUPERVISOR,
        )


class ArchiveTest(ServiceTestCase):
    def test_archive_forms_continuous_chain(self) -> None:
        first = self.service.append_archive(self.zone.zone_id, full_sections(), author="嘉善试点办")
        second = self.service.append_archive(
            self.zone.zone_id,
            full_sections(goals=["更新后的年度目标"]),
            author="嘉善试点办",
            author_role=Role.DECLARANT,
        )
        self.assertEqual(first.sequence, 1)
        self.assertIsNone(first.previous_id)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.previous_id, first.entry_id)
        trail = self.service.archive_trail(self.zone.zone_id)
        self.assertEqual([entry.sequence for entry in trail], [1, 2])
        self.assertEqual(trail[0].sections["goals"], ["建成跨省通办示范窗口"])

    def test_archive_requires_all_eight_sections(self) -> None:
        broken = full_sections()
        del broken["risks"]
        with self.assertRaisesRegex(RuleViolation, "缺少部分"):
            self.service.append_archive(self.zone.zone_id, broken, author="嘉善试点办")
        with self.assertRaisesRegex(RuleViolation, "未知部分"):
            self.service.append_archive(self.zone.zone_id, full_sections(extra=["x"]), author="嘉善试点办")
        with self.assertRaisesRegex(RuleViolation, "不能留空"):
            self.service.append_archive(self.zone.zone_id, full_sections(goals="   "), author="嘉善试点办")

    def test_archive_rejects_unknown_zone_and_role(self) -> None:
        with self.assertRaisesRegex(RuleViolation, "引领区不存在"):
            self.service.append_archive("zone-404", full_sections(), author="嘉善试点办")
        with self.assertRaises(PermissionDenied):
            self.service.append_archive(
                self.zone.zone_id, full_sections(), author="某企业", author_role=Role.PARTNER
            )
        with self.assertRaises(ValueError):
            self.service.register_zone("  ", "跨省通办")


class CommitmentTest(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.commitment = self.service.add_commitment(
            self.zone.zone_id,
            partner="毗邻县交通局",
            title="共建前置货站",
            resource="承担干线运输补贴每年两百万元",
            due="2026-09-01",
        )

    def test_reschedule_only_pending_and_only_two_reasons(self) -> None:
        fulfilled = self.service.add_commitment(
            self.zone.zone_id, "省物流协会", "数据共享", "提供运价数据", "2026-08-01"
        )
        self.service.fulfill_commitment(fulfilled.commitment_id)
        with self.assertRaisesRegex(RuleViolation, "只有未履行承诺"):
            self.service.reschedule_commitment(
                fulfilled.commitment_id, "2026-11-01", RescheduleReason.PROJECT_DELAY, actor="省级发展改革部门"
            )
        with self.assertRaisesRegex(RuleViolation, "重排原因只能是"):
            self.service.reschedule_commitment(
                self.commitment.commitment_id, "2026-11-01", "口径调整", actor="省级发展改革部门"
            )

    def test_reschedule_keeps_original_and_chains_new_commitment(self) -> None:
        new = self.service.reschedule_commitment(
            self.commitment.commitment_id,
            "2026-12-15",
            RescheduleReason.PARTNER_EXIT,
            actor="省级发展改革部门",
            note="原伙伴退出，由物流协会承接",
            partner="省物流协会",
        )
        old = self.service.get_commitment(self.commitment.commitment_id)
        self.assertEqual(old.status, CommitmentStatus.RESCHEDULED)
        self.assertEqual(new.status, CommitmentStatus.PENDING)
        self.assertEqual(new.replaces, self.commitment.commitment_id)
        self.assertEqual(new.partner, "省物流协会")
        self.assertEqual(new.reschedule_reason, RescheduleReason.PARTNER_EXIT)
        self.assertEqual(new.reschedule_actor, "省级发展改革部门")
        with self.assertRaisesRegex(RuleViolation, "只有未履行承诺"):
            self.service.reschedule_commitment(
                self.commitment.commitment_id, "2027-01-01", RescheduleReason.PROJECT_DELAY, actor="省级发展改革部门"
            )
        again = self.service.reschedule_commitment(
            new.commitment_id, "2027-02-01", RescheduleReason.PROJECT_DELAY, actor="省级发展改革部门"
        )
        self.assertEqual(again.replaces, new.commitment_id)

    def test_fulfilled_commitment_cannot_change_again(self) -> None:
        self.service.fulfill_commitment(self.commitment.commitment_id)
        with self.assertRaisesRegex(RuleViolation, "只有未履行承诺"):
            self.service.fulfill_commitment(self.commitment.commitment_id)


class EvaluationTest(ServiceTestCase):
    def test_declarant_only_supplements_and_evaluator_confirms(self) -> None:
        evaluation = self.service.open_evaluation(self.zone.zone_id, "跨省通办", actor_name="嘉善试点办")
        with self.assertRaises(PermissionDenied):
            self.service.submit_evidence(
                evaluation.evaluation_id, "自评表", "内容", False, "省级发展改革部门", Role.SUPERVISOR
            )
        with self.assertRaises(PermissionDenied):
            self.service.confirm_evaluation(
                evaluation.evaluation_id, "成效显著", "嘉善试点办", Role.DECLARANT
            )
        with self.assertRaisesRegex(RuleViolation, "没有评估证据"):
            self.service.confirm_evaluation(
                evaluation.evaluation_id, "成效显著", "独立评估员甲", Role.EVALUATOR
            )
        self.service.submit_evidence(
            evaluation.evaluation_id, "办件量月报", "月均办结一万两千件", False, "嘉善试点办", Role.DECLARANT
        )
        confirmed = self.service.confirm_evaluation(
            evaluation.evaluation_id, "成效显著", "独立评估员甲", Role.EVALUATOR
        )
        self.assertEqual(confirmed.status, EvaluationStatus.CONFIRMED)
        self.assertEqual(confirmed.confirmed_by, "独立评估员甲")

    def test_confirmed_evaluation_cannot_be_rewritten(self) -> None:
        evaluation = self.confirmed_evaluation()
        with self.assertRaisesRegex(RuleViolation, "不得改写"):
            self.service.confirm_evaluation(
                evaluation.evaluation_id, "换个结论", "独立评估员乙", Role.EVALUATOR
            )
        with self.assertRaisesRegex(RuleViolation, "已确认封存"):
            self.service.submit_evidence(
                evaluation.evaluation_id, "补充材料", "新数据", False, "嘉善试点办", Role.DECLARANT
            )

    def test_sensitive_data_follows_viewer_duty(self) -> None:
        evaluation = self.service.open_evaluation(self.zone.zone_id, "前置货站", actor_name="嘉善试点办")
        self.service.submit_evidence(
            evaluation.evaluation_id, "企业运价明细", "某企业专线运价下浮百分之十五", True, "嘉善试点办", Role.DECLARANT
        )
        self.service.submit_evidence(
            evaluation.evaluation_id, "货站吞吐量", "日均吞吐三百标箱", False, "嘉善试点办", Role.DECLARANT
        )
        as_receiver = self.service.view_evaluation(evaluation.evaluation_id, Role.RECEIVER, "某接收县")
        self.assertEqual(as_receiver["evidence"][0]["content"], MASKED)
        self.assertEqual(as_receiver["evidence"][1]["content"], "日均吞吐三百标箱")
        as_partner = self.service.view_evaluation(evaluation.evaluation_id, Role.PARTNER, "某物流企业")
        self.assertEqual(as_partner["evidence"][0]["content"], MASKED)
        for role in (Role.EVALUATOR, Role.SUPERVISOR):
            view = self.service.view_evaluation(evaluation.evaluation_id, role, "无关人员")
            self.assertEqual(view["evidence"][0]["content"], "某企业专线运价下浮百分之十五")
        as_owner = self.service.view_evaluation(evaluation.evaluation_id, Role.DECLARANT, "嘉善试点办")
        self.assertEqual(as_owner["evidence"][0]["content"], "某企业专线运价下浮百分之十五")


class PublishTest(ServiceTestCase):
    def test_publish_requires_confirmed_evaluation_and_sealed_fields(self) -> None:
        evaluation = self.service.open_evaluation(self.zone.zone_id, "跨省通办", actor_name="嘉善试点办")
        with self.assertRaisesRegex(RuleViolation, "已确认评估"):
            self.service.publish_experience(
                self.zone.zone_id, "跨省通办", evaluation.evaluation_id,
                {"办件量": "办结口径"}, ["签订通办协议"], ["涉密事项"],
                "省级发展改革部门", Role.SUPERVISOR,
            )
        confirmed = self.confirmed_evaluation()
        for kwargs, message in (
            ({"data_caliber": {}}, "数据口径"),
            ({"preconditions": []}, "前置条件"),
            ({"non_applicable": ["  "]}, "不适用范围"),
        ):
            with self.assertRaisesRegex(RuleViolation, message):
                self.service.publish_experience(
                    self.zone.zone_id, "跨省通办", confirmed.evaluation_id,
                    kwargs.get("data_caliber", {"办件量": "办结口径"}),
                    kwargs.get("preconditions", ["签订通办协议"]),
                    kwargs.get("non_applicable", ["涉密事项"]),
                    "省级发展改革部门", Role.SUPERVISOR,
                )
        with self.assertRaises(PermissionDenied):
            self.service.publish_experience(
                self.zone.zone_id, "跨省通办", confirmed.evaluation_id,
                {"办件量": "办结口径"}, ["签订通办协议"], ["涉密事项"],
                "嘉善试点办", Role.DECLARANT,
            )

    def test_versions_increment_and_stay_sealed(self) -> None:
        first = self.published_version()
        second_evaluation = self.confirmed_evaluation()
        second = self.service.publish_experience(
            self.zone.zone_id, "跨省通办", second_evaluation.evaluation_id,
            {"办件量": "办结口径，剔除退件"}, ["签订通办协议"], ["涉密事项"],
            "省级发展改革部门", Role.SUPERVISOR,
        )
        self.assertEqual((first.version, second.version), (1, 2))
        versions = self.service.experience_versions(self.zone.zone_id, "跨省通办")
        self.assertEqual([item.version for item in versions], [1, 2])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.version = 99  # type: ignore[misc]
        reloaded = self.reopen().get_version(first.version_id)
        self.assertEqual(reloaded.version, 1)
        self.assertEqual(reloaded.data_caliber, {"办件量": "按办结口径统计，含跨省联办件"})
        self.assertEqual(reloaded.non_applicable, ("涉密事项", "涉外审批事项"))


class IntakeTest(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service.append_archive(self.zone.zone_id, full_sections(), author="嘉善试点办")

    def test_same_submission_reuses_original_acceptance(self) -> None:
        first = self.service.accept_submission(
            self.zone.zone_id, "2026-09", {"跨省通办事项数": 128}, "嘉善试点办"
        )
        again = self.service.accept_submission(
            self.zone.zone_id, "2026-09", {"跨省通办事项数": 128}, "嘉善试点办"
        )
        self.assertFalse(first.reused)
        self.assertTrue(again.reused)
        self.assertEqual(again.acceptance_id, first.acceptance_id)
        self.assertEqual(len(self.service.store.state["acceptances"]), 1)

    def test_contradictory_indicator_goes_to_review_only(self) -> None:
        record = self.service.accept_submission(
            self.zone.zone_id, "2026-09", {"跨省通办事项数": 130, "联办窗口数": 12}, "嘉善试点办"
        )
        self.assertFalse(record.reused)
        accepted = self.service.store.state["accepted_indicators"][self.zone.zone_id]["2026-09"]
        self.assertNotIn("跨省通办事项数", accepted)
        self.assertEqual(accepted["联办窗口数"], 12)
        report = self.service.recover()
        self.assertEqual(len(report.pending_reviews), 1)
        review = report.pending_reviews[0]
        self.assertEqual(review.indicator, "跨省通办事项数")
        self.assertEqual((review.declared_value, review.confirmed_value), (130.0, 128.0))
        self.assertEqual(review.source, "基线指标")

    def test_contradiction_with_earlier_acceptance_and_resolution(self) -> None:
        self.service.accept_submission(self.zone.zone_id, "2026-09", {"联办窗口数": 12}, "嘉善试点办")
        self.service.accept_submission(self.zone.zone_id, "2026-09", {"联办窗口数": 15}, "嘉善试点办")
        report = self.service.recover()
        self.assertEqual(len(report.pending_reviews), 1)
        review = report.pending_reviews[0]
        self.assertEqual(review.source, "已受理报送")
        with self.assertRaises(PermissionDenied):
            self.service.resolve_review(review.review_id, "以台账为准", "某接收县", Role.RECEIVER)
        resolved = self.service.resolve_review(
            review.review_id, "经核对为口径差异，以基线台账为准", "省级发展改革部门", Role.SUPERVISOR
        )
        self.assertEqual(resolved.status, ReviewStatus.RESOLVED)
        self.assertEqual(self.service.recover().pending_reviews, ())
        with self.assertRaisesRegex(RuleViolation, "已办结"):
            self.service.resolve_review(review.review_id, "重复办结", "省级发展改革部门", Role.SUPERVISOR)

    def test_submission_requires_numeric_indicators(self) -> None:
        with self.assertRaisesRegex(RuleViolation, "数值"):
            self.service.accept_submission(
                self.zone.zone_id, "2026-09", {"跨省通办事项数": "一百二十八"}, "嘉善试点办"
            )
        with self.assertRaises(PermissionDenied):
            self.service.accept_submission(
                self.zone.zone_id, "2026-09", {"联办窗口数": 12}, "某接收县", Role.RECEIVER
            )


class AdoptionTest(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.version = self.published_version()

    def apply(self):
        return self.service.apply_adoption(
            "某接收县",
            self.version.version_id,
            differences=["本地未与毗邻地区签订通办协议", "事项清单颗粒度更粗"],
            compensations=["由省级协调毗邻地区补签协议", "三个月内完成清单对齐"],
            actor_name="某接收县发改局",
            actor_role=Role.RECEIVER,
        )

    def test_application_requires_differences_and_compensations(self) -> None:
        with self.assertRaisesRegex(RuleViolation, "本地差异"):
            self.service.apply_adoption(
                "某接收县", self.version.version_id, [], ["补偿措施"], "某接收县发改局", Role.RECEIVER
            )
        with self.assertRaisesRegex(RuleViolation, "补偿措施"):
            self.service.apply_adoption(
                "某接收县", self.version.version_id, ["本地差异"], [], "某接收县发改局", Role.RECEIVER
            )
        with self.assertRaises(PermissionDenied):
            self.service.apply_adoption(
                "某接收县", self.version.version_id, ["本地差异"], ["补偿措施"], "嘉善试点办", Role.DECLARANT
            )
        with self.assertRaisesRegex(RuleViolation, "经验版本不存在"):
            self.service.apply_adoption(
                "某接收县", "xp-404", ["本地差异"], ["补偿措施"], "某接收县发改局", Role.RECEIVER
            )

    def test_approval_shows_version_met_preconditions_and_deviation_consents(self) -> None:
        application = self.apply()
        with self.assertRaises(PermissionDenied):
            self.service.decide_adoption(
                application.application_id, [], {}, "某接收县发改局", Role.RECEIVER
            )
        decided = self.service.decide_adoption(
            application.application_id,
            preconditions_met=["完成事项清单对齐"],
            deviation_approvals={"与毗邻地区签订通办协议": "省级发展改革部门负责人"},
            actor_name="省级发展改革部门",
            actor_role=Role.SUPERVISOR,
        )
        self.assertEqual(decided.status, AdoptionStatus.APPROVED)
        decision = decided.decision
        self.assertIsNotNone(decision)
        self.assertEqual(decision.adopted_version, 1)
        self.assertEqual(decision.adopted_version_id, self.version.version_id)
        self.assertEqual(decision.preconditions_met, ("完成事项清单对齐",))
        self.assertEqual(len(decision.deviations), 1)
        deviation = decision.deviations[0]
        self.assertEqual(deviation.precondition, "与毗邻地区签订通办协议")
        self.assertEqual(deviation.approved_by, "省级发展改革部门负责人")
        self.assertIn("三个月内完成清单对齐", decision.compensations)
        with self.assertRaisesRegex(RuleViolation, "已有审批结果"):
            self.service.decide_adoption(application.application_id, [], {}, "省级发展改革部门", Role.SUPERVISOR)

    def test_uncovered_preconditions_are_rejected(self) -> None:
        application = self.apply()
        decided = self.service.decide_adoption(
            application.application_id,
            preconditions_met=["完成事项清单对齐"],
            deviation_approvals={},
            actor_name="省级发展改革部门",
            actor_role=Role.SUPERVISOR,
        )
        self.assertEqual(decided.status, AdoptionStatus.REJECTED)
        self.assertIn("与毗邻地区签订通办协议", decided.rejection_reason)
        self.assertIsNone(decided.decision)

    def test_approval_rejects_unknown_or_overlapping_preconditions(self) -> None:
        application = self.apply()
        with self.assertRaisesRegex(RuleViolation, "不在封存范围"):
            self.service.decide_adoption(
                application.application_id, ["编造的前提"], {}, "省级发展改革部门", Role.SUPERVISOR
            )
        with self.assertRaisesRegex(RuleViolation, "既满足又记偏差"):
            self.service.decide_adoption(
                application.application_id,
                ["完成事项清单对齐"],
                {"完成事项清单对齐": "省级发展改革部门负责人"},
                "省级发展改革部门",
                Role.SUPERVISOR,
            )


class RecoveryTest(ServiceTestCase):
    def test_restart_recovers_supervision_and_due_commitments(self) -> None:
        due = self.service.add_commitment(
            self.zone.zone_id, "毗邻县交通局", "共建前置货站", "干线运输补贴", "2026-09-15"
        )
        self.service.add_commitment(
            self.zone.zone_id, "省物流协会", "数据共享", "提供运价数据", "2027-01-15"
        )
        done = self.service.add_commitment(
            self.zone.zone_id, "毗邻县财政局", "资金配套", "配套一百万元", "2026-09-15"
        )
        self.service.fulfill_commitment(done.commitment_id)
        moved = self.service.add_commitment(
            self.zone.zone_id, "毗邻县环保局", "生态共治监测", "共享监测设备", "2026-09-15"
        )
        self.service.reschedule_commitment(
            moved.commitment_id, "2026-12-31", RescheduleReason.PROJECT_DELAY, actor="省级发展改革部门"
        )
        self.service.append_archive(self.zone.zone_id, full_sections(), author="嘉善试点办")
        self.service.accept_submission(self.zone.zone_id, "2026-09", {"跨省通办事项数": 130}, "嘉善试点办")
        version = self.published_version()
        self.service.apply_adoption(
            "某接收县", version.version_id, ["本地差异"], ["补偿措施"], "某接收县发改局", Role.RECEIVER
        )

        restarted = self.reopen()
        report = restarted.recover()
        self.assertEqual([item.commitment_id for item in report.due_commitments], [due.commitment_id])
        self.assertEqual(len(report.pending_reviews), 1)
        self.assertEqual(len(report.pending_applications), 1)
        self.assertEqual(report.task_count, 3)

        restarted.resolve_review(
            report.pending_reviews[0].review_id, "以基线台账为准", "省级发展改革部门", Role.SUPERVISOR
        )
        restarted.fulfill_commitment(due.commitment_id)
        restarted.decide_adoption(
            report.pending_applications[0].application_id,
            ["与毗邻地区签订通办协议", "完成事项清单对齐"],
            {},
            "省级发展改革部门",
            Role.SUPERVISOR,
        )
        self.assertEqual(self.reopen().recover().task_count, 0)

    def test_recovery_report_excludes_future_and_closed_items(self) -> None:
        self.service.add_commitment(
            self.zone.zone_id, "省物流协会", "数据共享", "提供运价数据", "2026-10-01"
        )
        report = self.reopen().recover()
        self.assertEqual(len(report.due_commitments), 1)
        self.assertEqual(report.pending_reviews, ())
        self.assertEqual(report.pending_applications, ())


if __name__ == "__main__":
    unittest.main()
