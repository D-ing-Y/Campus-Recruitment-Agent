# Campus Agent Web

本目录是候选人画像的本地单用户 Web 入口。它只负责 HTTP 和界面适配，复用项目现有的
`RuntimeFactory`、`SessionService`、`ResumeApplicationService` 与
`CandidateApplicationService`，不复制 Candidate/Resume 业务逻辑。

## 当前能力

- 创建或恢复本地 Session。
- 上传 10 MB 以内的文本型 PDF 简历。
- 按现有八区块 ResumeEvidence 流程确认、修改、删除或重试。
- 使用 confirmed ResumeEvidence 构建候选人画像。
- 根据现有充分性评价结果回答问题、上传 PDF/Markdown/TXT 补充材料、跳过或取消。
- 查看能力、教育、经历、责任边界、unknown、冲突和画像版本变化。

当前不含登录、公开部署、OCR、DOCX、岗位研究、人岗匹配或准备计划页面。

## 本地启动

在项目根目录执行一键启动脚本：

```bash
./scripts/dev/start_web.sh
```

脚本会同步当前 Python 包、按需安装 Web 依赖、启动 API 与前端，并在服务就绪后打开
[http://localhost:3000](http://localhost:3000)。按 `Ctrl+C` 可同时停止两个服务。若不希望
自动打开浏览器，可使用 `CAMPUS_WEB_NO_OPEN=1 ./scripts/dev/start_web.sh`。

以下是需要分别控制两个进程时的手动启动方式。

先在项目根目录安装 Python 环境，再安装 Web 依赖：

```bash
cd apps/web
npm ci
```

打开两个终端，均在 `apps/web` 下运行：

```bash
npm run api
```

```bash
npm run dev
```

然后访问 [http://localhost:3000](http://localhost:3000)。API 健康检查位于
[http://127.0.0.1:8765/api/health](http://127.0.0.1:8765/api/health)。

API 默认复用项目根目录的 `data/`，因此也复用当前本地模型配置。若希望隔离演示数据：

```bash
CAMPUS_WEB_DATA_ROOT=/absolute/path/to/demo-data npm run api
```

前端 API 地址可通过 `NEXT_PUBLIC_CAMPUS_API_URL` 替换，默认是
`http://127.0.0.1:8765`。

## 验证

```bash
npm run lint
npm test
cd ../..
.venv/bin/pytest -q \
  tests/unit/test_v072_web_adapter.py \
  tests/integration/test_v072_web_candidate_vertical.py
```

`npm test` 包含 production build 与服务端渲染断言。Python 纵向测试使用离线 Mock
模型走真实 HTTP/Application Service/Graph/Repository 路径；它不等价于真实模型或真实简历验收。

## 安全边界

- 服务端身份固定为 `local-web-user`，不信任客户端提交的 `user_id` 或服务器路径。
- 浏览器只在 `localStorage` 中保存非权威 Session ID。
- 临时上传会在应用服务归档完成后删除；业务证据继续由现有 BlobStore 管理。
- 错误响应不返回绝对临时路径、正文、Prompt、Cookie、API key 或堆栈。
- 本服务只绑定本机地址，不应直接暴露到公网。

架构和验收边界见：

- `docs/04_rfc/0018-web-candidate-vertical-slice.md`
- `docs/06_contracts/web-candidate-api-contract.md`
- `docs/07_evaluation/v0.7.2-web-candidate-vertical-slice-eval-report.md`
