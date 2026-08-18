"""实现经 SSRF 校验、地址钉住和响应限额保护的模型 HTTP Adapter。"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ai_platform_api.modules.model_gateway.domain.configuration import (
    CapabilityProbeResult,
    ProviderWireApi,
    RuntimeProviderAccess,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderConfigurationInvalidError,
)
from ai_platform_api.modules.model_gateway.domain.errors import ProviderInvocationError
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelCapability,
    ModelProvider,
    ModelRequest,
    ProviderResponse,
    TokenUsage,
)

DnsResolver = Callable[..., list[tuple[Any, ...]]]


@dataclass(frozen=True)
class ValidatedProviderTarget:
    """保存通过 SSRF 校验的供应商 URL、主机和解析地址。"""

    base_url: str
    hostname: str
    port: int
    path_prefix: str
    addresses: tuple[str, ...]


class ProviderTargetResolver(Protocol):
    """规范化供应商 URL 并解析主机地址，非审核地址一律拒绝。"""

    def resolve(self, value: str) -> ValidatedProviderTarget: ...


class StrictProviderBaseUrlPolicy:
    """仅允许显式批准的 HTTPS 域名和地址范围，阻止内网探测与重定向绕过。"""

    def __init__(
        self,
        allowed_hosts: tuple[str, ...],
        resolver: DnsResolver | None = None,
        *,
        allowed_resolved_networks: tuple[str, ...] = (),
    ) -> None:
        self._allowed_hosts = frozenset(
            host.strip().casefold().rstrip(".") for host in allowed_hosts
        )
        self._allowed_resolved_networks = _validated_fake_ip_networks(allowed_resolved_networks)
        self._resolver = resolver or cast("DnsResolver", socket.getaddrinfo)

    def normalize_and_validate(self, value: str) -> str:
        return self.resolve(value).base_url

    def resolve(self, value: str) -> ValidatedProviderTarget:
        normalized_input = value.strip()
        parsed = urlsplit(normalized_input)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or hostname not in self._allowed_hosts
            or len(normalized_input) > 2048
        ):
            raise ModelProviderConfigurationInvalidError
        path = _normalized_path(parsed)
        try:
            answers = self._resolver(hostname, 443, type=socket.SOCK_STREAM)
            addresses = tuple(sorted({str(answer[4][0]) for answer in answers}))
        except (OSError, UnicodeError, ValueError) as error:
            raise ModelProviderConfigurationInvalidError from error
        if not addresses or any(
            not _allowed_address(address, self._allowed_resolved_networks) for address in addresses
        ):
            raise ModelProviderConfigurationInvalidError
        base_url = urlunsplit(SplitResult("https", hostname, path, "", ""))
        return ValidatedProviderTarget(base_url, hostname, 443, path, addresses)


class OpenAiCompatibleCapabilityProbe:
    """使用固定合成提示探测能力；连接钉住已校验公网 IP，且绝不跟随重定向。"""

    def __init__(
        self,
        base_url_policy: StrictProviderBaseUrlPolicy,
        *,
        timeout_seconds: float = 10,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._base_url_policy = base_url_policy
        self._timeout_seconds = timeout_seconds
        self._ssl_context = ssl_context or ssl.create_default_context()

    def probe(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        wire_api: ProviderWireApi,
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult:
        try:
            # 1. 每轮探测先重新校验域名并固定地址，避免配置后发生 DNS 换址。
            target = self._base_url_policy.resolve(base_url)
            address = target.addresses[0]
            confirmed: set[ModelCapability] = set()
            # 2. 能力逐项验证并保留已确认集合，首个稳定错误立即终止后续外发。
            for capability in sorted(capabilities):
                status, content_type = self._probe_capability(
                    target,
                    address=address,
                    api_key=api_key,
                    model_id=model_id,
                    wire_api=wire_api,
                    capability=capability,
                )
                if status == 401 or status == 403:
                    return CapabilityProbeResult(
                        "failed", frozenset(confirmed), "PROBE_AUTHENTICATION"
                    )
                if status == 429:
                    return CapabilityProbeResult(
                        "failed", frozenset(confirmed), "PROBE_RATE_LIMITED"
                    )
                if status < 200 or status >= 300:
                    return CapabilityProbeResult(
                        "failed", frozenset(confirmed), "PROBE_CAPABILITY_UNSUPPORTED"
                    )
                if capability == "streaming" and "text/event-stream" not in content_type:
                    return CapabilityProbeResult(
                        "failed", frozenset(confirmed), "PROBE_INVALID_RESPONSE"
                    )
                confirmed.add(capability)
            return CapabilityProbeResult("passed", frozenset(confirmed))
        except ModelProviderConfigurationInvalidError:
            return CapabilityProbeResult("failed", frozenset(), "PROBE_URL_REJECTED")
        except (OSError, ssl.SSLError, TimeoutError, http.client.HTTPException, ValueError):
            return CapabilityProbeResult("failed", frozenset(), "PROBE_UNAVAILABLE")

    def _probe_capability(
        self,
        target: ValidatedProviderTarget,
        *,
        address: str,
        api_key: str,
        model_id: str,
        wire_api: ProviderWireApi,
        capability: ModelCapability,
    ) -> tuple[int, str]:
        # 1. 根据能力构造最小合成请求，探测内容不包含任何用户或工作空间数据。
        payload = _probe_payload(model_id, wire_api=wire_api, capability=capability)

        # 2. 使用已验证并钉住的目标地址发起请求，同时限制响应大小并确保连接关闭。
        connection = _PinnedHttpsConnection(
            target.hostname,
            address,
            port=target.port,
            timeout=self._timeout_seconds,
            context=self._ssl_context,
        )
        try:
            endpoint = _wire_endpoint(target.path_prefix, wire_api)
            connection.request(
                "POST",
                endpoint,
                body=json.dumps(payload, ensure_ascii=False).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream"
                    if capability == "streaming"
                    else "application/json",
                    "User-Agent": "ai-platform-capability-probe/1",
                },
            )
            response = connection.getresponse()
            # 限制探测响应读取量，避免不可信供应商以大响应占满进程内存。
            response.read(262_145)
            return response.status, response.getheader("Content-Type", "").casefold()
        finally:
            connection.close()


class OpenAiCompatibleRuntimeProviderFactory:
    """按已治理的短时凭证创建单次 Provider，不缓存或暴露明文 Key。"""

    def __init__(self, base_url_policy: StrictProviderBaseUrlPolicy) -> None:
        self._base_url_policy = base_url_policy

    def create(self, access: RuntimeProviderAccess) -> ModelProvider:
        return OpenAiCompatibleRuntimeProvider(access, self._base_url_policy)


class OpenAiCompatibleRuntimeProvider:
    """执行非流式 OpenAI-compatible 调用，原始供应商错误不得越过 Adapter。"""

    def __init__(
        self,
        access: RuntimeProviderAccess,
        base_url_policy: ProviderTargetResolver,
    ) -> None:
        self._access = access
        self._base_url_policy = base_url_policy
        self.provider_id = str(access.configuration.provider_id)

    def invoke(
        self,
        request: ModelRequest,
        model_id: str,
        timeout_ms: int,
    ) -> ProviderResponse:
        # 1. 重新执行 URL 策略并钉住首个审核地址，运行调用不接受重定向或动态换址。
        try:
            target = self._base_url_policy.resolve(self._access.configuration.base_url)
            wire_api = self._access.configuration.wire_api
            connection = _PinnedHttpsConnection(
                target.hostname,
                target.addresses[0],
                port=target.port,
                timeout=max(timeout_ms, 1) / 1000,
                context=ssl.create_default_context(),
            )
            try:
                connection.request(
                    "POST",
                    _wire_endpoint(target.path_prefix, wire_api),
                    body=json.dumps(
                        _runtime_payload(request, model_id=model_id, wire_api=wire_api),
                        ensure_ascii=False,
                    ).encode(),
                    headers={
                        "Authorization": f"Bearer {self._access.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "ai-platform-runtime/1",
                    },
                )
                response = connection.getresponse()
                # 2. 有界读取后先映射稳定供应商错误；原始网络和解析异常不能越过 Adapter。
                payload = response.read(4_194_305)
                if len(payload) > 4_194_304:
                    raise ProviderInvocationError(
                        "invalid_response", False, True, response.getheader("x-request-id")
                    )
                self._raise_for_status(response.status, response.getheader("x-request-id"))
            finally:
                connection.close()
        except ProviderInvocationError:
            raise
        except TimeoutError as error:
            raise ProviderInvocationError("timeout", True, True) from error
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as error:
            raise ProviderInvocationError("unavailable", True, True) from error
        provider_request_id = response.getheader("x-request-id")
        if wire_api == "responses":
            return self._parse_responses_response(payload, provider_request_id)
        return self._parse_chat_completions_response(payload, provider_request_id)

    @staticmethod
    def _raise_for_status(status: int, provider_request_id: str | None) -> None:
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise ProviderInvocationError("authentication", False, False, provider_request_id)
        if status == 429:
            raise ProviderInvocationError("rate_limited", True, True, provider_request_id)
        if status in {400, 422}:
            raise ProviderInvocationError("invalid_request", False, False, provider_request_id)
        if status == 404:
            raise ProviderInvocationError(
                "capability_unsupported", False, False, provider_request_id
            )
        if status >= 500:
            raise ProviderInvocationError("unavailable", True, True, provider_request_id)
        # 3xx 不跟随重定向，其他未知状态同样按不可信响应处理。
        raise ProviderInvocationError("invalid_response", False, True, provider_request_id)

    @staticmethod
    def _parse_chat_completions_response(
        payload: bytes, provider_request_id: str | None
    ) -> ProviderResponse:
        try:
            document = json.loads(payload)
            choice = document["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason") or "unknown"
            usage_document = document.get("usage")
            usage = (
                TokenUsage(
                    input_tokens=int(usage_document["prompt_tokens"]),
                    output_tokens=int(usage_document["completion_tokens"]),
                )
                if isinstance(usage_document, dict)
                else None
            )
            if not isinstance(content, str) or not isinstance(finish_reason, str):
                raise TypeError
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderInvocationError(
                "invalid_response", False, True, provider_request_id
            ) from error
        if finish_reason == "content_filter":
            raise ProviderInvocationError("content_policy", False, False, provider_request_id)
        return ProviderResponse(content, finish_reason, usage, provider_request_id)

    @staticmethod
    def _parse_responses_response(
        payload: bytes, provider_request_id: str | None
    ) -> ProviderResponse:
        """从原始 Responses 对象提取文本和用量，不依赖 SDK 的 `output_text` 辅助属性。"""

        try:
            # 1. 只接受消息中的文本输出；供应商拒绝内容必须映射为稳定策略错误。
            document = json.loads(payload)
            output = document["output"]
            if not isinstance(output, list):
                raise TypeError
            text_parts: list[str] = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    raise TypeError
                for part in content:
                    if not isinstance(part, dict):
                        raise TypeError
                    if part.get("type") == "refusal":
                        raise ProviderInvocationError(
                            "content_policy", False, False, provider_request_id
                        )
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text_parts.append(cast(str, part["text"]))
            if not text_parts:
                raise TypeError
            # 2. 将 Responses 状态和用量归一到内部模型，缺失关键状态时拒绝响应。
            status = document.get("status")
            if not isinstance(status, str):
                raise TypeError
            incomplete_details = document.get("incomplete_details")
            finish_reason = (
                "stop"
                if status == "completed"
                else str(incomplete_details.get("reason", status))
                if isinstance(incomplete_details, dict)
                else status
            )
            usage_document = document.get("usage")
            usage = (
                TokenUsage(
                    input_tokens=int(usage_document["input_tokens"]),
                    output_tokens=int(usage_document["output_tokens"]),
                )
                if isinstance(usage_document, dict)
                else None
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderInvocationError(
                "invalid_response", False, True, provider_request_id
            ) from error
        return ProviderResponse("".join(text_parts), finish_reason, usage, provider_request_id)


def _wire_endpoint(path_prefix: str, wire_api: ProviderWireApi) -> str:
    """把已校验 Base URL 前缀与固定协议端点组合，调用方不能注入任意路径。"""

    suffix = "responses" if wire_api == "responses" else "chat/completions"
    return f"{path_prefix}/{suffix}"


def _runtime_payload(
    request: ModelRequest,
    *,
    model_id: str,
    wire_api: ProviderWireApi,
) -> dict[str, object]:
    """按供应商协议生成最小运行载荷，并在 Responses 模式关闭服务端持久化。"""

    messages = [{"role": message.role, "content": message.content} for message in request.messages]
    if wire_api == "responses":
        return {
            "model": model_id,
            "input": messages,
            "max_output_tokens": request.max_output_tokens,
            "stream": False,
            "store": False,
        }
    return {
        "model": model_id,
        "messages": messages,
        "max_tokens": request.max_output_tokens,
        "stream": False,
    }


def _probe_payload(
    model_id: str,
    *,
    wire_api: ProviderWireApi,
    capability: ModelCapability,
) -> dict[str, object]:
    """生成不含用户数据的协议专用能力探测载荷。"""

    # 1. Responses 使用独立输入、工具和结构化输出字段，并明确关闭服务端持久化。
    if wire_api == "responses":
        payload: dict[str, object] = {
            "model": model_id,
            "instructions": "这是平台连通性探测。内容不包含用户数据。",
            "input": "仅回复 OK。",
            "max_output_tokens": 8,
            "store": False,
        }
        if capability == "streaming":
            payload["stream"] = True
        elif capability == "tools":
            payload["tools"] = [
                {
                    "type": "function",
                    "name": "synthetic_probe",
                    "description": "固定能力探测",
                    "parameters": {"type": "object", "properties": {}},
                    "strict": True,
                }
            ]
            payload["tool_choice"] = {"type": "function", "name": "synthetic_probe"}
        elif capability == "structured_output":
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "synthetic_probe",
                    "schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            }
        return payload

    # 2. Chat Completions 保持既有消息格式，避免旧供应商因协议事实新增而漂移。
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "这是平台连通性探测。内容不包含用户数据。"},
            {"role": "user", "content": "仅回复 OK。"},
        ],
        "max_tokens": 8,
        "temperature": 0,
    }
    if capability == "streaming":
        payload["stream"] = True
    elif capability == "tools":
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "synthetic_probe",
                    "description": "固定能力探测",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        payload["tool_choice"] = "required"
    elif capability == "structured_output":
        payload["response_format"] = {"type": "json_object"}
        payload["messages"] = [
            {"role": "system", "content": "输出 JSON。内容不包含用户数据。"},
            {"role": "user", "content": '{"status":"ok"}'},
        ]
    return payload


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        hostname: str,
        address: str,
        *,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=context)
        self._pinned_address = address
        self._pinned_ssl_context = context

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            None,
        )
        try:
            self.sock = self._pinned_ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _normalized_path(parsed: SplitResult) -> str:
    segments = [segment for segment in parsed.path.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        raise ModelProviderConfigurationInvalidError
    return "/" + "/".join(segments) if segments else ""


def _validated_fake_ip_networks(
    values: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network, ...]:
    """限制例外为 RFC 2544 基准网段，避免直接构造策略时绕过 Settings 校验。"""

    benchmark_network = ipaddress.IPv4Network("198.18.0.0/15")
    networks: list[ipaddress.IPv4Network] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value.strip(), strict=True)
        except ValueError as error:
            raise ValueError("模型供应商解析地址例外必须是规范 CIDR") from error
        if not isinstance(network, ipaddress.IPv4Network) or not network.subnet_of(
            benchmark_network
        ):
            raise ValueError("模型供应商解析地址例外只能包含 198.18.0.0/15 或其子网")
        networks.append(network)
    return tuple(
        sorted(set(networks), key=lambda item: (int(item.network_address), item.prefixlen))
    )


def _allowed_address(
    value: str,
    allowed_resolved_networks: tuple[ipaddress.IPv4Network, ...],
) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if (
        address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
    ):
        return False
    if address.is_global:
        return True
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in allowed_resolved_networks
    )
