import { useEffect, useState } from "react";
import { askQuestion, getManualChapters, getVehicles } from "./api";
import { AnswerCard } from "./components/AnswerCard";
import { ChapterFilter } from "./components/ChapterFilter";
import { ChatPanel } from "./components/ChatPanel";
import { ExampleQuestions } from "./components/ExampleQuestions";
import { VehicleSelector } from "./components/VehicleSelector";
import type { ChatResponse, Vehicle } from "./types";

export default function App() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [vehicleId, setVehicleId] = useState("");
  const [question, setQuestion] = useState("");
  const [chapters, setChapters] = useState<string[]>([]);
  const [selectedChapters, setSelectedChapters] = useState<string[]>([]);
  const [answer, setAnswer] = useState<ChatResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { getVehicles().then(setVehicles).catch(() => setError("无法连接后端服务，请确认后端已启动。")); }, []);
  useEffect(() => {
    let active = true;
    setChapters([]); setSelectedChapters([]);
    if (!vehicleId) return () => { active = false; };
    getManualChapters(vehicleId)
      .then((items) => { if (active) setChapters(items); })
      .catch(() => { if (active) setError("无法加载该车型的手册章节。") });
    return () => { active = false; };
  }, [vehicleId]);

  const submit = async () => {
    if (!vehicleId || !question.trim()) return;
    setLoading(true); setError(""); setAnswer(null);
    try { setAnswer(await askQuestion(vehicleId, question.trim(), selectedChapters.length ? selectedChapters : undefined)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "查询失败，请稍后重试。"); }
    finally { setLoading(false); }
  };

  return <main className="app-shell"><header><p className="eyebrow">LOCAL RAG DEMO</p><h1>汽车维修手册助手</h1><p>只根据已导入车型手册回答，并显示对应页码。</p></header>
    <section className="query-card"><div className="filters"><VehicleSelector vehicles={vehicles} value={vehicleId} onChange={setVehicleId} disabled={loading} /><ChapterFilter chapters={chapters} selected={selectedChapters} disabled={loading || !vehicleId} onChange={setSelectedChapters} /></div><div><ExampleQuestions vehicleId={vehicleId} disabled={loading} onSelect={setQuestion} /><ChatPanel question={question} disabled={!vehicleId || !question.trim()} loading={loading} onQuestionChange={setQuestion} onSubmit={submit} /></div></section>
    {error && <p className="error" role="alert">{error}</p>}
    {answer && <AnswerCard answer={answer} />}
  </main>;
}
