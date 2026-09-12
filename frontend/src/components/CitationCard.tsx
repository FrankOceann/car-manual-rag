import type { Citation } from "../types";

const evidenceLabels: Record<NonNullable<Citation["evidence_type"]>, string> = {
  pdf_text: "PDF 文本",
  page_ocr: "页面 OCR",
  image_ocr: "图片 OCR",
  image_description: "模型图片描述",
};

export function CitationCard({ citation, onOpen, onOpenAsset, busy }: {
  citation: Citation;
  onOpen: (citation: Citation) => void;
  onOpenAsset?: (citation: Citation) => void;
  busy: boolean;
}) {
  const source = evidenceLabels[citation.evidence_type ?? "pdf_text"];
  const open = citation.asset_id ? (onOpenAsset ?? onOpen) : onOpen;
  return <article className="citation"><strong>{citation.manual_title}</strong><span>{`${citation.chapter_title} · PDF 第 ${citation.page_number} 页`}</span><span>{`来源：${source}`}</span><p><b>原文节选：</b>{citation.excerpt}</p>{citation.manual_id && citation.version_id && <button className="example-button" disabled={busy} onClick={() => open(citation)}>{citation.asset_id ? "查看关联图片" : `查看 PDF 第 ${citation.page_number} 页`}</button>}</article>;
}
