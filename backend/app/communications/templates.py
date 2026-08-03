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


def render_template(
    *,
    subject: str,
    body: str,
    values: dict[str, object],
) -> tuple[str, str]:
    """Render placeholder values into a template subject and body."""

    placeholders = extract_placeholders(
        subject=subject,
        body=body,
    )
    missing = placeholders - values.keys()

    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing template values: {names}")

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(1)
        return str(values[placeholder])

    rendered_subject = _PLACEHOLDER_PATTERN.sub(replace, subject)
    rendered_body = _PLACEHOLDER_PATTERN.sub(replace, body)

    return rendered_subject, rendered_body


ALLOWED_PLACEHOLDERS = {
    "contact_name",
    "company_name",
    "area",
    "rep_name",
    "rep_email",
}

REQUIRED_PLACEHOLDERS_BY_TYPE = {
    "cold": {"rep_name", "rep_email"},
    "followup": {"rep_name", "rep_email"},
    "newsletter": set(),
}


def validate_template(
    *,
    template_type: str,
    subject: str,
    body: str,
) -> set[str]:
    """Validate template content against its communication type."""

    if template_type not in REQUIRED_PLACEHOLDERS_BY_TYPE:
        raise ValueError(f"Unsupported template type: {template_type}")

    return validate_template_placeholders(
        subject=subject,
        body=body,
        allowed_placeholders=ALLOWED_PLACEHOLDERS,
        required_placeholders=REQUIRED_PLACEHOLDERS_BY_TYPE[
            template_type
        ],
    )
