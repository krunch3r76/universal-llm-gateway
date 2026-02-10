"""
Environment variable expansion for federation configuration.

Supports ${VAR_NAME} syntax only.
- No default values (${VAR:-default}) — fail-fast preferred
- No nested expansion (${${INNER}}) — complexity not justified
- Secrets never logged (expansion is one-way)
"""

import os
import re


def expand_env_vars(value: str) -> str:
    """
    Expand environment variables in a string.

    Args:
        value: String potentially containing ${VAR} patterns

    Returns:
        String with all ${VAR} patterns replaced with environment values

    Raises:
        ValueError: If any referenced environment variable is not set
    """
    pattern = re.compile(r"\$\{([^}]+)\}")

    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable {var_name} not set")
        return env_value

    return pattern.sub(replace, value)
