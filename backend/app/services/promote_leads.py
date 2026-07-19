from sqlalchemy import text


def promote_apartments(session, area):

    stats = {
        "imported": 0,
        "updated": 0,
        "duplicates": 0,
        "rejected": 0,
    }

    rows = session.execute(
        text("""
            SELECT
                building_name,
                contact_phone,
                contact_email,
                contact_website,
                search_area,
                lead_score,
                management_company
            FROM apartment_staging
            WHERE search_area = :area
        """),
        {"area":area}
    ).fetchall()

    seen = set()

    for row in rows:

        name = (row.building_name or "").strip().lower()

        if not name:
            stats["rejected"] += 1
            continue

        # reject if no contact info
        if not row.contact_phone and not row.contact_website:
            stats["rejected"] += 1
            continue

        # duplicate inside staging
        if name in seen:
            stats["duplicates"] += 1
            continue

        seen.add(name)

        existing = session.execute(
            text("""
                SELECT id
                FROM leads
                WHERE LOWER(name) = :name
            """),
            {"name": name}
        ).fetchone()

        if existing:

            session.execute(
                text("""
                    UPDATE leads
                    SET
                        phone = COALESCE(phone, :phone),
                        email = COALESCE(email, :email),
                        website = COALESCE(website, :website),
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": existing.id,
                    "phone": row.contact_phone,
                    "email": row.contact_email,
                    "website": row.contact_website,
                },
            )

            stats["updated"] += 1

        else:

            session.execute(
                text("""
                    INSERT INTO leads (
                        name,
                        owner_name,
                        phone,
                        email,
                        website,
                        area,
                        lead_type,
                        source,
                        score,
                        status
                    )
                    VALUES (
                        :name,
                        :owner_name,
                        :phone,
                        :email,
                        :website,
                        :area,
                        'apartment',
                        'google_maps',
                        :score,
                        'new'
                    )
                """),
                {
                    "name": row.building_name,
                    "owner_name": row.management_company,
                    "phone": row.contact_phone,
                    "email": row.contact_email,
                    "website": row.contact_website,
                    "area": row.search_area,
                    "score": row.lead_score or 0,
                },
            )

            stats["imported"] += 1

    session.commit()

    return stats