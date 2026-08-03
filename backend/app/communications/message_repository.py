"""Database access for auditable communication messages."""

from sqlalchemy import text


MESSAGE_FIELDS = (
    "id",
    "lead_id",
    "template_id",
    "sender_identity_id",
    "sent_by",
    "recipient_email",
    "recipient_name",
    "subject",
    "body_text",
    "body_html",
    "message_type",
    "status",
    "idempotency_key",
    "provider_message_id",
    "attachment_name",
    "attachment_content_type",
    "attachment_size",
    "follow_up_date",
    "error_message",
    "sent_at",
    "created_at",
    "updated_at",
)

MESSAGE_SELECT = """
SELECT
    m.id, m.lead_id, m.template_id, m.sender_identity_id, m.sent_by,
    m.recipient_email, m.recipient_name, m.subject, m.body_text, m.body_html,
    m.message_type, m.status, m.idempotency_key, m.provider_message_id,
    m.attachment_name, m.attachment_content_type, m.attachment_size,
    m.follow_up_date, m.error_message, m.sent_at, m.created_at, m.updated_at
FROM email_messages m
"""


def row_dict(row, fields=None):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    fields = fields or MESSAGE_FIELDS
    return {field: getattr(row, field, None) for field in fields}


def get_lead(*, db, lead_id):
    row = db.execute(
        text(
            """
            SELECT id, name, owner_name, contact_person, email, area
            FROM leads
            WHERE id = :lead_id
            """
        ),
        {"lead_id": lead_id},
    ).fetchone()
    return row_dict(
        row,
        ("id", "name", "owner_name", "contact_person", "email", "area"),
    )


def get_template(*, db, template_id):
    row = db.execute(
        text(
            """
            SELECT id, slug, name, template_type, subject, body_text,
                   body_html, is_active
            FROM communication_templates
            WHERE id = :template_id
            """
        ),
        {"template_id": template_id},
    ).fetchone()
    return row_dict(
        row,
        (
            "id",
            "slug",
            "name",
            "template_type",
            "subject",
            "body_text",
            "body_html",
            "is_active",
        ),
    )


def get_sender_identity(*, db, sender_identity_id):
    row = db.execute(
        text(
            """
            SELECT id, display_name, email_address, reply_to_address,
                   provider, provider_reference, is_default, is_active
            FROM sender_identities
            WHERE id = :sender_identity_id
            """
        ),
        {"sender_identity_id": sender_identity_id},
    ).fetchone()
    return row_dict(
        row,
        (
            "id",
            "display_name",
            "email_address",
            "reply_to_address",
            "provider",
            "provider_reference",
            "is_default",
            "is_active",
        ),
    )


def is_recipient_suppressed(*, db, email_address):
    normalized = email_address.strip().lower()
    row = db.execute(
        text(
            """
            SELECT TRUE AS is_suppressed
            FROM suppression_list
            WHERE LOWER(email_address) = :email_address
              AND is_active = TRUE
            LIMIT 1
            """
        ),
        {"email_address": normalized},
    ).fetchone()
    return bool(row and getattr(row, "is_suppressed", True))


def find_message_by_idempotency_key(*, db, idempotency_key):
    row = db.execute(
        text(
            f"""
            {MESSAGE_SELECT}
            WHERE m.idempotency_key = :idempotency_key
            """
        ),
        {"idempotency_key": idempotency_key},
    ).fetchone()
    return row_dict(row)


def create_queued_message(*, db, values):
    row = db.execute(
        text(
            """
            INSERT INTO email_messages (
                lead_id, template_id, sender_identity_id, sent_by,
                recipient_email, recipient_name, subject, body_text, body_html,
                message_type, status, idempotency_key,
                attachment_name, attachment_content_type, attachment_size,
                follow_up_date, created_at, updated_at
            )
            VALUES (
                :lead_id, :template_id, :sender_identity_id, :sent_by,
                :recipient_email, :recipient_name, :subject, :body_text,
                :body_html, :message_type, 'queued', :idempotency_key,
                :attachment_name, :attachment_content_type, :attachment_size,
                :follow_up_date, NOW(), NOW()
            )
            RETURNING *
            """
        ),
        values,
    ).fetchone()
    return row_dict(row) or {}


def mark_message_sent(*, db, message_id, provider_message_id):
    db.execute(
        text(
            """
            UPDATE email_messages
            SET status = 'sent',
                provider_message_id = :provider_message_id,
                error_message = NULL,
                sent_at = NOW(),
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {
            "message_id": message_id,
            "provider_message_id": provider_message_id,
        },
    )


def mark_message_failed(*, db, message_id, error_message):
    db.execute(
        text(
            """
            UPDATE email_messages
            SET status = 'failed',
                error_message = :error_message,
                updated_at = NOW()
            WHERE id = :message_id
            """
        ),
        {
            "message_id": message_id,
            "error_message": error_message[:2000],
        },
    )


def update_lead_after_successful_send(
    *,
    db,
    lead_id,
    follow_up_date,
    changed_by,
    recipient_email,
):
    db.execute(
        text(
            """
            UPDATE leads
            SET last_contacted = NOW(),
                follow_up_date = :follow_up_date,
                contact_attempts = COALESCE(contact_attempts, 0) + 1,
                email_sent_at = NOW(),
                updated_at = NOW()
            WHERE id = :lead_id
            """
        ),
        {
            "lead_id": lead_id,
            "follow_up_date": follow_up_date,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO lead_events (
                lead_id, event_type, to_value, changed_by, note, created_at
            )
            VALUES (
                :lead_id, 'email_sent', :recipient_email,
                :changed_by, 'Manual communication sent', NOW()
            )
            """
        ),
        {
            "lead_id": lead_id,
            "recipient_email": recipient_email,
            "changed_by": changed_by,
        },
    )


def get_message(*, db, message_id):
    row = db.execute(
        text(
            f"""
            {MESSAGE_SELECT}
            WHERE m.id = :message_id
            """
        ),
        {"message_id": message_id},
    ).fetchone()
    return row_dict(row)


def list_messages(*, db, lead_id, limit):
    params = {"limit": limit}
    where = ""
    if lead_id is not None:
        where = "WHERE m.lead_id = :lead_id"
        params["lead_id"] = lead_id

    rows = db.execute(
        text(
            f"""
            {MESSAGE_SELECT}
            {where}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT :limit
            """
        ),
        params,
    ).fetchall()
    return [row_dict(row) for row in rows]
