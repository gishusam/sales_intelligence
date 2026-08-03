import pytest


def load_extract_placeholders():
    try:
        from app.communications.templates import extract_placeholders
    except (ImportError, ModuleNotFoundError):
        pytest.fail(
            "Create app.communications.templates.extract_placeholders",
            pytrace=False,
        )

    return extract_placeholders


def test_extracts_placeholders_from_subject_and_body():
    extract_placeholders = load_extract_placeholders()

    placeholders = extract_placeholders(
        subject="A better workflow for {company_name}",
        body=(
            "Hi {contact_name},\n\n"
            "We support property teams in {area}.\n\n"
            "Regards,\n"
            "{rep_name}\n"
            "{rep_email}"
        ),
    )

    assert placeholders == {
        "company_name",
        "contact_name",
        "area",
        "rep_name",
        "rep_email",
    }
