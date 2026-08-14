import { Button, Skeleton } from "antd";
import { BookOpen, Plus } from "lucide-react";

import { StateView } from "@/components/StateView/StateView";
import type { KnowledgeBaseSummary } from "@/api/services/knowledge";

import { securityLevelLabels } from "../config";
import styles from "../KnowledgeProductionPage.module.css";

interface KnowledgeBaseRailProps {
  items: readonly KnowledgeBaseSummary[];
  selectedId: string | null;
  isLoading: boolean;
  canCreate: boolean;
  onSelect: (knowledgeBaseId: string) => void;
  onCreate: () => void;
}

export function KnowledgeBaseRail({
  items,
  selectedId,
  isLoading,
  canCreate,
  onSelect,
  onCreate,
}: KnowledgeBaseRailProps) {
  return (
    <aside className={styles.rail} aria-label="知识库列表">
      <div className={styles.railHeading}>
        <div>
          <strong>知识库</strong>
          <span>{items.length} 个</span>
        </div>
        {canCreate && (
          <Button
            type="text"
            aria-label="创建知识库"
            icon={<Plus size={17} />}
            onClick={onCreate}
          />
        )}
      </div>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} title={false} />
      ) : items.length === 0 ? (
        <StateView
          kind="empty"
          title="还没有知识库"
          description="创建知识库后即可上传和管理文档。"
        />
      ) : (
        <div className={styles.railList}>
          {items.map((item) => (
            <button
              key={item.knowledge_base_id}
              type="button"
              className={`${styles.railItem} ${selectedId === item.knowledge_base_id ? styles.railItemActive : ""}`}
              onClick={() => onSelect(item.knowledge_base_id)}
            >
              <BookOpen size={17} aria-hidden="true" />
              <span>
                <strong>{item.name}</strong>
                <small>{securityLevelLabels[item.default_security_level]}</small>
              </span>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
