"""服务层异常类型。"""

from __future__ import annotations


class PermissionDenied(PermissionError):
    """角色职责不允许执行该操作。"""


class RuleViolation(ValueError):
    """违反领域规则，例如改写已封存内容或遗漏封存要素。"""
