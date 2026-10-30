def admits(user) -> bool:
    """Whether ``user`` may use the console: an active superuser."""
    return user.is_active and user.is_superuser
