import { useEffect, useRef, useState, type FormEvent } from "react";
import { errorMessage, type Api } from "../api";
import type { Job, Manual, Vehicle } from "../types";

const pending = (job: Job | null) => !!job && ["queued", "parsing", "embedding"].includes(job.status);
const statusLabels: Record<Job["status"], string> = { queued: "排队中", parsing: "解析中", embedding: "向量化中", succeeded: "已完成", failed: "失败" };

export function ManualAdmin({ api, vehicles }: { api: Api; vehicles: Vehicle[] }) {
  const [manuals, setManuals] = useState<Manual[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pollTick, setPollTick] = useState(0);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    api.manuals().then(items => { if (active) setManuals(items); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);
  useEffect(() => {
    const jobs = manuals.flatMap(manual => manual.versions.map(version => version.job)).filter(pending) as Job[];
    if (!jobs.length || busy) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const updates = await Promise.all(jobs.map(job => api.job(job.id)));
        if (!active) return;
        if (updates.some(job => !pending(job))) {
          const refreshed = await api.manuals();
          if (active) setManuals(refreshed);
        } else {
          setManuals(current => current.map(manual => ({ ...manual, versions: manual.versions.map(version => ({ ...version, job: updates.find(job => job.id === version.job?.id) ?? version.job })) })));
        }
      } catch (caught) {
        if (active) { setError(errorMessage(caught)); setPollTick(tick => tick + 1); }
      }
    }, 3000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [api, manuals, busy, pollTick]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
    finally { if (mounted.current) setBusy(false); }
  };
  const upload = (event: FormEvent<HTMLFormElement>, manualId?: string) => {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    void run(async () => {
      const result = await api.upload(data, manualId);
      if (!mounted.current) return;
      setNotice(`上传成功，任务 ${result.job_id} 已进入处理队列。`); form.reset();
      const items = await api.manuals(); if (mounted.current) setManuals(items);
    });
  };
  return <section className="admin-card"><h2>手册管理</h2><p>上传 PDF 后自动解析并建立索引；新版本处理成功后成为生效版本。</p>
    <form className="form-grid upload-form" onSubmit={event => upload(event)}><fieldset disabled={busy || loading}><legend>导入新手册</legend>
      <label className="field">手册车型<select name="vehicle_id" required defaultValue=""><option value="">请选择车型</option>{vehicles.map(vehicle => <option key={vehicle.id} value={vehicle.id}>{vehicle.brand}{vehicle.model} {vehicle.year}</option>)}</select></label>
      <label className="field">手册标题<input name="title" required maxLength={200} /></label>
      <label className="field">来源<input name="source" required maxLength={4000} placeholder="例如：厂商官网" /></label>
      <label className="field">PDF 文件<input name="file" type="file" accept=".pdf,application/pdf" required /></label>
      <button>上传手册</button>
    </fieldset></form>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {loading ? <p role="status">正在加载手册…</p> : !manuals.length && <p>暂无手册，请上传第一份 PDF。</p>}
    <div className="manual-list">{manuals.map(manual => <article className="manual-item" key={manual.id}>
      <div className="section-heading"><div><h3>{manual.title}</h3><p>{manual.vehicle_id} · {manual.source}</p></div><button className="secondary" disabled={busy} onClick={() => void run(async () => {
        await api.setEnabled(manual.id, !manual.enabled);
        if (mounted.current) setManuals(items => items.map(item => item.id === manual.id ? { ...item, enabled: !manual.enabled } : item));
      })}>{manual.enabled ? "停用手册" : "启用手册"}</button></div>
      <p>{manual.enabled ? "已启用" : "已停用"} · {manual.active_version_id ? `生效版本：${manual.active_version_id}` : "暂无生效版本"}</p>
      <ul className="version-list">{manual.versions.map(version => <li key={version.id}>
        <div className="section-heading"><strong>版本 {version.id}{manual.active_version_id === version.id ? "（当前生效）" : ""}</strong><span className={`status status-${version.job?.status ?? "succeeded"}`}>{version.job ? statusLabels[version.job.status] : "已导入"}</span></div>
        <p>{version.page_count ?? 0} 页 · {version.chunk_count ?? 0} 个片段 · {new Date(version.created_at).toLocaleString("zh-CN")}</p>
        <details><summary>文件校验值</summary><code>{version.sha256}</code></details>
        {version.job && <p>任务 {version.job.id} · 已尝试 {version.job.attempts} 次</p>}
        {version.job?.error && <p className="error">{version.job.error}</p>}
        {version.job?.status === "failed" && <button disabled={busy} onClick={() => void run(async () => {
          const job = await api.retry(version.job!.id);
          if (mounted.current) setManuals(items => items.map(item => ({ ...item, versions: item.versions.map(value => value.id === version.id ? { ...value, job } : value) })));
        })}>重试</button>}
      </li>)}</ul>
      <form className="new-version" onSubmit={event => upload(event, manual.id)}><label className="field">为 {manual.title} 上传新版本<input name="file" type="file" accept=".pdf,application/pdf" required disabled={busy} /></label><button disabled={busy}>上传新版本</button></form>
    </article>)}</div>
  </section>;
}
