import pytest
from fastapi import HTTPException

from app.auth import create_access_token, get_current_user, CurrentUser
from app.routers.settings import (
    EmailSettingsUpdate,
    update_email_settings,
)


class FailIfDatabaseTouched:
    def execute(self, *args, **kwargs):
        raise AssertionError(
            "Database should not be touched for an unauthorized template update"
        )

    def commit(self):
        raise AssertionError(
            "Database should not be committed for an unauthorized template update"
        )


def test_template_permission_is_restored_from_jwt():
    token = create_access_token({
        "user_id": 1,
        "name": "Template Maintainer",
        "email": "maintainer@example.com",
        "role": "sales",
        "can_manage_communication_templates": True,
    })

    user = get_current_user(token)

    assert user.can_manage_communication_templates is True


def test_normal_sales_user_cannot_update_email_templates():
    user = CurrentUser(
        id=2,
        name="Sales Rep",
        email="sales@example.com",
        role="sales",
    )

    body = EmailSettingsUpdate(
        sender_name="Changed Sender",
    )

    with pytest.raises(HTTPException) as exc:
        update_email_settings(
            body=body,
            db=FailIfDatabaseTouched(),
            user=user,
        )

    assert exc.value.status_code == 403


def test_template_permission_defaults_to_false():
    token = create_access_token({
        "user_id": 3,
        "name": "Sales Rep",
        "email": "sales@example.com",
        "role": "sales",
    })

    user = get_current_user(token)

    assert user.can_manage_communication_templates is False
