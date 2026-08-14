"""定义认证、注册和工作空间清单接口的请求与响应 Schema。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LoginRequest(BaseModel):
    """定义登录操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=1024)


class RegistrationRequest(BaseModel):
    """定义注册操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    password: SecretStr = Field(min_length=12, max_length=1024)


class RegistrationResponse(BaseModel):
    """定义注册操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    personal_workspace_id: UUID


class LoginResponse(BaseModel):
    """定义登录操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    personal_workspace_id: UUID | None = None
    csrf_token: str


class LogoutResponse(BaseModel):
    """定义退出登录操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    logged_out: bool


class AuthenticationContextResponse(BaseModel):
    """定义认证上下文操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    trace_id: str
    actor_id: UUID
    user_id: UUID | None
    workspace_id: UUID
    authentication_method: str
    credential_scopes: list[str] | None
