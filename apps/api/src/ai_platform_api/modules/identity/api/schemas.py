from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    csrf_token: str


class LogoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logged_out: bool


class AuthenticationContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    trace_id: str
    actor_id: UUID
    user_id: UUID | None
    workspace_id: UUID
    authentication_method: str
    credential_scopes: list[str] | None
