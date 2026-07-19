# Cloud Deployment

云服务器部署放在 v3.0 之后：

- FastAPI 服务化。
- Docker Compose。
- SQLite 或 Postgres。
- 可选 Qdrant / pgvector。
- 可选 Langfuse / LangSmith。

需要真实浏览器登录的网站仍由 Mac 处理正常登录和 Cookie/cURL 导入。云端 Graph 只能接收
credential ref；在远程 secret manager 与权限模型完成前，v0.5 live adapter 保持本地运行。
