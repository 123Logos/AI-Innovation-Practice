# AI赋能职业发展和面试指导平台

这是一个面向高校人工智能及相关专业的前后端分离职业成长平台设计项目，覆盖岗位洞察、能力证据、成长任务、人岗匹配、AI 简历、模拟面试和教学分析。

## 文档入口

- [产品需求文档](docs/PRD.md)：用户、范围、功能、创新点、指标和验收标准
- [开发与架构文档](docs/DEVELOPMENT.md)：技术栈、目录、AI、安全、测试和部署
- [接口文档](docs/API.md)：REST/SSE/WebRTC 适配层、数据格式、错误码和权限
- [数据库设计](docs/DATABASE.md)：MySQL 逻辑模型、约束、索引原则和第一版 DDL
- [测试文档](docs/TESTING.md)：测试策略、错误编号、用例、预期输出和发布门槛
- [项目当前进度](docs/PROJECT_STATUS.md)：已实现范围、验证结果、待办事项和 AI 更新协议
- [.env.example](.env.example)：不含真实密钥的配置模板
- [产品设想文档](产品设想文档.md)：原始需求与扩展建议

## 技术方案

- 后端：`uv` + Python 3.13 + FastAPI + MySQL 8
- 前端：Vue 3 + TypeScript + Vite + Element Plus
- AI：后端服务端调用 DeepSeek API
- 异步任务：Redis + Worker
- 文件：S3 兼容私有对象存储
- 部署：Docker Compose + 反向代理 + HTTPS

当前项目使用 MySQL 数据库 `ai_innovation_practice`。本地配置请以 `.env.example` 为模板，
并通过 `DATABASE_URL` 指向该数据库。

## 配置安全

```powershell
Copy-Item .env.example .env
```

开发环境可以使用本地配置；生产环境必须由部署平台注入环境变量，并至少替换：

- `JWT_SECRET_KEY`：至少 32 位强随机字符串
- `MYSQL_PASSWORD`、`DATABASE_URL`
- `DEEPSEEK_API_KEY`
- `S3_ACCESS_KEY_ID`、`S3_SECRET_ACCESS_KEY`
- `FRONTEND_URL`、`ALLOWED_ORIGINS`

真实 `.env`、数据库备份、对象存储密钥和 JWT 密钥不得提交到代码仓库。DeepSeek Key 只允许后端使用，不能写入 Vue 环境变量或浏览器代码。

## 设计原则

1. 前后端职责分离，所有敏感业务校验在后端执行。
2. AI 只提供可追溯、可编辑的建议，不直接替代教师或企业作出录用决定。
3. 匹配结果必须可解释，学生数据按组织、角色和授权隔离。
4. 组件、颜色、表单、空状态和错误状态统一，白色为主并支持移动端。
5. 外部 AI/媒体服务不可用时，核心档案和岗位数据仍可访问，任务可重试。

## 研究与治理基线

项目设计参考 NIST AI RMF、OWASP API Security Top 10、W3C WebRTC、ISO/IEC 25010:2023，以及 RAG 和 FActScore 相关论文。具体落地要求与引用见 [产品需求文档](docs/PRD.md)、[开发与架构文档](docs/DEVELOPMENT.md) 和 [测试文档](docs/TESTING.md)。

## 本地开发约定

后端代码落地后：

```powershell
uv venv --python 3.13
uv sync
uv run uvicorn app.main:app --reload
```

前端代码落地后：

```powershell
cd frontend
npm install
npm run dev
```

部署、迁移、测试、监控和接口联调要求以 [开发与架构文档](docs/DEVELOPMENT.md) 和 [接口文档](docs/API.md) 为准。
