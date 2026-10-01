"""落盘存储：单文件 JSON 快照。

每次变更先写临时文件再原子替换，进程在两次写入之间中断也不会
留下半个文件；服务重启后从快照恢复全部记录，继续督办。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _empty_state() -> dict[str, Any]:
    return {
        "zones": {},
        "archive": [],
        "commitments": {},
        "evaluations": {},
        "versions": {},
        "acceptances": {},
        "accepted_indicators": {},
        "reviews": {},
        "applications": {},
    }


class Store:
    """持有全部业务记录的字典状态，并负责持久化。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("存储文件内容无效")
            self.state = _empty_state()
            self.state.update(loaded)
        else:
            self.state = _empty_state()
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
