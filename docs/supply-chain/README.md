# 阶段 0 供应链与许可证基线

## 1. 产物范围

本目录记录 `P0-12` 的可复现供应链基线：

- `python-production.cdx.json`：由 `uv.lock` 导出的 Python 生产依赖 CycloneDX 1.5 SBOM，不包含开发依赖和 `ai-validation` 模型验证依赖。
- `node-production.cdx.json`：由 `pnpm-lock.yaml`、冻结安装图和包清单生成的 Web 生产依赖 CycloneDX 1.5 SBOM。
- `dependency-licenses.json`：Python 与 Node 生产依赖许可证清单。
- 本文档：镜像、模型、解析器和关键组件的许可证决策、漏洞扫描结果与发布边界。

生成产物不写入本机绝对路径、用户名、随机 UUID 或生成时间。执行以下命令可重新生成或检查漂移：

```bash
.venv/bin/python scripts/generate_supply_chain.py
.venv/bin/python scripts/generate_supply_chain.py --check
```

Python SBOM 从锁文件生成，包含满足各目标平台 Marker 的 55 个生产组件；许可证清单读取当前 macOS 冻结环境中实际安装的 49 个适用组件。两者数量不同是跨平台条件依赖导致的预期结果。Node SBOM 与许可证清单包含 76 个生产组件，生成器直接遍历冻结安装图及各包 `package.json`，不依赖 pnpm Store 的机器本地索引。Linux 镜像发布时仍需按镜像内实际安装结果生成发布级许可证清单。

## 2. 漏洞审计结果

| 范围 | 工具与版本 | 2026-08-13 结果 | 状态 |
| --- | --- | --- | --- |
| Node 生产依赖 | pnpm 11.20.0 `pnpm audit --prod --audit-level high` | 76 个生产/可选依赖，严重、高危、中危、低危均为 0 | `passed` |
| Python 生产依赖 | pip-audit 2.9.0，输入来自 `uv.lock` 的带 Hash requirements | 安全升级后 38 个适用依赖未发现已知漏洞 | `passed` |
| Docker 镜像 | Docker Scout 1.24.0 | 本机未登录 Docker Scout，无法查询 CVE | `not_configured` |

首次 Python 审计在 `pdfminer-six 20251107` 和 `starlette 0.48.0` 中发现 7 个已知漏洞。项目已将 FastAPI 升级至 `0.141.1`、Starlette 升级至 `1.6.0`、pdfplumber 升级至 `0.11.10`、pdfminer-six 升级至 `20260107`；复查结果为 0 个已知漏洞，现有 API、契约、Worker 和解析测试通过。

镜像扫描没有执行，不能描述为通过。阶段 1A 在可用的镜像扫描环境中接入 Trivy、Grype 或已认证的 Docker Scout，并把严重与高危漏洞设为合并门禁。

## 3. 关键组件与模型许可证决策

| 组件 | 当前固定版本 | 许可证 | 当前本地验证 | 商业发布边界 |
| --- | --- | --- | --- | --- |
| React、Ant Design、TanStack Query、Zustand | 见 Node SBOM | MIT | 允许 | 保留许可证与版权声明 |
| lucide-react | 见 Node SBOM | ISC | 允许 | 保留许可证与版权声明 |
| FastAPI / Starlette | 0.141.1 / 1.6.0 | MIT / BSD-3-Clause | 允许 | 保留许可证与版权声明 |
| Celery / Kombu | 5.5.3 / 5.5.4 | BSD-3-Clause | 允许 | 任务按至少一次投递和幂等消费设计，保留许可证与版权声明 |
| psycopg | 3.2.10 | LGPL-3.0 | 允许动态依赖 | 分发包保留许可证、修改说明和可替换边界，发布前复核 LGPL 义务 |
| PostgreSQL / pgvector | 16 / 0.8.6 | PostgreSQL License | 允许 | 宽松许可证，可进入商业包 |
| Redis | 7.4 | RSALv2 或 SSPLv1 | 只保留阶段 0 历史验证 | 已由 `P1A-02` 从默认组合移除，不进入商业发布包 |
| Valkey | 8.1.5 | BSD-3-Clause | 允许 | 默认缓存、锁和任务唤醒依赖；固定多架构镜像摘要并保留许可证文本 |
| MinIO Server | RELEASE.2025-07-23T15-54-02Z | AGPL-3.0 | 仅允许当前本地验证 | 分发或网络服务形态必须法律复核；首期优先抽象 S3 Adapter，并在商业发布前选择合规对象存储方案 |
| Apache Tika | 3.2.3 | Apache-2.0 | 允许 | 保留 NOTICE 与许可证文本 |
| Tesseract OCR | 5.5.0 | Apache-2.0 | 允许英文验证 | 中文 OCR 仍按阶段 1D 接入 PaddleOCR 或等效 Adapter，并重新检查模型和运行库许可证 |
| BAAI/bge-m3 | 5617a9f61b028005a4858fdac845db406aefb181 | MIT | 允许离线验证 | 模型卡、Revision 和许可证随发布清单固定 |
| BAAI/bge-reranker-v2-m3 | 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e | Apache-2.0 | 允许离线验证 | 模型卡、Revision、NOTICE 和许可证随发布清单固定 |

该表是工程合规基线，不替代正式法律意见。任何许可证不明确、发生变化或包含未复核权重来源的组件，只能保留在技术验证环境，不能进入商业发布包。

## 4. 阶段 1A 必做项

1. 已由 `P1A-02` 将 Redis 7.4 替换为固定版本 Valkey 8.1.5；后续 Session、队列和 SSE 唤醒能力接入时继续复用已验证的 RESP 边界。
2. 对 MinIO/S3 Adapter 做可替换边界验收，商业发布方案必须给出许可证和部署义务结论。
3. 将 Python、Node、镜像、模型和前端静态资源合并到 `ReleaseManifest`，每个发布版本保留 SBOM、许可证、镜像摘要和模型 Revision。
4. 接入镜像漏洞扫描和完整 Secret Scanner；扫描工具未配置时发布门禁失败，不能静默跳过。
5. 评估 Starlette 的 `httpx2` 测试客户端迁移，消除当前 `httpx` 兼容弃用警告。
