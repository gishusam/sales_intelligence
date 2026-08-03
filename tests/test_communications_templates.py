import pytest

from app.communications.templates import (
    extract_placeholders,
    render_template,
    validate_template,
    validate_template_placeholders,
)


def test_extracts_placeholders_from_subject_and_body():
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


def test_renders_template_subject_and_body():
    subject, body = render_template(
        subject="A better workflow for {company_name}",
        body=(
            "Hi {contact_name},\n\n"
            "We support property teams in {area}.\n\n"
            "Regards,\n{rep_name}"
        ),
        values={
            "company_name": "Example Agency",
            "contact_name": "Alice",
            "area": "Kilimani",
            "rep_name": "Samwel",
        },
    )

    assert subject == "A better workflow for Example Agency"
    assert body == (
        "Hi Alice,\n\n"
        "We support property teams in Kilimani.\n\n"
        "Regards,\nSamwel"
    )


def test_rendering_rejects_missing_placeholder_values():
    with pytest.raises(
        ValueError,
        match=r"Missing template values: area, rep_name",
    ):
        render_template(
            subject="A better workflow for {company_name}",
            body=(
                "Hi {contact_name},\n"
                "We support teams in {area}.\n"
                "Regards, {rep_name}"
            ),
            values={
                "company_name": "Example Agency",
                "contact_name": "Alice",
            },
        )


def test_cold_template_requires_sales_rep_identity():
    with pytest.raises(
        ValueError,
        match=r"Missing required placeholders: rep_email, rep_name",
    ):
        validate_template(
            template_type="cold",
            subject="A better workflow for {company_name}",
            body="Hi {contact_name}, we support teams in {area}.",
        )


def test_newsletter_does_not_require_sales_rep_identity():
    placeholders = validate_template(
        template_type="newsletter",
        subject="Nyumba Zetu market update",
        body="Latest property insights for teams in {area}.",
    )

    assert placeholders == {"area"}
