# 发布清单契约

## 1. 模块接口

`ReleaseManifest` 用一个不可变文档描述 Web、API、Worker、契约、数据库 Revision、运行时和镜像的发布组合。启动器、升级器和未来发布工具只需要调用同一兼容性检查，不自行复制版本判断规则。

模块对外只有两个核心能力：

1. 从构建系统提供的不可变元数据生成排序稳定、带自摘要的清单。
2. 使用版本化兼容矩阵校验完整组合，并一次返回所有不兼容原因。

## 2. 事实文件

| 文件 | 职责 |
| --- | --- |
| `release-manifest.v1.schema.json` | 发布清单的跨语言传输契约 |
| `compatibility-matrix.v1.schema.json` | 兼容矩阵 Schema |
| `compatibility-matrix.v1.json` | 当前允许的组件、数据库、运行时与镜像组合 |
| `../fixtures/release-manifest-input.v1.valid.json` | 全合成构建输入，用于验证生成器 |
| `../fixtures/release-manifest.v1.valid.json` | 由合成输入生成的 Golden Manifest |

当前默认组合固定 Valkey `8.1.5-alpine` 的多架构清单摘要，覆盖 `linux/amd64` 和 `linux/arm64`。阶段 0 使用 Redis 7.4 的历史结论只保留在阶段报告与 `ADR-001`，不进入当前发布组合。

## 3. 生成与漂移检查

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs contracts/fixtures/release-manifest-input.v1.valid.json \
  --output contracts/fixtures/release-manifest.v1.valid.json

.venv/bin/python scripts/generate_release_manifest.py \
  --inputs contracts/fixtures/release-manifest-input.v1.valid.json \
  --output contracts/fixtures/release-manifest.v1.valid.json \
  --check
```

真实构建必须显式提供每个源码快照和镜像的 `sha256` 摘要。生成器允许镜像引用包含可读标签，但摘要始终是兼容检查和发布追溯的事实，不能用 `latest` 等可变标签替代。

## 4. 失败规则

- Manifest Schema、兼容矩阵版本或自摘要不一致时拒绝。
- 必需组件或镜像缺失时拒绝。
- 组件 SemVer 超出 `[minimum_version, maximum_exclusive_version)` 时拒绝。
- 数据库 Revision 不在允许清单时拒绝。
- Node.js 或 Python 主次版本不符合矩阵时拒绝。
- 所有原因一次返回，调用方不得只修复第一个问题后盲目启动。
