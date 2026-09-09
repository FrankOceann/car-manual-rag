type Props = { question: string; disabled: boolean; loading: boolean; onQuestionChange: (value: string) => void; onSubmit: () => void };

export function ChatPanel({ question, disabled, loading, onQuestionChange, onSubmit }: Props) {
  return <section className="chat-panel" aria-label="问题输入"><label className="field">问题<textarea aria-label="问题" rows={4} value={question} placeholder="例如：如何检查发动机机油液位？" onChange={(event) => onQuestionChange(event.target.value)} /></label>
    <button type="button" onClick={onSubmit} disabled={disabled || loading}>{loading ? "正在查询…" : "开始查询"}</button>
  </section>;
}
