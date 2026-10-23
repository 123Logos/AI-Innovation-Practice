# Agent 交接文件
> 最后更新：2026-10-23 | 交接至 T2.5 执行前

## 1. 当前阶段

骨架阶段向第一条垂直切片过渡；T1–T2 已完成，T2.5 待执行。

**当前仓库的关键背景（新 agent 必须知道）：**

1. 仓库中有四份**体积不小的旧设计文档**真实存在，它们是上一阶段 agent 的产物，不是空壳：
   - `docs/PRD.md`（约 14KB，含完整产品功能描述，大概率包含冻结项）
   - `docs/API.md`（约 8.8KB）
   - `docs/DATABASE.md`（约 5.2KB，大概率对应 20+ 张表）
   - `docs/TESTING.md`（约 10.7KB）
2. T1.6 已为这四份文档在首行加入【范围声明】，明确“非 Phase 1 实现依据，冲突时以 `docs/MVP_SCOPE.md` 为准，不扩大 Phase 1 范围”。**这四份文档只读参考，禁止据此建表、建接口、扩测试范围。**
3. 因此，当你在 T4 建数据库时，**只看 `docs/MVP_SCOPE.md` 第 6 节（5 张表）和 Alembic 迁移**，不要打开 `docs/DATABASE.md` 找表结构；当你在 T5 写接口时，**只看 `MVP_SCOPE.md` 第 4 节 F1–F10**，不要打开 `docs/API.md` 找接口清单。
4. 若你认为某条冻结功能“必须提前做”，停下来向人类说明理由，等待书面批准，不得自行开工。

## 2. 已完成任务（按时间顺序）
| 任务 | 内容 | 验收结论 | 可复现证据（新 agent 可自行运行复核） |
|---|---|---|---|
| T1 | schema.sql 移到 docs/design/，PROJECT_STATUS 资产分级 | 通过（第二次补交） | `Get-ChildItem -Recurse -Filter schema.sql` 应无输出；`rg -n "本文件是领域设计草案" docs/design/schema-draft.sql` 命中第 1 行；`rg -n "保留复用|降级为设计稿|待补承重" docs/PROJECT_STATUS.md` 命中 13/22/32 行 |
| T1.5 | 约定文件重命名为 AGENT_CONVENTIONS.md + 定位声明 | 通过 | `Test-Path docs/Convention_on_Intelligent_Agents.md` 应为 False；`rg -n "AGENTS.md 的补充|禁止自行创建|前端技术栈见 ADR-001|T 级任务收尾" docs/AGENT_CONVENTIONS.md` 命中 4 行 |
| T1.6 | 四份旧文档加范围声明 + AGENT_CONVENTIONS 优先级 | 通过 | `rg -n "范围声明" docs/PRD.md docs/API.md docs/DATABASE.md docs/TESTING.md` 四份均命中第 1 行；`rg -n "上述文档不得扩大 Phase 1 范围" docs/AGENT_CONVENTIONS.md` 命中 1 行 |
| T2 | MVP_SCOPE 与 AGENTS 对照 | 通过（第二次补交，结论更正为存在差异） | 差异结论：实质缺失 1 条（AGENTS 侧缺“小程序 / 原生 App”，即 T2.5 要补的内容）；措辞差异 5 条（第 1/2/3/7/8 条），语义等价，不强制统一；完全一致 3 条（第 4/5/6 条）。复现方式：按第 7 节完成标准 1 修正脚本后重跑 Compare-Object |

> 说明：以上命令可在当前仓库直接运行复核。若某条命令结果与表中不符，说明仓库状态已变，先查 `git log` 与 `git status`，不要直接信任本表。

## 3. 当前未完成任务
- T2.5：给 AGENTS.md 3.2 补“小程序 / 原生 App”，并修正 Compare-Object 脚本区间 bug 重跑
- T3：补写/校对 ADR-001、ADR-002（ADR-001 待人类拍板前端是否 Vue3+TS+Vite）
- T4–T7：见 AGENTS.md 第 8 节

## 4. 已踩过的坑（下一个 agent 必须避免）

1. 交付第二段只列命令不贴输出 → 被 T1 打回；
2. 用“逐项一致”掩盖实质差异 → 被 T2 打回；
3. Compare-Object 脚本区间定位 bug 后用人工对照表绕开 → T2.5 必须修脚本重跑，不得用人工判断替代工具输出；
4. 计数不自洽（说 3 处却列 5 个编号）→ 交付前必须自检数字；
5. 人类手动修改过文件后，agent 交付时未声明“人类已改过”，只写“已按要求修改” → 责任归属不清。今后凡人类在任务期间改过文件，agent 必须在“改动文件”一节注明：`<文件>：人类于 <时间/轮次> 手动修改过 <内容>，本次仅做了 <格式/补充>`；
6. 用“工作区原有状态”一句话带过所有未提交变更 → T1.6 时 `git status` 混入 `schema.sql` 删除和 `PROJECT_STATUS.md` 修改，只用一句“此前已有状态”解释，导致 `git diff --stat` 无法区分本次与历史变更。今后必须用 `git diff --numstat -- <本次允许修改的路径>` 做限定路径的精确统计，并在交付中同时贴总 diff 与限定 diff。

## 5. 当前工作区状态（未提交/已提交）

```text
 M docs/DEVELOPMENT.md
?? docs/AGENT_HANDOFF.md
```

状态解读：

M docs/DEVELOPMENT.md：T2 时新增“UTF-8 无 BOM”约定，工作区修改、未暂存，与 T2.5 无关；

?? docs/AGENT_HANDOFF.md：本交接文件，未跟踪，尚未 commit。

最近一次提交：chore: lock Phase 1 scope and agent work protocol (T1/T1.5/T1.6)

说明：T2 与 T2 补交均为只读对照任务，无文件变更，故无对应 commit。T2.5 尚未执行，未产生任何变更。

## 6. 优先级链条（必须遵守）

```text
AGENTS.md
  > docs/MVP_SCOPE.md（产品范围）
  > docs/adr/（技术决策，如 ADR-001 响应式 Web、ADR-002 AI 先用 stub）
  > docs/AGENT_CONVENTIONS.md（协作细节）
  > docs/PRD.md / API.md / DATABASE.md / TESTING.md（仅参考，不得扩大 Phase 1 范围）
```

冲突时从上往下取；ADR 与 MVP_SCOPE 冲突时以 MVP_SCOPE 为准（范围优先于技术选型）。

## 7. 下一步唯一允许执行的任务
T2.5，指令如下：

【主任务】补 AGENTS.md 3.2 的“小程序 / 原生 App”一条。

【验证手段】修正 Compare-Object 脚本后重跑，作为补完后的验证，不是主任务本身。

【范围】本次只允许修改：
- AGENTS.md（仅在 3.2 冻结清单末尾新增一条：`- 小程序 / 原生 App`）
禁止修改任何其他文件。

【目标行为】
AGENTS.md 3.2 冻结清单包含“小程序 / 原生 App”，与 docs/MVP_SCOPE.md 第 5 节第 9 条对齐。

【完成标准】
1. 修正 Compare-Object 脚本的区间定位 bug（用 [Array]::IndexOf($agents, '---', $agentStart) 或等价方式），**贴修正后的脚本原文**，重新运行并贴“命令 + 完整原始输出 + 退出码”；
2. Compare-Object 输出中，“小程序 / 原生 App”**不得再出现在 `<=`（仅 MVP_SCOPE 侧）**，即已补到 AGENTS 侧；其余 SideIndicator 若存在，必须全部是措辞差异，逐条在对照表中标注“语义等价”，不得有新的实质缺失；
3. 手工对照表更新为 9 条，标注每条状态，并明确写出“措辞差异 X 处 / 完全一致 Y 处 / 实质缺失 0 处”，X+Y=9；
4. git diff --stat 贴原始输出（应只显示 AGENTS.md 一行变更）；
5. git status --short 贴原始输出。

【禁止】修改 MVP_SCOPE.md；修改其他 AGENTS.md 内容；新增文件；触碰冻结清单。

【交付格式】改动文件 / 验证命令与原始输出（含退出码）/ 对照表（计数必须自洽）/ DoD 对照 / 范围自检 / 未解决问题
