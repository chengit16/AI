# 阶段 5 验证报告

## 1. 当前结论

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 5：质量运营、合规与规模化 |
| 当前节点 | `P5-13` 阶段联合验收前置审计（`P5-04`、`P5-06` 等待真实输入） |
| 节点状态 | 受阻 |
| 阶段结论 | `not_run` |
| 报告日期 | 2026-08-19 |

阶段 5 的计划、入口审计、质量样本闭环、六层评估归因、成本归因机制、四级隔离控制面、本地合成 L3 迁移恢复、法规失败关闭、L4 独立实例交付、服务演进触发审计和五域治理控制台已经完成。`P5-04` 本地质量运营机制已在 `e821801` 提交，真实 Responses 供应商已经启用、政策批准并通过能力探测，但固定运行配置、网络区域、窗口和足够授权样本尚未形成，因此保持受阻；`P5-05` 已在 `eaa58fa` 提交，真实价格与供应商账单继续保持 `not_configured`。`P5-07`～`P5-11` 已分别在 `06a7e25`、`bd54bae`、`44b6ede`、`36838d8`、`fe0a946` 提交；`P5-12` 已通过最终镜像、13 项 doctor、桌面/移动浏览器和统一门禁，当前进入 `P5-13` 前置审计。真实质量、价格、容量、法域和 L3/L4 客户目标环境继续按实际状态独立门禁，不能因本地机制或单次能力探测通过而自动判定真实质量或生产适配通过。

## 2. 入口条件审计

| 条件 | 状态 | 当前证据与处理 |
| --- | --- | --- |
| 阶段 2 可靠性与数据治理 | `passed` | `stage-2-complete` 已存在，复用任务、数据生命周期、观测和故障演练基线 |
| 阶段 4 受控工具执行 | `passed` | `stage-4-complete` 指向 `add5219`；P4-13 十五场景、前后两轮 13 项诊断、最终镜像、双视口浏览器、`0.4.0` ReleaseManifest 和统一门禁通过 |
| 真实模型供应商审核 | `configured` | Responses 供应商已启用、政策已批准且能力探测通过；该结论不包含质量、限流、价格或账单验收 |
| 真实线上质量与反馈样本 | `not_run` | 尚无满足固定模型、参数、网络区域、窗口和样本量要求的数据 |
| 真实价格与供应商账单 | `not_configured` | 现有价格和用量事实可验证机制，不能给出真实成本目标结论 |
| 容量认证 | `not_run` | 缺少独立压测环境，不进入本地功能结论 |
| 镜像扫描 | `not_configured` | 开发级供应链可继续验证，正式发布门禁保持阻断 |
| L3/L4 目标环境 | `not_configured` | 可建设合成迁移和独立实例工具，不能声称客户生产环境通过 |
| 法域、法规策略与法律保留要求 | `not_configured` | 本地失败关闭机制已完成；真实适用政策、法务复核与监管结论仍不能自动推断 |
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
- 验收结论：`blocked`。本地确定性机制、专项门禁和当前树统一入口均已通过；该次机制验收时真实供应商、固定真实模型/参数/网络区域及足够授权样本尚未满足。2026-08-19 已追加供应商启用、政策批准和能力探测证据，但固定参数/网络区域/窗口与足够授权样本仍未满足，因此 `P5-04` 继续不标记完成。

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

## 10. `P5-09` 验证记录

- 契约与生产默认：新增 `contracts/lifecycle/regulatory-compliance-baseline.v1.*` 和 ADR-014，冻结版本化法域策略、四类保留期、外部审核状态、工作空间级法律保留/解除和三类生命周期裁决。生产组合根使用 `UnconfiguredRegulatoryPolicySource`，在没有受审核配置时始终返回 `not_configured`；测试合成策略显式标记为 `SYNTHETIC-P5-09`，不代表真实法规审核。
- 权限与读取边界：新增策略发布、保留创建、保留解除和证明读取四项独立权限及四个 HTTP API。法律保留不进入知识、问答或导出读取策略，不能扩大原主体权限；无导出权限仍被拒绝，已有导出权限在保留期间继续生效并生成低敏证明。
- 生命周期裁决：未配置法域时 `purge` 与 `retention` 在任何删除副作用前失败关闭，授权导出仍执行并记录 `not_configured`。活动保留阻止清除和保留期运行；解除后旧幂等键仍绑定原阻断证明，只有新键可以重新裁决。同一工作空间的保留激活与破坏性操作由 advisory transaction lock 和数据库 Trigger 串行化。
- 数据库与迁移：Revision `20260817_0068` 新增策略、保留、解除和证明四张不可变治理表，把证明外键接入导出、清除和保留期事实；普通更新/删除、缺少匹配允许证明、未配置策略激活保留及活动保留下的破坏性插入均由数据库拒绝。生命周期 Registry `v16` 将四表列为 `retained` 且不导出、不普通清除；权限 Registry `v23` 新增四权限、四 API、四菜单和四绑定。
- 非空升级与降级：已有工作空间从 `0067` 升级后保留 Registry 22 原菜单并追加 Registry 23 快照，系统 Owner 获得四项权限，数据库注册四条绑定；无新增事实时可精确恢复原发布。存在法规治理事实、自定义法规授权或 P5-09 后菜单发布时，降级失败关闭，避免静默丢失事实。
- 专项与联合验证：P5-09 契约、Resource Registry 和应用装配 `18/18`；P5-09 PostgreSQL `6/6`，生命周期、空 Schema Migration、P4-02/P4-03/P4-10/P4-11 历史 Revision、ReleaseManifest 与 Registry 联合回归 `47/47`。并发回归发现并修复裁决证明与随机运行 ID 脱钩问题，导出、清除和保留期现统一复用证明冻结的获胜操作身份。
- 静态门禁：Ruff Format/Lint 全仓 `742` 个文件、mypy strict `742` 个源文件、Python 注释、架构、OpenAPI/生成类型、权限 Registry、契约兼容、ReleaseManifest 和开发发布门禁均通过。
- 统一门禁：沙箱外原样执行 `./scripts/verify` 通过，React `58/58`、Python `905/905`、Ruff、mypy strict `742` 个源文件、架构、注释、契约、Registry、供应链、ReleaseManifest、生产构建和全部工程检查通过。本节点没有前端页面交付，桌面与移动浏览器验收不适用。
- 当前结论：`passed`。本地机制、专项联合回归和统一门禁已通过；真实适用法域、外部法务复核、监管认证和生产保留要求保持 `not_configured/not_run`，不形成真实法规合规结论。独立提交 SHA 在本节点提交后按交付规则回填。

## 11. `P5-10` 验证记录

- 契约与部署档案：新增 `contracts/deployment/private-instance-baseline.v1.*`，冻结独立实例、七类必需服务、安装/升级/回滚/恢复、升级前备份和恢复包回滚。应用恢复密钥只加密进入 `.aiprb`，恢复包加密密钥必须包外保管且不能进入 ReleaseManifest。L4 继续复用现有 Compose、OpenAPI、权限、审计、Alembic、ReleaseManifest 和本地恢复工具，不引入私有分支。
- 配置与离线边界：新增 `InstanceConfig` 失败关闭校验和中文交付手册。`synthetic_local` 固定本地 PostgreSQL、MinIO、文件密钥与 `offline_capable`；生产私有环境必须显式配置外部 PostgreSQL/S3、KMS/Vault、证书、客户环境和联网边界。真实模型供应商、客户环境、生产 KMS/Vault、证书、对象存储、镜像扫描和容量继续为 `not_configured/not_run`。
- 生命周期与版本安全：不可变状态机覆盖 `install → upgrade → rollback → restore`，禁止重复安装、非递增升级、无升级前快照回滚、携带密钥材料的恢复包和把新版本恢复包导入旧程序。`./platform validate-instance` 校验档案与 Compose，`./platform accept-stage-5-private-instance` 生成不含业务正文、凭证和密钥的低敏证据。
- 实例升级与诊断：修正阶段 4 doctor 将 `20260816_0061` 写死的问题，现从当前代码树解析唯一 Alembic head，多个 head 或无法解析时失败关闭。当前公共本地实例重建 API、Web、Migration、Tika、Worker 和 Scheduler 镜像，并从 `0061` 实际升级到 `20260817_0068`；13 项 doctor 全部通过。
- 真实备份恢复：在对象引用 `referenced=10, missing=0` 后创建 Revision `0068` 的加密恢复包并取得对象物理快照；恢复流程先创建操作前回退包，再停止写入、验证/解包、替换 PostgreSQL/MinIO/文件密钥、清空可重建 Valkey、执行 Migration 和对象检查。恢复后对象引用仍为 `10/10`，Revision `0068` 和 13 项 doctor 全部通过。
- 专项与统一门禁：P5-10 契约、配置和四步生命周期 `6/6`，与 Worker 运行边界联合 `10/10`；Ruff Format/Lint 和 mypy strict 通过。沙箱外原样执行 `./scripts/verify`，React `58/58`、Python `911/911`、Ruff、mypy strict `744` 个源文件、注释、架构、契约、Registry、供应链、ReleaseManifest、生产构建和全部工程检查通过。本节点没有前端页面交付，桌面与移动浏览器验收不适用。
- 验收结论：`passed`。结论仅覆盖当前 macOS Docker、本地全合成实例和真实本机 PostgreSQL/MinIO 恢复；不形成真实客户私有化、生产密钥/证书、Linux、镜像漏洞或容量通过结论。独立提交为 `36838d8`。

## 12. `P5-11` 验证记录

- 契约与决策：新增 `contracts/architecture/service-evolution-audit.v1.*` 和 ADR-015，按设计文档 17.8 节完整冻结五项服务拆分条件与四项 Go 条件。审计成功与迁移授权分开表达；当前决策为 `retain_modular_monolith`，原因码为 `no_verified_trigger`。
- 当前触发事实：独立扩缩容、单体瓶颈、SSE 5,000 持续连接和网关 CPU/内存/P95 四项为 `not_run`；团队所有权/发布节奏、目标故障域、客户合规独立部署、Runtime 独立扩缩容/故障隔离和 Go 负责人/运维窗口五项为 `not_configured`。九项均无 `triggered`，基准报告状态为 `not_run`；该结论不表示生产容量或性能通过。
- 失败关闭：任何 `triggered` 必须引用可复核证据；容量未通过时不能声明扩缩容或 SSE 条件触发，生产负载画像未配置时不能声明性能主瓶颈触发。启动 Go 必须同时具有已验证的服务拆分条件、Go 条件和 `passed` 可归因基准报告。
- 实现与写入权：新增 `./platform accept-stage-5-service-evolution` 生成只含状态与计数的低敏证据。当前仓库不存在 `services/edge-gateway/`、`services/agent-runtime-go/` 或 Go Runtime，Python 继续保持单一写入权；未来门禁固定 ADR、共享契约、影子流量结果对比、安全与工作空间隔离回归、单写切换、Python 路由回滚和禁止长期双写。
- 专项验证：P5-11 Schema、九项身份、未知状态、触发证据、Go 基准、目录门禁和低敏证据专项 `7/7`；与 P5-10 契约和 Worker 运行边界联合 `17/17`；原样 `./scripts/verify` 通过，React `58/58`、Python `918/918`、Ruff、mypy strict `746` 个源文件、注释、架构、契约、Registry、供应链、ReleaseManifest、生产构建和全部工程检查通过。
- 验收结论：`passed`。P5-11 已形成可复核的不拆分决策和未来迁移/回滚门禁，没有增加第二套后端运行时；容量、生产负载画像、团队所有权和 Go 运维能力继续保持 `not_run/not_configured`。独立提交为 `fe0a946`。

## 13. `P5-12` 验证记录

- 契约与状态保真：新增 `contracts/operations/control-tower-baseline.v1.*`，固定质量、成本、隔离、法规和私有实例五个治理分区。所有分区保持 `blocked`，真实供应商、价格、账单、法域、客户环境、生产 KMS/Vault、证书、对象存储、镜像扫描和容量继续原样显示为 `not_configured/not_run`，没有由本地结果重解释为通过。
- 服务端边界：新增工作空间级 `GET /api/v1/workspaces/{workspace_id}/operations/workbench/control-tower` 和 `operations.control_tower.read`。Application 重新核对可信工作空间、授权空间和精确权限；跨空间或其他运营权限不能读取。API 只投影冻结的低敏状态、原因码与摘要，不返回业务正文、凭证、密钥或外部连接信息。
- 前端与危险操作：新增 `/workspace/control-tower` 页面，五个分区统一显示来源状态和阻断原因。预算恢复、隔离迁移和法律保留三类危险操作只显示专用权限、二次确认和后端重新授权要求，不提供执行或授权绕过入口。
- Migration 与发布：Revision `20260817_0069` 为既有系统 Owner 授予控制台读取权，注册唯一只读 API 绑定，并从当前菜单快照追加 Registry `v24` 发布。无自定义授权、无后续发布时可安全恢复 Registry `v23`；存在管理员授权或后续菜单发布时拒绝降级。API 最终镜像补入 `contracts/operations`，ReleaseManifest、兼容矩阵、OpenAPI、React/Python 生成类型和 Registry 产物保持一致。
- 专项与联合验证：P5-12 契约、状态保真和空间隔离 `2/2`，Migration、菜单配置/发布、Registry 与 ReleaseManifest 联合 `22/22`；修正 P5-09 历史测试在 `upgrade head` 后仍期望 `0068` 的基线漂移后，P5-09/P5-12 Migration、契约、Registry 与 ReleaseManifest 联合回归 `28/28` 通过。`git diff --check`、Ruff、mypy strict、架构、注释、契约生成、Registry 和生产构建均通过。
- 容器与浏览器：最终 API、Web、Migration、Worker 与 Scheduler 镜像已重建，`http://127.0.0.1:3000/status` 可用，数据库 Revision 为 `20260817_0069`，13 项 `./platform doctor` 全部通过。`1440×900` 与 `390×844` 浏览器验收的页面级 `scrollWidth` 分别为 `1440` 与 `390`，无横向溢出，控制台 warning/error 均为空；五个治理分区、`blocked/not_configured/not_run`、三项危险操作的二次确认/后端重授权均可见，页面未展示凭证、密钥或外部连接信息。
- 最终统一门禁：第一次原样执行 `./scripts/verify` 时仅 P5-09 历史 Head 断言失败，其余 `919/920` 通过；修正断言并通过受影响联合回归后再次原样执行，React `58/58`、Python `920/920`、Ruff、mypy strict `750` 个源文件、注释、架构、OpenAPI/生成类型、权限 Registry、供应链、ReleaseManifest、契约兼容和生产构建全部通过。
- 验收结论：`passed`。P5-12 的菜单/API/字段权限、未配置状态可见性、危险操作提示、双视口体验和低敏边界均已完成；该结论不改变 P5-04/P5-06 的真实输入阻塞，也不形成生产质量、成本、法规、私有化或容量通过结论。独立提交为 `e2ac430`。

## 14. `P5-13` 前置审计记录

- 提交追溯：P5-01 计划基线为 `9088801`、入口完成提交为 `9647339`；P5-02～P5-05 为 `f299d1a`、`f006c52`、`e821801`、`eaa58fa`，P5-07～P5-12 为 `06a7e25`、`bd54bae`、`44b6ede`、`36838d8`、`fe0a946`、`e2ac430`。上述提交均可解析且按历史顺序位于当前分支；P5-06 没有实现提交，不能伪造为已完成。
- 运行与发布基础：干净提交 `e2ac430` 上再次执行 `./platform doctor`，Web、API、MinIO、Tika、PostgreSQL、Revision `20260817_0069`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项全部通过。通用 `contracts/fixtures/release-manifest.v1.valid.json` 生成漂移检查通过。
- 关闭制品：仓库只存在阶段 1～4 的正式本地 ReleaseManifest，不存在阶段 5 ReleaseManifest；`stage-5-complete` 标签不存在。这是当前正确状态，不以开发 Fixture 或 P5-12 镜像摘要冒充阶段关闭制品。
- 外部门禁：`P5-04` 已具备启用、政策批准且能力探测通过的真实 Responses 供应商，但仍缺固定参数/网络区域/窗口和足够授权样本；`P5-06` 仍缺已审核真实价格，且预算、异常用量、成本预测和自动降级节点尚未实现。控制台的阶段基线仍显示 `real_provider_not_configured`、`real_quality_samples_not_run`、`price_catalog_not_configured` 和 `supplier_statement_not_configured`，后续须由新验收事实驱动更新，不能手工改写为通过。
- 真实供应商接入准备：针对 Codex 类中转补充 `responses` 线协议事实、`/responses` 固定端点、请求/响应/Usage 解析和前端协议选择；旧供应商由 Revision `20260818_0070` 回填为 `chat_completions`。域名白名单继续失败关闭，本地 CC Switch/Clash Fake-IP 只允许显式配置 `198.18.0.0/15` 或其子网，且非 `local/test` 环境拒绝启动；RFC1918、回环、链路本地、其他保留地址、未登记域名、非 443 端口和重定向仍被拒绝。
- 当前验证事实：Fake-IP 红灯复现最初为 `2 failed`，实现后专项 `2/2`、供应商配置 `7/7`、模型网关联合 `22/22`、React `59/59`、Ruff、mypy 定向检查、Web TypeScript、契约生成漂移和 Compose 配置解析通过。统一入口的 Secret Scanner、契约/权限/供应链/注释/架构、前端构建与测试、Ruff 及 mypy strict `751` 个源文件均通过；全量 pytest 在沙箱内得到 `691 passed`，另有 `226 errors` 与 8 个基础设施失败，首个错误明确为访问 `127.0.0.1:5432` 时 `Operation not permitted`，同组 PostgreSQL、MinIO、Tika 和 Valkey 均被沙箱网络隔离。沙箱外原样重跑和真实 DNS 验证申请又被审批服务自身访问 `https://ai.input.im/responses` 的 `503 Service Unavailable` 拒绝，因此没有形成本轮沙箱外统一门禁或真实连通性、协议、限流、质量和价格通过结论。
- 2026-08-19 续验：当前提交 `c91b42b` 的工作树干净，`./platform doctor` 再次确认 Web、API、MinIO、Tika、PostgreSQL、Revision `20260818_0070`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项全部通过。原样执行 `./scripts/verify` 时，Secret Scanner、契约、Registry、SBOM、许可证、ReleaseManifest、供应链、注释、架构、React `59/59`、生产构建、Ruff 和 mypy strict `751` 个源文件均通过；全量 pytest 为 `691 passed`，另有 `226 errors` 与 `8 failed`，首个错误仍为沙箱访问 `127.0.0.1:5432` 时 `Operation not permitted`，MinIO、Tika 和 Valkey 同样受本机网络隔离。已确认目标账号存在且为 `active`，但仓库唯一提权入口 `./platform admin grant <login_name>` 在命令启动前被外部审批服务访问 `https://ai.input.im/responses` 的 `502 Bad Gateway` 拒绝；内置浏览器访问本地模型治理页也被 URL 安全策略拒绝。为避免绕过安全边界，本轮不改用数据库直写或其他浏览器通道，平台管理员生效、供应商创建、数据政策审核和能力探测仍等待用户在本机终端及页面完成。
- 管理员授权续验：用户随后在本机仓库根目录通过唯一提权入口执行授权，命令明确返回目标合成账号 `-> active`，平台管理员外部门禁已满足。内置浏览器 URL 安全策略仍不允许代理操作本地模型治理页，且 API Key 不应经对话、日志或 Git 传递；因此供应商创建、真实数据政策审核和能力探测继续由用户在本地页面完成，管理员授权成功本身不等同于供应商审核或探测通过。
- 管理员账号切换：用户指定另一个既有账号作为后续平台管理员，并在本机通过仓库唯一提权入口确认返回 `-> active`；个人登录名不写入报告。旧合成管理员暂不撤销，等待新账号实际登录并访问模型治理页通过后再收窄权限，避免管理入口被同时撤销。
- 真实供应商页面验收：用户提供的新管理员本地页面截图显示供应商标识 `codex`、Base URL `https://ai.input.im`、调用协议 `Responses`、探测模型 `gpt-5.6-sol`、声明能力“生成/流式输出”，治理状态为“已启用/已批准/探测通过”。截图不包含 API Key；该证据满足管理员访问、供应商创建、政策状态和能力探测事实，但不替代固定运行参数、足量质量样本、限流、价格或账单验收。旧合成管理员撤权命令再次在启动前被外部审批服务 `503 Service Unavailable` 拒绝，等待用户在本机执行。
- 运行配置核对：2026-08-19 尝试通过只读 PostgreSQL 查询确认当前发布配置是否已经绑定真实供应商，命令在访问 Docker 或数据库前被外部审批服务 `502 Bad Gateway` 拒绝；本次截图只覆盖供应商列表，因此当前运行配置身份、参数和发布状态仍未形成可审计证据。未改用数据库直写、未读取 Prompt 正文或凭证，也未绕过浏览器和后端权限边界。
- 运行配置详情续建：提交 `459a031` 为运行配置表补充只读详情入口，展示配置/Prompt 摘要、供应商、模型、能力、价格、网关策略和组件版本；系统 Prompt 正文与凭证不进入详情，零价格路由明确提示不能作为成本验收证据。React `60/60`、TypeScript、ESLint、Prettier、生产构建、前端架构与注释检查通过，最终镜像重建成功且 Revision `20260818_0070` 的 13 项 doctor 全部通过。原样 `./scripts/verify` 在项目命令启动前被外部审批服务 `502 Bad Gateway` 拒绝；桌面/移动浏览器仍等待用户在本地页面验收，因此该续建不作为 P5-04 完成或阶段关闭证据。
- 运行配置读取与小屏导航修复：用户创建运行配置后，列表返回内部错误并显示 `0` 条；读取 API 容器日志的授权命令仍在进入 Docker 前被外部审批服务 `502 Bad Gateway` 拒绝。最小仓储回归随后稳定复现 `RuntimeComponentVersions.__init__()` 因历史 `component_versions` 缺少三个后置接口字段而失败，Adapter 现仅为缺失字段补入保真的 `legacy-unversioned`，现有版本优先且其他损坏数据继续失败关闭；相关运行配置测试 `5/5` 通过。桌面侧栏中间导航区补入 `min-h-0 flex-1 overflow-y-auto overscroll-contain`，真实应用壳层测试先红后绿，React 全量 `60/60` 通过。Ruff 全仓、mypy strict `752` 个源文件、前后端注释、架构、UnoCSS、Web TypeScript/ESLint/Prettier 和生产构建均通过；新 API/Web 镜像重建成功，Revision `20260818_0070` 的 13 项 doctor 全部通过，代码提交为 `459a031`。最终原样 `./scripts/verify` 的沙箱外授权仍在项目命令启动前被外部审批服务 `503 Service Unavailable` 拒绝，因此本轮不声明统一门禁通过。应用内浏览器只能到达未登录页且 Chrome 连接不可用，登录后的运行配置列表与桌面/小屏交互验收仍待用户刷新页面确认。
- `current` 读取兼容续修：用户刷新后截图确认 `GET .../ai-runtime-configs/current` 仍返回 `INTERNAL_ERROR`，说明 `459a031` 只补三个后置字段的兼容范围不足。按截图请求 ID 读取 API 日志的授权再次在进入 Docker 前被外部审批服务 `503 Service Unavailable` 拒绝；随后以 `get_configuration()` 真实读取路径建立稀疏快照与废弃字段最小回归，旧实现稳定抛出 `unexpected keyword argument`。提交 `cd35b31` 现只投影 `RuntimeComponentVersions` 当前十个字段，全部缺失字段明确标记为 `legacy-unversioned`，历史额外键不进入当前领域对象，已知字段类型或空值损坏仍失败关闭；运行配置联合测试 `6/6`、Ruff、mypy strict `752` 个源文件、Python 注释、架构与仓库策略通过。提交 `7a44136` 进一步隔离列表与当前指针故障：列表成功时始终展示已创建配置，当前指针失败只显示可重试警告，并允许重新发布目标版本恢复指针；页面级回归先红后绿，React 全量 `61/61`、TypeScript、ESLint、Prettier、生产构建、前端注释、架构与 UnoCSS 均通过。API/Web 最终镜像再次重建，Revision `20260818_0070` 的 13 项 doctor 全部通过；原样 `./scripts/verify` 仍在项目命令启动前被同一外部 `503` 拒绝，登录后页面恢复结果等待用户对新镜像强制刷新确认。
- 当前配置与上传边界续验：用户已在本地页面确认目标运行配置成为“当前”。随后知识文档上传截图记录请求体 `3508986` 字节并由 `nginx/1.29.1` 直接返回 `413 Request Entity Too Large`；仓库配置确认 API 默认允许 `20 MiB`、最高可配置 `100 MiB`，但 Nginx 没有覆盖默认 `1 MiB` 请求体限制。提交 `7b69cf0` 仅为首版和新版本文档上传路由设置 `101 MiB` 代理边界，为最大文件预留 multipart 元数据空间，其他 API 不放宽，实际文件上限、类型识别和安全扫描仍由 API 失败关闭。代理回归先红后绿 `1/1`，上传安全与服务 `8/8`、工程联合 `21/21`、Ruff、格式、注释和 Diff 检查通过；新 Web 镜像启动成功且 Revision `20260818_0070` 的 13 项 doctor 全部通过。统一门禁的 Secret Scanner、契约、Registry、供应链、注释、架构、React `61/61`、生产构建、Ruff、mypy strict `753` 个源文件及非集成 Python `694` 项通过；PostgreSQL、MinIO、Tika 和 Valkey 集成因沙箱访问本机端口时 `Operation not permitted` 未执行成功，沙箱外重跑申请又被外部审批服务 `503 Service Unavailable` 拒绝。HTTP 重放同样受本机网络沙箱与审批服务 `502` 限制，修复后的登录态页面上传结果等待用户重试确认。
- 审计结论：`blocked`。本地实现、统一门禁、Migration、L3/L4 合成恢复、浏览器和运行诊断已经具备后续联合验收基础，但“必需节点全部完成”条件不成立。依赖满足前不运行通过态 P5-13 联合验收、不生成阶段 5 ReleaseManifest、不形成关闭提交，也不创建 `stage-5-complete` 标签。

## 15. 当前限制与下一步

- `P5-04` 本地机制和当前树统一门禁已通过；真实 Responses 供应商已启用、政策已批准且能力探测通过。真实质量结论仍缺少固定参数/网络区域/窗口和足够授权样本，因此节点保持受阻。
- 下一项可执行交接为基于已启用供应商创建不可变运行配置，固定模型、参数和网络区域，再以经过授权的固定数据集形成达到下限的离线、灰度和线上反馈样本；API Key 继续只由服务端加密边界使用，不进入对话、日志、文档或 Git。
- `P5-04` 的真实质量结论已具备审核通过的真实供应商，仍需要固定模型参数、网络区域、窗口和达到下限的授权样本；这些运行事实形成前不能标记真实质量目标通过。
- 真实线上反馈和真实模型质量仍为 `not_run`；本地全合成样本只证明采集、版本与隔离机制，不提供真实模型质量结论。
- 真实模型质量与成本节点不能仅靠代码完成。需要用户或项目方提供经过审核的供应商配置、数据政策、固定模型与价格版本，并积累足够的合成/真实授权样本后再验收。
- `P5-05` 机制、专项验收和当前树统一门禁已通过；真实价格与供应商账单继续保持 `not_configured`，P5-06 继续等待真实价格依赖。
- P5-08 本地合成 L3 迁移与恢复已完成；真实客户环境、生产密钥系统、生产对象存储、证书与监管验收继续单独记录。
- P5-09 本地失败关闭机制、专项门禁和统一入口均已通过；真实适用法域、法务复核、监管认证和生产保留要求仍为 `not_configured/not_run`，不能形成真实法规合规结论。
- P5-10 本地独立实例档案、升级、加密恢复和统一入口均已通过；真实客户环境、生产 KMS/Vault、证书、对象存储、Linux、镜像扫描和容量仍分别为 `not_configured/not_run`。
- P5-11 九项拆分与 Go 触发审计均无已验证触发项，当前保留模块化单体；`not_run/not_configured` 不是容量或性能通过结论。
- P5-12 已完成五域只读治理控制台并原样展示所有外部阻断状态。P5-13 前置审计已因 P5-04/P5-06 依赖受阻；两者完成前，不得把联合验收标记通过、生成阶段关闭提交或创建 `stage-5-complete` 标签。

## 16. 阶段结论

`not_run`。`P5-01`～`P5-03`、`P5-05`、`P5-07`～`P5-12` 已完成，`P5-04` 因固定运行事实与足量授权样本缺失保持受阻，`P5-06` 等待真实价格，`P5-13` 前置审计因此为 `blocked`。只有 `P5-01`～`P5-13` 全部完成、必需真实质量与成本门禁具有合法输入、统一门禁与联合验收通过、ReleaseManifest 可追溯并创建 `stage-5-complete` 标签后，阶段 5 才能关闭。
