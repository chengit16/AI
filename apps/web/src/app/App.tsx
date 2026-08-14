/** @description React 应用根组件，组合全局 Provider、BrowserRouter 与应用路由。 */
import { AppRoutes } from "@/routes/AppRoutes";
import { configureApiClient } from "@/api/client";
import { getSessionSnapshot } from "@/store/session";

configureApiClient(
  () => getSessionSnapshot(),
  () => getSessionSnapshot().clear(),
);

/** 渲染已注入统一 API 会话读取器的应用路由树。 */
export function App() {
  return <AppRoutes />;
}
