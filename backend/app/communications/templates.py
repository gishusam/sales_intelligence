"""Template parsing utilities for Communications."""

import re


_PLACEHOLDER_PATTERN = re.compile(
    r"(?<!{){([A-Za-z_][A-Za-z0-9_]*)}(?!})"
)


def extract_placeholders(*, subject: str, body: str) -> set[str]:
    """Return unique placeholder names found in a template."""

    content = f"{subject}\n{body}"
    return set(_PLACEHOLDER_PATTERN.findall(content))


def validate_template_placeholders(
    *,
    subject: str,
    body: str,
    allowed_placeholders: set[str],
) -> set[str]:
    """Validate that a template contains only supported placeholders."""

    placeholders = extract_placeholders(subject=subject, body=body)
    unsupported = placeholders - allowed_placeholders

    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"Unsupported placeholders: {names}")

    return placeholders
