from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "PSOAS_Windows_Installation_Guide.pdf"
SCREENSHOT = Path(
    "/var/folders/35/ckf0g5kj3dj2g8gpkxft90740000gn/T/"
    "codex-clipboard-Z6KMhY.png"
)


PAGE_W, PAGE_H = letter
MARGIN = 0.62 * inch
CONTENT_W = PAGE_W - 2 * MARGIN


styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        name="CoverTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=27,
        leading=32,
        textColor=colors.HexColor("#17324D"),
        alignment=TA_LEFT,
        spaceAfter=10,
    )
)
styles.add(
    ParagraphStyle(
        name="Subtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#496579"),
        spaceAfter=16,
    )
)
styles.add(
    ParagraphStyle(
        name="H1Blue",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=23,
        textColor=colors.HexColor("#17324D"),
        spaceBefore=2,
        spaceAfter=10,
    )
)
styles.add(
    ParagraphStyle(
        name="H2Blue",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#246A8D"),
        spaceBefore=9,
        spaceAfter=5,
    )
)
styles.add(
    ParagraphStyle(
        name="BodyClean",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#263746"),
        spaceAfter=6,
    )
)
styles.add(
    ParagraphStyle(
        name="SmallClean",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.8,
        leading=12,
        textColor=colors.HexColor("#526575"),
        spaceAfter=4,
    )
)
styles.add(
    ParagraphStyle(
        name="StepNumber",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
)
styles.add(
    ParagraphStyle(
        name="CodeBox",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=9.4,
        leading=13,
        textColor=colors.HexColor("#17324D"),
        leftIndent=0,
        rightIndent=0,
        spaceAfter=0,
    )
)


def p(text, style="BodyClean"):
    return Paragraph(text, styles[style])


def bullet(text):
    return Paragraph(f"&#8226;&nbsp;&nbsp;{text}", styles["BodyClean"])


def step_card(number, title, body):
    badge = Table([[Paragraph(str(number), styles["StepNumber"])]], colWidths=[0.36 * inch], rowHeights=[0.36 * inch])
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#246A8D")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    content = [p(f"{title}", "H2Blue"), p(body)]
    table = Table([[badge, content]], colWidths=[0.52 * inch, CONTENT_W - 0.52 * inch])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def callout(title, body, background="#EAF3F7", border="#8AB5C8"):
    content = [p(f"<b>{title}</b>", "BodyClean"), p(body, "SmallClean")]
    table = Table([[content]], colWidths=[CONTENT_W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(border)),
                ("LEFTPADDING", (0, 0), (-1, -1), 11),
                ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def code_box(text):
    table = Table([[Paragraph(text.replace("\n", "<br/>"), styles["CodeBox"])]], colWidths=[CONTENT_W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F6F8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D2D9")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7E0E5"))
    canvas.line(MARGIN, 0.43 * inch, PAGE_W - MARGIN, 0.43 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#71818C"))
    canvas.drawString(MARGIN, 0.25 * inch, "PSOAS Windows setup guide")
    canvas.drawRightString(PAGE_W - MARGIN, 0.25 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_story():
    story = []

    story += [
        Spacer(1, 0.25 * inch),
        p("PSOAS", "CoverTitle"),
        p("Windows setup guide", "Subtitle"),
        callout(
            "For Windows 10/11 x64 laptops",
            "This guide is for users who will copy the complete PSOAS folder from a USB drive and start it by double-clicking a file. Users do not need VS Code and should not type installation commands.",
        ),
        Spacer(1, 0.16 * inch),
        p("What you need", "H1Blue"),
        bullet("A Windows 10 or Windows 11 x64 laptop"),
        bullet("Internet access during the first launch"),
        bullet("The complete PSOAS project folder from the USB drive"),
        bullet("The supplied .env file in the project folder"),
        Spacer(1, 0.12 * inch),
        step_card(
            1,
            "Copy PSOAS onto the laptop",
            "Copy the entire folder from the USB drive to the laptop's local drive. A simple location is <b>C:\\PSOAS</b>. Do not run PSOAS directly from the USB drive.",
        ),
        p("The main folder should contain these items:", "BodyClean"),
        code_box("Run-PSOAS.cmd\nRun-PSOAS.ps1\nrequirements.txt\nsrc\\\ndata\\\nsysprompts\\\n.env"),
        Spacer(1, 0.12 * inch),
        callout(
            "Keep the .env file private",
            "The .env file contains API-key settings. Copy it only to an authorized laptop and do not email it, upload it, or share it with other people.",
            background="#FFF4E5",
            border="#E3B56C",
        ),
        PageBreak(),
    ]

    story += [
        p("Install Python 3.14.7", "H1Blue"),
        p("Use the official standalone Windows installer. Do not download Python Source, the embeddable package, or the ARM64 installer.", "BodyClean"),
        p("Open this website:", "BodyClean"),
        code_box("https://www.python.org/downloads/"),
        p("Then choose <b>Downloads - Windows</b>. Under <b>Download for Windows</b>, click the lower button labelled <b>Python 3.14.7</b> beside <b>Or get the standalone installer for</b>.", "BodyClean"),
    ]

    if SCREENSHOT.exists():
        img = Image(str(SCREENSHOT))
        max_w = CONTENT_W
        max_h = 4.05 * inch
        scale = min(max_w / img.imageWidth, max_h / img.imageHeight)
        img.drawWidth = img.imageWidth * scale
        img.drawHeight = img.imageHeight * scale
        img.hAlign = "CENTER"
        story += [Spacer(1, 0.05 * inch), img, Spacer(1, 0.04 * inch)]
    story += [
        p("The correct download is the standalone Windows installer shown near the bottom of the menu. It will usually download a file named similar to <b>python-3.14.7-amd64.exe</b>.", "SmallClean"),
        Spacer(1, 0.08 * inch),
        p("Run the installer", "H2Blue"),
        bullet("Open the downloaded .exe file."),
        bullet("Check <b>Add python.exe to PATH</b>."),
        bullet("Click <b>Install Now</b>."),
        bullet("Wait for the installation to finish."),
        Spacer(1, 0.1 * inch),
        callout(
            "If the company laptop blocks installation",
            "Do not disable antivirus, Windows security, or company controls. Try the current-user installation option if the installer offers it. If that is blocked too, contact IT and request approval for Python 3.14.7 (64-bit).",
            background="#FFF4E5",
            border="#E3B56C",
        ),
        PageBreak(),
    ]

    story += [
        p("Start PSOAS", "H1Blue"),
        p("After Python finishes installing, close and reopen any open File Explorer or VS Code windows that were already open. Then open the copied PSOAS folder.", "BodyClean"),
        p("Double-click this file:", "H2Blue"),
        code_box("Run-PSOAS.cmd"),
        p("Do not double-click the .ps1 file. Do not open Windows Terminal for setup.", "BodyClean"),
        Spacer(1, 0.08 * inch),
        p("What happens automatically", "H2Blue"),
        bullet("A private Python environment named .venv is created inside the PSOAS folder."),
        bullet("rich and the other packages in requirements.txt are installed."),
        bullet("The local document index is refreshed."),
        bullet("The PSOAS terminal opens."),
        Spacer(1, 0.1 * inch),
        callout(
            "First launch can take several minutes",
            "Keep the laptop connected to the internet and leave the window open. Large AI and document-processing packages may be downloaded. Do not close the window while setup is running.",
        ),
        Spacer(1, 0.14 * inch),
        p("Talk to the orchestrator", "H2Blue"),
        p("When PSOAS displays its prompt, type a normal question. For example:", "BodyClean"),
        code_box("What is the bull case for ASMI?"),
        Spacer(1, 0.12 * inch),
        p("For future launches", "H2Blue"),
        p("Open the PSOAS folder and double-click <b>Run-PSOAS.cmd</b> again. Do not delete the .venv, data, temp, or index folders.", "BodyClean"),
        Spacer(1, 0.12 * inch),
        callout(
            "If there is an error",
            "Keep the window open and send the exact error message to the PSOAS support contact. Do not delete folders or reinstall packages unless support asks you to.",
            background="#FDECEC",
            border="#D99A9A",
        ),
    ]
    return story


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=letter,
        rightMargin=MARGIN,
        leftMargin=MARGIN,
        topMargin=0.55 * inch,
        bottomMargin=0.62 * inch,
        title="PSOAS Windows Setup Guide",
        author="PSOAS",
    )
    doc.build(build_story(), onFirstPage=footer, onLaterPages=footer)
    print(OUTPUT)


if __name__ == "__main__":
    main()
