/** @description 工作空间标签创建、编辑、删除和恢复管理界面。 */
import { Button, Drawer, Form, Input, Modal, Popconfirm, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { Pencil, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import type {
  CreateKnowledgeTagRequest,
  KnowledgeTag,
  UpdateKnowledgeTagRequest,
} from "@/api/services/knowledgeOrganization";

interface TagManagerDrawerProps {
  /** 是否打开标签管理界面。 */
  open: boolean;
  /** 当前工作空间完整标签列表，包含删除项。 */
  items: readonly KnowledgeTag[];
  /** 标签列表是否正在首次加载。 */
  isLoading: boolean;
  /** 只控制创建入口，服务端仍独立授权。 */
  canCreate: boolean;
  /** 只控制编辑入口，服务端仍独立授权。 */
  canUpdate: boolean;
  /** 只控制删除入口，服务端仍独立授权。 */
  canDelete: boolean;
  /** 只控制恢复入口，服务端仍独立授权。 */
  canRestore: boolean;
  /** 任一标签命令是否正在提交。 */
  isPending: boolean;
  /** 关闭标签管理界面。 */
  onClose: () => void;
  /** 创建标签。 */
  onCreate: (body: CreateKnowledgeTagRequest) => Promise<unknown>;
  /** 更新标签。 */
  onUpdate: (tagId: string, body: UpdateKnowledgeTagRequest) => Promise<unknown>;
  /** 软删除标签。 */
  onDelete: (tagId: string) => Promise<unknown>;
  /** 恢复已删除标签。 */
  onRestore: (tagId: string) => Promise<unknown>;
}

interface TagFormValues {
  name: string;
  color: string;
}

/** 提供标签全生命周期操作；标签颜色不参与授权或检索范围。 */
export function TagManagerDrawer(props: TagManagerDrawerProps) {
  const [editing, setEditing] = useState<KnowledgeTag | "create" | null>(null);
  const [form] = Form.useForm<TagFormValues>();

  useEffect(() => {
    if (!editing) return;
    form.setFieldsValue({
      name: editing === "create" ? "" : editing.name,
      color: editing === "create" ? "#176b52" : (editing.color ?? "#176b52"),
    });
  }, [editing, form]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    if (editing === "create") await props.onCreate(values);
    else if (editing) await props.onUpdate(editing.tag_id, values);
    form.resetFields();
    setEditing(null);
  };
  const activeItems = props.items.filter((item) => item.status === "active");
  const deletedItems = props.items.filter((item) => item.status === "deleted");
  const columns: TableColumnsType<KnowledgeTag> = [
    {
      title: "标签",
      key: "tag",
      render: (_, item) => <Tag color={item.color ?? undefined}>{item.name}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (status: KnowledgeTag["status"]) => (status === "active" ? "使用中" : "已删除"),
    },
    {
      title: "操作",
      key: "actions",
      width: 136,
      render: (_, item) =>
        item.status === "active" ? (
          <div className="flex items-center gap-1">
            {props.canUpdate && (
              <Button
                type="text"
                aria-label={`编辑标签 ${item.name}`}
                icon={<Pencil size={15} />}
                onClick={() => setEditing(item)}
              />
            )}
            {props.canDelete && (
              <Popconfirm
                title="删除此标签？"
                description="文档内容不会删除。"
                okText="删除"
                cancelText="取消"
                onConfirm={() => props.onDelete(item.tag_id)}
              >
                <Button
                  type="text"
                  danger
                  aria-label={`删除标签 ${item.name}`}
                  icon={<Trash2 size={15} />}
                />
              </Popconfirm>
            )}
          </div>
        ) : props.canRestore ? (
          <Button
            type="text"
            icon={<RotateCcw size={15} />}
            onClick={() => void props.onRestore(item.tag_id)}
          >
            恢复
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Drawer title="标签管理" open={props.open} size="large" onClose={props.onClose}>
        <div className="mb-4 flex min-h-11 items-center justify-between gap-3">
          <span className="text-sm text-text-muted">
            使用中 {activeItems.length} · 已删除 {deletedItems.length}
          </span>
          {props.canCreate && (
            <Button type="primary" icon={<Plus size={16} />} onClick={() => setEditing("create")}>
              新建标签
            </Button>
          )}
        </div>
        <Table<KnowledgeTag>
          rowKey="tag_id"
          columns={columns}
          dataSource={[...activeItems, ...deletedItems]}
          loading={props.isLoading}
          pagination={false}
          scroll={{ x: 420 }}
        />
      </Drawer>
      <Modal
        title={editing === "create" ? "新建标签" : "编辑标签"}
        open={editing !== null}
        okText="保存"
        cancelText="取消"
        confirmLoading={props.isPending}
        onCancel={() => setEditing(null)}
        onOk={() => void handleSubmit().catch(() => undefined)}
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item label="名称" name="name" rules={[{ required: true }, { max: 80 }]}>
            <Input autoFocus />
          </Form.Item>
          <Form.Item label="颜色" name="color">
            <Input type="color" className="h-11 w-20 p-1" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
