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
    required_placeholders: set[str] | None = None,
) -> set[str]:
    """Validate supported and required template placeholders."""

    placeholders = extract_placeholders(subject=subject, body=body)
    unsupported = placeholders - allowed_placeholders

    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"Unsupported placeholders: {names}")

    required = required_placeholders or set()
    missing = required - placeholders

    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing required placeholders: {names}")

    return placeholders
