> 【范围声明】本文件为 API 设计参考，非 Phase 1 实现依据。Phase 1 API 实现范围以 docs/MVP_SCOPE.md 为准；本文件中属于 MVP_SCOPE.md 冻结清单的接口，Phase 1 不实现、不排期、不建表。与 docs/MVP_SCOPE.md 或 AGENTS.md 冲突时，以后两者为准；本文件不扩大 Phase 1 范围。

# 接口文档

## 1. 基础约定

- Base URL：`/api/v1`
- Content-Type：`application/json`
- 时间：ISO 8601 UTC，例如 `2026-10-22T09:00:00Z`
- ID：UUID 字符串
- 鉴权：`Authorization: Bearer <access_token>`
- 分页：`page` 从 1 开始，`page_size` 默认 20，最大 100

### 1.1 统一响应

成功：

```json
{"data": {}, "meta": {"request_id": "req_123"}}
```

分页：

```json
{"data": [], "meta": {"page": 1, "page_size": 20, "total": 42, "request_id": "req_123"}}
```

失败：

```json
{"error": {"code": "AUTH_FORBIDDEN", "message": "无权访问该资源", "details": {}}, "meta": {"request_id": "req_123"}}
```

常见错误码：`VALIDATION_ERROR`、`AUTH_REQUIRED`、`AUTH_INVALID_CREDENTIALS`、`AUTH_FORBIDDEN`、`RESOURCE_NOT_FOUND`、`CONSENT_REQUIRED`、`CONFLICT`、`RATE_LIMITED`、`AI_JOB_FAILED`、`AI_OUTPUT_UNSUPPORTED`、`MEDIA_UNAVAILABLE`、`INTERNAL_ERROR`。

错误响应必须包含稳定的 `code`、面向用户的 `message`、可供日志关联的 `request_id`；不得返回堆栈、SQL、密钥、供应商原始错误或其他租户数据。测试编号、预期 HTTP 状态、输出和处理规则见 [测试文档](TESTING.md)。

## 2. 认证与用户

### POST `/auth/register`

请求：

```json
{"email":"student@example.com","password":"Strong-password-1","role":"student","organization_code":"AI2026"}
```

返回 `201`：`{"data":{"user_id":"uuid","access_token":"...","refresh_token":"...","expires_in":900}}`

### POST `/auth/login`

请求 `{"email":"student@example.com","password":"..."}`；返回同注册。登录失败统一返回 `AUTH_INVALID_CREDENTIALS`，不暴露账号是否存在。

### POST `/auth/refresh` / POST `/auth/logout`

刷新令牌轮换；注销使当前 refresh token 失效，返回 `204`。

### GET `/me`

返回当前用户、组织、角色和可用权限。

## 3. 学生档案与技能

### GET/PATCH `/students/me/profile`

字段：`name`、`school`、`major`、`grade`、`city_preference`、`target_roles`、`bio`。服务端过滤不可公开字段。

### GET/POST `/students/me/evidence`

证据包括 `type`（project/internship/certificate/link/assessment）、`title`、`description`、`skills`、`url`、`occurred_at`、`visibility`。上传文件使用预签名 URL，不把文件二进制放进 JSON。

### GET/POST/PATCH/DELETE `/students/me/skills`

技能字段：`skill_id`、`level`、`source`、`evidence_ids`、`last_used_at`。删除只解除学生关联，不删除公共技能字典。

### GET `/students/me/consents`

### POST `/students/me/consents`

请求：

```json
{"purpose":"enterprise_recommendation","fields":["email","skills","resume_summary"],"target_id":"job-uuid","expires_at":"2026-12-31T00:00:00Z"}
```

### DELETE `/students/me/consents/{consent_id}`

撤回后新请求不得继续访问；已导出的文件按保留策略失效。

## 4. 岗位

### POST `/jobs`

教师、企业/校友可创建。字段：

```json
{"company_name":"示例科技","title":"AI应用工程师","location":"深圳","employment_type":"internship","description":"...","requirements":["Python","FastAPI"],"deadline":"2026-12-01"}
```

返回 `202` 并创建 `ai_job_id`，用于异步岗位分析。

### GET `/jobs` / GET `/jobs/{job_id}`

支持 `keyword`、`status`、`location`、`skill_id`、`page`、`page_size`。未审核岗位仅创建者和审核者可见。

### POST `/jobs/{job_id}/analyze`

返回 `202`：`{"data":{"ai_job_id":"uuid","status":"queued"}}`。重复请求使用幂等键，不能重复扣费或创建并发任务。

### POST `/jobs/{job_id}/publish` / POST `/jobs/{job_id}/close`

需要审核权限；过期岗位自动转为 `expired`。

## 5. 测评、匹配与成长

### GET `/assessments`

按 `skill_id`、`level`、`version` 查询可用题目集合，不返回答案。

### POST `/assessments/{assessment_id}/attempts`

创建测评尝试，返回 `attempt_id`。答案提交到 `POST /assessment-attempts/{attempt_id}/answers`，完成使用 `POST /assessment-attempts/{attempt_id}/submit`。

### POST `/match-runs`

请求：`{"job_id":"uuid","student_ids":["uuid"],"weight_version":"v1"}`。返回异步 `match_run_id`。

### GET `/match-runs/{match_run_id}` / GET `/match-runs/{match_run_id}/results`

结果包含 `score`、`matched_requirements`、`gaps`、`evidence`、`explanation`、`data_as_of`，不返回不可解释的单一黑盒结论。

### POST `/growth-plans`

请求：`{"job_id":"uuid","duration_days":30,"focus_gap_ids":["uuid"]}`。返回异步 AI 任务；任务完成后生成可编辑计划。

## 6. 简历

### POST `/resumes/generate`

请求：`{"student_id":"uuid","job_id":"uuid","template":"clean_white","max_pages":1.5}`。只能使用学生已确认资料，返回 `202` 和 `ai_job_id`。

### GET `/resumes` / GET `/resumes/{resume_id}`

返回版本、来源、确认状态和授权状态。

### PATCH `/resumes/{resume_id}/versions/{version_id}`

更新编辑后的区块。保存新版本，禁止覆盖审计历史。

### POST `/resumes/{resume_id}/export`

请求 `{"version_id":"uuid","format":"pdf"}`；返回异步任务。完成后返回短时下载 URL。

### POST `/resumes/{resume_id}/share`

请求 `{"version_id":"uuid","target_type":"enterprise","target_id":"uuid","fields":["contact","skills","projects"],"expires_at":"..."}`。

## 7. 模拟面试

### POST `/interviews`

请求：`{"job_id":"uuid","resume_version_id":"uuid","mode":"text","round":"technical","question_count":8}`。返回 `interview_id`。

### POST `/interviews/{interview_id}/turns`

文本模式请求 `{"answer":"..."}`；返回下一问题或结束状态。音视频模式仅提交转写片段和时间戳。

### POST `/interviews/{interview_id}/finish`

结束会话并创建反馈 AI 任务；返回 `202`。

### GET `/interviews/{interview_id}/report`

只对本人或获授权教师返回，包含回答证据、建议和待练习技能，不输出录用保证。

### POST `/interviews/{interview_id}/media-room`

返回短时 WebRTC 房间 token、ICE 配置引用和过期时间。业务 API 不接收长期媒体密钥；媒体供应商通过适配器实现。

## 8. 教师与学校

### GET `/teacher/classes/{class_id}/dashboard`

参数 `from`、`to`、`job_id`；返回聚合人数、技能缺口、目标岗位分布、任务完成率。默认隐藏小样本可识别数据。

### GET `/teacher/jobs/{job_id}/candidates`

只返回学生已授权摘要；支持 `min_score`、`class_id`、`skill_id`、分页。

### POST `/teacher/jobs/{job_id}/candidates/export`

请求导出字段白名单；返回异步导出任务。每次导出记录审计。

### GET `/school/analytics/skills`

学校管理员可查看脱敏的技能趋势、岗位-课程覆盖和专业缺口。

## 9. AI 任务与实时状态

### GET `/ai-jobs/{ai_job_id}`

返回：

```json
{"data":{"id":"uuid","type":"job_analysis","status":"running","progress":40,"retry_count":0,"error_code":null}}
```

### GET `/ai-jobs/{ai_job_id}/events`

SSE，事件 `progress`、`succeeded`、`failed`；客户端断线可用 `Last-Event-ID` 恢复。不得通过 SSE 推送 API Key 或未授权正文。

## 10. 幂等、并发和权限

- 创建 AI 任务、导出和分享接口支持 `Idempotency-Key`，服务端保存 24 小时结果。
- 资源读取按组织、角色、资源归属和 consent 联合判断，不能只依赖前端隐藏按钮。
- 并发编辑使用 `If-Match`/版本号；冲突返回 `CONFLICT` 和最新版本摘要。
- 管理员批量操作限制数量并要求二次确认。

## 11. OpenAPI 与联调

FastAPI 自动生成 `/openapi.json` 和 `/docs`；生产环境只在内网或通过管理员权限开放。前端根据稳定的 OpenAPI schema 生成 TypeScript 类型，禁止手写与接口不一致的重复类型。接口变更需增加版本或兼容字段，并更新本文件和契约测试。

## 12. 安全与 AI 输出约束

- 所有 `{id}` 路径参数先做组织级对象授权，再读取字段；不能仅依赖角色判断。
- `source_ids`、`evidence_ids` 和 `consent_id` 必须属于当前组织且处于有效授权状态。
- 生成接口返回 `ai_job_id`，不把未核验生成文本伪装成最终事实。
- AI 输出可包含 `support_status`：`supported`、`unsupported`、`needs_review`；`unsupported` 的关键事实必须阻断发布或导出。
- 错误码与日志事件一一对应，错误消息不泄露资源是否存在（例如未授权对象统一使用 `RESOURCE_NOT_FOUND` 或 `AUTH_FORBIDDEN`，按安全策略选择）。
