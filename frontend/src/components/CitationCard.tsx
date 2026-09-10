import type { Citation } from "../types";

export function CitationCard({ citation }: { citation: Citation }) {
  return <article className="citation"><strong>{citation.manual_title}</strong><span>{`${citation.chapter_title} · PDF 第 ${citation.page_number} 页`}</span><p><b>原文节选：</b>{citation.excerpt}</p></article>;
}
