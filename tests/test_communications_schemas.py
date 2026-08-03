import pytest
from pydantic import ValidationError

from app.communications.schemas import TemplateCreate, TemplateUpdate


def test_template_create_rejects_missing_required_placeholders():
    with pytest.raises(
        ValidationError,
        match=r"Missing required placeholders: rep_email, rep_name",
    ):
        TemplateCreate(
            slug="agency-introduction",
            name="Agency introduction",
            template_type="cold",
            subject="A better workflow for {company_name}",
            body_text=(
                "Hi {contact_name}, "
                "we support property teams in {area}."
            ),
        )


def test_template_create_accepts_valid_cold_template():
    payload = TemplateCreate(
        slug="agency-introduction",
        name="Agency introduction",
        template_type="cold",
        subject="A better workflow for {company_name}",
        body_text=(
            "Hi {contact_name},\n"
            "We support teams in {area}.\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
    )

    assert payload.slug == "agency-introduction"
    assert payload.template_type == "cold"


def test_template_slug_must_use_lowercase_kebab_case():
    with pytest.raises(ValidationError):
        TemplateCreate(
            slug="Agency Introduction",
            name="Agency introduction",
            template_type="newsletter",
            subject="Property market update",
            body_text="Latest news for {area}.",
        )


def test_template_update_requires_at_least_one_change():
    with pytest.raises(
        ValidationError,
        match=r"At least one template field must be supplied",
    ):
        TemplateUpdate()
