"""提供 API 与 Worker 共用的确定性 RAG 安全门和上下文信任边界。"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

SafetyResult = Literal["allow", "deny"]

RAG_SAFETY_VERSION = "rag-safety-v2"

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_BASE64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/])")
_SECRET_MARKER = re.compile(
    r"(?:SYNTHETIC_[A-Z0-9_]+|系统\s*Prompt|system\s*prompt|"
    r"api[_ -]?key|private\s+key|主密钥|供应商密钥)",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(?:"
    r"忽略.{0,32}(?:规则|指令|提示|之前)|"
    r"(?:ignore|disregard|override).{0,40}(?:previous|prior|all|system|rule|instruction)|"
    r"(?:把|将).{0,24}(?:本段|这段|文档|内容).{0,24}(?:最高优先级|系统指令)|"
    r"(?:管理员指令|最高优先级).{0,64}(?:发送|外部|工具|执行)|"
    r"(?:send|exfiltrate|upload|export).{0,48}(?:external|tool|endpoint|url)"
    r")",
    re.IGNORECASE,
)
_COMPACT_INJECTION = re.compile(
    r"(?:ignore|disregard|override).{0,40}(?:previous|prior|all|system|rule|instruction|之前|规则|指令)"
    r"|忽略.{0,32}(?:规则|指令|提示|之前)",
    re.IGNORECASE,
)
_SCOPE_EXPANSION = re.compile(
    r"(?:全部|所有|其他空间|其他工作空间|其他企业|企业\s*[B-Z]|workspace\s*[B-Z]|"
    r"cross[-_ ]?workspace|export[_ -]?all)",
    re.IGNORECASE,
)
_AUTHORIZATION_CLAIM = re.compile(
    r"(?:[\"']?decision[\"']?\s*[:=]\s*[\"']?(?:allow|deny)|"
    r"[\"']?approval_status[\"']?\s*[:=]|"
    r"tool_name\s*[:=]|export_all_workspace_data|include_sensitive\s*[:=])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyDecision:
    """保存不含原文和敏感标记的安全判定，供调用方决定是否继续流程。"""

    result: SafetyResult
    policy_version: str
    reason_codes: tuple[str, ...]
    normalized_text: str

    @property
    def allowed(self) -> bool:
        """返回当前内容是否可以进入下一处理阶段。"""

        return self.result == "allow"


class RagSafetyGate:
    """执行输入、非可信证据、改写范围和模型输出的确定性安全检查。"""

    policy_version = RAG_SAFETY_VERSION

    def inspect_user_query(self, query: str) -> SafetyDecision:
        """拒绝要求越过系统规则、读取秘密或扩大空间范围的用户问题。"""

        normalized = normalize_safety_text(query)
        reasons = _query_reasons(normalized)
        return _decision(normalized, reasons)

    def inspect_untrusted_evidence(self, evidence: str) -> SafetyDecision:
        """把文档当作不可信数据检查，禁止其升级为指令或触发外发动作。"""

        normalized = normalize_safety_text(evidence)
        reasons = _evidence_reasons(normalized)
        return _decision(normalized, reasons)

    def validate_rewrite_scope(
        self,
        original_query: str,
        rewritten_queries: tuple[str, ...],
        authorized_resource_ids: frozenset[str] | None,
    ) -> SafetyDecision:
        """确保查询改写只能收窄原请求，不能声明新的空间、资源或外发范围。"""

        original = normalize_safety_text(original_query)
        normalized = "\n".join(normalize_safety_text(query) for query in rewritten_queries)
        reasons: list[str] = list(_query_reasons(original))
        for query in rewritten_queries:
            candidate = normalize_safety_text(query)
            reasons.extend(_query_reasons(candidate))
            if _SCOPE_EXPANSION.search(candidate):
                reasons.append("scope_expansion")
            referenced_ids = set(re.findall(r"(?:doc|chunk)-[a-z0-9-]+", candidate, re.IGNORECASE))
            if authorized_resource_ids is not None and referenced_ids - authorized_resource_ids:
                reasons.append("unauthorized_resource")
        return _decision(normalized, tuple(dict.fromkeys(reasons)))

    def validate_model_output(
        self,
        output: str,
        *,
        tool_name: str | None = None,
        destination: str | None = None,
    ) -> SafetyDecision:
        """拒绝把模型文本、授权字段或工具参数当成后端授权结果。"""

        normalized = normalize_safety_text(output)
        reasons = list(_output_reasons(normalized))
        if tool_name or destination:
            reasons.append("unapproved_tool_request")
        return _decision(normalized, tuple(dict.fromkeys(reasons)))

    def wrap_untrusted_evidence(self, evidence: str) -> str | None:
        """通过安全检查后添加固定信任分隔符，拒绝时不返回任何证据文本。"""

        decision = self.inspect_untrusted_evidence(evidence)
        if not decision.allowed:
            return None
        return f"<untrusted_evidence>\n{decision.normalized_text}\n</untrusted_evidence>"


def normalize_safety_text(text: str) -> str:
    """使用 NFKC、零宽字符移除和空白折叠生成可审计的检测文本。"""

    normalized = unicodedata.normalize("NFKC", text)
    normalized = _ZERO_WIDTH.sub("", normalized)
    return " ".join(normalized.split())


def _decision(normalized: str, reasons: tuple[str, ...]) -> SafetyDecision:
    return SafetyDecision(
        result="deny" if reasons else "allow",
        policy_version=RAG_SAFETY_VERSION,
        reason_codes=reasons,
        normalized_text=normalized if not reasons else "",
    )


def _query_reasons(normalized: str) -> tuple[str, ...]:
    variants = _text_variants(normalized)
    reasons: list[str] = []
    if any(
        _INJECTION.search(variant) or _COMPACT_INJECTION.search(variant) for variant in variants
    ):
        reasons.append("prompt_injection")
    if any(_SECRET_MARKER.search(variant) for variant in variants):
        reasons.append("sensitive_request")
    if _SCOPE_EXPANSION.search(normalized):
        reasons.append("scope_expansion")
    return tuple(dict.fromkeys(reasons))


def _evidence_reasons(normalized: str) -> tuple[str, ...]:
    variants = _text_variants(normalized)
    reasons: list[str] = []
    if any(
        _INJECTION.search(variant) or _COMPACT_INJECTION.search(variant) for variant in variants
    ):
        reasons.append("untrusted_instruction")
    if any(_SECRET_MARKER.search(variant) for variant in variants):
        reasons.append("sensitive_marker")
    return tuple(dict.fromkeys(reasons))


def _output_reasons(normalized: str) -> tuple[str, ...]:
    variants = _text_variants(normalized)
    reasons: list[str] = []
    if any(_SECRET_MARKER.search(variant) for variant in variants):
        reasons.append("sensitive_marker")
    if _AUTHORIZATION_CLAIM.search(normalized):
        reasons.append("model_output_not_authority")
    return tuple(dict.fromkeys(reasons))


def _text_variants(normalized: str) -> tuple[str, ...]:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized, flags=re.UNICODE)
    variants = [normalized, compact]
    for token in _BASE64_TOKEN.findall(normalized):
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        variants.append(normalize_safety_text(decoded))
    return tuple(dict.fromkeys(variants))


def serialize_for_safety(value: object) -> str:
    """以稳定 JSON 序列化结构化上下文，避免调用方绕过文本检测。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "RAG_SAFETY_VERSION",
    "RagSafetyGate",
    "SafetyDecision",
    "normalize_safety_text",
    "serialize_for_safety",
]
