"""建一个组织（学校/学院/企业），并留一条审计记录。

**为什么是脚本而不是端点**：本批不开放「自助建校」的 HTTP 入口。
任何一个能自己造组织的人，都能立刻给自己一个 `school_admin` 角色，
而组织码是他自己起的——那就等于没有角色边界。组织的建立属于运维动作，
由这条命令完成；真正面向学校的建校流程在 B6 的邀请制里做。

用法：

    python -m scripts.seed_organization --code AI2026 --name "示例大学"
    python -m scripts.seed_organization --code ACME --name "示例科技" --kind company

幂等：同一个 `code` 再跑一次只会提示「已存在」，不会改名字、不会重复写审计。
"""

from __future__ import annotations

import argparse
import sys

from app.repositories.audit_log_repository import AuditAction, AuditLogRepository
from app.repositories.organization_repository import (
    OrganizationRepository,
    normalize_code,
)
from app.repositories.user_repository import session_scope

KINDS = ("school", "college", "company")


def seed(code: str, name: str, kind: str) -> int:
    normalized = normalize_code(code)
    if not normalized:
        print("组织码不能为空。", file=sys.stderr)
        return 1
    if kind not in KINDS:
        print(f"kind 只能是 {'/'.join(KINDS)} 之一。", file=sys.stderr)
        return 1

    with session_scope() as session:
        organizations = OrganizationRepository(session)
        existing = organizations.get_by_code(normalized)
        if existing is not None:
            print(f"组织已存在：{existing.code} · {existing.name}（未改动）")
            return 0
        organization = organizations.create(code=normalized, name=name, kind=kind)
        AuditLogRepository(session).record(
            action=AuditAction.ORGANIZATION_CREATE,
            # 系统动作没有操作者：留 NULL 而不是假造一个用户 id（ADR-021 §2.1）。
            actor_user_id=None,
            organization_id=organization.id,
            target_type="organization",
            target_id=organization.id,
            detail=f'{{"code":"{organization.code}","kind":"{organization.kind}"}}',
        )
        organizations.commit()
    print(f"已创建组织：{normalized} · {name}（{kind}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建一个组织并留审计记录")
    parser.add_argument("--code", required=True, help="组织码，同时是注册时要填的邀请码")
    parser.add_argument("--name", required=True, help="组织显示名")
    parser.add_argument("--kind", default="school", choices=KINDS, help="组织类型")
    args = parser.parse_args(argv)
    return seed(code=args.code, name=args.name, kind=args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
