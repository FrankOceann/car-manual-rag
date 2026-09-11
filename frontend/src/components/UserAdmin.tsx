import { useEffect, useRef, useState } from "react";
import { errorMessage, type Api } from "../api";
import type { User } from "../types";

export function UserAdmin({ api }: { api: Api }) {
  const [users, setUsers] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    api.users().then(items => { if (active) setUsers(items); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);
  return <section className="admin-card"><h2>用户管理</h2><form className="form-grid" onSubmit={async event => {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    setBusy(true); setError("");
    try {
      const created = await api.createUser(String(data.get("username")).trim(), String(data.get("password")), data.get("role") as User["role"]);
      if (mounted.current) { setUsers(items => [...items, created]); form.reset(); }
    } catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
    finally { if (mounted.current) setBusy(false); }
  }}><fieldset disabled={busy || loading}><legend>创建账号</legend>
    <label className="field">新用户名<input name="username" autoComplete="off" required maxLength={80} /></label>
    <label className="field">初始密码<input name="password" type="password" autoComplete="new-password" required minLength={10} maxLength={256} /></label>
    <label className="field">权限<select name="role" defaultValue="reader"><option value="reader">只读用户</option><option value="admin">管理员</option></select></label>
    <button>创建用户</button>
  </fieldset></form>
    {error && <p className="error" role="alert">{error}</p>}
    {loading ? <p role="status">正在加载用户…</p> : !users.length && <p>暂无用户。</p>}
    <ul className="user-list">{users.map(user => <li key={user.id}><span><strong>{user.username}</strong> · {user.role === "admin" ? "管理员" : "只读用户"} · {user.active ? "已启用" : "已停用"}</span>
      <button className="secondary" disabled={busy} onClick={async () => {
        setBusy(true); setError("");
        try { const updated = await api.setActive(user.id, !user.active); if (mounted.current) setUsers(items => items.map(item => item.id === user.id ? updated : item)); }
        catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
        finally { if (mounted.current) setBusy(false); }
      }}>{user.active ? "停用" : "启用"} {user.username}</button>
    </li>)}</ul>
  </section>;
}
