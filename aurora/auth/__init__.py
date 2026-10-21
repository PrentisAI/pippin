"""Accounts, teams and permissions of the web console.

With no accounts the console is single-user, as it always was: every request acts as the local
owner and nothing asks for a login.  Once an administrator exists (``aurora users add --admin``)
every request needs a signed-in user, and what each user may see and do in a project follows
from their role there (see :mod:`aurora.auth.permissions`).
"""

from aurora.auth.permissions import LOCAL_USER, PERMS, ROLES, User

__all__ = ["LOCAL_USER", "PERMS", "ROLES", "User"]
