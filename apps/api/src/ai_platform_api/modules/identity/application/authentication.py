from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import PlatformRequestContext, RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementFeatureDeniedError,
)
from ai_platform_api.modules.identity.application.errors import (
    ApiKeyConfigurationError,
    ApiKeyInvalidError,
    ApiKeyNotFoundError,
    AuthenticationRequiredError,
    CsrfValidationError,
    InvalidCredentialsError,
    WorkspaceContextDeniedError,
)
from ai_platform_api.modules.identity.domain.entitlements import EntitlementAccessReader
from ai_platform_api.modules.identity.domain.models import (
    AccountCredential,
    BrowserSession,
    IdentityReader,
    IdentityUnitOfWork,
    IssuedApiKey,
    LoginResult,
    OpenApiKey,
    PasswordVerifier,
    SecretDigester,
    SessionStore,
)


class AuthenticationService:
    """隐藏多种凭证协议，并只产出服务端重建的可信 RequestContext。"""

    def __init__(
        self,
        repository: IdentityReader,
        sessions: SessionStore,
        passwords: PasswordVerifier,
        secrets_digester: SecretDigester,
        session_ttl_seconds: int,
        entitlements: EntitlementAccessReader,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._passwords = passwords
        self._secrets = secrets_digester
        self._session_ttl_seconds = session_ttl_seconds
        self._entitlements = entitlements

    def login(self, login_name: str, password: str) -> LoginResult:
        account = self._repository.get_account_by_login(login_name.strip().casefold())
        password_valid = self._passwords.verify(
            account.password_hash if account is not None else None,
            password,
        )
        if account is None or account.status != "active" or not password_valid:
            raise InvalidCredentialsError

        # 默认个人空间是浏览器建立可信工作空间上下文的入口，缺失时拒绝创建残缺会话。
        personal_workspace_id = self._repository.get_personal_workspace_id(account.account_id)
        if personal_workspace_id is None:
            raise InvalidCredentialsError

        csrf_token = secrets.token_urlsafe(32)
        session = BrowserSession(
            account_id=account.account_id,
            auth_version=account.auth_version,
            csrf_digest=self._secrets.digest(csrf_token),
        )
        token = self._sessions.create(session, self._session_ttl_seconds)
        return LoginResult(token, csrf_token, account.account_id, personal_workspace_id)

    def browser_context(
        self,
        *,
        session_token: str | None,
        csrf_token: str | None,
        require_csrf: bool,
        workspace_id: UUID,
        request_id: UUID,
        trace: TraceContext,
    ) -> RequestContext:
        account = self._browser_account(
            session_token=session_token,
            csrf_token=csrf_token,
            require_csrf=require_csrf,
        )
        self._require_workspace_access(account.account_id, workspace_id)
        return RequestContext.trusted(
            actor_id=account.account_id,
            user_id=account.account_id,
            workspace_id=workspace_id,
            trace=trace,
            request_id=request_id,
            authentication_method="browser_session",
        )

    def platform_browser_context(
        self,
        *,
        session_token: str | None,
        csrf_token: str | None,
        require_csrf: bool,
        request_id: UUID,
        trace: TraceContext,
    ) -> PlatformRequestContext:
        """平台治理身份不携带虚构 workspace_id，且明确拒绝 Open API Key。"""

        account = self._browser_account(
            session_token=session_token,
            csrf_token=csrf_token,
            require_csrf=require_csrf,
        )
        return PlatformRequestContext(
            request_id=request_id,
            trace=trace,
            actor_id=account.account_id,
            account_id=account.account_id,
        )

    def api_key_context(
        self,
        *,
        credential: str,
        workspace_id: UUID,
        request_id: UUID,
        trace: TraceContext,
        now: datetime | None = None,
    ) -> RequestContext:
        key_id, secret = self._parse_api_key(credential)
        api_key = self._repository.get_api_key(key_id)
        current_time = now or datetime.now(UTC)
        if (
            api_key is None
            or api_key.workspace_id != workspace_id
            or api_key.revoked_at is not None
            or (api_key.expires_at is not None and api_key.expires_at <= current_time)
            or not self._secrets.matches(api_key.secret_digest, secret)
        ):
            raise ApiKeyInvalidError

        account = self._repository.get_account(api_key.created_by_account_id)
        if account is None or account.status != "active":
            raise ApiKeyInvalidError
        self._require_workspace_access(account.account_id, workspace_id)
        open_api = self._entitlements.get_open_api_entitlement(workspace_id)
        if open_api is None or not open_api.active:
            raise ApiKeyInvalidError
        return RequestContext.trusted(
            actor_id=api_key.actor_id,
            user_id=account.account_id,
            workspace_id=workspace_id,
            trace=trace,
            request_id=request_id,
            authentication_method="open_api_key",
            credential_scopes=frozenset(api_key.scopes),
        )

    def logout(self, session_token: str) -> None:
        self._sessions.revoke(session_token)

    def _require_workspace_access(self, account_id: UUID, workspace_id: UUID) -> None:
        access = self._repository.get_workspace_access(account_id, workspace_id)
        if access is None or not access.active:
            raise WorkspaceContextDeniedError

    def _browser_account(
        self,
        *,
        session_token: str | None,
        csrf_token: str | None,
        require_csrf: bool,
    ) -> AccountCredential:
        if session_token is None:
            raise AuthenticationRequiredError
        session = self._sessions.resolve(session_token)
        if session is None:
            raise AuthenticationRequiredError
        if require_csrf and (
            csrf_token is None or not self._secrets.matches(session.csrf_digest, csrf_token)
        ):
            raise CsrfValidationError
        account = self._repository.get_account(session.account_id)
        if (
            account is None
            or account.status != "active"
            or account.auth_version != session.auth_version
        ):
            self._sessions.revoke(session_token)
            raise AuthenticationRequiredError
        return account

    @staticmethod
    def _parse_api_key(credential: str) -> tuple[UUID, str]:
        try:
            prefix, secret = credential.split(".", maxsplit=1)
            if not prefix.startswith("aip_") or not secret:
                raise ValueError
            return UUID(hex=prefix.removeprefix("aip_")), secret
        except ValueError as error:
            raise ApiKeyInvalidError from error


class ApiKeyService:
    def __init__(
        self,
        repository: IdentityReader,
        unit_of_work: IdentityUnitOfWork,
        secrets_digester: SecretDigester,
        entitlements: EntitlementAccessReader,
    ) -> None:
        self._repository = repository
        self._unit_of_work = unit_of_work
        self._secrets = secrets_digester
        self._entitlements = entitlements

    def issue(
        self,
        *,
        context: RequestContext,
        name: str,
        scopes: tuple[str, ...],
        expires_at: datetime | None,
    ) -> IssuedApiKey:
        if context.user_id is None:
            raise AuthenticationRequiredError
        self._require_workspace_access(context.user_id, context.workspace_id)
        open_api = self._entitlements.get_open_api_entitlement(context.workspace_id)
        if open_api is None or not open_api.active:
            raise EntitlementFeatureDeniedError
        normalized_name = name.strip()
        normalized_scopes = tuple(sorted(set(scopes)))
        now = datetime.now(UTC)
        if (
            not normalized_name
            or len(normalized_name) > 255
            or not normalized_scopes
            or any(
                re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}", scope) is None
                for scope in normalized_scopes
            )
            or (expires_at is not None and expires_at <= now)
        ):
            raise ApiKeyConfigurationError
        key_id = uuid4()
        actor_id = uuid4()
        secret = secrets.token_urlsafe(32)
        plaintext = f"aip_{key_id.hex}.{secret}"
        api_key = OpenApiKey(
            key_id=key_id,
            actor_id=actor_id,
            workspace_id=context.workspace_id,
            created_by_account_id=context.user_id,
            secret_digest=self._secrets.digest(secret),
            scopes=normalized_scopes,
            expires_at=expires_at,
            revoked_at=None,
        )
        with self._unit_of_work as unit_of_work:
            unit_of_work.api_keys.add(api_key, name=normalized_name, last_four=secret[-4:])
            unit_of_work.commit()
        return IssuedApiKey(
            key_id=key_id,
            actor_id=actor_id,
            workspace_id=context.workspace_id,
            plaintext=plaintext,
            last_four=secret[-4:],
            scopes=api_key.scopes,
        )

    def revoke(self, *, context: RequestContext, key_id: UUID) -> None:
        if context.user_id is None:
            raise AuthenticationRequiredError
        self._require_workspace_access(context.user_id, context.workspace_id)
        with self._unit_of_work as unit_of_work:
            revoked = unit_of_work.api_keys.revoke(
                context.workspace_id,
                key_id,
                datetime.now(UTC),
            )
            if not revoked:
                raise ApiKeyNotFoundError
            unit_of_work.commit()

    def _require_workspace_access(self, account_id: UUID, workspace_id: UUID) -> None:
        access = self._repository.get_workspace_access(account_id, workspace_id)
        if access is None or not access.active:
            raise WorkspaceContextDeniedError
