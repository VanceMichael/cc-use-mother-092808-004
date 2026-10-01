"""县域试点与制度转化服务。

围绕引领区维护连续档案，并把经验发布、承诺重排、成效确认、报送受理、
矛盾复核和制度采用审批串成一条可追溯的流程：

- 档案只增不改：目标、基线指标、牵头与协同单位、制度条款、资金资源承诺、
  里程碑、风险、评估证据八个部分每次修订都形成新的链条节点；
- 经验发布时封存数据口径、前置条件和明确的不适用范围，版本只增不减；
- 伙伴退出或项目延期只能重排未履行承诺，已完成评估保持封存不得改写；
- 申报方只能补充材料，成效确认由独立评估人员完成；
- 企业敏感数据按接收者职责展示；
- 相同报送沿用原受理记录，矛盾指标单独进入复核；
- 全部记录落盘，中断后恢复督办和到期承诺；
- 接收地区申请采用时提交本地差异和补偿措施，批复写明采用版本、
  已满足前提和每一项偏差的同意人。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import date
from typing import Any, Callable, Iterable

from .errors import PermissionDenied, RuleViolation
from .models import (
    ARCHIVE_SECTIONS,
    AcceptanceRecord,
    AdoptionApplication,
    AdoptionDecision,
    AdoptionStatus,
    ArchiveEntry,
    Commitment,
    CommitmentStatus,
    DeviationConsent,
    Evaluation,
    EvaluationStatus,
    EvidenceItem,
    ExperienceVersion,
    RecoveryReport,
    RescheduleReason,
    ReviewItem,
    ReviewStatus,
    Role,
    Zone,
    require_text,
)
from .store import Store

MASKED = "〔已按接收者职责脱敏〕"

# 企业敏感数据只向履行评估、督导职责的角色和材料提交方本人完整展示。
_SENSITIVE_VISIBLE_ROLES = frozenset({Role.EVALUATOR, Role.SUPERVISOR})


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _check_date(value: Any, label: str) -> str:
    text = require_text(value, label)
    try:
        date.fromisoformat(text)
    except ValueError:
        raise RuleViolation(f"{label}必须是ISO格式日期") from None
    return text


def _require_text_list(values: Iterable[str], label: str) -> list[str]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise RuleViolation(f"{label}必须是文本列表")
    items = [require_text(item, label) for item in values]
    if not items:
        raise RuleViolation(f"{label}不能为空")
    return items


class PilotTransferService:
    """县域试点与制度转化服务的入口。"""

    def __init__(self, store: Store, clock: Callable[[], date] = date.today) -> None:
        self.store = store
        self.clock = clock

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _now(self) -> str:
        return self.clock().isoformat()

    def _save(self) -> None:
        self.store.save()

    @staticmethod
    def _require_role(role: Role | str, allowed: frozenset[Role], action: str) -> Role:
        try:
            resolved = Role(role)
        except ValueError:
            raise PermissionDenied(f"未知角色不能{action}") from None
        if resolved not in allowed:
            raise PermissionDenied(f"{resolved.value}不能{action}")
        return resolved

    def _zone(self, zone_id: str) -> Zone:
        raw = self.store.state["zones"].get(zone_id)
        if raw is None:
            raise RuleViolation("引领区不存在")
        return Zone.from_dict(raw)

    def _evaluation(self, evaluation_id: str) -> Evaluation:
        raw = self.store.state["evaluations"].get(evaluation_id)
        if raw is None:
            raise RuleViolation("评估不存在")
        return Evaluation.from_dict(raw)

    def _commitment(self, commitment_id: str) -> Commitment:
        raw = self.store.state["commitments"].get(commitment_id)
        if raw is None:
            raise RuleViolation("承诺不存在")
        return Commitment.from_dict(raw)

    def _version(self, version_id: str) -> ExperienceVersion:
        raw = self.store.state["versions"].get(version_id)
        if raw is None:
            raise RuleViolation("经验版本不存在")
        return ExperienceVersion.from_dict(raw)

    def _application(self, application_id: str) -> AdoptionApplication:
        raw = self.store.state["applications"].get(application_id)
        if raw is None:
            raise RuleViolation("采用申请不存在")
        return AdoptionApplication.from_dict(raw)

    # ------------------------------------------------------------------
    # 连续档案
    # ------------------------------------------------------------------

    def register_zone(self, name: str, category: str, actor_role: Role | str = Role.SUPERVISOR) -> Zone:
        """登记一个引领区，五类引领区各自建档。"""
        self._require_role(actor_role, frozenset({Role.SUPERVISOR, Role.DECLARANT}), "登记引领区")
        zone = Zone(
            zone_id=_new_id("zone"),
            name=require_text(name, "引领区名称"),
            category=require_text(category, "引领区类别"),
        )
        self.store.state["zones"][zone.zone_id] = zone.to_dict()
        self._save()
        return zone

    def append_archive(
        self,
        zone_id: str,
        sections: dict[str, Any],
        author: str,
        author_role: Role | str = Role.DECLARANT,
    ) -> ArchiveEntry:
        """追加一个档案节点。八个部分必须齐全，历史节点永不改写。"""
        self._require_role(author_role, frozenset({Role.DECLARANT, Role.SUPERVISOR}), "维护档案")
        self._zone(zone_id)
        if not isinstance(sections, dict):
            raise RuleViolation("档案内容必须是字典")
        missing = [key for key in ARCHIVE_SECTIONS if key not in sections]
        unknown = [key for key in sections if key not in ARCHIVE_SECTIONS]
        if missing:
            raise RuleViolation(f"连续档案缺少部分: {'、'.join(missing)}")
        if unknown:
            raise RuleViolation(f"连续档案包含未知部分: {'、'.join(unknown)}")
        for key, value in sections.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                raise RuleViolation(f"档案部分{key}不能留空")
        trail = [ArchiveEntry.from_dict(raw) for raw in self.store.state["archive"] if raw["zone_id"] == zone_id]
        previous = max(trail, key=lambda entry: entry.sequence, default=None)
        entry = ArchiveEntry(
            entry_id=_new_id("ar"),
            zone_id=zone_id,
            sequence=(previous.sequence + 1) if previous else 1,
            previous_id=previous.entry_id if previous else None,
            sections=dict(sections),
            author=require_text(author, "档案记录人"),
            created_at=self._now(),
        )
        self.store.state["archive"].append(entry.to_dict())
        self._save()
        return entry

    def archive_trail(self, zone_id: str) -> list[ArchiveEntry]:
        """按序号返回引领区的完整档案链。"""
        self._zone(zone_id)
        trail = [ArchiveEntry.from_dict(raw) for raw in self.store.state["archive"] if raw["zone_id"] == zone_id]
        return sorted(trail, key=lambda entry: entry.sequence)

    # ------------------------------------------------------------------
    # 跨域承诺
    # ------------------------------------------------------------------

    def add_commitment(self, zone_id: str, partner: str, title: str, resource: str, due: str) -> Commitment:
        """登记一条跨域承诺，含资金资源承诺内容和履行期限。"""
        self._zone(zone_id)
        commitment = Commitment(
            commitment_id=_new_id("cm"),
            zone_id=zone_id,
            partner=require_text(partner, "跨域伙伴"),
            title=require_text(title, "承诺事项"),
            resource=require_text(resource, "资金资源承诺"),
            due=_check_date(due, "履行期限"),
            status=CommitmentStatus.PENDING,
            created_at=self._now(),
        )
        self.store.state["commitments"][commitment.commitment_id] = commitment.to_dict()
        self._save()
        return commitment

    def get_commitment(self, commitment_id: str) -> Commitment:
        return self._commitment(commitment_id)

    def fulfill_commitment(self, commitment_id: str) -> Commitment:
        """确认承诺已履行，履行后不可再变动。"""
        commitment = self._commitment(commitment_id)
        if commitment.status != CommitmentStatus.PENDING:
            raise RuleViolation("只有未履行承诺可以确认履行")
        updated = replace(commitment, status=CommitmentStatus.FULFILLED, closed_at=self._now())
        self.store.state["commitments"][commitment_id] = updated.to_dict()
        self._save()
        return updated

    def reschedule_commitment(
        self,
        commitment_id: str,
        new_due: str,
        reason: RescheduleReason | str,
        actor: str,
        note: str = "",
        partner: str | None = None,
    ) -> Commitment:
        """因伙伴退出或项目延期重排未履行承诺。

        原承诺保留并标记为已重排，新承诺通过 replaces 指向原承诺，
        已履行或已重排的承诺不得再变动。
        """
        old = self._commitment(commitment_id)
        if old.status != CommitmentStatus.PENDING:
            raise RuleViolation("只有未履行承诺可以重排")
        try:
            resolved_reason = RescheduleReason(reason)
        except ValueError:
            allowed = "、".join(item.value for item in RescheduleReason)
            raise RuleViolation(f"承诺重排原因只能是: {allowed}") from None
        new = Commitment(
            commitment_id=_new_id("cm"),
            zone_id=old.zone_id,
            partner=require_text(partner, "跨域伙伴") if partner is not None else old.partner,
            title=old.title,
            resource=old.resource,
            due=_check_date(new_due, "新的履行期限"),
            status=CommitmentStatus.PENDING,
            created_at=self._now(),
            replaces=old.commitment_id,
            reschedule_reason=resolved_reason,
            reschedule_actor=require_text(actor, "重排经办人"),
            reschedule_note=note.strip() if isinstance(note, str) else "",
        )
        archived = replace(old, status=CommitmentStatus.RESCHEDULED, closed_at=self._now())
        self.store.state["commitments"][old.commitment_id] = archived.to_dict()
        self.store.state["commitments"][new.commitment_id] = new.to_dict()
        self._save()
        return new

    # ------------------------------------------------------------------
    # 评估证据与成效确认
    # ------------------------------------------------------------------

    def open_evaluation(self, zone_id: str, subject: str, actor_name: str) -> Evaluation:
        """申报方发起一次成效评估，之后只能继续补充材料。"""
        self._zone(zone_id)
        evaluation = Evaluation(
            evaluation_id=_new_id("ev"),
            zone_id=zone_id,
            subject=require_text(subject, "评估对象"),
            declarant=require_text(actor_name, "申报方"),
            status=EvaluationStatus.COLLECTING,
            created_at=self._now(),
        )
        self.store.state["evaluations"][evaluation.evaluation_id] = evaluation.to_dict()
        self._save()
        return evaluation

    def submit_evidence(
        self,
        evaluation_id: str,
        title: str,
        content: str,
        sensitive: bool,
        actor_name: str,
        actor_role: Role | str = Role.DECLARANT,
    ) -> EvidenceItem:
        """申报方补充评估材料。材料只追加不修改，评估确认后停止接收。"""
        self._require_role(actor_role, frozenset({Role.DECLARANT}), "补充评估材料")
        evaluation = self._evaluation(evaluation_id)
        if evaluation.status != EvaluationStatus.COLLECTING:
            raise RuleViolation("评估已确认封存，不能再补充材料")
        item = EvidenceItem(
            item_id=_new_id("ei"),
            title=require_text(title, "材料标题"),
            content=require_text(content, "材料内容"),
            sensitive=bool(sensitive),
            submitted_by=require_text(actor_name, "材料提交人"),
            submitted_at=self._now(),
        )
        evaluation.evidence.append(item)
        self.store.state["evaluations"][evaluation_id] = evaluation.to_dict()
        self._save()
        return item

    def confirm_evaluation(
        self,
        evaluation_id: str,
        conclusion: str,
        actor_name: str,
        actor_role: Role | str,
    ) -> Evaluation:
        """独立评估人员确认成效，确认后评估封存，不得改写。"""
        self._require_role(actor_role, frozenset({Role.EVALUATOR}), "确认评估成效")
        evaluation = self._evaluation(evaluation_id)
        if evaluation.status == EvaluationStatus.CONFIRMED:
            raise RuleViolation("已完成评估不得改写")
        if not evaluation.evidence:
            raise RuleViolation("没有评估证据，不能确认成效")
        confirmed = replace(
            evaluation,
            status=EvaluationStatus.CONFIRMED,
            conclusion=require_text(conclusion, "评估结论"),
            confirmed_by=require_text(actor_name, "确认人"),
            confirmed_at=self._now(),
        )
        self.store.state["evaluations"][evaluation_id] = confirmed.to_dict()
        self._save()
        return confirmed

    def view_evaluation(self, evaluation_id: str, viewer_role: Role | str, viewer_name: str = "") -> dict[str, Any]:
        """按接收者职责展示评估内容，企业敏感数据对无职责角色脱敏。"""
        evaluation = self._evaluation(evaluation_id)
        try:
            role = Role(viewer_role)
        except ValueError:
            role = None
        privileged = role in _SENSITIVE_VISIBLE_ROLES or (
            role == Role.DECLARANT and viewer_name == evaluation.declarant
        )
        evidence = []
        for item in evaluation.evidence:
            shown = item.to_dict()
            if item.sensitive and not privileged:
                shown["content"] = MASKED
            evidence.append(shown)
        return {
            "evaluation_id": evaluation.evaluation_id,
            "zone_id": evaluation.zone_id,
            "subject": evaluation.subject,
            "status": evaluation.status.value,
            "declarant": evaluation.declarant,
            "conclusion": evaluation.conclusion,
            "confirmed_by": evaluation.confirmed_by,
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # 经验版本发布与封存
    # ------------------------------------------------------------------

    def publish_experience(
        self,
        zone_id: str,
        experience: str,
        evaluation_id: str,
        data_caliber: dict[str, str],
        preconditions: Iterable[str],
        non_applicable: Iterable[str],
        actor_name: str,
        actor_role: Role | str = Role.SUPERVISOR,
    ) -> ExperienceVersion:
        """依据已确认评估发布经验版本，封存数据口径、前置条件和不适用范围。"""
        self._require_role(actor_role, frozenset({Role.SUPERVISOR}), "发布经验")
        self._zone(zone_id)
        evaluation = self._evaluation(evaluation_id)
        if evaluation.zone_id != zone_id:
            raise RuleViolation("评估与引领区不一致")
        if evaluation.status != EvaluationStatus.CONFIRMED:
            raise RuleViolation("只能依据已确认评估发布经验")
        if not isinstance(data_caliber, dict) or not data_caliber:
            raise RuleViolation("发布时必须封存数据口径")
        caliber = {require_text(key, "数据口径指标"): require_text(value, "数据口径说明") for key, value in data_caliber.items()}
        sealed_preconditions = tuple(_require_text_list(preconditions, "前置条件"))
        sealed_non_applicable = tuple(_require_text_list(non_applicable, "不适用范围"))
        name = require_text(experience, "经验名称")
        existing = [
            ExperienceVersion.from_dict(raw)
            for raw in self.store.state["versions"].values()
            if raw["zone_id"] == zone_id and raw["experience"] == name
        ]
        number = max((item.version for item in existing), default=0) + 1
        version = ExperienceVersion(
            version_id=_new_id("xp"),
            zone_id=zone_id,
            experience=name,
            version=number,
            evaluation_id=evaluation_id,
            data_caliber=caliber,
            preconditions=sealed_preconditions,
            non_applicable=sealed_non_applicable,
            published_by=require_text(actor_name, "发布人"),
            published_at=self._now(),
        )
        self.store.state["versions"][version.version_id] = version.to_dict()
        self._save()
        return version

    def get_version(self, version_id: str) -> ExperienceVersion:
        return self._version(version_id)

    def experience_versions(self, zone_id: str, experience: str) -> list[ExperienceVersion]:
        """按版本号升序返回某条经验的全部封存版本。"""
        versions = [
            ExperienceVersion.from_dict(raw)
            for raw in self.store.state["versions"].values()
            if raw["zone_id"] == zone_id and raw["experience"] == experience
        ]
        return sorted(versions, key=lambda item: item.version)

    # ------------------------------------------------------------------
    # 报送受理与矛盾复核
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _reference_indicators(self, zone_id: str) -> dict[str, float]:
        """最新档案节点中的数值型基线指标，作为矛盾检测的参照。"""
        trail = [raw for raw in self.store.state["archive"] if raw["zone_id"] == zone_id]
        if not trail:
            return {}
        latest = max(trail, key=lambda raw: raw["sequence"])
        section = latest["sections"].get("baseline_indicators") or {}
        if not isinstance(section, dict):
            return {}
        return {
            key: value
            for key, value in section.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }

    def accept_submission(
        self,
        zone_id: str,
        period: str,
        indicators: dict[str, float],
        submitted_by: str,
        actor_role: Role | str = Role.DECLARANT,
    ) -> AcceptanceRecord:
        """受理一份指标报送。

        相同报送再次出现时沿用原受理记录；与基线或已受理口径矛盾的
        指标不并入台账，单独生成复核事项。
        """
        self._require_role(actor_role, frozenset({Role.DECLARANT}), "报送指标")
        self._zone(zone_id)
        require_text(period, "报送期")
        require_text(submitted_by, "报送人")
        if not isinstance(indicators, dict) or not indicators:
            raise RuleViolation("报送指标不能为空")
        for name, value in indicators.items():
            require_text(name, "指标名称")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RuleViolation("指标值必须是数值")
        fingerprint = self._fingerprint(
            {
                "zone_id": zone_id,
                "period": period,
                "submitted_by": submitted_by,
                "indicators": indicators,
            }
        )
        existing = self.store.state["acceptances"].get(fingerprint)
        if existing is not None:
            return replace(AcceptanceRecord.from_dict(existing), reused=True)

        record = AcceptanceRecord(
            acceptance_id=_new_id("ac"),
            fingerprint=fingerprint,
            zone_id=zone_id,
            period=period,
            submitted_by=submitted_by,
            submitted_at=self._now(),
            indicators=dict(indicators),
        )
        references = self._reference_indicators(zone_id)
        accepted = self.store.state["accepted_indicators"].setdefault(zone_id, {}).setdefault(period, {})
        for name, value in indicators.items():
            if name in accepted:
                reference, source = accepted[name], "已受理报送"
            else:
                reference, source = references.get(name), "基线指标"
            if reference is not None and float(reference) != float(value):
                review = ReviewItem(
                    review_id=_new_id("rv"),
                    acceptance_id=record.acceptance_id,
                    zone_id=zone_id,
                    period=period,
                    indicator=name,
                    declared_value=float(value),
                    confirmed_value=float(reference),
                    source=source,
                    status=ReviewStatus.PENDING,
                    created_at=self._now(),
                )
                self.store.state["reviews"][review.review_id] = review.to_dict()
            else:
                accepted[name] = value
        self.store.state["acceptances"][fingerprint] = record.to_dict()
        self._save()
        return record

    def resolve_review(
        self,
        review_id: str,
        note: str,
        actor_name: str,
        actor_role: Role | str,
    ) -> ReviewItem:
        """复核矛盾指标，由省级督导或独立评估人员办结。"""
        self._require_role(actor_role, frozenset({Role.SUPERVISOR, Role.EVALUATOR}), "复核矛盾指标")
        raw = self.store.state["reviews"].get(review_id)
        if raw is None:
            raise RuleViolation("复核事项不存在")
        review = ReviewItem.from_dict(raw)
        if review.status != ReviewStatus.PENDING:
            raise RuleViolation("复核事项已办结")
        resolved = replace(
            review,
            status=ReviewStatus.RESOLVED,
            resolved_by=require_text(actor_name, "复核人"),
            resolved_note=require_text(note, "复核说明"),
        )
        self.store.state["reviews"][review_id] = resolved.to_dict()
        self._save()
        return resolved

    # ------------------------------------------------------------------
    # 制度采用申请与审批
    # ------------------------------------------------------------------

    def apply_adoption(
        self,
        region: str,
        version_id: str,
        differences: Iterable[str],
        compensations: Iterable[str],
        actor_name: str,
        actor_role: Role | str = Role.RECEIVER,
    ) -> AdoptionApplication:
        """接收地区申请采用某一版经验，必须提交本地差异和补偿措施。"""
        self._require_role(actor_role, frozenset({Role.RECEIVER}), "申请采用制度")
        self._version(version_id)
        application = AdoptionApplication(
            application_id=_new_id("ad"),
            region=require_text(region, "接收地区"),
            version_id=version_id,
            differences=_require_text_list(differences, "本地差异"),
            compensations=_require_text_list(compensations, "补偿措施"),
            status=AdoptionStatus.PENDING,
            submitted_by=require_text(actor_name, "申请人"),
            submitted_at=self._now(),
        )
        self.store.state["applications"][application.application_id] = application.to_dict()
        self._save()
        return application

    def decide_adoption(
        self,
        application_id: str,
        preconditions_met: Iterable[str],
        deviation_approvals: dict[str, str],
        actor_name: str,
        actor_role: Role | str = Role.SUPERVISOR,
    ) -> AdoptionApplication:
        """审批采用申请。

        封存版本中的每一条前置条件，要么列入已满足，要么列入偏差并
        写明同意人；覆盖不全的申请退回。批复直接写明采用版本、已满足
        前提和每一项偏差的同意人。
        """
        self._require_role(actor_role, frozenset({Role.SUPERVISOR}), "审批采用申请")
        application = self._application(application_id)
        if application.status != AdoptionStatus.PENDING:
            raise RuleViolation("申请已有审批结果")
        version = self._version(application.version_id)
        sealed = set(version.preconditions)
        if isinstance(preconditions_met, str) or not isinstance(preconditions_met, Iterable):
            raise RuleViolation("已满足前提必须是文本列表")
        met = [require_text(item, "已满足前提") for item in preconditions_met]
        approvals = dict(deviation_approvals or {})
        for precondition in met:
            if precondition not in sealed:
                raise RuleViolation(f"已满足前提不在封存范围内: {precondition}")
        for precondition, approver in approvals.items():
            if precondition not in sealed:
                raise RuleViolation(f"偏差前提不在封存范围内: {precondition}")
            require_text(approver, "偏差同意人")
        overlap = set(met) & set(approvals)
        if overlap:
            raise RuleViolation(f"同一前提不能既满足又记偏差: {'、'.join(sorted(overlap))}")
        uncovered = [item for item in version.preconditions if item not in set(met) | set(approvals)]
        if uncovered:
            application.status = AdoptionStatus.REJECTED
            application.rejection_reason = f"前置条件未全部覆盖: {'、'.join(uncovered)}"
            self.store.state["applications"][application_id] = application.to_dict()
            self._save()
            return application
        now = self._now()
        decision = AdoptionDecision(
            application_id=application_id,
            adopted_version_id=version.version_id,
            adopted_version=version.version,
            preconditions_met=tuple(met),
            deviations=tuple(
                DeviationConsent(precondition=precondition, approved_by=approver.strip(), approved_at=now)
                for precondition, approver in approvals.items()
            ),
            differences=tuple(application.differences),
            compensations=tuple(application.compensations),
            decided_by=require_text(actor_name, "审批人"),
            decided_at=now,
        )
        application.status = AdoptionStatus.APPROVED
        application.decision = decision
        self.store.state["applications"][application_id] = application.to_dict()
        self._save()
        return application

    # ------------------------------------------------------------------
    # 中断恢复
    # ------------------------------------------------------------------

    def recover(self) -> RecoveryReport:
        """恢复督办视图：到期未履行承诺、待复核指标、待审批采用申请。"""
        today = self.clock()
        due = []
        for raw in self.store.state["commitments"].values():
            commitment = Commitment.from_dict(raw)
            if commitment.status == CommitmentStatus.PENDING and date.fromisoformat(commitment.due) <= today:
                due.append(commitment)
        reviews = [
            ReviewItem.from_dict(raw)
            for raw in self.store.state["reviews"].values()
            if raw["status"] == ReviewStatus.PENDING
        ]
        applications = [
            AdoptionApplication.from_dict(raw)
            for raw in self.store.state["applications"].values()
            if raw["status"] == AdoptionStatus.PENDING
        ]
        return RecoveryReport(
            due_commitments=tuple(sorted(due, key=lambda item: (item.due, item.commitment_id))),
            pending_reviews=tuple(sorted(reviews, key=lambda item: item.review_id)),
            pending_applications=tuple(sorted(applications, key=lambda item: item.application_id)),
        )
