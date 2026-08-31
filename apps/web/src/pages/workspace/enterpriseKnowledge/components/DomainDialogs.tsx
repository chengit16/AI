/** @description 团队知识域、RAG 策略和原子声明范围表单。 */
import { Alert, Button, Drawer, Form, Input, InputNumber, Select } from "antd";
import { useEffect } from "react";

import type {
  EnterpriseKnowledgePortal,
  TeamKnowledgeDomain,
} from "@/api/services/enterpriseKnowledge";

import type { DomainFormValues, DomainScopeFormValues } from "../types";

interface DomainEditorProps {
  /** 门户统一快照提供初始范围可选项。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 编辑目标；空值表示创建知识域。 */
  domain: TeamKnowledgeDomain | null;
  /** 抽屉是否打开。 */
  open: boolean;
  /** 保存请求是否正在提交。 */
  pending: boolean;
  /** 关闭表单且不提交。 */
  onClose: () => void;
  /** 提交知识域元数据与 RAG 策略；创建模式还包含初始范围。 */
  onSubmit: (values: DomainFormValues) => void;
}

/** 创建或编辑知识域；策略参数变化时由服务端生成新策略版本。 */
export function DomainEditor(props: DomainEditorProps) {
  const [form] = Form.useForm<DomainFormValues>();
  useEffect(() => {
    if (!props.open) return;
    form.setFieldsValue({
      name: props.domain?.name ?? "",
      description: props.domain?.description ?? undefined,
      memberIds: [...(props.domain?.member_ids ?? [])],
      departmentIds: [...(props.domain?.department_ids ?? [])],
      knowledgeBaseIds: [...(props.domain?.knowledge_base_ids ?? [])],
      ragMode: props.domain?.rag_policy.mode ?? "balanced",
      topK: props.domain?.rag_policy.top_k ?? 8,
      minimumScore: props.domain?.rag_policy.minimum_score ?? 0.25,
    });
  }, [form, props.domain, props.open]);

  return (
    <Drawer
      title={props.domain ? "编辑团队知识域" : "新建团队知识域"}
      open={props.open}
      size={600}
      destroyOnHidden
      onClose={props.onClose}
      extra={
        <Button
          type="primary"
          loading={props.pending}
          onClick={() => void form.validateFields().then(props.onSubmit)}
        >
          保存知识域
        </Button>
      }
    >
      <Alert
        className="mb-5"
        type="info"
        showIcon
        title="运行范围只会收窄"
        description="声明范围还必须与当前主体的 PDP 授权取交集；空交集不会回退到全企业。"
      />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="name" label="知识域名称" rules={[{ required: true, min: 1, max: 120 }]}>
          <Input placeholder="例如：研发交付知识域" />
        </Form.Item>
        <Form.Item name="description" label="维护说明">
          <Input.TextArea rows={3} maxLength={1000} />
        </Form.Item>
        <div className="grid grid-cols-3 gap-4 phone-down:grid-cols-1">
          <Form.Item name="ragMode" label="RAG 模式" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "balanced", label: "均衡" },
                { value: "precision", label: "精准优先" },
                { value: "recall", label: "召回优先" },
              ]}
            />
          </Form.Item>
          <Form.Item name="topK" label="Top K" rules={[{ required: true }]}>
            <InputNumber className="w-full" min={1} max={50} precision={0} />
          </Form.Item>
          <Form.Item name="minimumScore" label="最低相关分" rules={[{ required: true }]}>
            <InputNumber className="w-full" min={0} max={1} step={0.05} precision={2} />
          </Form.Item>
        </div>
        {!props.domain && (
          <>
            <Form.Item name="memberIds" label="初始直接成员">
              <Select
                mode="multiple"
                options={props.snapshot.members.map((item) => ({
                  value: item.membership_id,
                  label: item.display_name ?? "受限成员",
                }))}
              />
            </Form.Item>
            <Form.Item name="departmentIds" label="初始部门范围">
              <Select
                mode="multiple"
                options={props.snapshot.departments.map((item) => ({
                  value: item.department_id,
                  label: item.name,
                }))}
              />
            </Form.Item>
            <Form.Item name="knowledgeBaseIds" label="初始知识库范围">
              <Select
                mode="multiple"
                options={props.snapshot.knowledge_bases.map((item) => ({
                  value: item.knowledge_base_id,
                  label: item.name + " · " + item.default_security_level,
                }))}
              />
            </Form.Item>
          </>
        )}
      </Form>
    </Drawer>
  );
}

interface DomainScopeEditorProps {
  /** 门户可见选项已经由服务端按字段和资源权限裁剪。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 当前范围目标；空值时关闭抽屉。 */
  domain: TeamKnowledgeDomain | null;
  /** 保存请求是否正在提交。 */
  pending: boolean;
  /** 关闭抽屉。 */
  onClose: () => void;
  /** 一次提交成员、部门和知识库完整集合。 */
  onSubmit: (values: DomainScopeFormValues) => void;
}

/** 原子替换知识域的三类声明范围，避免拆分写入产生越权中间态。 */
export function DomainScopeEditor(props: DomainScopeEditorProps) {
  const [form] = Form.useForm<DomainScopeFormValues>();
  useEffect(() => {
    if (!props.domain) return;
    form.setFieldsValue({
      memberIds: [...props.domain.member_ids],
      departmentIds: [...props.domain.department_ids],
      knowledgeBaseIds: [...props.domain.knowledge_base_ids],
    });
  }, [form, props.domain]);

  return (
    <Drawer
      title={props.domain ? "配置声明范围 · " + props.domain.name : "配置声明范围"}
      open={Boolean(props.domain)}
      size={600}
      destroyOnHidden
      onClose={props.onClose}
      extra={
        <Button
          type="primary"
          loading={props.pending}
          onClick={() => void form.validateFields().then(props.onSubmit)}
        >
          原子保存范围
        </Button>
      }
    >
      <Alert
        className="mb-5"
        type="warning"
        showIcon
        title="本次保存会整体替换三类范围"
        description="空集合代表明确不声明该类范围，不会被解释为全企业；服务端会重新验证所有引用。"
      />
      <Form form={form} layout="vertical">
        <Form.Item name="memberIds" label="直接成员">
          <Select
            mode="multiple"
            showSearch
            optionFilterProp="label"
            options={props.snapshot.members.map((item) => ({
              value: item.membership_id,
              label: item.display_name ?? "受限成员",
            }))}
          />
        </Form.Item>
        <Form.Item name="departmentIds" label="部门范围">
          <Select
            mode="multiple"
            options={props.snapshot.departments.map((item) => ({
              value: item.department_id,
              label: item.name,
            }))}
          />
        </Form.Item>
        <Form.Item name="knowledgeBaseIds" label="知识库范围">
          <Select
            mode="multiple"
            options={props.snapshot.knowledge_bases.map((item) => ({
              value: item.knowledge_base_id,
              label: item.name + " · " + item.default_security_level,
            }))}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
