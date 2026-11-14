"""动作层的组织闸（`ADR-025` §2.5，第 18 轮裁定 ① 的落地形态）。

第 18 轮把「存量无组织账号」定成**渐进式强制**：数据层组织归属永久可空、不刷数据，
只在「必须依托组织才能完成的事」上拦截并提示「请先绑定组织」。

本模块就是那句话的唯一实现物，形态刻意与 `app/core/deadline.py::is_expired` 对齐：

- **纯函数**：不碰数据库、不认识 `CurrentUserContext`、不带任何上屏文案；
- **可枚举**：哪些动作算「必须依托组织」是一张 `OrganizationAction` 表，
  不是散在若干服务里的 `if context.organization is None`；
- **只判「有没有」，不判「是不是这个组织」**：跨组织的归属判断属于各自的业务
  （岗位审核读 `jobs.organization_id`，成员管理读 `organization_members`），
  塞进这里会变成一个什么都要知道的上帝函数。

为什么不带文案：本仓的 4xx 文案只允许来自 `app/core/error_messages.py`（G3 判据）。
若这个模块自己写一句「请先绑定组织」，同一个语义就会有第二个来源，而
`error_messages.py` 里那条 `JOB_ORGANIZATION_REQUIRED` 会慢慢和它漂移。
"""

from __future__ import annotations

from enum import StrEnum


class OrganizationAction(StrEnum):
    """需要组织归属才能完成的动作。

    新增取值时**必须**同时回答两件事：错误文案落在 `ErrorMessage` 的哪一条、
    以及个人版有没有被连带拦住（裁定 ① 的另一半：个人功能不因为没组织而不可用）。

    - `PUBLISH_JOB`：提交岗位审核（`ADR-024` §2.3）。裁定原文点名的动作；
    - `BULK_DOWNLOAD`：组织批量下载（`FR-08`，批次 B10b）。B6 时占位，
      `ADR-029` §2.1 起由真实端点启用——占位时写的口径没有改，这正是它的用处；
    - `VIEW_DASHBOARD`：教师端学情看板（B10a）。「查看数据」也算动作，
      因为会先读到本组织的聚合数据；
    - `EXPORT_RESUME`：单份简历 PDF 导出（B10b）。
    """

    PUBLISH_JOB = "publish_job"
    BULK_DOWNLOAD = "bulk_download"
    VIEW_DASHBOARD = "view_dashboard"
    EXPORT_RESUME = "export_resume"


class ActionRequiresOrganization(Exception):
    """动作需要组织归属，而账号还没有。

    `action` 是**机读**的，不是给用户看的：服务层拿它决定翻成哪个域异常
    （岗位侧 → `JobOrganizationRequiredError`）。
    """

    def __init__(self, action: OrganizationAction) -> None:
        super().__init__(f"action requires an organization: {action}")
        self.action = action


def require_organization(
    organization_id: str | None,
    *,
    action: OrganizationAction,
) -> str:
    """返回非空的组织 id，或抛 `ActionRequiresOrganization`。

    `organization_id` 为空串与 `None` 同等对待：数据库里这一列可空，而「空串」
    只可能来自上游某个没有归一化的拼接；把它当「有组织」放过去，后面每一处
    按 id 查成员关系都会查不到，表现为莫名其妙的 404。
    """
    if organization_id:
        return organization_id
    raise ActionRequiresOrganization(action)
