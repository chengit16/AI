/** @description 当前会话的平台管理员资格查询 Hook。 */
import { useQuery } from "@tanstack/react-query";

import { PlatformApiError } from "@/api/client";
import { getPlatformModelProviders } from "@/api/services/platformModels";
import { useSessionStore } from "@/store/session";

/** 平台管理员资格由后端逐请求复核；前端只缓存本次会话的展示结论。 */
export function usePlatformAdministration() {
  const accountId = useSessionStore((state) => state.accountId);
  const providers = useQuery({
    queryKey: ["platform-model-providers", accountId],
    queryFn: ({ signal }) => getPlatformModelProviders(signal),
    enabled: Boolean(accountId),
    retry: false,
    staleTime: 0,
  });
  const isDenied =
    providers.error instanceof PlatformApiError &&
    ["PLATFORM_ADMIN_REQUIRED", "POLICY_DENIED"].includes(providers.error.code);
  return {
    providers,
    isPlatformAdministrator: providers.isSuccess,
    isDenied,
  };
}
