# 汽车维修手册 RAG

本项目基于公开可获取或已获授权的原厂手册，为比亚迪海豹、丰田卡罗拉和本田思域提供车型隔离的检索问答演示。

资料来源和版权状态记录在 [docs/data-sources.md](docs/data-sources.md)。在对应条目确认并标记为“已确认可用”前，手册不得导入。

## 后端开发

在 `backend/` 中复制 `.env.example` 为 `.env`，仅在该本地文件中填入 `DEEPSEEK_API_KEY`。运行测试：

```bash
uv run pytest -v
```
