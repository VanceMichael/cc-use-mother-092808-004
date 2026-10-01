# 县域试点经验复制与偏差说明

县域试点与制度转化服务：管理五类引领区的连续档案、跨域承诺、独立评估证据、经验封存版本与接收地区采用审批。

## 解决的问题

督导推广嘉善跨省通办、生态共治、前置货站经验时，材料只写成果、不留适用条件与失败调整，照搬出现偏差无从说明。本服务把推广所需的全部要素固化为连续档案与只增台账：

- 五类引领区均记录**目标、基线指标（含数据口径）、牵头与协同单位、制度条款、资金资源承诺、里程碑、风险、评估证据**；
- 经验**发布即封存**数据口径、前置条件和明确的不适用范围；
- 伙伴退出或项目延期**可重排未履行承诺**，但已完成评估与已履行承诺锁定、不可改写；
- **申报方只能补充材料，成效由独立评估人员确认**；企业敏感数据按接收者职责展示；
- **相同报送沿用原受理记录**；矛盾指标单独进入复核，原确认值不变；
- 服务**中断后重放台账**恢复督办与到期承诺；
- 接收地区采用时提交**本地差异与补偿措施**，审批卡片直接显示采用的经验版本与封存摘要、已满足前提、偏差同意人。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `src/pilot_transfer_service.py` | 事件台账与转化服务（建档、封存、重排、评估、受理、复核、恢复、采用审批） |
| `src/news_context_004.py` | 领域上下文读取与校验 |
| `contracts/context.schema.json` | 领域上下文结构契约 |
| `contracts/journal.schema.json` | 只增事件台账信封契约（序号、角色、哈希链） |
| `fixtures/context.json` | 领域事实与约束（第 2 版，脱敏） |
| `fixtures/five_zones_seed.json` | 五类引领区示例种子（脱敏演练数据） |
| `docs/domain-rules.md` | 角色职责与核心规则 |
| `tests/` | 领域资料测试与服务业务规则测试（25 项） |

## 快速示例

```python
from pathlib import Path
from src.pilot_transfer_service import (
    Actor, EventJournal, PilotTransferService, PROVINCE, EVALUATOR, RECEIVER, SYSTEM, load_seed,
)

service = PilotTransferService(EventJournal.open("journal.jsonl"))   # 不存在则新建，打开即重放校验
load_seed(service, Actor("prov-01", PROVINCE), "fixtures/five_zones_seed.json")

# 独立评估确认成效（申报方无权）→ 省级发布封存版本
service.confirm_evaluation(Actor("eval-01", EVALUATOR), zone_code="Z01", evidence={...})
service.publish_experience(Actor("prov-01", PROVINCE), zone_code="Z01", version=1, ...)

# 接收地区申请采用，提交差异与补偿；省级核对全部前置条件后审批
service.apply_adoption(Actor("recv-01", RECEIVER), zone_code="Z01", version=1, ...)
card = service.adoption_card("ADP-001")   # 版本/前提/偏差同意人一目了然

# 中断重启后恢复在办事项
PilotTransferService(EventJournal.open("journal.jsonl")).restore_after_interruption(
    Actor("sys", SYSTEM), now="2026-10-31T00:00:00+00:00")
```

## 关键不变量

- 台账只增：事件含前条 SHA-256 摘要形成哈希链，改写任何已落盘记录都会在重放时被检出；
- 封存版本不可变：发布后的补充材料不改变版本快照摘要；
- 评估证据不可变：承诺重排、指标复核、重新审批均不能覆盖已确认值；
- 角色强制：事件类型与提交角色在台账层校验，越权操作直接拒绝。

## 开发命令

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

编译检查：

```bash
python3 -m compileall -q src tests
```

两条命令只读写仓库内文件（恢复测试使用临时目录），不需要连接外部业务系统。
