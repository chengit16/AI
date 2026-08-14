from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ai_platform_api.modules.model_gateway.domain.configuration import (
    CapabilityProbeResult,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderConfigurationInvalidError,
)
from ai_platform_api.modules.model_gateway.domain.models import ModelCapability

DnsResolver = Callable[..., list[tuple[Any, ...]]]


@dataclass(frozen=True)
class ValidatedProviderTarget:
    base_url: str
    hostname: str
    port: int
    path_prefix: str
    addresses: tuple[str, ...]


class StrictProviderBaseUrlPolicy:
    """仅允许运维显式批准的公网 HTTPS 域名，阻止内网探测与重定向绕过。"""

    def __init__(
        self,
        allowed_hosts: tuple[str, ...],
        resolver: DnsResolver | None = None,
    ) -> None:
        self._allowed_hosts = frozenset(
            host.strip().casefold().rstrip(".") for host in allowed_hosts
        )
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
        if not addresses or any(not _public_address(address) for address in addresses):
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
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult:
        try:
            target = self._base_url_policy.resolve(base_url)
            address = target.addresses[0]
            confirmed: set[ModelCapability] = set()
            for capability in sorted(capabilities):
                status, content_type = self._probe_capability(
                    target,
                    address=address,
                    api_key=api_key,
                    model_id=model_id,
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
        capability: ModelCapability,
    ) -> tuple[int, str]:
        payload: dict[str, object] = {
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

        connection = _PinnedHttpsConnection(
            target.hostname,
            address,
            port=target.port,
            timeout=self._timeout_seconds,
            context=self._ssl_context,
        )
        try:
            endpoint = f"{target.path_prefix}/chat/completions"
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


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global and not address.is_multicast and not address.is_unspecified)
