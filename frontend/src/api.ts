import type { ChatRequest, ChatResponse, Job, Manual, Session, User, Vehicle } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8001";
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : "请求失败，请稍后重试。";

export class ApiError extends Error {
  constructor(public status: number, detail: string, public requestId: string) {
    super(`${detail}${requestId ? `（请求 ID：${requestId}）` : ""}`);
  }
}

export function createApi(token?: string, onUnauthorized?: (error: ApiError) => void) {
  async function response(path: string, options: RequestInit = {}, requestToken = token) {
    const headers = new Headers(options.headers);
    if (requestToken) headers.set("Authorization", `Bearer ${requestToken}`);
    const result = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
    if (!result.ok) {
      const body = await result.json().catch(() => ({}));
      const error = new ApiError(result.status, typeof body.detail === "string" ? body.detail : `请求失败（${result.status}）`, body.request_id || result.headers.get("X-Request-ID") || "");
      if (result.status === 401 && requestToken) onUnauthorized?.(error);
      throw error;
    }
    return result;
  }
  async function request<T>(path: string, options?: RequestInit): Promise<T> {
    return (await response(path, options)).json() as Promise<T>;
  }
  const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return {
    login: (username: string, password: string) => request<Session>("/auth/login", json("POST", { username, password })),
    me: () => request<User>("/auth/me"),
    vehicles: () => request<Vehicle[]>("/vehicles"),
    chapters: (vehicleId: string) => request<string[]>(`/manuals/${encodeURIComponent(vehicleId)}/chapters`),
    ask: (vehicleId: string, question: string, chapterTitles?: string[]) => request<ChatResponse>("/chat", json("POST", { vehicle_id: vehicleId, question, chapter_titles: chapterTitles } satisfies ChatRequest)),
    manuals: () => request<Manual[]>("/admin/manuals"),
    upload: (data: FormData, manualId?: string) => request<{ manual_id: string; version_id: string; job_id: string; status: string }>(manualId ? `/admin/manuals/${encodeURIComponent(manualId)}/versions` : "/admin/manuals", { method: "POST", body: data }),
    setEnabled: (id: string, enabled: boolean) => request<Pick<Manual, "id" | "enabled">>(`/admin/manuals/${encodeURIComponent(id)}`, json("PATCH", { enabled })),
    job: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
    retry: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
    users: () => request<User[]>("/admin/users"),
    createUser: (username: string, password: string, role: User["role"]) => request<User>("/admin/users", json("POST", { username, password, role })),
    setActive: (id: string, active: boolean) => request<User>(`/admin/users/${encodeURIComponent(id)}`, json("PATCH", { active })),
    pdf: async (manualId: string, versionId: string) => (await response(`/manuals/${encodeURIComponent(manualId)}/versions/${encodeURIComponent(versionId)}/file`)).blob(),
    openAsset: async (manualId: string, versionId: string, assetId: string, assetToken: string): Promise<Blob> =>
      (await response(`/manuals/${encodeURIComponent(manualId)}/versions/${encodeURIComponent(versionId)}/assets/${encodeURIComponent(assetId)}/file`, {}, assetToken)).blob(),
  };
}
export type Api = ReturnType<typeof createApi>;
