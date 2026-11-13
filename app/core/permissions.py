"""角色与权限的唯一映射表（批次 B5，`docs/adr/ADR-021` §2.3）。

一张 4 行的静态表，刻意**不引** Casbin / OPA 这类策略引擎：本批的规则就是常量，
引进来等于背上一个依赖 + 一套 DSL + 一个新的「策略文件与代码不同步」故障面
（`AGENTS.md` §4.6 也要求新依赖先写 ADR，这次没有理由写那一篇）。

两条纪律：

1. **本模块只做纯函数**：不 import FastAPI、不碰数据库。谁是当前用户、他属于哪个组织，
   由 `app/services/dependencies.py` 查出来再传进来。
2. **前端拿到的权限与后端判定的权限同源**（都读 `ROLE_PERMISSIONS`），
   所以不会出现「前端给了按钮、后端 403」的口径分叉。但前端那份**不是**权限边界：
   每个端点自己再判一次。
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """账号在某个组织内的角色。

    `PLATFORM_ADMIN` 只作枚举值存在：不接受自助注册（ADR-021 §2.4），
    也没有对应页面——它由运维在库里授予，本批不发任何能造出它的端点。
    """

    STUDENT = "student"
    TEACHER = "teacher"
    SCHOOL_ADMIN = "school_admin"
    PLATFORM_ADMIN = "platform_admin"


#: 自助注册允许声明的角色：**没有**。批次 B6 收口（`ADR-025` §1.1）把注册请求体里的
#: `role` 一起删了，理由是「声明了角色却没有组织」只能靠静默降级来实现，而静默降级
#: 正是 `ADR-021` §2.2 要关掉的口子。角色现在只有两个来源：邀请码（`ADR-023` §2.1）
#: 与运维在库里授予 `platform_admin`。
#:
#: 同理，「教师/管理员必须带有效组织码」那条规则（`ADR-021` §2.4）也随之删除：
#: 没有「注册时声明角色」这个入口，就没有「声明了却没有组织」这个状态。
#: 这里刻意不留一个空集合或 `_DEPRECATED` 常量——留一个名字在那里，
#: 下一个人只会以为还有一条没被删干净的路径。

PROFILE_READ = "profile:read"
PROFILE_WRITE = "profile:write"
RESUME_READ = "resume:read"
RESUME_WRITE = "resume:write"
JOB_READ = "job:read"
JOB_WRITE = "job:write"
#: 审核岗位（`ADR-024` §2.2）。`docs/PRD.md:28` 写「教师……审核岗位」，
#: `:31` 写「平台管理员：处理内容审核」，所以教师及以上都有，学生没有。
JOB_REVIEW = "job:review"
INTERVIEW_OWN = "interview:own"
ORG_MEMBER_READ = "org:member:read"
ORG_MEMBER_MANAGE = "org:member:manage"
AUDIT_READ = "audit:read"

#: 学生自己那份能力。后两个角色是它的**超集**，所以这里写清楚再合并，不重复抄三遍。
_STUDENT_PERMISSIONS: frozenset[str] = frozenset(
    {
        PROFILE_READ,
        PROFILE_WRITE,
        RESUME_READ,
        RESUME_WRITE,
        JOB_READ,
        JOB_WRITE,
        INTERVIEW_OWN,
    }
)

_TEACHER_PERMISSIONS: frozenset[str] = _STUDENT_PERMISSIONS | {ORG_MEMBER_READ, JOB_REVIEW}

ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.STUDENT: _STUDENT_PERMISSIONS,
    UserRole.TEACHER: _TEACHER_PERMISSIONS,
    UserRole.SCHOOL_ADMIN: _TEACHER_PERMISSIONS | {ORG_MEMBER_MANAGE, AUDIT_READ},
    # 平台管理员的可见面比校长更宽（跨组织审计），但**不含**读写学生简历原文——
    # PRD §3 写的是「不得默认查看学生私密原文」，这条红线不因为角色高就消失。
    UserRole.PLATFORM_ADMIN: _TEACHER_PERMISSIONS | {ORG_MEMBER_MANAGE, AUDIT_READ},
}


def permissions_for(role: UserRole) -> list[str]:
    """角色 → 权限列表。返回值排序后再给前端，避免同一份权限每次顺序不同。"""
    return sorted(ROLE_PERMISSIONS[role])


def role_has_permission(role: UserRole, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS[role]
