import type { Citation } from "../types";

export function CitationCard({ citation, onOpen, busy }: { citation: Citation; onOpen: (citation: Citation) => void; busy: boolean }) {
  return <article className="citation"><strong>{citation.manual_title}</strong><span>{`${citation.chapter_title} · PDF 第 ${citation.page_number} 页`}</span><p><b>原文节选：</b>{citation.excerpt}</p>{citation.manual_id && citation.version_id && <button className="example-button" disabled={busy} onClick={() => onOpen(citation)}>查看 PDF 第 {citation.page_number} 页</button>}</article>;
}
