import type { components } from "@/api/generated/platform-api.v1";
import { apiRequest } from "@/api/client";

export type Entitlement = components["schemas"]["EntitlementResponse"];

export function getWorkspaceEntitlement(workspaceId: string, signal?: AbortSignal) {
  return apiRequest<Entitlement>(`/api/v1/workspaces/${workspaceId}/entitlements`, { signal });
}

export function setWorkspaceOpenApiFeature(workspaceId: string, enabled: boolean) {
  return apiRequest<Entitlement>(
    `/api/v1/workspaces/${workspaceId}/entitlements/features/open-api`,
    { method: "POST", body: { enabled } },
  );
}
