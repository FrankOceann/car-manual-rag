export type Vehicle = { id: string; brand: string; model: string; year: number };

export type Citation = {
  manual_title: string;
  chapter_title: string;
  page_number: number;
  excerpt: string;
};

export type ChatResponse = {
  answer: string;
  steps: string[];
  warnings: string[];
  citations: Citation[];
  grounded: boolean;
};
