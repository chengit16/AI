/** @description 当前会话的平台管理员资格查询 Hook。 */
import { useQuery } from "@tanstack/react-query";

import { getPlatformAdministration } from "@/api/services/platformModels";
import { useSessionStore } from "@/store/session";

/** 平台管理员资格由后端逐请求复核；前端只缓存本次会话的展示结论。 */
export function usePlatformAdministration() {
  const accountId = useSessionStore((state) => state.accountId);
  const administration = useQuery({
    queryKey: ["platform-administration", accountId],
    queryFn: ({ signal }) => getPlatformAdministration(signal),
    enabled: Boolean(accountId),
    retry: false,
    staleTime: 0,
  });
  const isPlatformAdministrator = administration.data?.is_platform_administrator === true;
  return {
    administration,
    isPlatformAdministrator,
    isDenied: administration.isSuccess && !isPlatformAdministrator,
  };
}
