from sqlalchemy import text


def promote_agencies(session, area):

    stats = {
        "imported": 0,
        "updated": 0,
        "duplicates": 0,
        "rejected": 0,
    }

    rows = session.execute(
        text("""
            SELECT
                business_name,
                phone,
                website,
                area,
                rating,
                review_count
            FROM google_places_leads
            WHERE area = :area
        """),
        {"area": area}
    ).fetchall()

    seen = set()

    for row in rows:

        name = (row.business_name or "").strip().lower()

        if not name:
            stats["rejected"] += 1
            continue

        # must have phone or website
        if not row.phone and not row.website:
            stats["rejected"] += 1
            continue

        # duplicate inside current scrape
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

        score = min(
            (row.review_count or 0) * 0.15 +
            (row.rating or 0) * 5,
            100
        )

        if existing:

            session.execute(
                text("""
                    UPDATE leads
                    SET
                        phone = COALESCE(phone, :phone),
                        website = COALESCE(website, :website),
                        score = GREATEST(score, :score),
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": existing.id,
                    "phone": row.phone,
                    "website": row.website,
                    "score": score,
                }
            )

            stats["updated"] += 1

        else:

            session.execute(
                text("""
                    INSERT INTO leads (
                        name,
                        phone,
                        website,
                        area,
                        lead_type,
                        source,
                        score,
                        status
                    )
                    VALUES (
                        :name,
                        :phone,
                        :website,
                        :area,
                        'agency',
                        'google_places',
                        :score,
                        'new'
                    )
                """),
                {
                    "name": row.business_name,
                    "phone": row.phone,
                    "website": row.website,
                    "area": row.area,
                    "score": score,
                }
            )

            stats["imported"] += 1

    session.commit()

    return stats