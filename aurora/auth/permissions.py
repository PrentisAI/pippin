"""Who may do what in a project.

A project's permissions are seven separate rights.  A role is a named set of them; a member can
also hold a custom set.  What a user ends up with in a project is the union of

* their own membership (role or custom set),
* the memberships of groups they belong to,
* the project's access for everyone in the team (none, view, or view and comment), unless the
  project is confidential or the user is a guest,

and then the team's rules take some rights away again (e.g. guests may not start runs).
Team admins manage people and rules; they get no rights in a project they are not a member of.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PERMS: tuple[str, ...] = ("view", "comment", "edit", "run", "decide", "share", "manage")
PERM_LABELS = {
    "view": "See runs, results, files and shared ideas",
    "comment": "Comment and join shared brainstorms",
    "edit": "Edit shared ideas, files and settings",
    "run": "Start runs (charged to their own key)",
    "decide": "Answer decisions at gates",
    "share": "Invite people and create links",
    "manage": "Change settings, delete the project",
}
ROLES: dict[str, frozenset[str]] = {
    "owner": frozenset(PERMS),
    "editor": frozenset({"view", "comment", "edit", "run", "decide"}),
    "commenter": frozenset({"view", "comment"}),
    "viewer": frozenset({"view"}),
}
TEAM_ACCESS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "view": frozenset({"view"}),
    "comment": frozenset({"view", "comment"}),
}
TEAM_ROLES = ("admin", "member", "guest")

# The team's rules and their defaults
DEFAULT_RULES: dict[str, Any] = {
    "external_links": True,            # members may create read-only links for people outside the team
    "guests_can_run": False,           # guests may start runs (charged to their own key)
    "new_projects_confidential": False,
    "skills_need_approval": True,      # skills learned in a project reach the team after an admin approves
    "members_see_all_projects": False,  # members see that projects they are not in exist
    "allowed_providers": [],           # empty: any model provider
    "team_key_for_members": True,      # members without a key of their own may use the team key
    "monthly_budget_usd": None,
    "per_person_usd": None,
    "per_project_usd": None,
    "member_domains": [],              # invitees with these email domains join as members, others as guests
}


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    name: str
    team_role: str = "member"          # admin | member | guest
    is_local: bool = False             # the single user of a console without accounts
    groups: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_admin(self) -> bool:
        return self.is_local or self.team_role == "admin"

    @property
    def is_guest(self) -> bool:
        return self.team_role == "guest"

    def public(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "email": self.email, "name": self.name, "team_role": self.team_role,
                "is_local": self.is_local}


LOCAL_USER = User("local", "", "You", "admin", is_local=True)


def role_of(perms: set[str] | frozenset[str]) -> str:
    """The role whose rights are exactly *perms*, or 'custom'."""
    for name, rights in ROLES.items():
        if set(perms) == rights:
            return name
    return "custom"


def member_rights(role: str, custom: list[str] | None = None) -> frozenset[str]:
    if role == "custom":
        return frozenset(p for p in (custom or []) if p in PERMS)
    return ROLES.get(role, frozenset())


def project_rights(user: User, access: dict[str, Any] | None, members: list[dict[str, Any]],
                   rules: dict[str, Any]) -> frozenset[str]:
    """What *user* may do in a project with sharing settings *access* and *members*
    (rows with ``principal`` ``user:<id>`` or ``group:<id>``, ``role`` and ``perms``)."""
    if user.is_local:
        return frozenset(PERMS)
    rights: set[str] = set()
    mine = {f"user:{user.user_id}", *(f"group:{g}" for g in user.groups)}
    for m in members:
        if m["principal"] in mine:
            rights |= member_rights(m["role"], m.get("perms"))
    access = access or {}
    if not user.is_guest and not access.get("confidential"):
        rights |= TEAM_ACCESS.get(access.get("team_access") or "none", frozenset())
    if user.is_guest and not rules.get("guests_can_run"):
        rights.discard("run")
    return frozenset(rights)
