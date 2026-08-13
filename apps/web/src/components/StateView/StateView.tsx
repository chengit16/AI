import { AlertTriangle, Inbox, LockKeyhole } from "lucide-react";
import type { ReactNode } from "react";

import styles from "./StateView.module.css";

interface StateViewProps {
  kind: "empty" | "error" | "denied";
  title: string;
  description: string;
  action?: ReactNode;
}

const icons = { empty: Inbox, error: AlertTriangle, denied: LockKeyhole };

export function StateView({ kind, title, description, action }: StateViewProps) {
  const Icon = icons[kind];
  return (
    <section className={styles.root} role={kind === "error" ? "alert" : "status"}>
      <span className={styles.icon}>
        <Icon size={22} aria-hidden="true" />
      </span>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
        {action && <div className={styles.action}>{action}</div>}
      </div>
    </section>
  );
}
