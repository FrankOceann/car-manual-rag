# 演示测试问题

| 场景 | 车型 | 问题 | 预期结果 |
| --- | --- | --- | --- |
| 已导入资料检索 | 本田思域 2024 | 如何检查发动机机油液位？ | 显示操作步骤、注意事项与第 657、658 页等引用；`grounded: true`。 |
| 同车型变体问题 | 本田思域 2024 | 机油液位低于下限怎么办？ | 只依据本田思域手册回答并给出页码。 |
| 尚未导入资料 | 丰田卡罗拉 2024 | 胎压警告是什么意思？ | 明确提示资料不足；无维修步骤、无引用；`grounded: false`。 |
| 尚未导入资料 | 比亚迪海豹 2024 | 如何检查轮胎？ | 明确提示资料不足；无维修步骤、无引用；`grounded: false`。 |
| 接口校验 | 不支持车型 | 任意问题 | API 返回 422，不会跨车型检索。 |
| 接口校验 | 本田思域 2024 | 空白问题 | API 返回 422。 |

本表中的官方手册问题仅用于本地演示；资料的版权和访问条件以 [data-sources.md](data-sources.md) 为准。

## 2026-09-10 离线检索评测结果

评测命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -v
cd ..\frontend
npm test -- --run
npm run build
cd ..\backend
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --mode baseline
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --mode hybrid
```

本次结果来自本地生成的忽略文件：

- `backend/data/eval-reports/20260910T140928999282Z-baseline.json`
- `backend/data/eval-reports/20260910T140947908625Z-hybrid.json`

评测题集共 14 个用例：10 个有手册依据的检索用例，4 个比亚迪未导入手册的拒答用例。车型分布为 Honda Civic 6 个、Toyota Corolla 4 个、BYD Seal 4 个。页码使用 PDF 物理页序号，与导入块元数据一致。

| 模式 | 用例数 | 有依据用例 | Recall@1 | Recall@3 | MRR | 候选引用精度 | 拒答准确率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 14 | 10 | 0.400 | 0.900 | 0.600 | 0.275 | 1.000 |
| hybrid | 14 | 10 | 0.100 | 0.100 | 0.100 | 0.025 | 1.000 |

混合检索在这组真实题集上是明确回归，不是改进：Recall@3 从 0.900 降到 0.100，MRR 从 0.600 降到 0.100，候选引用精度从 0.275 降到 0.025。拒答准确率保持 1.000，只说明未导入 BYD 手册时没有跨车型返回证据，不代表回答质量达标。

已知失败：

| 模式 | 用例 | 期望页 | 返回页 | 失败原因 |
| --- | --- | --- | --- | --- |
| baseline | `civic-trunk-opener` | 189 | 23, 186, 178, 200 | 未检出期望页 |
| hybrid | `civic-trunk-opener` | 189 | 23, 1, 186, 49 | 未检出期望页 |
| hybrid | `civic-trailer-towing` | 449 | 3, 63, 46, 1 | 未检出期望页 |
| hybrid | `civic-engine-oil-grade` | 656 | 32, 3, 7, 5 | 未检出期望页 |
| hybrid | `civic-oil-check-wait` | 657 | 32, 15, 1, 3 | 未检出期望页 |
| hybrid | `civic-jump-start-voltage` | 724 | 8, 4, 176, 3 | 未检出期望页 |
| hybrid | `civic-emergency-towing` | 742 | 3, 33, 18, 7 | 未检出期望页 |
| hybrid | `toyota-emergency-stop-purpose` | 16 | 1, 2, 4, 12 | 未检出期望页 |
| hybrid | `toyota-emergency-stop-cancel` | 17 | 1, 3, 4, 4 | 未检出期望页 |
| hybrid | `toyota-emergency-stop-hold` | 18 | 1, 4, 3, 2 | 未检出期望页 |
