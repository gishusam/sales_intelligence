import scrapy


class ListingItem(scrapy.Item):
    """
    One row = one property listing from BuyRentKenya or Jiji.
    Every field maps directly to a column in listing_staging.
    """
    # Property
    property_name   = scrapy.Field()   # e.g. "3 Bed Apartment in Kilimani"
    property_type   = scrapy.Field()   # apartment / house / villa / bedsitter
    bedrooms        = scrapy.Field()   # integer

    # Price
    price_kes       = scrapy.Field()   # integer e.g. 85000
    raw_price       = scrapy.Field()   # original string e.g. "KES 85,000/mo"

    # Location
    area            = scrapy.Field()   # e.g. "Kilimani"
    raw_address     = scrapy.Field()   # full address string

    # Owner / agency
    owner_name      = scrapy.Field()   # agency or landlord name
    owner_type      = scrapy.Field()   # agency / pm / owner
    owner_phone     = scrapy.Field()
    owner_email     = scrapy.Field()
    owner_website   = scrapy.Field()

    # Source tracking
    listing_date    = scrapy.Field()   # date posted — key for recency scoring
    source          = scrapy.Field()   # buyrentkenya / jiji
    source_url      = scrapy.Field()   # unique listing URL
    source_id       = scrapy.Field()
    scraped_at      = scrapy.Field()
