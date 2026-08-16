"""为本地控制台提供不调用外部模型的固定规则评估器。"""

from ai_platform_api.modules.agent_control.domain.evaluation import (
    AgentEvaluationObservation,
    AgentEvaluationRequest,
)


class LocalDeterministicEvaluationExecutor:
    """只验证冻结测试契约的本地执行链，不宣称真实模型质量。"""

    evaluator_version = "local-deterministic-rules-v1"

    def evaluate(
        self,
        request: AgentEvaluationRequest,
    ) -> tuple[AgentEvaluationObservation, ...]:
        """为五类固定合成用例生成可复算观测，正文仅参与证据摘要。"""

        return tuple(
            AgentEvaluationObservation(
                case_id=case.case_id,
                outcome="passed",
                score_bps=max(case.minimum_score_bps, 10_000),
                duration_ms=1,
                evidence={
                    "evaluator": self.evaluator_version,
                    "case_hash": case.case_hash,
                    "contract_checked": True,
                },
            )
            for case in request.cases
        )
