"""Template parsing, validation, and rendering utilities."""

import re
from collections.abc import Mapping


_PLACEHOLDER_PATTERN = re.compile(
    r"(?<!{){([A-Za-z_][A-Za-z0-9_]*)}(?!})"
)

ALLOWED_PLACEHOLDERS = frozenset(
    {
        "contact_name",
        "company_name",
        "area",
        "rep_name",
        "rep_email",
    }
)

REQUIRED_PLACEHOLDERS_BY_TYPE = {
    "cold": frozenset({"rep_name", "rep_email"}),
    "followup": frozenset({"rep_name", "rep_email"}),
    "newsletter": frozenset(),
}


def extract_placeholders(*, subject: str, body: str) -> set[str]:
    """Return unique placeholder names found in a subject and body."""

    content = f"{subject}\n{body}"
    return set(_PLACEHOLDER_PATTERN.findall(content))


def validate_template_placeholders(
    *,
    subject: str,
    body: str,
    allowed_placeholders: set[str] | frozenset[str],
    required_placeholders: set[str] | frozenset[str] | None = None,
) -> set[str]:
    """Validate supported and mandatory placeholders."""

    placeholders = extract_placeholders(
        subject=subject,
        body=body,
    )

    unsupported = placeholders - set(allowed_placeholders)

    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"Unsupported placeholders: {names}")

    required = set(required_placeholders or ())
    missing = required - placeholders

    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing required placeholders: {names}")

    return placeholders


def validate_template(
    *,
    template_type: str,
    subject: str,
    body: str,
) -> set[str]:
    """Validate a template against its communication-type policy."""

    if template_type not in REQUIRED_PLACEHOLDERS_BY_TYPE:
        raise ValueError(
            f"Unsupported template type: {template_type}"
        )

    return validate_template_placeholders(
        subject=subject,
        body=body,
        allowed_placeholders=ALLOWED_PLACEHOLDERS,
        required_placeholders=REQUIRED_PLACEHOLDERS_BY_TYPE[
            template_type
        ],
    )


def render_template(
    *,
    subject: str,
    body: str,
    values: Mapping[str, object],
) -> tuple[str, str]:
    """Render a validated template with personalization values."""

    placeholders = extract_placeholders(
        subject=subject,
        body=body,
    )

    missing = placeholders - set(values)

    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing template values: {names}")

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(1)
        return str(values[placeholder])

    return (
        _PLACEHOLDER_PATTERN.sub(replace, subject),
        _PLACEHOLDER_PATTERN.sub(replace, body),
    )
