import re


async def dismiss_google_consent(page):
    """Reject Google's optional cookies when Maps redirects to consent."""
    if "consent.google.com" not in page.url:
        return False

    reject = page.get_by_role("button", name=re.compile("Reject all", re.I))
    if await reject.count() == 0:
        raise RuntimeError("Google consent page did not expose a Reject all button")

    await reject.first.click()
    await page.wait_for_load_state("domcontentloaded")
    return True
