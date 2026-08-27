"""Consistent prefixed ID generation, matching the id style already used
throughout the codebase (chk_, rec_, evt_ etc.) - one place to generate them
instead of ad hoc `f"x_{uuid.uuid4().hex[:8]}"` scattered per file."""
import uuid


def new_id(prefix: str, length: int = 12) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:length]}"
