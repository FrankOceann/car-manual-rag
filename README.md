# 汽车维修手册 RAG

本项目为比亚迪海豹、丰田卡罗拉和本田思域提供车型隔离的检索问答演示。回答仅基于本地已导入的资料，并会显示手册页码引用；资料不足时不会编造维修步骤。

资料来源和版权状态记录在 [docs/data-sources.md](docs/data-sources.md)。在对应条目确认并标记为“已确认可用”前，手册不得导入。

完整原厂 PDF 不会提交到仓库。仅可将用户本地取得、并在 `docs/data-sources.md` 中标记为“已确认可用”的资料导入本机向量库。

## 运行项目

在 `backend/` 中复制 `.env.example` 为 `.env`，仅在该本地文件中填入 `DEEPSEEK_API_KEY`。然后执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```

另开一个终端启动网页：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173/`，选择车型并提交问题。

前端默认请求本机 `http://127.0.0.1:8001`。部署到其他地址时，在 `frontend/.env.local` 中设置 `VITE_API_BASE_URL`。

## 导入本地 PDF

将 PDF 放在 `backend/data/manuals/`，确认其资料来源行的状态为“已确认可用”后执行：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\import_manual.py --vehicle-id honda-civic --pdf data\manuals\honda-civic-2024-owner-manual.pdf --title "2024 Honda Civic Owner's Manual"
```

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -v

cd ..\frontend
npm test -- --run
npm run build
```

可复现的问题见 [docs/test-questions.md](docs/test-questions.md)。

## 离线检索评测

先按上面的步骤安装依赖并导入已确认可用的思域和卡罗拉资料。评测需要本地 Chroma 库及已缓存的嵌入模型，不调用 DeepSeek，也不下载模型；无需 API 密钥。

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/evaluate_retrieval.py --mode baseline
.\.venv\Scripts\python.exe scripts/evaluate_retrieval.py --mode hybrid
```

默认读取 `backend/evals/cases.yaml` 的 14 道人工题：6 道思域、4 道卡罗拉和 4 道海豹拒答题。`--cases <YAML路径>` 可指定题集。预期页码为 PDF 物理页序号（从 1 开始），不是印刷页码；卡罗拉第 16–18 页对应印刷页 224–226。`expected_chapters` 保存导入的章节标签，仅作预期结果记录，不作为检索过滤器，以免把答案范围泄露给检索器。

两种模式均最多返回 4 个证据块，距离阈值为 1.1。报告指标定义如下：

| 指标 | 定义 |
| --- | --- |
| Recall@1 / Recall@3 | 前 1 / 3 个原始证据块是否命中任一预期页，命中计 1，否则计 0 |
| MRR | 首个命中预期页的原始证据块排名的倒数，未命中计 0 |
| citation_candidate_precision | 前 4 个证据块按手册标题和页码去重后，属于预期页的候选比例；无证据计 0 |
| refusal_accuracy | 命中题是否有证据、拒答题是否无证据；符合预期计 1，否则计 0 |

前三类相关性指标仅在 `should_grounded: true` 的题目上取算术平均；拒答题不影响该分母，没有命中题的车型显示 `N/A`（JSON 为 `null`）。`refusal_accuracy` 在全部题目上取平均，只衡量证据有无，不代表答案正确性。总体按题目平均，另按车型汇总。

每次运行仅在被 Git 忽略的 `backend/data/eval-reports/` 下生成带 UTC 时间戳的 JSON 和 Markdown，包含逐题返回页、命中页、首个命中排名、失败原因；JSON 另含候选块 ID、距离和章节。报告位置不受当前工作目录影响。未知车型、空问题、重复 ID、无预期页的命中题和未导入车型的命中题会报错并以非零状态退出，配置错误会指出题目 ID。指标中的正常漏检仍生成报告，以便比较基线和混合检索的真实表现。
