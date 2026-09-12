import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApi, errorMessage, type Api, type ApiError } from "./api";
import { AnswerCard } from "./components/AnswerCard";
import { ChapterFilter } from "./components/ChapterFilter";
import { ChatPanel } from "./components/ChatPanel";
import { ExampleQuestions } from "./components/ExampleQuestions";
import { VehicleSelector } from "./components/VehicleSelector";
import { ManualAdmin } from "./components/ManualAdmin";
import { UserAdmin } from "./components/UserAdmin";
import type { ChatResponse, Citation, Session, Vehicle } from "./types";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const expire = useCallback((token: string, caught: ApiError) => {
    setSession(current => {
      if (current?.access_token !== token) return current;
      setError(errorMessage(caught));
      return null;
    });
  }, []);
  return <main className="app-shell">
    <header><p className="eyebrow">MANUAL KNOWLEDGE BASE</p><h1>汽车维修手册助手</h1><p>只根据已导入车型手册回答，并显示对应页码。</p></header>
    {session ? <Workspace key={session.access_token} session={session} expire={expire} logout={() => { setError(""); setSession(null); }} /> :
      <Login error={error} setError={setError} onLogin={value => { setError(""); setSession(value); }} />}
  </main>;
}

function Login({ error, setError, onLogin }: { error: string; setError: (message: string) => void; onLogin: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  return <section className="admin-card login-card"><h2>登录知识库</h2><p>请使用管理员分配的账号。刷新页面后需要重新登录。</p>
    <form className="form-grid" onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError("");
      try { onLogin(await createApi().login(username.trim(), password)); }
      catch (caught) { setError(errorMessage(caught)); }
      finally { setBusy(false); }
    }}>
      <label className="field">用户名<input autoComplete="username" required value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label className="field">密码<input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></label>
      <button disabled={busy || !username.trim() || !password}>{busy ? "登录中…" : "登录"}</button>
    </form>{error && <p className="error" role="alert">{error}</p>}
  </section>;
}

function Workspace({ session, expire, logout }: { session: Session; expire: (token: string, error: ApiError) => void; logout: () => void }) {
  const api = useMemo(() => createApi(session.access_token, error => expire(session.access_token, error)), [session.access_token, expire]);
  const [page, setPage] = useState("chat");
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api.vehicles().then(items => { if (active) setVehicles(items); }).catch(caught => { if (active) setError(errorMessage(caught)); });
    return () => { active = false; };
  }, [api, page]);
  return <><div className="session-bar"><span>{session.user.username} · {session.user.role === "admin" ? "管理员" : "只读用户"}</span><button className="secondary" onClick={logout}>退出登录</button></div>
    <nav className="tabs" aria-label="工作区"><button aria-current={page === "chat" ? "page" : undefined} onClick={() => setPage("chat")}>手册问答</button>
      {session.user.role === "admin" && <><button aria-current={page === "manuals" ? "page" : undefined} onClick={() => setPage("manuals")}>手册管理</button><button aria-current={page === "users" ? "page" : undefined} onClick={() => setPage("users")}>用户管理</button></>}
    </nav>
    {error && <p className="error" role="alert">{error}</p>}
    {page === "chat" && <Query api={api} token={session.access_token} vehicles={vehicles} />}
    {session.user.role === "admin" && page === "manuals" && <ManualAdmin api={api} vehicles={vehicles} />}
    {session.user.role === "admin" && page === "users" && <UserAdmin api={api} />}
  </>;
}

function Query({ api, token, vehicles }: { api: Api; token: string; vehicles: Vehicle[] }) {
  const [vehicleId, setVehicleId] = useState("");
  const [question, setQuestion] = useState("");
  const [chapters, setChapters] = useState<string[]>([]);
  const [selectedChapters, setSelectedChapters] = useState<string[]>([]);
  const [answer, setAnswer] = useState<ChatResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdf, setPdf] = useState<{ blob: Blob; page: number } | null>(null);
  const generation = useRef(0);
  const pdfGeneration = useRef(0);
  useEffect(() => () => { generation.current++; pdfGeneration.current++; }, []);
  const changeVehicle = (id: string) => {
    generation.current++; pdfGeneration.current++;
    setVehicleId(id); setAnswer(null); setError(""); setLoading(false); setPdf(null); setPdfBusy(false);
    setChapters([]); setSelectedChapters([]);
  };
  useEffect(() => {
    let active = true;
    if (vehicleId) api.chapters(vehicleId).then(items => { if (active) setChapters(items); }).catch(caught => { if (active) setError(errorMessage(caught)); });
    return () => { active = false; };
  }, [api, vehicleId]);
  const submit = async () => {
    if (!vehicleId || !question.trim()) return;
    const current = ++generation.current;
    pdfGeneration.current++; setPdf(null); setPdfBusy(false);
    setLoading(true); setError(""); setAnswer(null);
    try { const result = await api.ask(vehicleId, question.trim(), selectedChapters.length ? selectedChapters : undefined); if (current === generation.current) setAnswer(result); }
    catch (caught) { if (current === generation.current) setError(errorMessage(caught)); }
    finally { if (current === generation.current) setLoading(false); }
  };
  const openCitation = async (citation: Citation) => {
    if (!citation.manual_id || !citation.version_id) return;
    const current = ++pdfGeneration.current;
    setPdfBusy(true); setError("");
    try {
      if (citation.asset_id) {
        const blob = await api.openAsset(citation.manual_id, citation.version_id, citation.asset_id, token);
        if (current === pdfGeneration.current) {
          const url = URL.createObjectURL(blob);
          window.open(url, "_blank", "noopener,noreferrer");
          URL.revokeObjectURL(url);
        }
      } else {
        const blob = await api.pdf(citation.manual_id, citation.version_id);
        if (current === pdfGeneration.current) setPdf({ blob, page: citation.page_number });
      }
    }
    catch (caught) { if (current === pdfGeneration.current) setError(errorMessage(caught)); }
    finally { if (current === pdfGeneration.current) setPdfBusy(false); }
  };
  return <><section className="query-card"><div className="filters"><VehicleSelector vehicles={vehicles} value={vehicleId} onChange={changeVehicle} /><ChapterFilter chapters={chapters} selected={selectedChapters} disabled={loading || !vehicleId} onChange={setSelectedChapters} /></div><div><ExampleQuestions vehicleId={vehicleId} disabled={loading} onSelect={setQuestion} /><ChatPanel question={question} disabled={!vehicleId || !question.trim()} loading={loading} onQuestionChange={setQuestion} onSubmit={submit} /></div></section>
    {error && <p className="error" role="alert">{error}</p>}
    {answer && <AnswerCard answer={answer} onOpenCitation={openCitation} pdfBusy={pdfBusy} />}
    {pdf && <PdfViewer blob={pdf.blob} page={pdf.page} onClose={() => setPdf(null)} />}
  </>;
}

function PdfViewer({ blob, page, onClose }: { blob: Blob; page: number; onClose: () => void }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const value = URL.createObjectURL(blob); setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [blob]);
  return <section className="admin-card pdf-panel"><div className="section-heading"><h2>原始手册 · PDF 第 {page} 页</h2><button className="secondary" onClick={onClose}>关闭 PDF</button></div>
    {url && <><p><a href={`${url}#page=${page}`} target="_blank" rel="noreferrer">在新窗口查看 PDF</a></p><iframe title="手册 PDF" src={`${url}#page=${page}`} /></>}
  </section>;
}
