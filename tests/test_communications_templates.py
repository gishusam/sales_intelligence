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


def test_rejects_unsupported_placeholders():
    from app.communications.templates import validate_template_placeholders

    with pytest.raises(
        ValueError,
        match=r"Unsupported placeholders: customer_name",
    ):
        validate_template_placeholders(
            subject="Hello {customer_name}",
            body="Regards, {rep_name}",
            allowed_placeholders={
                "contact_name",
                "company_name",
                "area",
                "rep_name",
                "rep_email",
            },
        )


def test_rejects_missing_required_placeholders():
    from app.communications.templates import validate_template_placeholders

    with pytest.raises(
        ValueError,
        match=r"Missing required placeholders: rep_email, rep_name",
    ):
        validate_template_placeholders(
            subject="A better workflow for {company_name}",
            body="Hello {contact_name}",
            allowed_placeholders={
                "contact_name",
                "company_name",
                "area",
                "rep_name",
                "rep_email",
            },
            required_placeholders={
                "rep_name",
                "rep_email",
            },
        )
