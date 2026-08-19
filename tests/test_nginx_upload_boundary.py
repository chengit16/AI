"""验证 Nginx 文档上传边界不会先于 API 拒绝合法文件。"""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONFIG_PATH = REPOSITORY_ROOT / "infra/nginx/default.conf"
UPLOAD_LOCATION = (
    "location ~ ^/api/v1/workspaces/[^/]+/knowledge-bases/[^/]+/"
    "documents/(upload|[^/]+/versions/upload)$"
)


def _location_body(config: str, declaration: str) -> str:
    """提取无嵌套 Nginx location，确保上限没有意外放宽到全部 API。"""

    start = config.find(declaration)
    assert start >= 0, f"缺少 Nginx location: {declaration}"
    body_start = config.find("{", start)
    body_end = config.find("}", body_start)
    assert body_start >= 0 and body_end > body_start
    return config[body_start + 1 : body_end]


def test_document_upload_proxy_limit_exceeds_api_maximum() -> None:
    config = NGINX_CONFIG_PATH.read_text(encoding="utf-8")
    upload_location = _location_body(config, UPLOAD_LOCATION)
    general_api_location = _location_body(config, "location /api/")

    # API 允许最大 100 MiB 文件，代理额外预留 1 MiB multipart 元数据空间。
    assert "client_max_body_size 101m;" in upload_location
    assert "client_max_body_size" not in general_api_location
    assert config.count("client_max_body_size") == 1
