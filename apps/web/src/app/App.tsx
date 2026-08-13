import { AppRoutes } from "@/routes/AppRoutes";
import { configureApiClient } from "@/api/client";
import { getSessionSnapshot } from "@/store/session";

configureApiClient(
  () => getSessionSnapshot(),
  () => getSessionSnapshot().clear(),
);

export function App() {
  return <AppRoutes />;
}
