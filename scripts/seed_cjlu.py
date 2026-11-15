"""注入「中国计量大学 + 教师侧账号」这批测试数据。

**为什么是脚本而不是端点**：`school_admin` / `teacher` 不能自助获得——谁能调用
「建一个管理员」的 HTTP 入口，谁就能给自己提权，那等于没有角色边界（同一理由见
`scripts/seed_organization.py` 的文件头与 `ADR-021` §2.2）。所以建学校、建管理员、
建教师这三件事留在运维脚本里。

用法：

    uv run python scripts/seed_cjlu.py        # 直接按文件跑（脚本自己补 sys.path）
    uv run python -m scripts.seed_cjlu        # 与 `seed_organization.py` 同一支调用方式

**幂等**：同一份数据跑第二次不会报错、不会重复建、不会改已经动过的角色之外的字段。
具体三态：

| 情况 | 这一次做什么 |
|---|---|
| 组织码不存在 | 建组织 + 一条 `organization.create` 审计（`actor_user_id=None`，系统动作不假造操作者） |
| 组织码已存在 | 跳过，取它的 id |
| 账号不存在 | 建用户（密码 `hash_password` 走 bcrypt，与注册路径同一支）+ 建成员关系 + 一条 `member.join` 审计 |
| 账号已存在、角色不同 | **改成目标角色** + 一条 `member.role_change` 审计，并打印出来 |
| 账号已存在、角色相同 | 什么都不做，只报「已就位」 |

最后一律回读一遍数据库再打印，所以那行 ✅ 是**查询结果**，不是「我以为写进去了」。
"""

from __future__ import annotations

import sys
from pathlib import Path

# `uv run python scripts/seed_cjlu.py` 这种按文件直跑时，`sys.path[0]` 是 scripts/，
# 包根（仓库根）不在搜索路径里，`import app` 会直接 ModuleNotFoundError。
# 补这一行是为了让老板写在指令里的那条命令真的能跑；用 `-m` 跑时这段是空操作。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password
from app.repositories.audit_log_repository import (
    AuditAction,
    AuditLogRepository,
)
from app.repositories.organization_repository import (
    OrganizationRepository,
    normalize_code,
)
from app.repositories.user_repository import UserRepository, session_scope

ORG_NAME = "中国计量大学"
#: 固定的组织码：它同时是邀请码（`ADR-021` §2.1 把 `organizations.code` 定义成一码两用），
#: 写死是为了让局域网测试里每人拿到的都是同一个班。
ORG_CODE = "CJLU2026"
#: 测试数据专用口令。**明文只出现在这一行**，落库的是 bcrypt 哈希。
#: 公网部署前必须删掉这两个账号（`docs/PROJECT_STATUS.md` §4 那条探针账号的账一并处理）。
PASSWORD = "12345678"

#: (用户名, 组织内角色)。角色取值见 `app/core/permissions.py::UserRole`。
ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("cjlu_admin", "school_admin"),
    ("cjlu_teacher", "teacher"),
)


def _seed() -> str:
    """建齐三件实体，返回归一后的组织码（校验那一步要用它再读一次）。"""
    with session_scope() as session:
        users = UserRepository(session)
        orgs = OrganizationRepository(session)
        audit = AuditLogRepository(session)

        code = normalize_code(ORG_CODE)
        if not code:
            raise ValueError(f"组织码归一后为空：{ORG_CODE!r}")

        organization = orgs.get_by_code(code)
        if organization is None:
            organization = orgs.create(code=code, name=ORG_NAME, kind="school")
            # 系统动作没有操作者：留 NULL 而不是假造一个用户 id（ADR-021 §2.1）。
            audit.record(
                action=AuditAction.ORGANIZATION_CREATE,
                actor_user_id=None,
                organization_id=organization.id,
                target_type="organization",
                target_id=organization.id,
                detail=f'{{"code":"{organization.code}","kind":"{organization.kind}"}}',
            )
            print(f"已创建组织：{organization.code} · {organization.name}")
        else:
            print(f"组织已存在：{organization.code} · {organization.name}（未改动）")

        for username, role in ACCOUNTS:
            user = users.get_by_username(username)
            if user is None:
                user = users.create_user(
                    username=username,
                    password_hash=hash_password(PASSWORD),
                    # 提交点交给我们自己：账号 + 成员关系 + 审计必须同生共死
                    # （与注册那一笔同一个理由，`ADR-021` §2.5）。
                    commit=False,
                )
                created_user = True
            else:
                created_user = False

            member = orgs.get_member(user.id)
            if member is None:
                orgs.add_member(organization_id=organization.id, user_id=user.id, role=role)
                audit.record(
                    action=AuditAction.MEMBER_JOIN,
                    actor_user_id=None,
                    organization_id=organization.id,
                    target_type="user",
                    target_id=user.id,
                    detail=f'{{"username":"{username}","role":"{role}"}}',
                )
                what = "建成员关系" if not created_user else "建账号 + 成员关系"
            elif member.role != role:
                # 先记下旧值：`set_member_role` 就地改那个对象，之后再读 `member.role`
                # 拿到的已经是新角色，审计里那条「previous_role」就会写成改完的值。
                previous_role = member.role
                orgs.set_member_role(member, role)
                audit.record(
                    action=AuditAction.MEMBER_ROLE_CHANGE,
                    actor_user_id=None,
                    organization_id=organization.id,
                    target_type="user",
                    target_id=user.id,
                    detail=f'{{"username":"{username}","previous_role":"{previous_role}","role":"{role}"}}',
                )
                what = f"角色已改（{previous_role} → {role}）"
            else:
                what = "已就位，未改动"

            print(f"账号 {username}：{what}（角色 {role}）")

        orgs.commit()

    return code


def _verify(code: str) -> int:
    """回读数据库确认三件实体真的在、真的关联上了。"""
    with session_scope() as session:
        orgs = OrganizationRepository(session)
        organization = orgs.get_by_code(code)
        if organization is None:
            print("校验失败：组织读不回来", file=sys.stderr)
            return 1
        members = orgs.list_members(organization.id)

    by_name = {user.username: member.role for member, user in members}
    print(
        f"校验：组织 {organization.name}（{organization.code}）"
        f"，成员 {len(members)} 人 → "
        + "、".join(f"{name}={by_name.get(name, '未绑定')}" for name, _role in ACCOUNTS)
    )
    missing = [name for name, _role in ACCOUNTS if name not in by_name]
    if missing:
        print(f"校验失败：这些账号没有出现在成员名单里：{missing}", file=sys.stderr)
        return 1
    for name, role in ACCOUNTS:
        if by_name[name] != role:
            print(f"校验失败：{name} 的角色是 {by_name[name]}，不是 {role}", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    code = _seed()
    if _verify(code) != 0:
        return 1
    line = "✅ 测试数据注入完成：中国计量大学 & cjlu_admin & cjlu_teacher"
    try:
        print(line)
    except UnicodeEncodeError:
        # 本机的控制台是 GBK（cp936），里面没有 U+2705 这个码位——
        # 不能让脚本在「全部写成功之后」因为一行日志崩掉，降级成 ASCII 前缀再打一遍。
        print("[OK] " + line[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
