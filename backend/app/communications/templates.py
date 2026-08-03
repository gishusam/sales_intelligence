"""Template parsing utilities for Communications."""

import re


_PLACEHOLDER_PATTERN = re.compile(
    r"(?<!{){([A-Za-z_][A-Za-z0-9_]*)}(?!})"
)


def extract_placeholders(*, subject: str, body: str) -> set[str]:
    """Return unique placeholder names found in a template."""

    content = f"{subject}\n{body}"
    return set(_PLACEHOLDER_PATTERN.findall(content))
