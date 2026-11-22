APPROVED_ADMIN_ROLES: frozenset[str] = frozenset({
    "super_admin",
    "operations_admin",
    "finance_admin",
    "content_moderator",
    "support_admin",
})
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


def resolve_canonical_admin_role(role: str | None) -> str:
    """
    Resolve an admin user's stored role into one of the five canonical admin role
    identifiers. Maps legacy 'admin' to 'super_admin'.
    """
    if not role or not isinstance(role, str):
        raise ValueError("Invalid admin role")
    cleaned = role.strip().lower()
    if cleaned == LEGACY_ADMIN_ROLE:
        return "super_admin"
    if cleaned in APPROVED_ADMIN_ROLES:
        return cleaned
    raise ValueError(f"Unrecognized admin role '{role}'")
