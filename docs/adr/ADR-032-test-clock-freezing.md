# ADR-032: 测试用时钟冻结（dev 依赖 `freezegun`）

- 状态：已采纳（Accepted，ADR 先行，代码随本文件同分支落地）
- 日期：2026-11-16
- 关联裁定：第 29 轮老板指令「修复跨零点脆弱用例：为 `test_expired_jobs_are_hidden_by_default_on_the_app_clock` 引入时间冻结（如 freezegun 或 pytest 的 freeze_time），固定测试当前时间，消除跨 00:00 的失败」
- 关联：`ADR-017`（过期是读侧判定）、`ADR-024` §2.4（已过期岗位不能提交/通过）

## 1. 背景：一条只在午夜红的用例

第 28 轮全量跑（23:47 → 00:00）出现 `1 failed, 815 passed`，红的正是
`tests/test_jobs.py::test_expired_jobs_are_hidden_by_default_on_the_app_clock`。取证：

- 用例开头 `today = date.today()` 取到的是 **11-15**；
- 它按「还没到期」创建四份岗位（其中一份 `deadline = today`），再逐份走
  `POST /jobs/{id}/submit-review` 发布；
- 服务端 `app/services/job_service.py` 的过期判定用 `date.today()`（2.G 的既有决定：
  **不用**东八区的 `shanghai_today()`，理由写在该文件 §1 第 1 条），
  跑到 `00:00` 之后它看到的「今天」已经是 **11-16**；
- 于是「今天截止」那份被判过期 → `submit-review` 回 409 `JOB_EXPIRED` → 断言 200 的那一行红。

单跑同一条用例 `1 passed`。所以这不是业务坏了，是**一条用例横跨了两个「今天」**：
它的正确性取决于跑到第几秒，这类红既不可复现也不可辩护，而 CI 每晚都在靠近那条线。

## 2. 决策

### 2.1 用 `freezegun` 把整条用例冻在一个时间点上

`freezegun` 的 `freeze_time("2026-11-16 09:00:00")` 替换的是 `datetime` 模块层的
`date.today()` / `datetime.now()`，因此**测试侧与服务侧同时**被钉在同一时刻——
这正是这条失效模式需要的粒度：判据在 `app/services/job_service.py` 里有五处
`date.today()`（`:227` / `:249` / `:570` / `:667` / `:840`），一处一处去 monkeypatch
既漏得快，也挡不住将来新增第六处。

固定时刻选**当天上午 09:00**，不是午夜也不是正午：上午是「离两条午夜线都远」的位置，
读用例的人一眼能确认它不会跨天。

### 2.2 为什么是 `freezegun` 而不是手写 monkeypatch

| 方案 | 结论 |
|---|---|
| `monkeypatch.setattr(job_service, "date", FakeDate)` | 要在五个调用点各自的模块里 patch，新增调用点就漏；`datetime.date` 是内置类型，不能直接 `setattr` |
| 给 `job_service` 注入 `today_override` | 要改生产代码的构造签名（`MatchService` 有 `_today_override`，`JobService` 没有）——为了测试给生产加一条可注入时钟，是「用产品代码给测试让路」，本仓的既有取向是不加的 |
| `freezegun`（选它） | 一处装饰器，测试侧与服务侧同时冻结，生产代码一行不动 |

### 2.3 依赖范围：dev group，运行时零影响

`freezegun` 只进 `[dependency-groups] dev`，不进 `dependencies`：
`app/` 里没有任何一处 import 它，CI 的运行时镜像与生产部署都不会因此变胖。
它自己拖进来的是 **`python-dateutil` → `tzdata`** 两跳（实测 `uv.lock`），
所以 dev group 净增三个包，不是「一个包」。这一点写清楚，是因为「测试依赖不算依赖」
这句话在这类地方最容易蒙混过关。

## 3. 后果

**正面**

1. 那条用例从此与「什么时候跑」无关，午夜不再需要有人盯着重跑。
2. 判据被写死成一个常量日期：将来谁改了过期判定（比如换成 `shanghai_today()`），
   这条用例会以「固定时刻下的确定行为」报红，而不是以「今天恰好跨了点」的偶发红。

**代价**

1. 多一个 dev 依赖（供应链上的一格，只在测试期被执行）。
2. `freeze_time` 是全局补丁：用例内部**不要**再混用真实时钟（比如拿 `time.monotonic()`
   算耗时），冻住之后那些数字没有意义。本批只在这一条用例上用，作用域是装饰器级的。
3. 它只修了这一条。仓库里其它用 `date.today()` 的用例（如档案「开始日期不能是未来」那组）
   理论上有同一类风险，只是窗口更窄（它们不跨调用取日期）。**没有顺手全改**——
   那是另一轮的账，等它们真的红过一次再说。

## 4. 验收判据

1. 在**跨零点的时刻**跑这条用例仍然绿（本机取证：00:0x 跑一次）。
2. 用例里不再出现 `date.today()` 与 `timedelta` 的实时组合，日期由 `FREEZE_AT` 常量给出。
3. `uv run pytest -q` 全绿；`freezegun` 只出现在 dev group，`app/` 下 grep 不到它。
