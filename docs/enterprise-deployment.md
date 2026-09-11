# 企业版本机部署与运维

本方案面向单机演示及小规模内部验证。浏览器统一访问 `http://127.0.0.1:8080`，Nginx 提供构建后的 React 页面，并将 `/api/` 转发到 FastAPI。Compose 默认不发布 PostgreSQL、Redis、Chroma 或 API 的宿主机端口。首次安装需要网络下载镜像、Python/npm 依赖和嵌入模型；完成模型准备后，检索使用本地 CPU 模型，只有生成回答需要 DeepSeek 服务。

## 首次启动

前提：Docker Engine/Desktop 正常运行 Linux x86_64 容器，Docker Compose v2+，约 8 GB 可用内存及足够保存模型、PDF、镜像和备份的磁盘空间。下面命令均在仓库根目录执行。

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32)); print(secrets.token_hex(32))"
```

把两行随机值分别填入 `.env` 的 `POSTGRES_PASSWORD`、`JWT_SECRET`，再填入个人 `DEEPSEEK_API_KEY`。数据库密码使用十六进制字符串，避免连接 URL 转义问题。不要把密钥放入前端 `VITE_*` 变量、构建参数、截图或 Git。`.env` 不随 Docker build 发送。

```powershell
docker compose config --quiet
docker compose build api nginx
docker compose up -d --wait postgres redis chroma
docker compose run --rm migrate
docker compose --profile setup run --rm prepare-model
docker compose run --rm api python -m scripts.create_admin --username admin
docker compose up -d --wait
```

后端镜像默认从官方 PyPI 获取锁定依赖。若 Docker 容器访问官方 PyPI 出现 TLS EOF，可在单次构建中显式覆盖索引地址，随后照常启动；该参数不会写入镜像运行时配置或 Git：

```powershell
docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple api nginx
```

企业网络应优先使用经安全团队批准的内部代理或镜像，并保留 `requirements.lock` 作为版本依据。

创建管理员命令交互输入密码，不把密码留在命令历史。模型只在 `prepare-model` 明确执行时下载；API/worker 的 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1` 禁止运行时隐式下载。模型卷为空时 readiness 失败，先检查模型准备步骤。不要同时运行多个 beat 实例。

打开页面登录后，进入手册管理上传自己确认可用的 PDF，观察导入任务从等待、处理到完成。按车型提问，检查引用原文与 PDF 页码。扫描版 PDF 缺少文本时会报告失败，应先取得含可提取文字的合法资料。

## 生命周期与配置

```powershell
docker compose ps
docker compose logs --tail 100 api worker beat
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/live').read().decode())"
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').read().decode())"
docker compose stop
docker compose start
```

`/health/live` 检查进程是否存活；`/health/ready` 检查当前服务依赖与本地模型是否可用。Nginx `/health` 仅表示静态网关存活，API readiness 可通过 `/api/health/ready` 访问。Compose 健康依赖控制首次启动顺序，不会因依赖后续故障自动重启其他服务。排障时结合应用日志和任务状态。

| 配置 | Compose 默认值或要求 |
| --- | --- |
| `DATABASE_URL` | 从 `.env` 的 PostgreSQL 参数组装，使用 psycopg 驱动 |
| `REDIS_URL` | `redis://redis:6379/0`，任务队列与共享运行状态 |
| `CHROMA_HOST` / `CHROMA_PORT` | `chroma` / `8000`，HTTP 客户端模式 |
| `STORAGE_PATH` | `/app/data/manuals` |
| `MODEL_PATH` | `/app/data/models/embedding` |
| `JWT_SECRET` | 必填的随机密钥；轮换后已有令牌失效 |
| `DEEPSEEK_API_KEY` | 运行时注入；为空时 readiness 失败，无法正常生成在线回答 |

持久化卷包括 PostgreSQL 元数据、Chroma 向量、原始 PDF、本地模型、Redis AOF 和 beat 调度文件。普通 `docker compose down` 保留卷；日常操作不要附加 `--volumes`。同一份 `.env`、代码版本及应用镜像也应保存到受控位置，以便灾难恢复。更改 `POSTGRES_PASSWORD` 不会自动修改已有数据库账号密码，应通过数据库管理员流程同步修改。

## 备份与恢复演练

备份脚本依赖宿主机 Python 3.11+ 和 Docker CLI，适用于 PowerShell、macOS/Linux shell。它会短暂停止网关/API/beat，再等待 worker 正常结束；随后导出 PostgreSQL，停止 Chroma/Redis 后打包数据卷，计算 SHA-256，最后恢复此前运行的服务。大模型卷也会打包，预留相应时间与磁盘。

```powershell
python deployment/backup.py --project car-manual-rag --output deployment/backups/demo-20260911
python deployment/restore.py --project car-manual-rag-restore --backup deployment/backups/demo-20260911
```

输出目录必须不存在。恢复脚本验证 manifest 与每个文件的 SHA-256，要求全新的项目名、容器和数据卷；绝不删除或覆盖现有卷。恢复只启动数据库、Redis、Chroma，避免和原项目抢占 8080。出现中断时保留部分恢复现场，再选新的项目名重试。脚本只接受自己保管的可信备份；校验和不是身份认证。

恢复依赖备份时的后端镜像 ID（用于解包）以及相同版本的 PostgreSQL、Redis、Chroma。迁移到新机器时，先通过 `docker save` / `docker load` 转移备份 manifest 中对应的应用镜像，并保留原发布代码；不要使用新数据库版本直接恢复旧卷。

演练后先核对数据库中的用户、手册、任务及问答记录，再安排切换窗口：停止原项目，让恢复项目构建并启动应用，确认登录、PDF 引用、已有任务和新导入都正常。原项目卷仍保留，可用于回滚。切换示例：

```powershell
docker compose -p car-manual-rag stop
docker compose -p car-manual-rag-restore up -d --build --wait
```

Redis 恢复包含尚未处理的队列消息；启动 worker 后观察任务恢复和幂等行为。模型版本或分块策略发生变化时，应先进行应用层重建索引验证。备份包含原始 PDF、账号密码哈希及业务记录，保存在本地受限目录，禁止提交或公开上传。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| Compose 报 `Set JWT_SECRET` / `Set POSTGRES_PASSWORD` | 在根目录创建并填写 `.env`，不要误填到 `backend/.env` |
| Docker engine `permission denied` | 确认 Docker Desktop 已启动、当前系统账号有引擎权限；不要把引擎端口公开 |
| API 一直 unhealthy | 查看 readiness 响应与 API 日志；检查模型准备、数据库迁移、Redis 和 Chroma 状态 |
| 模型下载失败 | 检查下载网络，重试 setup 命令；不要删除已存在的其他业务卷 |
| Docker 构建访问 PyPI 出现 TLS EOF | 先排查企业代理或证书；必要时单次传入受信任镜像的 `PIP_INDEX_URL` 构建参数 |
| 登录失效 | 检查令牌过期、账号状态与 `JWT_SECRET` 是否更换，重新登录 |
| 上传 HTTP 413 | Nginx 限制总请求 110 MiB；应用自己的 PDF 大小上限也会生效 |
| 导入长时间等待 | 检查 worker/Redis，确认 beat 单实例运行及任务错误详情；不要重复批量上传 |
| DeepSeek 报错或限流 | 检查 key、网络、服务配额和请求日志；密钥不要贴到问题报告 |
| 更新 API 容器后网关短暂 502 | Nginx 会重查 Docker DNS；检查 API readiness，等待恢复 |
| 备份/恢复退出非零 | 保留日志和部分目录，检查可用空间、Docker 权限、镜像存在性；使用新的输出目录或项目名重试 |

## CI 与版本维护

CI 分别运行后端离线测试、前端安装/测试/构建，以及使用真实 PostgreSQL/Redis/Chroma 的服务集成测试。集成测试用固定假嵌入模型，不调用 DeepSeek 或下载模型。`deployment/compose.test.yaml` 仅在测试时把依赖端口绑定到 loopback；正式运行不加载此覆盖文件。CI 还构建后端与网关镜像，并校验 PyTorch 不含 CUDA。

Python 依赖安装以 `backend/requirements.lock` 为准，前端使用 `npm ci` 与 `package-lock.json`。基础镜像固定补丁版本，并非不可变 digest；应定期更新补丁、重新执行 CI 和恢复演练，发布时可进一步记录镜像 digest。镜像版本与路径依据 [Docker 官方镜像清单](https://github.com/docker-library/official-images/tree/master/library)、[PostgreSQL 镜像说明](https://hub.docker.com/_/postgres)、[Chroma Docker 部署文档](https://docs.trychroma.com/guides/deploy/docker) 及 [Chroma 健康检查](https://cookbook.chromadb.dev/running/health-checks/) 核对。
