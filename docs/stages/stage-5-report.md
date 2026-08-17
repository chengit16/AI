# 阶段 5 验证报告

## 1. 当前结论

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 5：质量运营、合规与规模化 |
| 当前节点 | `P5-09` 法规策略与法律保留（法域与适用策略 `not_configured`；`P5-04`、`P5-06` 等待真实输入） |
| 节点状态 | 进行中 |
| 阶段结论 | `not_run` |
| 报告日期 | 2026-08-17 |

阶段 5 的计划、入口审计、质量样本闭环、六层评估归因、成本归因机制、四级隔离控制面和本地合成 L3 迁移恢复已经完成。`P5-04` 本地质量运营机制已在 `e821801` 提交，当前树统一门禁已通过，但真实供应商和足够样本仍未配置，因此保持受阻；`P5-05` 已在 `eaa58fa` 提交并由当前树统一门禁解除原有工程阻塞，真实价格与供应商账单继续保持 `not_configured`。`P5-07` 已在 `06a7e25` 提交，`P5-08` 已完成并进入 `P5-09`；真实供应商、价格、容量、法域和 L3/L4 客户目标环境继续按实际状态独立门禁，不能因本地机制通过而自动判定真实质量或生产适配通过。

## 2. 入口条件审计

| 条件 | 状态 | 当前证据与处理 |
| --- | --- | --- |
| 阶段 2 可靠性与数据治理 | `passed` | `stage-2-complete` 已存在，复用任务、数据生命周期、观测和故障演练基线 |
| 阶段 4 受控工具执行 | `passed` | `stage-4-complete` 指向 `add5219`；P4-13 十五场景、前后两轮 13 项诊断、最终镜像、双视口浏览器、`0.4.0` ReleaseManifest 和统一门禁通过 |
| 真实模型供应商审核 | `not_configured` | Mock Provider 不能替代真实协议、质量、限流、价格或数据政策结论 |
| 真实线上质量与反馈样本 | `not_run` | 尚无满足固定模型、参数、网络区域、窗口和样本量要求的数据 |
| 真实价格与供应商账单 | `not_configured` | 现有价格和用量事实可验证机制，不能给出真实成本目标结论 |
| 容量认证 | `not_run` | 缺少独立压测环境，不进入本地功能结论 |
| 镜像扫描 | `not_configured` | 开发级供应链可继续验证，正式发布门禁保持阻断 |
| L3/L4 目标环境 | `not_configured` | 可建设合成迁移和独立实例工具，不能声称客户生产环境通过 |
| 法域、法规策略与法律保留要求 | `not_configured` | `P5-09` 实现前必须固定适用政策；未知政策不能自动推断 |
| 生产 KMS/Vault、证书和外部对象存储 | `not_configured` | 本地文件密钥和 MinIO 只验证适配边界，不代表生产集成通过 |

## 3. `P5-01` 验证记录

- 已以 `ai-intelligent-platform-v2-design.md` 的隔离等级、质量成本闭环、阶段 5 建设内容、17.8/17.9 拆分条件和 21.10 指标为事实来源。
- 已按 `docs/governance/delivery-and-git.md` 固定 `P5-01`～`P5-13` 节点、依赖、完成门禁、证据要求和独立提交规则。
- 已把质量运营、成本归因/预算、L1～L4 隔离、法规策略/法律保留、私有化交付、服务拆分审计和运营控制台纳入主节点。
- 已明确 `LLM Grading`、多模态、具体多源连接器、真实外部工具、SaaS 商业发布、容量认证和未触发的 Go 演进保持后置或条件范围。
- 已明确阶段 4 未关闭时只完成计划节点；阶段 4 关闭后允许按依赖启动实现节点，真实供应商、价格、容量、法域和私有化外部系统未配置时仍使用 `not_configured/not_run`，不由合成结果替代。
- 文档、工程规则、Python 注释与契约专项测试 `53/53` 通过，`git diff --check` 通过。
- 2026-08-17 08:30 CST 原样执行 `./scripts/verify`，敏感信息、契约生成、权限注册表、SBOM/许可证、ReleaseManifest 漂移、供应链、模块依赖、注释、UnoCSS、兼容性、React `58/58` 和生产构建通过；Python 阶段因沙箱拒绝访问本机 PostgreSQL、Valkey、MinIO、Tika 与 OCR 服务，结果为 `607 passed / 8 failed / 194 errors`，不作为项目逻辑失败或统一门禁通过证据。
- 初次申请提升权限重跑 `./scripts/verify` 时曾在项目代码执行前被审批服务 `503 Service Unavailable` 拒绝；计划基线按用户指令提交为 `9088801` 并继续保持进行中，没有以提交替代验收。
- 审批恢复后，P4-12 与 P4-13 正式入口均在干净修订上通过。阶段 4 关闭材料暂存后原样运行 `./scripts/verify`，受限沙箱内的首次 Python 结果为 `633 passed / 8 failed / 194 errors`，错误均为本机 PostgreSQL、Valkey、MinIO、Tika 与 OCR 连接被 `Operation not permitted` 拒绝；使用已批准的同一原样入口在沙箱外复验后 React `58/58`、Python `835/835`、mypy strict `677` 个源文件及全部工程检查通过，确认该批连接错误不是项目逻辑失败。
- 阶段 4 关闭提交为 `add5219`，annotated tag `stage-4-complete` 精确指向该提交。至此 `P5-01` 的计划、事实源、依赖、外部条件、专项检查、统一门禁和入口事实同步均完成，结论为 `passed`。

## 4. `P5-02` 验证记录

- 契约与数据边界：新增 `contracts/quality/quality-sample-baseline.v1.*`，固定 `run_failure`、`user_feedback` 和 `human_correction` 三类来源、对应权限、必需正文、SHA-256 期望值和删除 tombstone；三条采集样本与一条删除样本均明确为全合成，真实线上反馈与真实模型质量在契约中保持 `not_run`。
- Application 行为：公开 `QualitySampleService` 完成采集、删除、最新数据集和历史快照查询；正文只在调用期间短暂存在，持久化样本只保存输入、输出、反馈和修正摘要。完整策略决策、工作空间/部门/账号/资源范围、字段遮罩与最高密级随样本版本冻结；跨空间、字段遮罩、资源范围、密级或权限不满足时失败关闭。
- 版本与并发：同来源同版本同内容返回原快照，同版本内容漂移和版本倒退返回 `IDEMPOTENCY_CONFLICT`；更高来源版本追加样本与数据集版本，历史不改写。PostgreSQL advisory transaction lock 串行化同工作空间版本分配，六路同来源并发只产生一个数据集版本。
- 数据库与生命周期：Revision `20260817_0062` 新增 `quality_sample_versions`、`quality_dataset_versions` 和 `quality_dataset_members`，普通事务的更新/删除由三层 Trigger 阻断，生命周期清理仅能通过既有 `ai_platform.lifecycle_purge` 受控旁路；Registry 升级为 `v10`，三表均纳入工作空间隔离导出与清除。真实 PostgreSQL/MinIO/Valkey 生命周期验证两个空间隔离、导出不含正文、目标空间清除和其他空间保留。
- 专项验证：质量契约 `4/4`、质量 PostgreSQL `7/7`，连同 Registry、真实生命周期和 Migration 往返的联合回归为 `25/25`；Ruff 全仓 `691` 个文件、mypy strict `691` 个源文件、架构、Python 注释 `13/13`、当前文件与完整历史 Secret Scanner 均通过。
- Migration 回归：首次原样 `./scripts/verify` 的 React `58/58` 和绝大多数 Python 场景通过，Python 结果为 `843/847`；四项失败均来自阶段 4 测试把 `head` 硬编码为 `20260816_0061`。已把发布兼容矩阵追加 `20260817_0062`，同步四项真实往返断言，定向回归 `11/11` 通过，没有放宽原有破坏性降级保护。
- 最终统一门禁：修正后再次原样执行 `./scripts/verify`，Git Diff、全合成数据集、全历史 Secret Scanner、OpenAPI/生成类型、权限 Registry、SBOM/许可证、ReleaseManifest 漂移、开发供应链、前后端架构与注释、UnoCSS、契约兼容、React 格式/Lint/类型/`58/58` 测试、生产构建、Ruff、mypy strict 和 Python `847/847` 全部通过。
- 验收结论：`passed`。`P5-02` 的来源、授权投影、去重、版本、状态、删除传播、跨空间、敏感正文和无授权修正门禁均已完成；本节点没有前端交付，桌面与移动浏览器验收不适用。

## 5. `P5-03` 验证记录

- 契约与策略：新增 `contracts/quality/quality-evaluation-baseline.v1.*`，冻结 `retrieval`、`citation`、`model`、`prompt`、`workflow`、`tool` 六层顺序、每层最少 2 个样本、整数基点最低分、`deterministic-layered-v1` 评估器版本、低基数原因码和 `LLM Grading=false`。全合成通过夹具包含 12 条观测，真实模型质量和线上反馈仍标记为 `not_run`。
- 聚合与失败关闭：运行事实绑定工作空间、数据集版本/摘要、服务、AgentRelease、运行配置、策略和评估器身份；数据集、服务、Release、配置、评估器或批次成员身份漂移分别给出原因码并将六层置为失败。样本不足、`failed`、`timeout`、`skipped` 和低于阈值统一失败关闭；`unauthorized_access`、`restricted_field_leakage`、`invalid_citation`、`unauthorized_tool_call` 作为安全硬失败不能被其他高分样本抵消。
- 证据边界：执行器只接收工作空间、目标身份和样本版本 ID；观测 evidence 只在 Application 内计算 SHA-256，运行、层和样本结果表不保存 Prompt、回答、引用或工具正文。未知样本、重复观测和非 JSON 证据失败关闭，结果身份对同一目标幂等，内容漂移返回 `IDEMPOTENCY_CONFLICT`。
- 数据库与生命周期：Revision `20260817_0063` 新增 `quality_evaluation_runs`、`quality_evaluation_layer_results` 和 `quality_evaluation_sample_results`，三表复合外键绑定工作空间/数据集/样本，普通事务更新与删除由既有不可变 Trigger 阻断；Registry `v11` 将三表纳入工作空间隔离导出和受控清除。Migration 空库往返、真实生命周期导出/清除、跨空间读取和三表破坏性写入拒绝通过。
- 专项与统一门禁：契约 `10/10`，P5-03 PostgreSQL `4/4`，生命周期 `6/6`；全仓原样 `./scripts/verify` 通过，React `58/58`、Python `861/861`、Ruff、mypy strict `700` 个源文件、架构、注释、契约兼容、发布兼容和生产构建全部通过。本节点没有前端交付，桌面与移动浏览器验收不适用。
- 验收结论：`passed`。该结论仅覆盖本地确定性机制和全合成事实，不代表真实模型质量、供应商协议、价格、线上样本或容量认证。

## 6. `P5-04` 验证记录

- 契约与发布门禁：新增 `contracts/quality/quality-operations-baseline.v1.*`，固定 `offline`、`canary`、`online_feedback` 三类来源，以及 `offline_release`、`canary_promotion`、`online_continuation` 三个递增阶段。引用存在率必须为 `100%`，引用支持率不低于 `95%`，回答可接受率不低于 `85%`，正向反馈率不低于 `80%`，越权、字段泄漏和未授权工具调用均必须为零。
- 身份与失败关闭：窗口固定工作空间、P5-03 运行、数据集、服务、AgentRelease、运行配置及其 `content_hash`、供应商版本、模型、参数摘要、网络区域和时间范围；任何身份漂移传播到三类来源并失败关闭。同一窗口身份幂等，结果漂移返回 `IDEMPOTENCY_CONFLICT`；主窗口原因只聚合当前发布阶段必需来源。
- 证据与状态边界：浏览器或调用方只能选择冻结目标，指标必须由受信 `QualityOperationCollector` 采集；证据正文只计算 SHA-256，不持久化。`core_functional`、`provider_integration`、`ai_quality`、`capacity_certification` 四维状态独立保存；Mock 或全合成证据只能得到 `not_configured/not_run` 和阻断结论。
- 数据库与生命周期：Revision `20260817_0064` 新增 `quality_operation_windows` 和 `quality_operation_source_results` 两张只追加事实表，普通更新与删除由 Trigger 阻断；Repository 强制核对 Service、AgentRelease 和运行配置身份。Registry 升级为 `v12`，两表纳入工作空间隔离导出与受控清除，发布兼容矩阵和历史 Migration head 断言同步到 `0064`。
- 专项验证：P5-04 契约 `7/7`、真实 PostgreSQL `4/4`，生命周期、Migration、P5-04 与阶段 4 发布兼容联合 `27/27`，本地契约、Registry 与注释组合 `29/29`；Ruff Format/Lint、mypy strict `708` 个源文件、Python 注释、架构、契约兼容和 `git diff --check` 均通过。PostgreSQL 测试中的“合成 P5-04 供应商”只验证隔离 Schema 内的配置解析，不代表真实供应商审核。
- 统一门禁：初次在沙箱外原样执行 `./scripts/verify` 时，审批服务在项目代码启动前返回 `502 Bad Gateway`，请求 ID 为 `b029ee67-e8ee-41c0-98f5-6358f2109b31`。P5-07 当前树随后原样通过统一入口，覆盖 P5-04 契约、PostgreSQL、生命周期和历史回归，因此工程阻塞已经解除。
- 验收结论：`blocked`。本地确定性机制、专项门禁和当前树统一入口均已通过，但已审核真实供应商、固定真实模型/参数/网络区域及足够授权样本仍未满足，因此 `P5-04` 不标记完成；本节点没有前端交付，桌面与移动浏览器验收不适用。

## 7. `P5-05` 验证记录

- 契约与计量边界：新增 `contracts/quality/quality-cost-attribution-baseline.v1.*`，固定七类成本组件、来源与组件映射、整数最小货币单位、逐账本条目向上取整、失败与重试纳入和证据正文不持久化规则。同一来源 Attempt 可使用不同 `meter_key` 保存模型输入、输出等多个计量项，重复来源计量和非法重试身份失败关闭。
- 金额与对账：每条账本同时保存平台估算金额、可选供应商报告金额、最终确认金额和金额来源；窗口固定币种、价格版本、价格目录摘要、网络区域与时间范围。供应商账单支持 `matched`、`explained`、`failed`、`not_run`、`not_configured`，非零差异只接受账期、舍入、折扣、抵扣和税费五类原因；合成价格只能得到 `price_verification_status=not_configured`。
- 身份、幂等与隔离：受信采集器只接收冻结目标，Repository 从权威 Service、AgentRelease 和运行配置解析身份；同一窗口身份幂等，结果漂移返回 `IDEMPOTENCY_CONFLICT`，采集器或发布身份漂移失败关闭。跨工作空间查询不可见，证据正文只在 Application 内计算 SHA-256，不进入窗口、账本或聚合事实。
- 数据库与生命周期：Revision `20260817_0065` 新增 `cost_attribution_windows`、`cost_ledger_entries` 和 `cost_attribution_lines` 三张只追加事实表，数据库检查计量单位、金额来源、价格身份、摘要格式和金额范围，普通更新/删除由 Trigger 阻断；Registry 升级为 `v13`，三表纳入工作空间隔离导出与受控清除，发布兼容矩阵和历史 Migration head 断言同步到 `0065`。
- 专项验证：P5-05 契约 `7/7`、真实 PostgreSQL `4/4`；生命周期、Migration 与 P5-05 联合 `15/15`，全部受影响契约、Registry、发布清单和历史 Migration 断言联合 `43/43`；全仓 Ruff Format/Lint、mypy strict `716` 个源文件、Python 注释、契约兼容和 `git diff --check` 通过。生命周期导出验证目标空间窗口/账本/七类聚合分别为 `1/9/7` 行且不含证据正文，受控清除后三表为空；本节点没有前端交付，桌面与移动浏览器验收不适用。
- 统一门禁：初次在沙箱外原样执行 `./scripts/verify` 时，审批服务在项目代码启动前返回 `502 Bad Gateway`，请求 ID 为 `6d82f244-2bd9-4c35-a7bc-3a4e2aba31ea`。P5-07 当前树随后原样通过统一入口，覆盖 P5-05 全部契约、PostgreSQL、生命周期和历史回归，因此工程阻塞已经解除。
- 验收结论：`passed`。成本归因机制、专项门禁和当前树统一入口均已通过；真实价格、供应商账户和供应商账单仍为 `not_configured`，该完成结论不形成真实成本目标结论，也不满足 P5-06 的真实价格依赖。

## 8. `P5-07` 验证记录

- 契约与边界：新增 `contracts/isolation/workspace-isolation-baseline.v1.*`，冻结 L1～L4、四类套餐上限、合规状态、迁移状态机、包含 `rollback_required` 的活动计划集合和服务端路由规则。Identity 继续拥有工作空间与套餐写入权，调用方只能请求目标等级，不能提交合规结论或覆盖路由键。
- 策略与路由：无已完成路由时确定性解析为 L1；个人空间越级、未知套餐、套餐版本漂移、跨工作空间计划、非法状态转换和恢复未收口时的第二计划全部失败关闭。L2 仍使用共享数据库、对象存储和密钥路由，搜索命名空间由服务端按工作空间精确生成；P5-08/P5-10 完成前，应用与数据库均拒绝 L3/L4 路由激活。
- 数据库与生命周期：Revision `20260817_0066` 新增 `workspace_isolation_policy_versions`、`workspace_isolation_migration_plans` 和 `workspace_isolation_route_versions`。策略与路由只追加，迁移状态、版本、活动唯一性和历史删除由 Trigger/约束保护；Registry `v14` 将三表列为可导出且普通业务清除不删除的治理事实。ADR-012 固定模块写入权、单一权威路由和 P5-08/P5-10 后置边界。
- 专项验证：P5-07 契约、真实 PostgreSQL、生命周期和 Migration 联合 `20/20`，阶段 4 历史 Revision、ReleaseManifest 与 Registry 受影响回归 `21/21`。数据库反例覆盖未批准路由、错误 L2 工作空间命名空间、直接删除迁移历史、非法跳转、策略/路由改写、套餐漂移、跨空间和提前 L3 激活。
- 统一门禁：原样 `./scripts/verify` 通过，包含全历史 Secret Scanner、OpenAPI/生成类型、权限 Registry、SBOM/许可证、ReleaseManifest、开发供应链、架构、注释、UnoCSS、契约兼容、React `58/58`、生产构建、Ruff、mypy strict `729` 个源文件和 Python `892/892`。本节点没有前端交付，桌面与移动浏览器验收不适用。
- 验收结论：`passed`。P5-07 的资格、版本、迁移计划和 L1/L2 路由失败关闭已完成；真实 L3/L4 客户环境、生产 KMS/Vault、对象存储和恢复继续为 `not_configured/not_run`，由 P5-08、P5-10 独立验收。

## 9. `P5-08` 验证记录

- 契约与写入权：新增 `contracts/isolation/l3-isolation-migration-baseline.v1.*`，冻结独立数据库、独立 Bucket、独立密钥、六类有序检查点和 `source → target` 单一写入切换。浏览器、模型输出和普通业务模块不能提交资源连接、凭证、密钥材料、路由键或通过结论；目标写入在路由原子提交前保持关闭，不建立长期双写。
- 资源档案与状态编排：新增 `L3IsolationMigrationService` 和受信 `L3MigrationExecutor` 端口。控制面先提交 `executing`，物理执行器在短事务外执行复制与恢复；资源档案只保存服务端路由键、数据库/Bucket 身份摘要、独立密钥指纹和配置摘要。源/目标密钥指纹相同、执行器覆盖路由、证据结构漂移或控制面提交冲突均失败关闭并进入 `rollback_required`。
- 六项检查点：固定 `source_snapshot`、`database_copy`、`object_copy`、`derived_index_rebuild`、`backup_restore` 和 `deletion_propagation`。派生索引由目标事实重新生成，备份恢复到第三个干净数据库和 Bucket 后比较；六项必须全部为 `passed` 且源/目标摘要一致，数据库才允许 L3 路由与计划 `completed` 原子提交。
- 失败恢复与越权边界：外部清理前先确认待回滚计划属于可信工作空间，跨空间恢复不会先产生副作用。失败和恢复尝试只追加，只有源端仍权威且目标禁写时才能进入 `rolled_back`；资源档案、检查点和恢复事实的普通更新/删除均由 Trigger 拒绝。
- 真实基础设施适配：`PostgresMinioL3MigrationExecutor` 使用显式工作空间表清单和有界快照，在三个真实独立临时 PostgreSQL 数据库、三个独立 MinIO Bucket 和两份不同的 32 字节合成密钥上迁移全合成高合规工作空间。目标数据库只含目标空间两条事实，不含另一个合成空间；目标 Bucket 只含目标对象，删除墓碑对象保持不存在；派生索引重建、第三资源恢复和独立密钥指纹均通过。
- 数据库、生命周期与架构：Revision `20260817_0067` 新增 `l3_isolation_resource_profiles`、`l3_isolation_migration_checkpoints` 和 `l3_isolation_recovery_records`，并把 L3 路由 Trigger 从“全部拒绝”收紧为“仅完整证据允许”，L4 继续拒绝。Registry 升级为 `v15` 并把三表列为可导出、普通业务清除不删除的治理事实；ADR-013 固定事务外迁移、第三资源恢复、单写与回滚协议。
- 专项与联合验证：P5-08 契约和真实 PostgreSQL/MinIO 用例 `4/4`；P5-07/P5-08、Lifecycle 与 Migration 联合 `23/23`；全部受 Revision、Registry、ReleaseManifest 和历史 head 影响的回归 `41/41`。数据库反例覆盖缺档案提前路由、错资源路由、检查点失败、跨空间激活/恢复、档案改写和检查点/恢复历史删除。
- 统一门禁：在沙箱外原样执行 `./scripts/verify`，全历史 Secret Scanner、OpenAPI/生成类型、权限 Registry、SBOM/许可证、ReleaseManifest、开发供应链、架构、注释、UnoCSS、契约兼容、React `58/58`、生产构建、Ruff、mypy strict `735` 个源文件和 Python `896/896` 全部通过。本节点没有前端交付，桌面与移动浏览器验收不适用。
- 验收结论：`passed`。该结论只覆盖本地全合成 L3 机制与真实本机 PostgreSQL/MinIO 资源；真实客户环境、生产 KMS/Vault、生产对象存储、真实法规审核和容量认证继续为 `not_configured/not_run`，不得由本地结果替代。

## 10. 当前限制与下一步

- `P5-04` 本地机制和当前树统一门禁已通过，但真实质量结论仍缺少已审核真实供应商、固定模型/参数/网络区域和足够授权样本，因此节点保持受阻。
- `P5-04` 的真实质量结论仍需要已审核真实供应商、固定模型/参数/网络区域/窗口和达到下限的授权样本；未配置前不能标记真实质量目标通过。
- 真实线上反馈和真实模型质量仍为 `not_run`；本地全合成样本只证明采集、版本与隔离机制，不提供真实模型质量结论。
- 真实模型质量与成本节点不能仅靠代码完成。需要用户或项目方提供经过审核的供应商配置、数据政策、固定模型与价格版本，并积累足够的合成/真实授权样本后再验收。
- `P5-05` 机制、专项验收和当前树统一门禁已通过；真实价格与供应商账单继续保持 `not_configured`，P5-06 继续等待真实价格依赖。
- P5-08 本地合成 L3 迁移与恢复已完成；真实客户环境、生产密钥系统、生产对象存储、证书与监管验收继续单独记录。
- 当前进入 `P5-09`，适用法域、法规策略和法律保留要求仍为 `not_configured`；未配置时只能建设并验证失败关闭机制，不能形成真实法规合规结论。

## 11. 阶段结论

`not_run`。`P5-01`～`P5-03`、`P5-05`、`P5-07` 和 `P5-08` 已完成，`P5-04` 因真实供应商与样本依赖保持受阻，`P5-06` 等待真实价格，当前推进 `P5-09`，其余节点尚未完成。只有 `P5-01`～`P5-13` 全部完成、必需真实质量与成本门禁具有合法输入、统一门禁与联合验收通过、ReleaseManifest 可追溯并创建 `stage-5-complete` 标签后，阶段 5 才能关闭。
