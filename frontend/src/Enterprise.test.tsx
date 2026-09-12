import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { CitationCard } from "./components/CitationCard";

const reader = { id: "u1", username: "reader", role: "reader", active: true };
const admin = { ...reader, username: "admin", role: "admin" };
const answer = { answer: "请按手册步骤检查机油。", steps: ["停在水平地面"], warnings: [], grounded: true,
  citations: [{ manual_id: "m1", version_id: "v1", manual_title: "思域手册", chapter_title: "机油检查", page_number: 657, excerpt: "Park on level ground." }] };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
let role = reader;
let overrides: Record<string, (init?: RequestInit) => Response | Promise<Response>>;
let calls: { path: string; init?: RequestInit }[];
async function login() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("用户名"), role.username);
  await user.type(screen.getByLabelText("密码"), "password123");
  await user.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByRole("button", { name: "退出登录" });
  return user;
}
async function query() {
  const user = await login();
  await user.selectOptions(await screen.findByLabelText("车型"), "honda-civic");
  await user.click(await screen.findByRole("button", { name: "如何检查发动机机油液位？" }));
  await user.click(screen.getByRole("button", { name: "开始查询" }));
  return user;
}

// jsdom's FormData reads its internal file list, while user-event patches the public list.
// Preserve real multipart fields and bridge only that browser emulation limitation.
function supportUploadedFiles() {
  const NativeFormData = FormData;
  vi.stubGlobal("FormData", class extends NativeFormData {
    constructor(form?: HTMLFormElement) {
      super(form);
      form?.querySelectorAll<HTMLInputElement>('input[type="file"]').forEach(input => {
        if (input.files?.[0]) this.set(input.name, input.files[0]);
      });
    }
  });
}

describe("enterprise assistant", () => {
  beforeEach(() => {
    role = reader; overrides = {}; calls = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      calls.push({ path, init });
      if (overrides[path]) return overrides[path](init);
      if (path === "/auth/login") return json({ access_token: "test-token", token_type: "bearer", user: role });
      if (path === "/auth/me") return json(role);
      if (path === "/vehicles") return json([{ id: "honda-civic", brand: "本田", model: "思域", year: 2024 }, { id: "toyota-corolla", brand: "丰田", model: "卡罗拉", year: 2024 }]);
      if (path.endsWith("/chapters")) return json(["第 657 页", "第 724 页"]);
      if (path === "/chat") return json(answer);
      if (path === "/admin/manuals" || path === "/admin/users") return json([]);
      throw new Error(`Unexpected request ${path}`);
    }));
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("requires login, sends bearer credentials, hides admin controls, and logs out", async () => {
    render(<App />);
    expect(screen.queryByLabelText("车型")).not.toBeInTheDocument();
    const user = await query();
    expect(await screen.findByText("机油检查 · PDF 第 657 页")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "手册管理" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "用户管理" })).not.toBeInTheDocument();
    expect(new Headers(calls.find(c => c.path === "/chat")?.init?.headers).get("Authorization")).toBe("Bearer test-token");
    expect(localStorage.length).toBe(0);
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
    expect(screen.queryByText(answer.answer)).not.toBeInTheDocument();
  });
  it("clears the session on 401 without replaying the failed request", async () => {
    overrides["/chat"] = () => json({ detail: "登录已过期", request_id: "req-expired" }, 401);
    render(<App />); await query();
    expect(await screen.findByRole("button", { name: "登录" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("req-expired");
    expect(calls.filter(c => c.path === "/chat")).toHaveLength(1);
  });
  it("shows backend errors with their request ID", async () => {
    overrides["/auth/login"] = () => json({ detail: "账号已停用", request_id: "req-disabled" }, 403);
    render(<App />); const user = userEvent.setup();
    await user.type(screen.getByLabelText("用户名"), "reader");
    await user.type(screen.getByLabelText("密码"), "password123");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("账号已停用");
    expect(screen.getByRole("alert")).toHaveTextContent("req-disabled");
  });
  it("clears answers and ignores late answers after switching vehicles", async () => {
    render(<App />); const user = await query();
    await screen.findByText(answer.answer);
    await user.selectOptions(screen.getByLabelText("车型"), "toyota-corolla");
    expect(screen.queryByText(answer.answer)).not.toBeInTheDocument();
    let resolve!: (response: Response) => void;
    overrides["/chat"] = () => new Promise(r => { resolve = r; });
    await user.click(screen.getByRole("button", { name: "开始查询" }));
    await user.selectOptions(screen.getByLabelText("车型"), "honda-civic");
    await act(async () => { resolve(json(answer)); });
    expect(screen.queryByText(answer.answer)).not.toBeInTheDocument();
  });
  it("fetches cited PDF with authorization and releases its blob URL on logout", async () => {
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:manual-test") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
    overrides["/manuals/m1/versions/v1/file"] = () => new Response(new Blob(["PDF"], { type: "application/pdf" }));
    render(<App />); const user = await query();
    await user.click(await screen.findByRole("button", { name: "查看 PDF 第 657 页" }));
    expect(await screen.findByTitle("手册 PDF")).toHaveAttribute("src", "blob:manual-test#page=657");
    expect(new Headers(calls.find(c => c.path.endsWith("/file"))?.init?.headers).get("Authorization")).toBe("Bearer test-token");
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(revoke).toHaveBeenCalledWith("blob:manual-test");
  });
  it("labels image evidence and sends image citations to the asset callback", async () => {
    const citation = { manual_id: "m1", version_id: "v1", asset_id: "asset 1", evidence_type: "image_ocr" as const,
      manual_title: "思域手册", chapter_title: "仪表盘", page_number: 12, excerpt: "机油压力警告灯" };
    const onOpen = vi.fn();
    const onOpenAsset = vi.fn();
    const user = userEvent.setup();
    render(<CitationCard citation={citation} onOpen={onOpen} onOpenAsset={onOpenAsset} busy={false} />);
    expect(screen.getByText("来源：图片 OCR")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看关联图片" }));
    expect(onOpenAsset).toHaveBeenCalledWith(citation);
    expect(onOpen).not.toHaveBeenCalled();
  });
  it("keeps ordinary citations on the PDF-page callback", async () => {
    const citation = answer.citations[0];
    const onOpen = vi.fn();
    const onOpenAsset = vi.fn();
    const user = userEvent.setup();
    render(<CitationCard citation={citation} onOpen={onOpen} onOpenAsset={onOpenAsset} busy={false} />);
    expect(screen.getByText("来源：PDF 文本")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 PDF 第 657 页" }));
    expect(onOpen).toHaveBeenCalledWith(citation);
    expect(onOpenAsset).not.toHaveBeenCalled();
  });
  it("opens authenticated image assets in a new tab and releases their blob URL", async () => {
    const imageAnswer = { ...answer, citations: [{ ...answer.citations[0], asset_id: "asset 1", evidence_type: "image_description" }] };
    overrides["/chat"] = () => json(imageAnswer);
    overrides["/manuals/m1/versions/v1/assets/asset%201/file"] = () => new Response(new Blob(["image"], { type: "image/png" }));
    const createObjectURL = vi.fn(() => "blob:image-test");
    const revokeObjectURL = vi.fn();
    const open = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    vi.stubGlobal("open", open);
    render(<App />); const user = await query();
    await user.click(await screen.findByRole("button", { name: "查看关联图片" }));
    expect(new Headers(calls.find(c => c.path.includes("/assets/"))?.init?.headers).get("Authorization")).toBe("Bearer test-token");
    expect(open).toHaveBeenCalledWith("blob:image-test", "_blank", "noopener,noreferrer");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:image-test");
  });
  it("uploads manuals, displays errors, and retries failed jobs", async () => {
    supportUploadedFiles();
    role = admin;
    const manual = { id: "m1", vehicle_id: "honda-civic", title: "新手册", source: "厂商官网", enabled: true, active_version_id: null,
      versions: [{ id: "v1", sha256: "abc", page_count: 0, chunk_count: 0, created_at: "2026-09-11T00:00:00Z", job: { id: "j1", status: "failed", error: "无法解析 PDF", attempts: 1 } }] };
    let uploaded = false;
    overrides["/admin/manuals"] = init => {
      if (init?.method === "POST") { uploaded = true; return json({ manual_id: "m1", version_id: "v1", job_id: "j1", status: "queued" }, 202); }
      return json(uploaded ? [manual] : []);
    };
    overrides["/jobs/j1/retry"] = () => json({ id: "j1", status: "queued", error: null, attempts: 1 });
    render(<App />); const user = await login();
    await user.click(screen.getByRole("button", { name: "手册管理" }));
    await user.selectOptions(await screen.findByLabelText("手册车型"), "honda-civic");
    await user.type(screen.getByLabelText("手册标题"), "新手册");
    await user.type(screen.getByLabelText("来源"), "厂商官网");
    await user.upload(screen.getByLabelText("PDF 文件"), new File(["pdf"], "manual.pdf", { type: "application/pdf" }));
    // jsdom does not include user-event's file list in native constraint validation.
    fireEvent.submit(screen.getByRole("button", { name: "上传手册" }).closest("form")!);
    expect(await screen.findByText("无法解析 PDF")).toBeInTheDocument();
    const body = calls.find(c => c.path === "/admin/manuals" && c.init?.method === "POST")?.init?.body as FormData;
    expect(body.get("vehicle_id")).toBe("honda-civic");
    expect((body.get("file") as File).name).toBe("manual.pdf");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("排队中")).toBeInTheDocument();
  });
  it("creates and disables reader accounts", async () => {
    role = admin; let created = false;
    overrides["/admin/users"] = init => { if (init?.method === "POST") { created = true; return json(reader, 201); } return json(created ? [reader] : []); };
    overrides["/admin/users/u1"] = () => json({ ...reader, active: false });
    render(<App />); const user = await login();
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    await user.type(await screen.findByLabelText("新用户名"), "reader");
    await user.type(screen.getByLabelText("初始密码"), "password123");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    await user.click(await screen.findByRole("button", { name: "停用 reader" }));
    expect(await screen.findByRole("button", { name: "启用 reader" })).toBeInTheDocument();
    expect(JSON.parse(String(calls.find(c => c.path === "/admin/users/u1")?.init?.body))).toEqual({ active: false });
  });

  it("prevents creating data while the initial administrator list is still loading", async () => {
    role = admin;
    overrides["/admin/manuals"] = () => new Promise(() => {});
    overrides["/admin/users"] = () => new Promise(() => {});
    render(<App />); const user = await login();
    await user.click(screen.getByRole("button", { name: "手册管理" }));
    expect(screen.getByRole("button", { name: "上传手册" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(screen.getByRole("button", { name: "创建用户" })).toBeDisabled();
  });

  it("matches backend account and manual field limits", async () => {
    role = admin;
    render(<App />); const user = await login();
    await user.click(screen.getByRole("button", { name: "用户管理" }));
    expect(await screen.findByLabelText("新用户名")).toHaveAttribute("maxlength", "80");
    expect(screen.getByLabelText("初始密码")).toHaveAttribute("minlength", "10");
    expect(screen.getByLabelText("初始密码")).toHaveAttribute("maxlength", "256");
    await user.click(screen.getByRole("button", { name: "手册管理" }));
    expect(await screen.findByLabelText("手册标题")).toHaveAttribute("maxlength", "200");
    expect(screen.getByLabelText("来源")).toHaveAttribute("maxlength", "4000");
  });

  it("keeps a new account signed in when an old account request returns 401", async () => {
    let resolve!: (response: Response) => void;
    overrides["/chat"] = () => new Promise(r => { resolve = r; });
    render(<App />); const user = await query();
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    role = admin;
    overrides["/auth/login"] = () => json({ access_token: "new-admin-token", token_type: "bearer", user: admin });
    await login();
    await act(async () => { resolve(json({ detail: "旧登录已过期", request_id: "old-request" }, 401)); });
    expect(screen.getByRole("button", { name: "用户管理" })).toBeInTheDocument();
    expect(screen.queryByText(/old-request/)).not.toBeInTheDocument();
    expect(screen.queryByText(answer.answer)).not.toBeInTheDocument();
  });

  it("ignores chapters and PDF responses from a vehicle that is no longer selected", async () => {
    let resolveChapters!: (response: Response) => void;
    let resolvePdf!: (response: Response) => void;
    overrides["/manuals/honda-civic/chapters"] = () => new Promise(r => { resolveChapters = r; });
    overrides["/manuals/m1/versions/v1/file"] = () => new Promise(r => { resolvePdf = r; });
    render(<App />); const user = await query();
    await user.click(await screen.findByRole("button", { name: "查看 PDF 第 657 页" }));
    await user.selectOptions(screen.getByLabelText("车型"), "toyota-corolla");
    await screen.findByLabelText("第 724 页");
    await act(async () => {
      resolveChapters(json(["旧车型专属章节"]));
      resolvePdf(new Response(new Blob(["PDF"], { type: "application/pdf" })));
    });
    expect(screen.queryByLabelText("旧车型专属章节")).not.toBeInTheDocument();
    expect(screen.getByLabelText("第 724 页")).toBeInTheDocument();
    expect(screen.queryByTitle("手册 PDF")).not.toBeInTheDocument();
  });

  it("polls pending jobs every three seconds and stops once the version is active", async () => {
    role = admin; let jobCalls = 0;
    const manual = { id: "m1", vehicle_id: "honda-civic", title: "思域手册", source: "厂商", enabled: true, active_version_id: null,
      versions: [{ id: "v1", sha256: "abc", page_count: 0, chunk_count: 0, created_at: "2026-09-11T00:00:00Z", job: { id: "j1", status: "queued", error: null, attempts: 0 } }] };
    overrides["/admin/manuals"] = () => json(jobCalls >= 2 ? [{ ...manual, active_version_id: "v1", versions: [{ ...manual.versions[0], page_count: 20, chunk_count: 40, job: { id: "j1", status: "succeeded", error: null, attempts: 1 } }] }] : [manual]);
    overrides["/jobs/j1"] = () => { jobCalls++; return json({ id: "j1", status: jobCalls === 1 ? "parsing" : "succeeded", error: null, attempts: 1 }); };
    render(<App />); await login();
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "手册管理" })); });
    expect(screen.getByText("排队中")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2999); });
    expect(jobCalls).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByText("解析中")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("版本 v1（当前生效）")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(jobCalls).toBe(2);
  });

  it("uploads a new version to the existing manual and can disable that manual", async () => {
    role = admin; supportUploadedFiles(); let uploaded = false;
    const manual = { id: "m1", vehicle_id: "honda-civic", title: "思域手册", source: "厂商", enabled: true, active_version_id: "v1", versions: [] };
    overrides["/admin/manuals"] = () => json([{ ...manual, versions: uploaded ? [{ id: "v2", sha256: "abc", page_count: 0, chunk_count: 0, created_at: "2026-09-11T00:00:00Z", job: { id: "j2", status: "queued", error: null, attempts: 0 } }] : [] }]);
    overrides["/admin/manuals/m1/versions"] = () => { uploaded = true; return json({ manual_id: "m1", version_id: "v2", job_id: "j2", status: "queued" }, 202); };
    overrides["/admin/manuals/m1"] = () => json({ id: "m1", enabled: false });
    render(<App />); const user = await login();
    await user.click(screen.getByRole("button", { name: "手册管理" }));
    await user.upload(await screen.findByLabelText("为 思域手册 上传新版本"), new File(["pdf"], "v2.pdf", { type: "application/pdf" }));
    fireEvent.submit(screen.getByRole("button", { name: "上传新版本" }).closest("form")!);
    expect(await screen.findByText("版本 v2")).toBeInTheDocument();
    expect(screen.getByText("已启用 · 生效版本：v1")).toBeInTheDocument();
    const body = calls.find(c => c.path === "/admin/manuals/m1/versions")?.init?.body as FormData;
    expect((body.get("file") as File).name).toBe("v2.pdf");
    await user.click(screen.getByRole("button", { name: "停用手册" }));
    expect(await screen.findByRole("button", { name: "启用手册" })).toBeInTheDocument();
    expect(JSON.parse(String(calls.find(c => c.path === "/admin/manuals/m1")?.init?.body))).toEqual({ enabled: false });
  });
});
