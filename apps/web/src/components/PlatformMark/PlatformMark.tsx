import styles from "./PlatformMark.module.css";

interface PlatformMarkProps {
  compact?: boolean;
}

export function PlatformMark({ compact = false }: PlatformMarkProps) {
  return (
    <div className={styles.root} aria-label="AI 智能平台">
      <span className={styles.symbol}>AI</span>
      {!compact && <span className={styles.name}>智能平台</span>}
    </div>
  );
}
