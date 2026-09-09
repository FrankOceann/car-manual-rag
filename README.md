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
