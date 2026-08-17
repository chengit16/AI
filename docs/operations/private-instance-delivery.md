# L4 私有化独立实例交付手册

## 交付边界

P5-10 将平台作为独立实例交付。实例继续复用同一套 React、FastAPI、Worker、OpenAPI、权限、审计、Alembic Migration、备份恢复和 ReleaseManifest，不维护行为不同的私有分支。部署档案位于 `contracts/deployment/private-instance-baseline.v1.json`，结构由同名 Schema 冻结。

本地验收使用 `synthetic_local` 环境和合成数据。PostgreSQL、Valkey、MinIO、Tika 与应用镜像由本地 Compose 管理；模型供应商、生产 KMS/Vault、正式证书、生产对象存储、真实客户环境和容量认证分别保持 `not_configured` 或 `not_run`。本地通过不表示生产私有化已上线。

## 安装、升级、回滚与恢复

1. 安装前运行 `./platform validate-instance`，确认 `.env`、Compose 配置和主密钥目录满足本地边界，再运行 `./platform start`。
2. 升级必须使用目标版本完整 ReleaseManifest。平台先执行 `./platform backup <path.aiprb>`，再由唯一 `migrate` 服务运行 `alembic upgrade head`。升级失败不得依赖 Down Migration，必须停止写入并使用升级前恢复包回滚。
3. 回滚只允许恢复最近一次升级前快照，不能把新版本恢复包导入旧版本程序；恢复前必须显式使用 `--confirm-replace`，恢复后执行 `./platform doctor` 和对象引用检查。
4. 主密钥和任务签名密钥不进入 Git 或 ReleaseManifest，只能加密封装进 `.aiprb` 恢复包。恢复包加密密钥绝不能进入同一恢复包，必须由操作员在实例外单独保管。

本地合成生命周期验收命令为：

```bash
./platform accept-stage-5-private-instance
```

该命令只生成低敏 JSON 证据，记录四个操作、Release、Schema Revision、外部状态和摘要，不记录业务正文、连接凭证或密钥材料。

## 离线与联网要求

`synthetic_local` 支持 `offline_capable`：应用和基础设施不需要外网，镜像必须预先存在于本机 Docker 缓存。真实模型供应商若启用，需要显式切换到 `egress_required` 并配置审核过的网络区域、数据政策和凭证；不能因为本地 Mock Provider 可用而标记真实供应商通过。

生产私有化还必须补齐客户环境、KMS/Vault、证书、对象存储、镜像扫描、Linux 宿主机和容量认证证据。缺少任一输入时，交付状态保持阻断或未配置，不通过文档措辞绕过门禁。
