import pytest
from pydantic import ValidationError


def test_template_create_rejects_missing_required_placeholders():
    from app.communications.schemas import TemplateCreate

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
