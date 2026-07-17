"""Generate the Sales Intelligence Google Cloud-style architecture diagram."""

from __future__ import annotations

import base64
from pathlib import Path


ROOT = Path(__file__).parent
ICON_DIR = ROOT / "icons"
OUTPUT = ROOT / "sales-intelligence-architecture.svg"


def esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def icon(name: str, x: int, y: int, size: int = 56) -> str:
    data = base64.b64encode((ICON_DIR / name).read_bytes()).decode("ascii")
    return (
        f'<image x="{x}" y="{y}" width="{size}" height="{size}" '
        f'preserveAspectRatio="xMidYMid meet" href="data:image/svg+xml;base64,{data}"/>'
    )


def label(text: str, x: int, y: int, size: int = 16, weight: str = "400", fill: str = "#243447") -> str:
    return f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" font-size="{size}px" font-weight="{weight}" fill="{fill}">{esc(text)}</text>'


def multiline(lines: list[str], x: int, y: int, size: int = 14, fill: str = "#5f6b7a") -> str:
    return "".join(label(line, x, y + i * 20, size, "400", fill) for i, line in enumerate(lines))


def card(x: int, y: int, w: int, h: int, title: str, subtitle: list[str], accent: str, body: str = "") -> str:
    return (
        f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#ffffff" '
        f'stroke="{accent}" stroke-width="2" filter="url(#shadow)"/>'
        f'<rect x="{x}" y="{y}" width="7" height="{h}" rx="3" fill="{accent}"/>'
        f'{label(title, x + 25, y + 34, 18, "700", "#202124")}'
        f'{multiline(subtitle, x + 25, y + 60)}'
        f'{label(body, x + 25, y + h - 18, 12, "600", accent) if body else ""}</g>'
    )


def arrow(x1: int, y1: int, x2: int, y2: int, text: str, dashed: bool = False, color: str = "#5f6368") -> str:
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    mx, my = (x1 + x2) // 2, (y1 + y2) // 2
    return (
        f'<path d="M{x1},{y1} L{x2},{y2}" fill="none" stroke="{color}" stroke-width="2.5" '
        f'{dash} marker-end="url(#arrow)"/>'
        f'<rect x="{mx - len(text) * 3.3:.0f}" y="{my - 17}" width="{len(text) * 6.6 + 12:.0f}" height="22" rx="11" fill="#ffffff" opacity="0.94"/>'
        f'{label(text, mx - len(text) * 3.3 + 6, my - 2, 11, "600", color)}'
    )


def build() -> str:
    parts: list[str] = []
    parts.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1920" height="1080" viewBox="0 0 1920 1080">
<title>Sales Intelligence Google Cloud architecture</title>
<desc>FastAPI Cloud Run service dispatches a Playwright Cloud Run Job, both backed by Supabase PostgreSQL.</desc>
<defs>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#1a73e8" flood-opacity="0.12"/></filter>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5f6368"/></marker>
  <linearGradient id="header" x1="0" x2="1"><stop offset="0" stop-color="#1a73e8"/><stop offset="1" stop-color="#4285f4"/></linearGradient>
</defs>
<rect width="1920" height="1080" fill="#f8fafc"/>
<rect width="1920" height="126" fill="url(#header)"/>
<circle cx="72" cy="63" r="30" fill="#ffffff" opacity="0.18"/>
<path d="M58 70 Q72 43 86 70 Q72 84 58 70Z" fill="#ffffff"/>
''')
    parts.append(label("Sales Intelligence", 122, 60, 31, "700", "#ffffff"))
    parts.append(label("Google Cloud runtime architecture", 122, 90, 16, "400", "#dbeafe"))
    parts.append(label("sales-intelligens  ·  europe-west1  ·  request-driven Playwright scraping", 1320, 72, 14, "600", "#e8f0fe"))

    # Project boundary and external systems.
    parts.append('<rect x="58" y="162" width="1804" height="746" rx="24" fill="#eef5ff" stroke="#4285f4" stroke-width="2.5"/>')
    parts.append('<rect x="86" y="146" width="350" height="34" rx="17" fill="#4285f4"/>')
    parts.append(label("GOOGLE CLOUD PROJECT  ·  sales-intelligens", 106, 169, 14, "700", "#ffffff"))
    parts.append(label("Public entry", 100, 264, 13, "700", "#6b7280"))
    parts.append(label("Managed data", 1434, 264, 13, "700", "#6b7280"))
    parts.append(label("Build and operate", 320, 740, 13, "700", "#6b7280"))

    # Arrows are drawn before cards so the cards sit cleanly on top.
    parts.append(arrow(282, 394, 355, 394, "HTTPS /api/*", False, "#1a73e8"))
    parts.append(arrow(686, 358, 844, 358, "run override", False, "#7b1fa2"))
    parts.append(arrow(1189, 358, 1372, 358, "SQL over TLS", False, "#0f9d58"))
    parts.append(arrow(1010, 478, 1010, 560, "scrape", False, "#ea4335"))
    parts.append(arrow(1190, 445, 1372, 445, "status + leads", False, "#0f9d58"))
    parts.append(arrow(492, 590, 492, 475, "runtime secrets", True, "#5f6368"))
    parts.append(arrow(492, 590, 1010, 475, "runtime secrets", True, "#5f6368"))
    parts.append(arrow(282, 795, 515, 795, "push", True, "#5f6368"))
    parts.append(arrow(688, 795, 858, 795, "image", True, "#5f6368"))
    parts.append(arrow(1050, 795, 1050, 475, "deploy", True, "#5f6368"))
    parts.append('<path d="M1140,795 L1140,738 L730,738 L730,520 L687,520" fill="none" stroke="#5f6368" stroke-width="2.5" stroke-dasharray="7 6" marker-end="url(#arrow)"/>')
    parts.append('<rect x="905" y="721" width="72" height="22" rx="11" fill="#ffffff" opacity="0.94"/>')
    parts.append(label("deploy", 923, 736, 11, "600", "#5f6368"))

    # External user card.
    parts.append('<g><rect x="88" y="316" width="194" height="156" rx="14" fill="#ffffff" stroke="#9aa0a6" stroke-width="2" filter="url(#shadow)"/>')
    parts.append('<circle cx="185" cy="362" r="28" fill="#e8f0fe" stroke="#1a73e8" stroke-width="2"/>')
    parts.append('<circle cx="185" cy="354" r="8" fill="#1a73e8"/><path d="M169 379 Q185 363 201 379" fill="none" stroke="#1a73e8" stroke-width="5" stroke-linecap="round"/>')
    parts.append(label("Frontend users", 125, 421, 17, "700", "#202124"))
    parts.append(label("Lovable / Vercel", 132, 447, 13, "400", "#5f6b7a"))
    parts.append('</g>')

    # Core runtime cards.
    parts.append(icon("cloud_run.svg", 380, 306, 64))
    parts.append(card(355, 286, 332, 188, "Cloud Run API", ["FastAPI · public HTTPS endpoint", "min instances 0 · max instances 1", "JWT login + lead/report APIs"], "#1a73e8", "sales-intelligence-api"))
    parts.append(icon("cloud_run.svg", 870, 296, 64))
    parts.append(card(844, 276, 346, 202, "Cloud Run Job", ["Playwright worker · one task", "1 vCPU · 1 GiB · 15 minute limit", "max retries 1 · direct DB status"], "#1a73e8", "sales-scraper"))

    # Supabase external boundary and data store.
    parts.append('<rect x="1360" y="292" width="424" height="230" rx="18" fill="#ecfdf5" stroke="#34a853" stroke-width="2" stroke-dasharray="8 6"/>')
    parts.append(label("EXTERNAL MANAGED SERVICE", 1382, 322, 12, "700", "#188038"))
    parts.append('<ellipse cx="1470" cy="382" rx="43" ry="14" fill="#3ecf8e"/><path d="M1427 382v52c0 8 19 15 43 15s43-7 43-15v-52" fill="#3ecf8e"/><ellipse cx="1470" cy="434" rx="43" ry="14" fill="#22a06b"/>')
    parts.append(label("Supabase PostgreSQL", 1532, 388, 18, "700", "#202124"))
    parts.append(multiline(["IPv4 pooler · TLS", "leads + scraper_runs", "staging + run records"], 1532, 416, 13, "4b6355"))

    # Web sources.
    parts.append('<g><rect x="845" y="560" width="340" height="146" rx="14" fill="#fff7ed" stroke="#f29900" stroke-width="2" filter="url(#shadow)"/>')
    parts.append('<circle cx="905" cy="615" r="30" fill="#fce8d1" stroke="#f29900" stroke-width="2"/><path d="M879 615h52M905 589v52M887 598q18 12 36 0M887 632q18-12 36 0" fill="none" stroke="#e37400" stroke-width="2"/>')
    parts.append(label("Web sources", 954, 610, 18, "700", "#202124"))
    parts.append(multiline(["property listings", "business directories", "Playwright browser targets"], 954, 638, 13, "7c5a28"))
    parts.append('</g>')

    # Secrets and delivery lane.
    parts.append(icon("secret_manager.svg", 380, 600, 58))
    parts.append(card(355, 578, 332, 140, "Secret Manager", ["DB URL + password", "app secret + JWT secret"], "#f29900", "runtime configuration"))
    parts.append('<rect x="88" y="758" width="1170" height="112" rx="16" fill="#ffffff" stroke="#b7c4d6" stroke-width="1.5"/>')
    parts.append('<rect x="108" y="739" width="180" height="30" rx="15" fill="#ffffff" stroke="#b7c4d6" stroke-width="1.5"/>')
    parts.append(label("DELIVERY PIPELINE", 130, 760, 12, "700", "#5f6b7a"))
    parts.append('<g><circle cx="210" cy="816" r="27" fill="#f1f3f4" stroke="#5f6368" stroke-width="2"/><path d="M197 816h26M210 803v26" stroke="#5f6368" stroke-width="4"/><path d="M202 808l8-5 8 5" fill="none" stroke="#5f6368" stroke-width="3"/>')
    parts.append(label("GitHub", 246, 813, 17, "700", "#202124")); parts.append(label("cloud-run-deploy", 246, 837, 12, "400", "#5f6b7a")); parts.append('</g>')
    parts.append(icon("cloud_build.svg", 500, 786, 58)); parts.append(label("Cloud Build", 566, 813, 17, "700", "#202124")); parts.append(label("build container", 566, 837, 12, "400", "#5f6b7a"))
    parts.append(icon("artifact_registry.svg", 860, 786, 58)); parts.append(label("Artifact Registry", 926, 813, 17, "700", "#202124")); parts.append(label("api + worker images", 926, 837, 12, "400", "#5f6b7a"))

    # Budget / operations callout.
    parts.append('<g><rect x="1395" y="180" width="410" height="78" rx="14" fill="#fff8e1" stroke="#f9ab00" stroke-width="2"/>')
    parts.append('<circle cx="1432" cy="219" r="18" fill="#f9ab00"/><path d="M1432 207v15M1432 229v2" stroke="#ffffff" stroke-width="3" stroke-linecap="round"/>')
    parts.append(label("Cost guardrail", 1464, 214, 16, "700", "#7c4a03")); parts.append(label("$20 monthly budget alerts · not a hard cap", 1464, 237, 12, "400", "#8a6500")); parts.append('</g>')

    # Footer legend.
    parts.append('<rect x="58" y="948" width="1804" height="84" rx="16" fill="#ffffff" stroke="#d6dee8"/>')
    parts.append(label("FLOW LEGEND", 88, 981, 12, "700", "#5f6b7a"))
    parts.append('<line x1="222" y1="977" x2="286" y2="977" stroke="#5f6368" stroke-width="2.5" marker-end="url(#arrow)"/>')
    parts.append(label("runtime data / control", 300, 982, 13, "400", "#5f6b7a"))
    parts.append('<line x1="530" y1="977" x2="594" y2="977" stroke="#5f6368" stroke-width="2.5" stroke-dasharray="7 6" marker-end="url(#arrow)"/>')
    parts.append(label("build / deployment", 608, 982, 13, "400", "#5f6b7a"))
    parts.append('<rect x="850" y="963" width="18" height="18" rx="4" fill="#eef5ff" stroke="#4285f4"/><rect x="878" y="963" width="18" height="18" rx="4" fill="#ecfdf5" stroke="#34a853"/>')
    parts.append(label("GCP project", 910, 982, 13, "400", "#5f6b7a")); parts.append(label("external system", 1036, 982, 13, "400", "#5f6b7a"))
    parts.append(label("Generated from the official Google Cloud architecture icon set", 1392, 982, 12, "400", "#7b8794"))
    parts.append('</svg>')
    return "".join(parts)


if __name__ == "__main__":
    OUTPUT.write_text(build(), encoding="utf-8")
    print(OUTPUT)
