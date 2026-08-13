import { create } from "zustand";

interface SessionState {
  accountId: string | null;
  workspaceId: string | null;
  csrfToken: string | null;
  setAuthenticated: (accountId: string, workspaceId: string, csrfToken: string) => void;
  setWorkspaceId: (workspaceId: string) => void;
  clear: () => void;
}

const sessionStorageKey = "ai-platform.session.v1";

function loadSession(): Pick<SessionState, "accountId" | "workspaceId" | "csrfToken"> {
  try {
    const raw = window.sessionStorage.getItem(sessionStorageKey);
    if (!raw) return { accountId: null, workspaceId: null, csrfToken: null };
    const value = JSON.parse(raw) as Record<string, unknown>;
    return {
      accountId: typeof value.accountId === "string" ? value.accountId : null,
      workspaceId: typeof value.workspaceId === "string" ? value.workspaceId : null,
      csrfToken: typeof value.csrfToken === "string" ? value.csrfToken : null,
    };
  } catch {
    return { accountId: null, workspaceId: null, csrfToken: null };
  }
}

function persistSession(state: Pick<SessionState, "accountId" | "workspaceId" | "csrfToken">) {
  window.sessionStorage.setItem(sessionStorageKey, JSON.stringify(state));
}

export const useSessionStore = create<SessionState>((set) => ({
  ...loadSession(),
  setAuthenticated: (accountId, workspaceId, csrfToken) => {
    const next = { accountId, workspaceId, csrfToken };
    persistSession(next);
    set(next);
  },
  setWorkspaceId: (workspaceId) =>
    set((state) => {
      const next = { accountId: state.accountId, workspaceId, csrfToken: state.csrfToken };
      persistSession(next);
      return { workspaceId };
    }),
  clear: () => {
    window.sessionStorage.removeItem(sessionStorageKey);
    set({ accountId: null, workspaceId: null, csrfToken: null });
  },
}));

export function getSessionSnapshot() {
  return useSessionStore.getState();
}
