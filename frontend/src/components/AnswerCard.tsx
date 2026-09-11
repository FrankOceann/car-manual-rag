import type { ChatResponse, Citation } from "../types";
import { CitationCard } from "./CitationCard";

export function AnswerCard({ answer, onOpenCitation, pdfBusy }: { answer: ChatResponse; onOpenCitation: (citation: Citation) => void; pdfBusy: boolean }) {
  if (!answer.grounded) return <section className="answer-card no-evidence"><h2>暂无可靠手册依据</h2><p>{answer.answer}</p></section>;
  return <section className="answer-card"><h2>手册建议</h2><p className="answer">{answer.answer}</p>
    {answer.steps.length > 0 && <><h3>操作步骤</h3><ol>{answer.steps.map((step) => <li key={step}>{step}</li>)}</ol></>}
    {answer.warnings.length > 0 && <><h3>注意事项</h3><ul className="warnings">{answer.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></>}
    <h3>手册引用</h3><div className="citations">{answer.citations.map((citation, index) => <CitationCard key={`${citation.page_number}-${index}`} citation={citation} onOpen={onOpenCitation} busy={pdfBusy} />)}</div>
  </section>;
}
