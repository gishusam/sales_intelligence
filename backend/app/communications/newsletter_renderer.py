"""Render structured newsletter blocks."""

from html import escape

from app.communications.newsletter_schemas import NewsletterBlock


def render_blocks(
    blocks: list[NewsletterBlock],
) -> tuple[str, str]:
    text_parts = []
    html_parts = []

    for block in blocks:
        if block.kind == "heading":
            text_parts.append(block.text or "")
            html_parts.append(
                f"<h2>{escape(block.text or '')}</h2>"
            )
        elif block.kind == "paragraph":
            text_parts.append(block.text or "")
            html_parts.append(
                f"<p>{escape(block.text or '')}</p>"
            )
        elif block.kind == "link":
            label = block.text or ""
            url = block.url or ""
            text_parts.append(f"{label}: {url}")
            html_parts.append(
                f'<p><a href="{escape(url, quote=True)}">'
                f"{escape(label)}</a></p>"
            )
        else:
            text_parts.append("---")
            html_parts.append("<hr>")

    return "\n\n".join(text_parts), "".join(html_parts)
