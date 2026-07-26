"""Generate diagram / wireframe PNGs for the KSP Datathon submission deck."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)

NAVY = (15, 42, 78)
SAFFRON = (232, 119, 34)
GREEN = (26, 107, 64)
WHITE = (255, 255, 255)
LIGHT = (245, 248, 252)
MUTED = (90, 110, 140)
CARD = (255, 255, 255)
BORDER = (200, 214, 232)
ACCENT = (40, 90, 160)


def font(size: int, bold: bool = False):
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def center_text(draw, box, text, fnt, fill):
    x0, y0, x1, y1 = box
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((x0 + x1 - tw) / 2, (y0 + y1 - th) / 2), text, font=fnt, fill=fill)


def wrap_text(draw, text, fnt, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), trial, font=fnt)[2] <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_box(draw, xy, title, subtitle=None, fill=CARD, title_fill=NAVY, border=BORDER):
    rounded_rect(draw, xy, 12, fill=fill, outline=border, width=2)
    x0, y0, x1, y1 = xy
    f_title = font(18, True)
    f_sub = font(13)
    title_bbox = draw.textbbox((0, 0), title, font=f_title)
    tw = title_bbox[2] - title_bbox[0]
    th = title_bbox[3] - title_bbox[1]
    if subtitle:
        sb = draw.textbbox((0, 0), subtitle, font=f_sub)
        sw, sh = sb[2] - sb[0], sb[3] - sb[1]
        total_h = th + 6 + sh
        ty = y0 + (y1 - y0 - total_h) / 2
        draw.text(((x0 + x1 - tw) / 2, ty), title, font=f_title, fill=title_fill)
        draw.text(((x0 + x1 - sw) / 2, ty + th + 6), subtitle, font=f_sub, fill=MUTED)
    else:
        center_text(draw, xy, title, f_title, title_fill)


def arrow(draw, start, end, color=ACCENT):
    draw.line([start, end], fill=color, width=3)
    x1, y1 = end
    # simple chevron
    draw.polygon([(x1, y1), (x1 - 8, y1 - 6), (x1 - 8, y1 + 6)], fill=color)


def make_process_flow():
    img = Image.new("RGB", (1600, 780), LIGHT)
    draw = ImageDraw.Draw(img)
    title_f = font(26, True)
    draw.text((40, 24), "Investigator Use-Case / Process Flow", font=title_f, fill=NAVY)

    steps = [
        (40, 100, 280, 210, "1. Ask (Voice / Chat)", "EN + KN natural language"),
        (340, 100, 580, 210, "2. Understand", "Sarvam STT + LangGraph"),
        (640, 100, 880, 210, "3. Route & Query", "SQL / Graph / Analytics"),
        (940, 100, 1180, 210, "4. Explain & Answer", "Audit trail + TTS"),
        (1240, 100, 1560, 210, "5. Act", "Dashboard / Network / PDF"),
    ]
    for box in steps:
        draw_box(draw, box[:4], box[4], box[5], fill=WHITE)
    for i in range(4):
        x0 = steps[i][2]
        x1 = steps[i + 1][0]
        arrow(draw, (x0 + 4, 155), (x1 - 4, 155))

    # Demo path
    demof = font(18, True)
    draw.text((40, 260), "Demo conversation path", font=demof, fill=SAFFRON)

    demo = [
        ('"Show robbery cases in Hubballi"', "SQL → FIR list"),
        ('"Show only pending ones"', "Context retained"),
        ('"Who is the IO?"', "Employee lookup"),
        ('"Show accused history"', "Cross-case search"),
        ('"Show criminal network"', "Neo4j graph"),
        ('"Export investigation"', "PDF + RBAC"),
    ]
    x, y = 40, 310
    for i, (q, a) in enumerate(demo):
        bx = x + (i % 3) * 520
        by = y + (i // 3) * 180
        rounded_rect(draw, (bx, by, bx + 480, by + 140), 14, fill=WHITE, outline=BORDER, width=2)
        draw.rounded_rectangle((bx, by, bx + 8, by + 140), radius=4, fill=SAFFRON)
        qf, af = font(15, True), font(14)
        for li, line in enumerate(wrap_text(draw, q, qf, 440)):
            draw.text((bx + 24, by + 24 + li * 22), line, font=qf, fill=NAVY)
        draw.text((bx + 24, by + 90), a, font=af, fill=GREEN)

    img.save(OUT / "process_flow.png", quality=95)
    print("wrote process_flow.png")


def make_architecture():
    img = Image.new("RGB", (1600, 860), LIGHT)
    draw = ImageDraw.Draw(img)
    draw.text((40, 20), "Crime AI — System Architecture", font=font(26, True), fill=NAVY)

    # Layers
    layers = [
        (40, 80, 1560, 180, "Presentation Layer", "Next.js + Tailwind  |  Chat · Dashboard · Network Graph · Crime Map · Voice"),
        (40, 220, 1560, 360, "AI Orchestration", "FastAPI + LangGraph  |  Conversation · SQL · Analytics · Graph · Report Agents  |  Gemini 2.5 Flash"),
        (40, 400, 760, 560, "Voice Pipeline", "LiveKit transport · Silero VAD · Sarvam STT (saarika) · Sarvam TTS (bulbul)"),
        (800, 400, 1560, 560, "Security & Access", "JWT Role Auth (Investigator / SHO / DSP / Analyst / Admin) · PDF role-gating · Audit trail"),
        (40, 600, 520, 780, "Operational Data", "Neon PostgreSQL\n+ pgvector\nKSP FIR schema"),
        (560, 600, 1040, 780, "Relationship Graph", "Neo4j Aura\nCo-accused · Shared victims\nOfficer–case networks"),
        (1080, 600, 1560, 780, "Hosting", "Zoho Catalyst AppSail\nStatic Client Hosting\nFile Store (reports)"),
    ]
    colors = [NAVY, ACCENT, SAFFRON, GREEN, NAVY, ACCENT, GREEN]
    for (x0, y0, x1, y1, title, body), c in zip(layers, colors):
        rounded_rect(draw, (x0, y0, x1, y1), 14, fill=WHITE, outline=BORDER, width=2)
        draw.rectangle((x0, y0, x0 + 10, y1), fill=c)
        draw.text((x0 + 28, y0 + 16), title, font=font(18, True), fill=c)
        for li, line in enumerate(body.split("\n")):
            draw.text((x0 + 28, y0 + 52 + li * 28), line, font=font(15), fill=MUTED)

    # arrows between top layers
    for y in (190, 370, 570):
        draw.polygon([(800, y), (790, y - 10), (810, y - 10)], fill=MUTED)

    img.save(OUT / "architecture.png", quality=95)
    print("wrote architecture.png")


def make_wireframes():
    img = Image.new("RGB", (1600, 820), LIGHT)
    draw = ImageDraw.Draw(img)
    draw.text((40, 18), "UI Wireframes — Crime AI Prototype", font=font(24, True), fill=NAVY)

    screens = [
        (40, 70, 520, 400, "Login", ["Role-based JWT login", "Investigator / SHO / DSP", "Analyst / Administrator"]),
        (540, 70, 1020, 400, "Chat + Voice", ["EN / KN NL queries", "Mic / LiveKit voice", "Streaming answers + audit"]),
        (1040, 70, 1560, 400, "Dashboard", ["FIR KPIs & trends", "Hotspots + early warnings", "Leaflet crime map"]),
        (40, 430, 780, 780, "Criminal Network", ["Neo4j force graph", "Accused ↔ Case ↔ Victim", "Repeat-offender links"]),
        (820, 430, 1560, 780, "Investigation PDF", ["Conversation history", "SQL/Cypher evidence", "Role-gated export"]),
    ]
    for x0, y0, x1, y1, title, bullets in screens:
        rounded_rect(draw, (x0, y0, x1, y1), 14, fill=WHITE, outline=BORDER, width=2)
        # fake browser chrome
        draw.rectangle((x0, y0, x1, y0 + 36), fill=NAVY)
        draw.ellipse((x0 + 14, y0 + 12, x0 + 24, y0 + 22), fill=(255, 99, 71))
        draw.ellipse((x0 + 30, y0 + 12, x0 + 40, y0 + 22), fill=(255, 205, 57))
        draw.ellipse((x0 + 46, y0 + 12, x0 + 56, y0 + 22), fill=(40, 200, 100))
        draw.text((x0 + 70, y0 + 8), title, font=font(14, True), fill=WHITE)
        # content area
        draw.rectangle((x0 + 16, y0 + 52, x1 - 16, y0 + 120), fill=(235, 241, 250))
        draw.text((x0 + 28, y0 + 74), f"{title} screen", font=font(16, True), fill=NAVY)
        for i, b in enumerate(bullets):
            draw.ellipse((x0 + 28, y0 + 150 + i * 42, x0 + 38, y0 + 160 + i * 42), fill=SAFFRON)
            draw.text((x0 + 50, y0 + 142 + i * 42), b, font=font(15), fill=MUTED)

    img.save(OUT / "wireframes.png", quality=95)
    print("wrote wireframes.png")


def make_snapshots_placeholder():
    """Composite snapshot board describing prototype screens (no live browser captures)."""
    img = Image.new("RGB", (1600, 820), LIGHT)
    draw = ImageDraw.Draw(img)
    draw.text((40, 18), "Prototype Snapshots — Key Screens", font=font(24, True), fill=NAVY)

    cards = [
        (40, 70, 520, 420, "💬 Conversational Chat", "Natural-language FIR / person / analytics queries with streaming replies and tool audit trail."),
        (540, 70, 1020, 420, "🎤 Voice Interaction", "Browser mic → Sarvam STT → LangGraph → Sarvam TTS. LiveKit realtime path ready."),
        (1040, 70, 1560, 420, "📊 Officer Dashboard", "FIR totals, trends by district/month/crime head, hotspots, socio insights, early warnings."),
        (40, 450, 520, 780, "🕸 Network Graph", "Neo4j visualization of co-accused and repeat-offender linkages across cases."),
        (540, 450, 1020, 780, "🗺 Crime Hotspot Map", "Leaflet map over CaseMaster lat/long for spatial pattern discovery."),
        (1040, 450, 1560, 780, "📄 PDF Export", "Investigation summary with explainable SQL/Cypher evidence; role-gated for SHO+."),
    ]
    for x0, y0, x1, y1, title, body in cards:
        rounded_rect(draw, (x0, y0, x1, y1), 14, fill=WHITE, outline=BORDER, width=2)
        draw.rectangle((x0, y0, x1, y0 + 56), fill=NAVY)
        draw.text((x0 + 18, y0 + 14), title, font=font(16, True), fill=WHITE)
        yy = y0 + 80
        for line in wrap_text(draw, body, font(15), x1 - x0 - 40):
            draw.text((x0 + 20, yy), line, font=font(15), fill=MUTED)
            yy += 26

    img.save(OUT / "snapshots.png", quality=95)
    print("wrote snapshots.png")


if __name__ == "__main__":
    make_process_flow()
    make_architecture()
    make_wireframes()
    make_snapshots_placeholder()
