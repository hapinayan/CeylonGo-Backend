import os

APPROVED_ADMIN_ROLES: frozenset[str] = frozenset({
    "super_admin",
    "operations_admin",
    "finance_admin",
    "content_moderator",
    "support_admin",
})
MVP_ADMIN_ROLES = frozenset({"admin", "super_admin"})
LEGACY_ADMIN_ROLE = "admin"
ADMIN_ROLES: frozenset[str] = APPROVED_ADMIN_ROLES | {LEGACY_ADMIN_ROLE}


def is_admin_role(role: str | None) -> bool:
    """
    Return True if the role is one of the five approved admin role identifiers
    or the legacy 'admin' role.
    """
    if not role or not isinstance(role, str):
        return False
    return role.strip().lower() in ADMIN_ROLES


def resolve_canonical_admin_role(role: str | None, *, legacy_compat: bool | None = None) -> str:
    """
    Resolve an admin user's stored role.
    In legacy compatibility mode (USE_LEGACY_RUNTIME=true), legacy 'admin' maps to 'super_admin'.
    In active Academic MVP runtime, ordinary 'admin' remains 'admin' to prevent
    automatic privilege escalation to super_admin.
    """
    if not role or not isinstance(role, str):
        raise ValueError("Invalid admin role")
    cleaned = role.strip().lower()
    if cleaned in APPROVED_ADMIN_ROLES:
        return cleaned
    if cleaned == LEGACY_ADMIN_ROLE:
        if legacy_compat is None:
            legacy_compat = os.getenv("USE_LEGACY_RUNTIME", "false").lower() == "true"
        return "super_admin" if legacy_compat else "admin"
    raise ValueError(f"Unrecognized admin role '{role}'")
