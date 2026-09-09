import type { Citation } from "../types";

export function CitationCard({ citation }: { citation: Citation }) {
  return <article className="citation"><strong>{citation.manual_title}</strong><span>{`${citation.chapter_title} · 第 ${citation.page_number} 页`}</span><p>{citation.excerpt}</p></article>;
}
