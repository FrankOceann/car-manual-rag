const EXAMPLES: Record<string, string[]> = {
  "honda-civic": ["如何检查发动机机油液位？", "跨接启动应使用多少伏的辅助电池？", "紧急拖车时应该使用哪种运输设备？"],
  "toyota-corolla": ["紧急驾驶停止系统在什么情况下启动？", "紧急驾驶停止系统如何取消？"],
  "byd-seal": ["标准轮胎气压是多少？"],
};

type Props = { vehicleId: string; disabled: boolean; onSelect: (question: string) => void };

export function ExampleQuestions({ vehicleId, disabled, onSelect }: Props) {
  const examples = EXAMPLES[vehicleId] ?? [];
  if (!examples.length) return null;
  return <section className="examples" aria-label="示例问题"><p>示例问题</p><div>{examples.map((question) => <button key={question} type="button" className="example-button" disabled={disabled} onClick={() => onSelect(question)}>{question}</button>)}</div></section>;
}
