"""县域试点与制度转化服务。

围绕五类引领区建立从目标、基线、承诺、里程碑、风险到评估证据的连续档案，
并以只增台账（append-only journal）落实以下规则：

- 经验发布即封存：数据口径、前置条件、不适用范围随版本冻结；
- 伙伴退出或项目延期只能重排未履行承诺，已完成评估不得改写；
- 申报方只能补充材料，成效确认由独立评估角色完成；
- 企业敏感数据按接收者职责展示；
- 相同报送沿用原受理记录，矛盾指标单独进入复核；
- 服务中断后可重放台账，恢复督办与到期承诺；
- 接收地区采用制度须申报本地差异与补偿措施，审批结果可直接看出
  采用版本、已满足前提与偏差同意人。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DOMAIN = "county-pilot-transfer"
GENESIS = "GENESIS"

# 角色
PROVINCE = "province"        # 省级发展改革部门（督导、发布、审批）
PILOT = "pilot"              # 县级试点单位（申报方，只能补充材料）
PARTNER = "partner"          # 跨域合作伙伴
EVALUATOR = "evaluator"      # 独立评估人员（成效确认）
RECEIVER = "receiver"        # 经验接收地区
SYSTEM = "system"            # 系统（恢复、督办）

ROLE_NAMES = {
    PROVINCE: "省级发展改革部门",
    PILOT: "县级试点单位",
    PARTNER: "跨域合作伙伴",
    EVALUATOR: "独立评估人员",
    RECEIVER: "经验接收地区",
    SYSTEM: "系统",
}

# 可查阅企业敏感数据的职责
ENTERPRISE_DATA_DUTY = frozenset({PROVINCE, EVALUATOR})
MASKED = "***按接收者职责脱敏***"

# 事件类型 -> 允许提交的角色
EVENT_AUTHORITY: dict[str, frozenset[str]] = {
    "zone.registered": frozenset({PROVINCE}),
    "profile.supplemented": frozenset({PILOT}),
    "submission.received": frozenset({PROVINCE}),
    "submission.deduplicated": frozenset({PROVINCE}),
    "experience.published": frozenset({PROVINCE}),
    "plan.rearranged": frozenset({PROVINCE}),
    "evaluation.confirmed": frozenset({EVALUATOR}),
    "metric.conflict.flagged": frozenset({EVALUATOR, PROVINCE, SYSTEM}),
    "metric.review.recorded": frozenset({EVALUATOR}),
    "supervision.opened": frozenset({PROVINCE, SYSTEM}),
    "supervision.closed": frozenset({PROVINCE, EVALUATOR}),
    "supervision.restored": frozenset({SYSTEM}),
    "commitment.fulfilled": frozenset({PROVINCE, PILOT, PARTNER}),
    "adoption.applied": frozenset({RECEIVER}),
    "adoption.decided": frozenset({PROVINCE}),
}


class ServiceError(ValueError):
    """业务规则冲突。"""


class AuthorizationError(ServiceError):
    """角色无权执行该操作。"""


class ImmutableRecordError(ServiceError):
    """试图改写已封存或已确认的记录。"""


class TamperDetected(ServiceError):
    """台账链摘要校验失败，存在被改写或损坏的记录。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str
    unit: str = ""

    @property
    def name(self) -> str:
        return ROLE_NAMES.get(self.role, self.role)


@dataclass
class Event:
    seq: int
    at: str
    actor_id: str
    role: str
    type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def to_line(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "at": self.at,
                "actor_id": self.actor_id,
                "role": self.role,
                "type": self.type,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "hash": self.hash,
            },
            ensure_ascii=False,
        )


class EventJournal:
    """只增事件台账。

    每条事件携带前条摘要形成哈希链；事件即时追加落盘，
    打开时重放校验，末尾写入中断产生的残行被截除，其余记录完整恢复。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.events: list[Event] = []

    @classmethod
    def open(cls, path: str | Path | None) -> "EventJournal":
        journal = cls(Path(path) if path else None)
        if journal.path is None or not journal.path.exists():
            if journal.path is not None:
                journal.path.parent.mkdir(parents=True, exist_ok=True)
                journal.path.touch()
            return journal

        raw = journal.path.read_text(encoding="utf-8")
        lines = [line for line in raw.splitlines() if line.strip()]
        kept_lines: list[str] = []
        recovered = 0
        for index, line in enumerate(lines):
            try:
                data = json.loads(line)
                event = Event(
                    seq=int(data["seq"]),
                    at=data["at"],
                    actor_id=data["actor_id"],
                    role=data["role"],
                    type=data["type"],
                    payload=data["payload"],
                    prev_hash=data["prev_hash"],
                    hash=data["hash"],
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                # 中断时只写了一半的残行：仅允许出现在末尾并截除；
                # 出现在中间说明记录被破坏，不能静默吞掉。
                if index != len(lines) - 1:
                    raise TamperDetected(f"第{index + 1}条记录损坏且不在台账末尾")
                recovered += 1
                continue
            if event.hash != journal._digest(event):
                raise TamperDetected(f"第{event.seq}条记录与封存摘要不一致")
            journal.events.append(event)
            kept_lines.append(line)

        if recovered:
            journal.path.write_text(
                "".join(line + "\n" for line in kept_lines), encoding="utf-8"
            )
        journal._verify_chain()
        return journal

    @property
    def head(self) -> str:
        return self.events[-1].hash if self.events else GENESIS

    def _digest(self, event: Event) -> str:
        return canonical_hash(
            [
                event.seq,
                event.at,
                event.actor_id,
                event.role,
                event.type,
                event.payload,
                event.prev_hash,
            ]
        )

    def _verify_chain(self) -> None:
        prev = GENESIS
        for expected_seq, event in enumerate(self.events, start=1):
            if event.seq != expected_seq:
                raise TamperDetected(f"第{event.seq}条记录序号不连续")
            if event.prev_hash != prev:
                raise TamperDetected(f"第{event.seq}条记录断链")
            prev = event.hash

    def append(
        self,
        actor: Actor,
        event_type: str,
        payload: dict[str, Any],
        at: str | None = None,
    ) -> Event:
        if actor.role not in EVENT_AUTHORITY.get(event_type, frozenset()):
            raise AuthorizationError(
                f"{actor.name}无权提交{event_type}，应由"
                + "/".join(ROLE_NAMES[r] for r in EVENT_AUTHORITY[event_type])
                + "办理"
            )
        event = Event(
            seq=len(self.events) + 1,
            at=at or _utcnow(),
            actor_id=actor.actor_id,
            role=actor.role,
            type=event_type,
            payload=payload,
            prev_hash=self.head,
            hash="",
        )
        event.hash = self._digest(event)
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.to_line() + "\n")
                handle.flush()
                import os

                os.fsync(handle.fileno())
        return event


@dataclass
class Zone:
    code: str
    name: str
    objective: str
    lead_unit: str
    cooperating_units: list[str]
    baseline_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    clauses: list[dict[str, str]] = field(default_factory=list)
    commitments: dict[str, dict[str, Any]] = field(default_factory=dict)
    milestones: dict[str, dict[str, Any]] = field(default_factory=dict)
    risks: list[dict[str, str]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[int] = field(default_factory=list)


class PilotTransferService:
    """从台账重放出的县域试点与制度转化服务。"""

    def __init__(self, journal: EventJournal) -> None:
        self.journal = journal
        self.zones: dict[str, Zone] = {}
        self.submissions: dict[str, dict[str, Any]] = {}
        self.conflicts: dict[str, dict[str, Any]] = {}
        self.supervision: list[dict[str, Any]] = []
        self.adoptions: dict[str, dict[str, Any]] = {}
        self._adoption_seq = 0
        for event in journal.events:
            self._apply(event)
    # ------------------------------------------------------------------ 工具

    def _zone(self, code: str) -> Zone:
        if code not in self.zones:
            raise ServiceError(f"引领区{code}尚未建档")
        return self.zones[code]

    def _record(self, actor: Actor, event_type: str, payload: dict[str, Any],
                at: str | None = None) -> Event:
        event = self.journal.append(actor, event_type, payload, at=at)
        self._apply(event)
        return event

    def _apply(self, event: Event) -> None:
        head, _, tail = event.type.partition(".")
        handler = getattr(self, f"_on_{head}_{tail.replace('.', '_')}", None)
        if handler is not None:
            handler(event)

    def zone_archive(self, code: str) -> dict[str, Any]:
        """返回引领区连续档案（目标至评估证据的全要素与事件脉络）。"""
        zone = self._zone(code)
        return {
            "code": zone.code,
            "name": zone.name,
            "objective": zone.objective,
            "lead_unit": zone.lead_unit,
            "cooperating_units": list(zone.cooperating_units),
            "baseline_metrics": json.loads(json.dumps(zone.baseline_metrics, ensure_ascii=False)),
            "clauses": list(zone.clauses),
            "commitments": json.loads(json.dumps(list(zone.commitments.values()), ensure_ascii=False)),
            "milestones": json.loads(json.dumps(list(zone.milestones.values()), ensure_ascii=False)),
            "risks": list(zone.risks),
            "materials": list(zone.materials),
            "evidence": json.loads(json.dumps(list(zone.evidence.values()), ensure_ascii=False)),
            "versions": list(zone.versions),
            "timeline": list(zone.timeline),
        }

    # ------------------------------------------------------------ 建档与补充

    def register_zone(
        self,
        actor: Actor,
        *,
        code: str,
        name: str,
        objective: str,
        baseline_metrics: list[dict[str, Any]],
        lead_unit: str,
        cooperating_units: list[str],
        clauses: list[dict[str, str]],
        commitments: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        risks: list[dict[str, str]],
        at: str | None = None,
    ) -> Event:
        """建立引领区连续档案：目标、基线指标、牵头协同、条款、承诺、里程碑、风险。"""
        if code in self.zones:
            raise ServiceError(f"引领区{code}已建档，禁止覆盖，只能补充材料或发布新版本")
        if not baseline_metrics:
            raise ServiceError("基线指标不能为空")
        for metric in baseline_metrics:
            self._validate_metric(metric)
        for item in commitments:
            self._require_keys(item, ("id", "bearer", "content", "due"))
        for item in milestones:
            self._require_keys(item, ("id", "name", "due"))

        payload = {
            "code": code,
            "name": name,
            "objective": objective,
            "lead_unit": lead_unit,
            "cooperating_units": list(cooperating_units),
            "baseline_metrics": baseline_metrics,
            "clauses": list(clauses),
            "commitments": commitments,
            "milestones": milestones,
            "risks": list(risks),
        }
        return self._record(actor, "zone.registered", payload, at=at)

    @staticmethod
    def _validate_metric(metric: dict[str, Any]) -> None:
        PilotTransferService._require_keys(metric, ("code", "name", "calibre", "baseline"))
        calibre = metric["calibre"]
        PilotTransferService._require_keys(calibre, ("scope", "source", "frequency"))

    @staticmethod
    def _require_keys(value: dict[str, Any], keys: tuple[str, ...]) -> None:
        missing = [key for key in keys if key not in value or value[key] in (None, "", [])]
        if missing:
            raise ServiceError(f"材料缺少必填项：{','.join(missing)}")

    def supplement_material(
        self,
        actor: Actor,
        zone_code: str,
        section: str,
        content: Any,
        at: str | None = None,
    ) -> Event:
        """申报方补充材料（成效确认不在此列）。"""
        if section == "evidence":
            raise AuthorizationError("申报方只能补充材料，评估证据须由独立评估人员确认")
        if section not in {"clauses", "risks", "materials"}:
            raise ServiceError("补充材料仅可追加到制度条款、风险或佐证材料")
        return self._record(
            actor,
            "profile.supplemented",
            {"zone_code": zone_code, "section": section, "content": content},
            at=at,
        )

    def _on_zone_registered(self, event: Event) -> None:
        p = event.payload
        zone = Zone(
            code=p["code"],
            name=p["name"],
            objective=p["objective"],
            lead_unit=p["lead_unit"],
            cooperating_units=list(p["cooperating_units"]),
            baseline_metrics={m["code"]: dict(m) for m in p["baseline_metrics"]},
            clauses=list(p["clauses"]),
            commitments={
                c["id"]: {**c, "status": "pending", "history": []} for c in p["commitments"]
            },
            milestones={
                m["id"]: {**m, "status": "open", "history": []} for m in p["milestones"]
            },
            risks=list(p["risks"]),
        )
        zone.timeline.append(event.seq)
        self.zones[p["code"]] = zone

    def _on_profile_supplemented(self, event: Event) -> None:
        zone = self._zone(event.payload["zone_code"])
        section = event.payload["section"]
        content = event.payload["content"]
        if section == "clauses":
            zone.clauses.append(content)
        elif section == "risks":
            zone.risks.append(content)
        else:
            zone.materials.append({"event_seq": event.seq, "content": content})
        zone.timeline.append(event.seq)

    # ------------------------------------------------------------ 报送受理

    def receive_submission(
        self,
        actor: Actor,
        *,
        zone_code: str,
        submitter_id: str,
        title: str,
        content: Any,
        at: str | None = None,
    ) -> tuple[Event, bool]:
        """受理报送；相同报送再次出现时沿用原受理记录，返回(事件, 是否首次受理)。"""
        self._zone(zone_code)
        signature = canonical_hash([zone_code, submitter_id, title, content])
        if signature in self.submissions:
            original = self.submissions[signature]
            event = self._record(
                actor,
                "submission.deduplicated",
                {
                    "zone_code": zone_code,
                    "submitter_id": submitter_id,
                    "title": title,
                    "signature": signature,
                    "original_event_seq": original["event_seq"],
                    "original_accepted_at": original["accepted_at"],
                },
                at=at,
            )
            return event, False

        event = self._record(
            actor,
            "submission.received",
            {
                "zone_code": zone_code,
                "submitter_id": submitter_id,
                "title": title,
                "content": content,
                "signature": signature,
            },
            at=at,
        )
        return event, True

    def _on_submission_received(self, event: Event) -> None:
        p = event.payload
        self.submissions[p["signature"]] = {
            "event_seq": event.seq,
            "zone_code": p["zone_code"],
            "accepted_at": event.at,
            "title": p["title"],
        }
        self._zone(p["zone_code"]).timeline.append(event.seq)

    def _on_submission_deduplicated(self, event: Event) -> None:
        self._zone(event.payload["zone_code"]).timeline.append(event.seq)

    # ------------------------------------------------------------ 经验发布封存

    def publish_experience(
        self,
        actor: Actor,
        *,
        zone_code: str,
        preconditions: list[dict[str, str]],
        non_applicability: list[str],
        result_summary: str,
        evidence_refs: list[str],
        at: str | None = None,
    ) -> Event:
        """发布经验版本：封存数据口径、前置条件与明确的不适用范围。"""
        zone = self._zone(zone_code)
        if not preconditions:
            raise ServiceError("发布经验必须列明前置条件")
        if not non_applicability:
            raise ServiceError("发布经验必须明确不适用范围")
        for ref in evidence_refs:
            if ref not in zone.evidence or not zone.evidence[ref].get("confirmed"):
                raise ServiceError(f"证据{ref}未经独立评估确认，不能作为发布依据")
        for condition in preconditions:
            self._require_keys(condition, ("id", "description"))

        snapshot = {
            "zone_code": zone_code,
            "zone_name": zone.name,
            "version": len(zone.versions) + 1,
            "data_calibres": {
                code: metric["calibre"] for code, metric in zone.baseline_metrics.items()
            },
            "preconditions": list(preconditions),
            "non_applicability": list(non_applicability),
            "result_summary": result_summary,
            "evidence_refs": list(evidence_refs),
            "lead_unit": zone.lead_unit,
            "cooperating_units": list(zone.cooperating_units),
            "clauses": list(zone.clauses),
        }
        sealed_hash = canonical_hash(snapshot)
        payload = {**snapshot, "sealed_hash": sealed_hash}
        return self._record(actor, "experience.published", payload, at=at)

    def _on_experience_published(self, event: Event) -> None:
        p = event.payload
        sealed = {
            key: p[key]
            for key in p
        }
        sealed["sealed_at"] = event.at
        sealed["sealed_by"] = event.actor_id
        self._zone(p["zone_code"]).versions.append(sealed)
        self._zone(p["zone_code"]).timeline.append(event.seq)

    def sealed_version(self, zone_code: str, version: int) -> dict[str, Any]:
        zone = self._zone(zone_code)
        for item in zone.versions:
            if item["version"] == version:
                return item
        raise ServiceError(f"{zone_code}第{version}版经验不存在")

    # ------------------------------------------------------------ 承诺重排

    def rearrange_plan(
        self,
        actor: Actor,
        *,
        zone_code: str,
        reason: str,
        commitment_changes: list[dict[str, Any]] | None = None,
        milestone_changes: list[dict[str, Any]] | None = None,
        at: str | None = None,
    ) -> Event:
        """伙伴退出或项目延期时重排未履行承诺；已完成评估不受影响。"""
        if reason not in {"partner_exit", "delay"}:
            raise ServiceError("重排原因须为partner_exit（伙伴退出）或delay（项目延期）")
        zone = self._zone(zone_code)
        commitment_changes = commitment_changes or []
        milestone_changes = milestone_changes or []
        if not commitment_changes and not milestone_changes:
            raise ServiceError("重排须至少包含一项承诺或里程碑调整")

        locked_refs = {
            ref
            for evidence in zone.evidence.values()
            if evidence.get("confirmed")
            for ref in evidence.get("commitment_refs", [])
        }
        for change in commitment_changes:
            item = zone.commitments.get(change["id"])
            if item is None:
                raise ServiceError(f"承诺{change['id']}不存在")
            if item["status"] == "fulfilled":
                raise ImmutableRecordError(
                    f"承诺{change['id']}已履行，伙伴退出或延期只能重排未履行承诺"
                )
            if change["id"] in locked_refs:
                raise ImmutableRecordError(
                    f"承诺{change['id']}已纳入完成评估，不得重排或改写"
                )
            if reason == "partner_exit" and not change.get("new_bearer"):
                raise ServiceError("伙伴退出须指定承接单位new_bearer")
        for change in milestone_changes:
            item = zone.milestones.get(change["id"])
            if item is None:
                raise ServiceError(f"里程碑{change['id']}不存在")
            if item["status"] == "reached":
                raise ImmutableRecordError(f"里程碑{change['id']}已达成，不得改期")
            if change["id"] in locked_refs:
                raise ImmutableRecordError(f"里程碑{change['id']}已纳入完成评估，不得改期")
            if not change.get("new_due"):
                raise ServiceError("里程碑改期须提供new_due")

        return self._record(
            actor,
            "plan.rearranged",
            {
                "zone_code": zone_code,
                "reason": reason,
                "commitment_changes": commitment_changes,
                "milestone_changes": milestone_changes,
            },
            at=at,
        )

    def _on_plan_rearranged(self, event: Event) -> None:
        p = event.payload
        zone = self._zone(p["zone_code"])
        for change in p["commitment_changes"]:
            item = zone.commitments[change["id"]]
            item["history"].append(
                {"event_seq": event.seq, "reason": p["reason"], "snapshot": deepcopy(item)}
            )
            if "new_bearer" in change:
                item["bearer"] = change["new_bearer"]
            if "new_due" in change:
                item["due"] = change["new_due"]
            item["status"] = "rearranged"
        for change in p["milestone_changes"]:
            item = zone.milestones[change["id"]]
            item["history"].append(
                {"event_seq": event.seq, "reason": p["reason"], "snapshot": deepcopy(item)}
            )
            item["due"] = change["new_due"]
            item["status"] = "rescheduled"
        zone.timeline.append(event.seq)

    def fulfill_commitment(self, actor: Actor, zone_code: str, commitment_id: str,
                           at: str | None = None) -> Event:
        zone = self._zone(zone_code)
        if commitment_id not in zone.commitments:
            raise ServiceError(f"承诺{commitment_id}不存在")
        if zone.commitments[commitment_id]["status"] == "fulfilled":
            raise ImmutableRecordError(f"承诺{commitment_id}已履行，不得重复登记")
        return self._record(
            actor,
            "commitment.fulfilled",
            {"zone_code": zone_code, "commitment_id": commitment_id},
            at=at,
        )

    def _on_commitment_fulfilled(self, event: Event) -> None:
        p = event.payload
        zone = self._zone(p["zone_code"])
        zone.commitments[p["commitment_id"]]["status"] = "fulfilled"
        zone.timeline.append(event.seq)

    # ------------------------------------------------------------ 独立评估

    def confirm_evaluation(
        self,
        actor: Actor,
        *,
        zone_code: str,
        evidence: dict[str, Any],
        at: str | None = None,
    ) -> Event:
        """独立评估人员确认成效；与既有确认矛盾的指标单独进入复核，不覆盖原值。"""
        zone = self._zone(zone_code)
        self._require_keys(evidence, ("id", "metric_code", "period", "value"))
        if evidence["id"] in zone.evidence:
            raise ImmutableRecordError(f"证据{evidence['id']}已确认封存，不得重复提交或改写")
        metric_code = evidence["metric_code"]
        if metric_code not in zone.baseline_metrics:
            raise ServiceError(f"指标{metric_code}不在基线指标目录中")

        for prior in zone.evidence.values():
            if (
                prior.get("confirmed")
                and prior["metric_code"] == metric_code
                and prior["period"] == evidence["period"]
                and prior["value"] != evidence["value"]
            ):
                conflict_id = f"CF-{len(self.conflicts) + 1:03d}"
                self._record(
                    actor,
                    "metric.conflict.flagged",
                    {
                        "conflict_id": conflict_id,
                        "zone_code": zone_code,
                        "metric_code": metric_code,
                        "period": evidence["period"],
                        "values": [
                            {"evidence_id": prior["id"], "value": prior["value"]},
                            {"evidence_id": evidence["id"], "value": evidence["value"]},
                        ],
                    },
                    at=at,
                )
                raise ServiceError(
                    f"指标{metric_code}与已确认证据{prior['id']}矛盾，已进入复核{conflict_id}，"
                    "原确认结果保持不变"
                )

        payload = {"zone_code": zone_code, "evidence": {**evidence, "confirmed": True,
                                                        "confirmed_by": actor.actor_id}}
        return self._record(actor, "evaluation.confirmed", payload, at=at)

    def _on_evaluation_confirmed(self, event: Event) -> None:
        p = event.payload
        evidence = dict(p["evidence"])
        evidence["confirmed_at"] = event.at
        self._zone(p["zone_code"]).evidence[evidence["id"]] = evidence
        self._zone(p["zone_code"]).timeline.append(event.seq)

    def record_conflict(self, actor: Actor, zone_code: str, metric_code: str,
                        description: str, at: str | None = None) -> Event:
        conflict_id = f"CF-{len(self.conflicts) + 1:03d}"
        return self._record(
            actor,
            "metric.conflict.flagged",
            {
                "conflict_id": conflict_id,
                "zone_code": zone_code,
                "metric_code": metric_code,
                "period": None,
                "description": description,
                "values": [],
            },
            at=at,
        )

    def _on_metric_conflict_flagged(self, event: Event) -> None:
        p = event.payload
        self.conflicts[p["conflict_id"]] = {
            "conflict_id": p["conflict_id"],
            "zone_code": p["zone_code"],
            "metric_code": p["metric_code"],
            "period": p.get("period"),
            "values": list(p.get("values", [])),
            "description": p.get("description", ""),
            "status": "open",
            "findings": [],
            "flagged_event_seq": event.seq,
        }
        self._zone(p["zone_code"]).timeline.append(event.seq)

    def resolve_conflict(self, actor: Actor, conflict_id: str, finding: str,
                         at: str | None = None) -> Event:
        """复核结论单独记录，原确认指标一律不被改写。"""
        if conflict_id not in self.conflicts:
            raise ServiceError(f"复核事项{conflict_id}不存在")
        return self._record(
            actor,
            "metric.review.recorded",
            {"conflict_id": conflict_id, "finding": finding},
            at=at,
        )

    def _on_metric_review_recorded(self, event: Event) -> None:
        conflict = self.conflicts[event.payload["conflict_id"]]
        conflict["findings"].append(
            {"event_seq": event.seq, "by": event.actor_id, "finding": event.payload["finding"]}
        )
        conflict["status"] = "resolved"
        self._zone(conflict["zone_code"]).timeline.append(event.seq)

    @staticmethod
    def _mask_evidence(evidence: dict[str, Any], viewer_role: str) -> dict[str, Any]:
        """企业敏感数据按接收者职责展示。"""
        view = dict(evidence)
        sensitive = view.pop("sensitive_fields", {})
        if sensitive:
            if viewer_role in ENTERPRISE_DATA_DUTY:
                view["sensitive_fields"] = dict(sensitive)
            else:
                view["sensitive_fields"] = {key: MASKED for key in sensitive}
                view["_notice"] = "企业敏感数据已按接收者职责脱敏"
        return view

    def view_evidence(self, viewer: Actor, zone_code: str) -> list[dict[str, Any]]:
        zone = self._zone(zone_code)
        return [self._mask_evidence(item, viewer.role) for item in zone.evidence.values()]

    # ------------------------------------------------------------ 督办与恢复

    def open_supervision(self, actor: Actor, zone_code: str, item: str, due: str,
                         at: str | None = None) -> Event:
        return self._record(
            actor,
            "supervision.opened",
            {"zone_code": zone_code, "item": item, "due": due},
            at=at,
        )

    def _on_supervision_opened(self, event: Event) -> None:
        p = event.payload
        self.supervision.append(
            {
                "event_seq": event.seq,
                "zone_code": p["zone_code"],
                "item": p["item"],
                "due": p["due"],
                "status": "open",
            }
        )
        self._zone(p["zone_code"]).timeline.append(event.seq)

    def close_supervision(self, actor: Actor, event_seq: int,
                          at: str | None = None) -> Event:
        return self._record(
            actor,
            "supervision.closed",
            {"opened_event_seq": event_seq},
            at=at,
        )

    def _on_supervision_closed(self, event: Event) -> None:
        target = event.payload["opened_event_seq"]
        for item in self.supervision:
            if item["event_seq"] == target:
                item["status"] = "closed"
        self._zone_by_event(target).timeline.append(event.seq)

    def _zone_by_event(self, seq: int) -> Zone:
        for zone in self.zones.values():
            if seq in zone.timeline:
                return zone
        raise ServiceError(f"事件{seq}未关联引领区")

    def pending_after_interruption(self, now: str) -> dict[str, Any]:
        """重放后清点：到期未履行承诺、逾期里程碑、在办督办。"""
        due_commitments, overdue_milestones = [], []
        for zone in self.zones.values():
            for item in zone.commitments.values():
                if item["status"] != "fulfilled" and item["due"] <= now:
                    due_commitments.append({"zone_code": zone.code, **item})
            for item in zone.milestones.values():
                if item["status"] in {"open", "rescheduled"} and item["due"] < now:
                    overdue_milestones.append({"zone_code": zone.code, **item})
        open_supervision = [
            {"zone_code": item["zone_code"], "item": item["item"], "due": item["due"]}
            for item in self.supervision
            if item["status"] == "open" and item["due"] <= now
        ]
        return {
            "due_commitments": due_commitments,
            "overdue_milestones": overdue_milestones,
            "open_supervision": open_supervision,
        }

    def restore_after_interruption(self, actor: Actor, now: str,
                                   at: str | None = None) -> dict[str, Any]:
        """中断恢复：清点并登记恢复事件；同状态重复恢复不重复登记。"""
        pending = self.pending_after_interruption(now)
        signature = canonical_hash(pending)
        for event in self.journal.events:
            if (
                event.type == "supervision.restored"
                and event.payload.get("pending_signature") == signature
            ):
                return {"resumed": pending, "event_seq": event.seq, "deduplicated": True}
        event = self._record(
            actor,
            "supervision.restored",
            {"at_point": now, "pending_signature": signature, "resumed": pending},
            at=at,
        )
        return {"resumed": pending, "event_seq": event.seq, "deduplicated": False}

    def _on_supervision_restored(self, event: Event) -> None:
        touched = {
            item["zone_code"]
            for bucket in event.payload["resumed"].values()
            for item in bucket
        }
        for code in touched:
            self.zones[code].timeline.append(event.seq)

    # ------------------------------------------------------------ 采用申请与审批

    def apply_adoption(
        self,
        actor: Actor,
        *,
        zone_code: str,
        version: int,
        receiver_region: str,
        local_differences: list[dict[str, str]],
        compensation_measures: list[dict[str, str]],
        at: str | None = None,
    ) -> Event:
        """接收地区申请采用某版经验，提交本地差异和补偿措施。"""
        sealed = self.sealed_version(zone_code, version)
        if not local_differences:
            raise ServiceError("采用申请必须提交本地差异说明（无差异也须显式声明）")
        for diff in local_differences:
            self._require_keys(diff, ("id", "description"))
        diff_ids = {diff["id"] for diff in local_differences}
        for measure in compensation_measures:
            self._require_keys(measure, ("difference_id", "measure"))
            if measure["difference_id"] not in diff_ids:
                raise ServiceError(
                    f"补偿措施指向的差异{measure['difference_id']}不存在"
                )
        self._adoption_seq += 1
        application_id = f"ADP-{self._adoption_seq:03d}"
        return self._record(
            actor,
            "adoption.applied",
            {
                "application_id": application_id,
                "zone_code": zone_code,
                "basis_version": version,
                "basis_sealed_hash": sealed["sealed_hash"],
                "receiver_region": receiver_region,
                "local_differences": local_differences,
                "compensation_measures": compensation_measures,
                "status": "pending",
            },
            at=at,
        )

    def _on_adoption_applied(self, event: Event) -> None:
        p = event.payload
        self.adoptions[p["application_id"]] = dict(p)
        self.adoptions[p["application_id"]]["event_seq"] = event.seq
        self._adoption_seq = max(self._adoption_seq, int(p["application_id"].split("-")[1]))
        self._zone(p["zone_code"]).timeline.append(event.seq)

    def decide_adoption(
        self,
        actor: Actor,
        *,
        application_id: str,
        approved: bool,
        precondition_results: dict[str, bool],
        deviation_approvals: list[dict[str, str]] | None = None,
        note: str = "",
        at: str | None = None,
    ) -> Event:
        """省级审批：核对前提满足情况，并逐项登记偏差获得了谁的同意。"""
        application = self.adoptions.get(application_id)
        if application is None:
            raise ServiceError(f"采用申请{application_id}不存在")
        if application["status"] != "pending":
            raise ImmutableRecordError(f"申请{application_id}已审批，结论不得改写")
        sealed = self.sealed_version(application["zone_code"], application["basis_version"])
        condition_ids = {c["id"] for c in sealed["preconditions"]}
        if set(precondition_results) != condition_ids:
            raise ServiceError("前提核对必须覆盖封存版本的全部前置条件")

        compensated = {m["difference_id"] for m in application["compensation_measures"]}
        deviation_approvals = deviation_approvals or []
        endorsed: dict[str, str] = {}
        for item in deviation_approvals:
            self._require_keys(item, ("difference_id", "approved_by"))
            if item["difference_id"] not in {d["id"] for d in application["local_differences"]}:
                raise ServiceError(f"偏差批准指向的差异{item['difference_id']}不存在")
            endorsed[item["difference_id"]] = item["approved_by"]

        unresolved = [
            diff["id"]
            for diff in application["local_differences"]
            if diff["id"] not in compensated and diff["id"] not in endorsed
        ]
        if unresolved:
            raise ServiceError(
                f"差异{','.join(unresolved)}既无补偿措施也无偏差同意，不能批准采用"
            )
        if approved and not all(precondition_results.values()):
            failed = [k for k, ok in precondition_results.items() if not ok]
            raise ServiceError(f"前置条件尚未满足：{','.join(failed)}，不能批准采用")

        payload = {
            "application_id": application_id,
            "approved": approved,
            "precondition_results": precondition_results,
            "deviation_approvals": deviation_approvals,
            "note": note,
        }
        event = self._record(actor, "adoption.decided", payload, at=at)
        return event

    def _on_adoption_decided(self, event: Event) -> None:
        application = self.adoptions[event.payload["application_id"]]
        application["status"] = (
            "approved" if event.payload["approved"] else "rejected"
        )
        application["decision_event_seq"] = event.seq
        self._zone(application["zone_code"]).timeline.append(event.seq)

    def adoption_card(self, application_id: str) -> dict[str, Any]:
        """审批结果卡片：采用哪一版经验、哪些前提满足、哪些偏差由谁同意，一目了然。"""
        application = self.adoptions.get(application_id)
        if application is None:
            raise ServiceError(f"采用申请{application_id}不存在")
        sealed = self.sealed_version(application["zone_code"], application["basis_version"])
        conditions = {c["id"]: c["description"] for c in sealed["preconditions"]}
        decision = next(
            (
                e.payload
                for e in self.journal.events
                if e.type == "adoption.decided" and e.payload["application_id"] == application_id
            ),
            None,
        )
        results = decision["precondition_results"] if decision else {}
        approvals = decision["deviation_approvals"] if decision else []
        compensation = {m["difference_id"]: m["measure"] for m in application["compensation_measures"]}
        return {
            "application_id": application_id,
            "receiver_region": application["receiver_region"],
            "status": application["status"],
            "adopted_experience": {
                "zone_code": application["zone_code"],
                "version": application["basis_version"],
                "sealed_hash": application["basis_sealed_hash"],
                "non_applicability": list(sealed["non_applicability"]),
            },
            "preconditions": [
                {
                    "id": cid,
                    "description": conditions[cid],
                    "satisfied": results.get(cid, False),
                }
                for cid in conditions
            ],
            "differences": [
                {
                    "id": diff["id"],
                    "description": diff["description"],
                    "compensation": compensation.get(diff["id"]),
                    "approved_by": next(
                        (a["approved_by"] for a in approvals if a["difference_id"] == diff["id"]),
                        None,
                    ),
                }
                for diff in application["local_differences"]
            ],
        }


def load_seed(service: PilotTransferService, actor: Actor, seed_path: str | Path) -> None:
    """按种子文件批量建立五类引领区档案（供示例与演练使用）。"""
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    if seed.get("domain") != DOMAIN:
        raise ServiceError("种子领域标识不一致")
    for zone in seed["zones"]:
        service.register_zone(
            actor,
            code=zone["code"],
            name=zone["name"],
            objective=zone["objective"],
            baseline_metrics=zone["baseline_metrics"],
            lead_unit=zone["lead_unit"],
            cooperating_units=zone["cooperating_units"],
            clauses=zone.get("clauses", []),
            commitments=zone.get("commitments", []),
            milestones=zone.get("milestones", []),
            risks=zone.get("risks", []),
        )
