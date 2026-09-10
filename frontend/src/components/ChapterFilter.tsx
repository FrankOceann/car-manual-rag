type Props = {
  chapters: string[];
  selected: string[];
  disabled: boolean;
  onChange: (chapters: string[]) => void;
};

export function ChapterFilter({ chapters, selected, disabled, onChange }: Props) {
  if (!chapters.length) return null;
  const toggle = (chapter: string) => onChange(
    selected.includes(chapter)
      ? selected.filter((item) => item !== chapter)
      : [...selected, chapter],
  );
  return <fieldset className="chapter-filter" disabled={disabled}>
    <legend>限定章节（可选）</legend>
    <p>不选择时检索整本已导入手册。</p>
    <div className="chapter-options">{chapters.map((chapter) => <label key={chapter}>
      <input type="checkbox" checked={selected.includes(chapter)} onChange={() => toggle(chapter)} /> {chapter}
    </label>)}</div>
  </fieldset>;
}
