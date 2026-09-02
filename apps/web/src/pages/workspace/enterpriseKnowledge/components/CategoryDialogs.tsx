/** @description 企业分类元数据与显式文档绑定表单。 */
import { Alert, Drawer, Form, Input, Select, Switch } from "antd";
import { useEffect } from "react";

import type {
  EnterpriseCategory,
  EnterpriseKnowledgePortal,
} from "@/api/services/enterpriseKnowledge";

import type { CategoryFormValues } from "../types";

interface CategoryEditorProps {
  /** 统一快照提供父分类、部门和文档选项。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 编辑目标；空值表示创建分类。 */
  category: EnterpriseCategory | null;
  /** 创建时预选的父分类。 */
  parentCategoryId?: string;
  /** 抽屉是否打开。 */
  open: boolean;
  /** 保存请求是否正在提交。 */
  pending: boolean;
  /** 关闭表单且不提交。 */
  onClose: () => void;
  /** 提交分类完整元数据；创建模式还包含初始文档绑定。 */
  onSubmit: (values: CategoryFormValues) => void;
}

/** 创建或编辑分类元数据，可见策略选择部门时强制要求范围。 */
export function CategoryEditor(props: CategoryEditorProps) {
  const [form] = Form.useForm<CategoryFormValues>();
  const visibility = Form.useWatch("visibility", form) ?? "public";
  useEffect(() => {
    if (!props.open) return;
    form.setFieldsValue({
      name: props.category?.name ?? "",
      description: props.category?.description ?? undefined,
      parentCategoryId: props.category?.parent_category_id ?? props.parentCategoryId,
      visibility: props.category?.visibility ?? "public",
      departmentIds: [...(props.category?.department_ids ?? [])],
      documentIds: [...(props.category?.document_ids ?? [])],
      approvalRequired: props.category?.approval_required ?? false,
    });
  }, [form, props.category, props.open, props.parentCategoryId]);
  const categoryOptions = props.snapshot.categories
    .filter((item) => item.status === "active" && item.category_id !== props.category?.category_id)
    .map((item) => ({ value: item.category_id, label: item.name }));
  const departmentOptions = props.snapshot.departments.map((item) => ({
    value: item.department_id,
    label: item.name,
  }));
  const documentOptions = props.snapshot.documents.map((item) => ({
    value: item.document_id,
    label: item.title + " · " + item.security_level,
  }));
  return (
    <Drawer
      title={props.category ? "编辑企业分类" : "新建企业分类"}
      open={props.open}
      size={560}
      destroyOnHidden
      onClose={props.onClose}
      extra={
        <button
          type="button"
          className="ant-btn ant-btn-primary"
          disabled={props.pending}
          onClick={() => void form.validateFields().then(props.onSubmit)}
        >
          {props.pending ? "保存中" : "保存分类"}
        </button>
      }
    >
      <Alert
        className="mb-5"
        type="info"
        showIcon
        title="分类策略不替代文档权限"
        description="分类只建立治理关系；用户仍需同时通过文档和知识库的服务端授权。"
      />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="name" label="分类名称" rules={[{ required: true, min: 1, max: 120 }]}>
          <Input placeholder="例如：制度与规范" />
        </Form.Item>
        <Form.Item name="description" label="治理说明">
          <Input.TextArea rows={3} maxLength={1000} />
        </Form.Item>
        <Form.Item name="parentCategoryId" label="父分类">
          <Select allowClear options={categoryOptions} placeholder="不选择则创建根分类" />
        </Form.Item>
        <Form.Item name="visibility" label="分类可见策略" rules={[{ required: true }]}>
          <Select
            options={[
              { value: "public", label: "全企业公开" },
              { value: "departments", label: "指定部门" },
              { value: "private", label: "仅治理人员" },
            ]}
            onChange={(value) => {
              if (value !== "departments") form.setFieldValue("departmentIds", []);
            }}
          />
        </Form.Item>
        {visibility === "departments" && (
          <Form.Item
            name="departmentIds"
            label="可见部门"
            rules={[{ required: true, type: "array", min: 1, message: "至少选择一个部门" }]}
          >
            <Select mode="multiple" options={departmentOptions} />
          </Form.Item>
        )}
        {!props.category && (
          <Form.Item name="documentIds" label="初始文档绑定">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={documentOptions}
              placeholder="可稍后单独维护"
            />
          </Form.Item>
        )}
        <Form.Item
          name="approvalRequired"
          label="发布审批"
          valuePropName="checked"
          extra="开启后，该分类中的就绪文档只能通过发布申请和审批链切换发布指针。"
        >
          <Switch checkedChildren="需要审批" unCheckedChildren="直接发布" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

interface CategoryDocumentBinderProps {
  /** 门户可见文档选项已由服务端执行密级裁剪。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 当前绑定目标；空值时关闭抽屉。 */
  category: EnterpriseCategory | null;
  /** 保存请求是否正在提交。 */
  pending: boolean;
  /** 关闭抽屉。 */
  onClose: () => void;
  /** 整体替换当前分类文档集合。 */
  onSubmit: (documentIds: readonly string[]) => void;
}

/** 整体替换分类显式文档关系，避免连续增删产生中间事实。 */
export function CategoryDocumentBinder(props: CategoryDocumentBinderProps) {
  const [form] = Form.useForm<{ documentIds: string[] }>();
  useEffect(() => {
    if (props.category) form.setFieldsValue({ documentIds: [...props.category.document_ids] });
  }, [form, props.category]);
  return (
    <Drawer
      title={props.category ? "绑定文档 · " + props.category.name : "绑定分类文档"}
      open={Boolean(props.category)}
      size={560}
      destroyOnHidden
      onClose={props.onClose}
      extra={
        <button
          type="button"
          className="ant-btn ant-btn-primary"
          disabled={props.pending}
          onClick={() =>
            void form.validateFields().then((values) => props.onSubmit(values.documentIds ?? []))
          }
        >
          {props.pending ? "保存中" : "保存绑定"}
        </button>
      }
    >
      <Alert
        className="mb-5"
        type="warning"
        showIcon
        title="本次保存会整体替换绑定"
        description="文档原文件、版本、发布指针、解析产物和 Chunk 不会被复制或修改。"
      />
      <Form form={form} layout="vertical">
        <Form.Item name="documentIds" label="分类文档">
          <Select
            mode="multiple"
            showSearch
            optionFilterProp="label"
            options={props.snapshot.documents.map((item) => ({
              value: item.document_id,
              label: item.title + " · " + item.security_level,
            }))}
            placeholder="选择当前授权范围内的文档"
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
