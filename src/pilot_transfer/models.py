"""县域试点与制度转化服务的数据模型。

模型只描述结构与序列化，业务规则集中在 service 层强制执行。
所有模型都可以无损地转成 JSON 字典并从字典还原，支撑中断后恢复。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .errors import RuleViolation


class Role(str, Enum):
    """参与方角色，是权限判断的唯一依据。"""

    DECLARANT = "县级试点单位"
    EVALUATOR = "独立评估人员"
    SUPERVISOR = "省级发展改革部门"
    PARTNER = "跨域合作伙伴"
    RECEIVER = "经验接收地区"


class CommitmentStatus(str, Enum):
    PENDING = "未履行"
    FULFILLED = "已履行"
    RESCHEDULED = "已重排"


class RescheduleReason(str, Enum):
    """只有这两类原因允许触发承诺重排。"""

    PARTNER_EXIT = "伙伴退出"
    PROJECT_DELAY = "项目延期"


class EvaluationStatus(str, Enum):
    COLLECTING = "材料补充中"
    CONFIRMED = "成效已确认"


class ReviewStatus(str, Enum):
    PENDING = "待复核"
    RESOLVED = "已复核"


class AdoptionStatus(str, Enum):
    PENDING = "待审批"
    APPROVED = "已批准"
    REJECTED = "已退回"


ARCHIVE_SECTIONS: tuple[str, ...] = (
    "goals",
    "baseline_indicators",
    "units",
    "clauses",
    "funding_commitments",
    "milestones",
    "risks",
    "evidence_refs",
)
"""连续档案必须完整携带的八个部分：目标、基线指标、牵头与协同单位、
制度条款、资金资源承诺、里程碑、风险、评估证据。"""


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleViolation(f"{label}不能为空")
    return value.strip()


@dataclass(frozen=True)
class Zone:
    """一个引领区。"""

    zone_id: str
    name: str
    category: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Zone":
        return cls(zone_id=raw["zone_id"], name=raw["name"], category=raw["category"])


@dataclass(frozen=True)
class ArchiveEntry:
    """连续档案中的一个节点，只增不改，通过 previous_id 串成链。"""

    entry_id: str
    zone_id: str
    sequence: int
    previous_id: str | None
    sections: dict[str, Any]
    author: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ArchiveEntry":
        return cls(
            entry_id=raw["entry_id"],
            zone_id=raw["zone_id"],
            sequence=int(raw["sequence"]),
            previous_id=raw["previous_id"],
            sections=dict(raw["sections"]),
            author=raw["author"],
            created_at=raw["created_at"],
        )


@dataclass(frozen=True)
class Commitment:
    """跨域承诺。重排不修改原记录，而是生成一条指向原记录的新承诺。"""

    commitment_id: str
    zone_id: str
    partner: str
    title: str
    resource: str
    due: str
    status: CommitmentStatus
    created_at: str
    replaces: str | None = None
    reschedule_reason: RescheduleReason | None = None
    reschedule_actor: str | None = None
    reschedule_note: str = ""
    closed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["reschedule_reason"] = self.reschedule_reason.value if self.reschedule_reason else None
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Commitment":
        return cls(
            commitment_id=raw["commitment_id"],
            zone_id=raw["zone_id"],
            partner=raw["partner"],
            title=raw["title"],
            resource=raw["resource"],
            due=raw["due"],
            status=CommitmentStatus(raw["status"]),
            created_at=raw["created_at"],
            replaces=raw.get("replaces"),
            reschedule_reason=RescheduleReason(raw["reschedule_reason"]) if raw.get("reschedule_reason") else None,
            reschedule_actor=raw.get("reschedule_actor"),
            reschedule_note=raw.get("reschedule_note", ""),
            closed_at=raw.get("closed_at"),
        )


@dataclass
class EvidenceItem:
    """申报方补充的一份评估材料，只允许追加，不允许修改。"""

    item_id: str
    title: str
    content: str
    sensitive: bool
    submitted_by: str
    submitted_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvidenceItem":
        return cls(
            item_id=raw["item_id"],
            title=raw["title"],
            content=raw["content"],
            sensitive=bool(raw["sensitive"]),
            submitted_by=raw["submitted_by"],
            submitted_at=raw["submitted_at"],
        )


@dataclass
class Evaluation:
    """一次成效评估。确认之后即封存，任何改写都会被拒绝。"""

    evaluation_id: str
    zone_id: str
    subject: str
    declarant: str
    status: EvaluationStatus
    created_at: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    conclusion: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "zone_id": self.zone_id,
            "subject": self.subject,
            "declarant": self.declarant,
            "status": self.status.value,
            "created_at": self.created_at,
            "evidence": [item.to_dict() for item in self.evidence],
            "conclusion": self.conclusion,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Evaluation":
        return cls(
            evaluation_id=raw["evaluation_id"],
            zone_id=raw["zone_id"],
            subject=raw["subject"],
            declarant=raw["declarant"],
            status=EvaluationStatus(raw["status"]),
            created_at=raw["created_at"],
            evidence=[EvidenceItem.from_dict(item) for item in raw.get("evidence", [])],
            conclusion=raw.get("conclusion"),
            confirmed_by=raw.get("confirmed_by"),
            confirmed_at=raw.get("confirmed_at"),
        )


@dataclass(frozen=True)
class ExperienceVersion:
    """一条已发布经验的封存版本，数据口径、前置条件和不适用范围不可再变。"""

    version_id: str
    zone_id: str
    experience: str
    version: int
    evaluation_id: str
    data_caliber: dict[str, str]
    preconditions: tuple[str, ...]
    non_applicable: tuple[str, ...]
    published_by: str
    published_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "zone_id": self.zone_id,
            "experience": self.experience,
            "version": self.version,
            "evaluation_id": self.evaluation_id,
            "data_caliber": dict(self.data_caliber),
            "preconditions": list(self.preconditions),
            "non_applicable": list(self.non_applicable),
            "published_by": self.published_by,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExperienceVersion":
        return cls(
            version_id=raw["version_id"],
            zone_id=raw["zone_id"],
            experience=raw["experience"],
            version=int(raw["version"]),
            evaluation_id=raw["evaluation_id"],
            data_caliber=dict(raw["data_caliber"]),
            preconditions=tuple(raw["preconditions"]),
            non_applicable=tuple(raw["non_applicable"]),
            published_by=raw["published_by"],
            published_at=raw["published_at"],
        )


@dataclass(frozen=True)
class AcceptanceRecord:
    """报送受理记录。相同报送再次出现时沿用同一条记录，reused 置真。"""

    acceptance_id: str
    fingerprint: str
    zone_id: str
    period: str
    submitted_by: str
    submitted_at: str
    indicators: dict[str, float]
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AcceptanceRecord":
        return cls(
            acceptance_id=raw["acceptance_id"],
            fingerprint=raw["fingerprint"],
            zone_id=raw["zone_id"],
            period=raw["period"],
            submitted_by=raw["submitted_by"],
            submitted_at=raw["submitted_at"],
            indicators=dict(raw["indicators"]),
            reused=bool(raw.get("reused", False)),
        )


@dataclass(frozen=True)
class ReviewItem:
    """矛盾指标复核事项，独立于受理记录单独跟踪。"""

    review_id: str
    acceptance_id: str
    zone_id: str
    period: str
    indicator: str
    declared_value: float
    confirmed_value: float
    source: str
    status: ReviewStatus
    created_at: str
    resolved_by: str | None = None
    resolved_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewItem":
        return cls(
            review_id=raw["review_id"],
            acceptance_id=raw["acceptance_id"],
            zone_id=raw["zone_id"],
            period=raw["period"],
            indicator=raw["indicator"],
            declared_value=float(raw["declared_value"]),
            confirmed_value=float(raw["confirmed_value"]),
            source=raw["source"],
            status=ReviewStatus(raw["status"]),
            created_at=raw["created_at"],
            resolved_by=raw.get("resolved_by"),
            resolved_note=raw.get("resolved_note"),
        )


@dataclass(frozen=True)
class DeviationConsent:
    """某一条前置条件上的偏差，以及同意该偏差的人。"""

    precondition: str
    approved_by: str
    approved_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeviationConsent":
        return cls(
            precondition=raw["precondition"],
            approved_by=raw["approved_by"],
            approved_at=raw["approved_at"],
        )


@dataclass(frozen=True)
class AdoptionDecision:
    """采用批复：直接写明采用哪一版经验、哪些前提已满足、哪些偏差获得了谁的同意。"""

    application_id: str
    adopted_version_id: str
    adopted_version: int
    preconditions_met: tuple[str, ...]
    deviations: tuple[DeviationConsent, ...]
    differences: tuple[str, ...]
    compensations: tuple[str, ...]
    decided_by: str
    decided_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "adopted_version_id": self.adopted_version_id,
            "adopted_version": self.adopted_version,
            "preconditions_met": list(self.preconditions_met),
            "deviations": [item.to_dict() for item in self.deviations],
            "differences": list(self.differences),
            "compensations": list(self.compensations),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AdoptionDecision":
        return cls(
            application_id=raw["application_id"],
            adopted_version_id=raw["adopted_version_id"],
            adopted_version=int(raw["adopted_version"]),
            preconditions_met=tuple(raw["preconditions_met"]),
            deviations=tuple(DeviationConsent.from_dict(item) for item in raw["deviations"]),
            differences=tuple(raw["differences"]),
            compensations=tuple(raw["compensations"]),
            decided_by=raw["decided_by"],
            decided_at=raw["decided_at"],
        )


@dataclass
class AdoptionApplication:
    """接收地区提出的制度采用申请，必须附本地差异和补偿措施。"""

    application_id: str
    region: str
    version_id: str
    differences: list[str]
    compensations: list[str]
    status: AdoptionStatus
    submitted_by: str
    submitted_at: str
    decision: AdoptionDecision | None = None
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "region": self.region,
            "version_id": self.version_id,
            "differences": list(self.differences),
            "compensations": list(self.compensations),
            "status": self.status.value,
            "submitted_by": self.submitted_by,
            "submitted_at": self.submitted_at,
            "decision": self.decision.to_dict() if self.decision else None,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AdoptionApplication":
        return cls(
            application_id=raw["application_id"],
            region=raw["region"],
            version_id=raw["version_id"],
            differences=list(raw["differences"]),
            compensations=list(raw["compensations"]),
            status=AdoptionStatus(raw["status"]),
            submitted_by=raw["submitted_by"],
            submitted_at=raw["submitted_at"],
            decision=AdoptionDecision.from_dict(raw["decision"]) if raw.get("decision") else None,
            rejection_reason=raw.get("rejection_reason"),
        )


@dataclass(frozen=True)
class RecoveryReport:
    """中断恢复后的督办视图：到期未履行承诺、待复核指标、待审批申请。"""

    due_commitments: tuple[Commitment, ...]
    pending_reviews: tuple[ReviewItem, ...]
    pending_applications: tuple[AdoptionApplication, ...]

    @property
    def task_count(self) -> int:
        return len(self.due_commitments) + len(self.pending_reviews) + len(self.pending_applications)
