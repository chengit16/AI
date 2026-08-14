/** @description 应用壳层桌面折叠和移动导航抽屉状态仓库。 */
import { create } from "zustand";

interface UiState {
  sidebarCollapsed: boolean;
  mobileNavigationOpen: boolean;
  toggleSidebar: () => void;
  setMobileNavigationOpen: (open: boolean) => void;
}

/** 管理仅影响应用壳层布局的瞬时 UI 状态，不持久化业务事实。 */
export const useUiStore = create<UiState>((set) => ({
  sidebarCollapsed: false,
  mobileNavigationOpen: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setMobileNavigationOpen: (mobileNavigationOpen) => set({ mobileNavigationOpen }),
}));
