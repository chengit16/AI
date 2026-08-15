# 阶段 1G 全合成端到端数据集

`p1g01-v1.json` 是 `P1G-01` 固定的功能验收集，供 `P1G-02` 及后续阶段复用。数据集由 [`scripts/generate_p1g01_dataset.py`](../../../scripts/generate_p1g01_dataset.py) 确定性生成，不依赖网络、数据库、系统时间、随机数或真实资料。

## 数据范围

- 100 个合成用户、7 个部门、1 个企业空间和 1 个个人空间。
- 360 份企业文档与 4 份个人文档，总数保持在本地功能验收的 300～500 份范围内。
- 覆盖企业所有者、管理员、部门负责人、普通员工、知识管理员、审批人、外部协作者、停用成员和个人空间所有者。
- 覆盖公开、内部、机密、受限四级文档，以及当前发布、版本冲突、过期、草稿、隔离和软删除状态。
- 覆盖个人/企业主流程、菜单与接口权限、跨空间和部门越权、字段遮罩、旧版本、Prompt Injection、SSE 恢复、工作流审批、模型主备降级和配额异常。

固定产物不包含密码、API Key、手机号、证件号或真实企业标识。后续本地装载器只能通过 `p1g01-local-synthetic` 凭证配置注入测试凭证，凭证不得写回 Fixture、报告或 Git。

## 生成与校验

```bash
uv run --locked python scripts/generate_p1g01_dataset.py
uv run --locked python scripts/generate_p1g01_dataset.py --check
uv run --locked pytest tests/test_p1g01_synthetic_dataset.py
```

修改生成规则时必须保持现有版本逐字节稳定；需要改变实体语义、规模、关键场景或预期结果时，应新建数据集版本，而不是原地改变 `p1g01-v1`。
