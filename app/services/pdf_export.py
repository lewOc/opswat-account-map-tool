"""Customer-ready PDF export for v2 account maps."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape as html_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.account_map import AccountMap, BuyerPersona, Evidence, Signal, UseCase


OPSWAT_RED = colors.HexColor("#C8102E")
OPSWAT_DARK = colors.HexColor("#111827")
OPSWAT_MID = colors.HexColor("#4B5563")
OPSWAT_LIGHT = colors.HexColor("#F3F4F6")
OPSWAT_LINE = colors.HexColor("#D1D5DB")


def export_account_map_pdf(account_map: AccountMap, output_path: Path) -> Path:
    """Render a branded account-map report and return the written PDF path."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=24 * mm,
        bottomMargin=18 * mm,
        title=f"OPSWAT Account Map - {account_map.target_account.name}",
        author="OPSWAT",
    )
    story = build_report_story(account_map, styles)
    doc.build(
        story,
        onFirstPage=lambda canvas, document: draw_page_branding(canvas, document, account_map.target_account.name),
        onLaterPages=lambda canvas, document: draw_page_branding(canvas, document, account_map.target_account.name),
    )
    return output_path


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "CoverKicker",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=OPSWAT_RED,
            alignment=TA_LEFT,
            spaceAfter=5,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=31,
            textColor=OPSWAT_DARK,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=OPSWAT_MID,
            spaceAfter=12,
        ),
        "h1": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=OPSWAT_DARK,
            spaceBefore=12,
            spaceAfter=7,
        ),
        "h2": ParagraphStyle(
            "SubHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=14,
            textColor=OPSWAT_RED,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "CardHeading",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=OPSWAT_DARK,
            spaceBefore=5,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.8,
            leading=12.2,
            textColor=OPSWAT_DARK,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.4,
            leading=10,
            textColor=OPSWAT_MID,
            spaceAfter=3,
        ),
        "small_bold": ParagraphStyle(
            "SmallBold",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.4,
            leading=10,
            textColor=OPSWAT_DARK,
            spaceAfter=2,
        ),
        "metric_number": ParagraphStyle(
            "MetricNumber",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=20,
            alignment=TA_CENTER,
            textColor=OPSWAT_RED,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.2,
            leading=9,
            alignment=TA_CENTER,
            textColor=OPSWAT_MID,
        ),
        "quote": ParagraphStyle(
            "Quote",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8.8,
            leading=12.2,
            textColor=OPSWAT_DARK,
            leftIndent=6,
            borderColor=OPSWAT_RED,
            borderWidth=1,
            borderPadding=5,
            spaceBefore=3,
            spaceAfter=6,
        ),
    }


def build_report_story(account_map: AccountMap, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    target = account_map.target_account
    generated = format_generated_at(account_map.meta.generated_at)
    focus = account_map.meta.focus or "Account discovery and value mapping"
    products = sorted(
        {
            product.product or product.slug
            for use_case in account_map.recommended_use_cases
            for product in use_case.opswat_products
        }
    )
    story: list[Flowable] = [
        Paragraph("OPSWAT ACCOUNT MAP", styles["cover_kicker"]),
        Paragraph(f"How OPSWAT can help {clean(target.name)}", styles["cover_title"]),
        Paragraph(clean(target.summary), styles["subtitle"]),
        metric_table(
            [
                ("Use cases", str(len(account_map.recommended_use_cases))),
                ("Signals", str(len(account_map.account_signals))),
                ("Products", str(len(products))),
                ("Sources", str(len(account_map.research_evidence))),
            ],
            styles,
        ),
        Spacer(1, 8),
        Paragraph("Prepared for a customer discovery conversation", styles["h1"]),
        two_column_fact_table(
            [
                ("Account", target.name),
                ("Sector", target.sector),
                ("Country", target.country or "To confirm"),
                ("Focus", focus),
                ("Generated", generated),
            ],
            styles,
        ),
        Paragraph("Priority ways OPSWAT can help", styles["h1"]),
        bullet_list(
            [
                f"{use_case_display_title(use_case)}: {use_case.business_value or use_case.business_value_narrative}"
                for use_case in account_map.recommended_use_cases[:5]
            ],
            styles,
        ),
        Paragraph("Account Signals", styles["h1"]),
    ]

    story.extend(signal_blocks(account_map.account_signals, styles))
    story.append(PageBreak())
    story.append(Paragraph("Recommended Use Cases", styles["h1"]))
    for use_case in account_map.recommended_use_cases:
        story.extend(use_case_block(use_case, styles))

    story.append(PageBreak())
    story.append(Paragraph("Buyer Map", styles["h1"]))
    story.extend(buyer_blocks(account_map.buyer_map, styles))
    story.append(Paragraph("Outreach Plan", styles["h1"]))
    story.append(Paragraph(clean(account_map.outreach.opening_angle), styles["body"]))
    if account_map.outreach.email_subjects:
        story.append(Paragraph("Email subject angles", styles["h2"]))
        story.append(bullet_list(account_map.outreach.email_subjects, styles))
    if account_map.outreach.first_call_agenda:
        story.append(Paragraph("First call agenda", styles["h2"]))
        story.append(bullet_list(account_map.outreach.first_call_agenda, styles))

    if account_map.assumptions_and_gaps:
        story.append(Paragraph("Discovery Items To Validate", styles["h1"]))
        story.append(
            bullet_list(
                [f"{gap.item} - {gap.how_to_validate}" for gap in account_map.assumptions_and_gaps],
                styles,
            )
        )

    story.append(PageBreak())
    story.append(Paragraph("Evidence Appendix", styles["h1"]))
    story.extend(evidence_blocks(account_map.research_evidence, styles))
    return story


def metric_table(metrics: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    cells = [
        [Paragraph(value, styles["metric_number"]), Paragraph(label.upper(), styles["metric_label"])]
        for label, value in metrics
    ]
    table = Table([cells], colWidths=[39 * mm] * len(metrics), hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, OPSWAT_LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, OPSWAT_LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def two_column_fact_table(facts: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [Paragraph(clean(label), styles["small_bold"]), Paragraph(clean(value), styles["small"])]
        for label, value in facts
        if value
    ]
    table = Table(rows, colWidths=[31 * mm, 127 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.5, OPSWAT_LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, OPSWAT_LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def signal_blocks(signals: Iterable[Signal], styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    blocks: list[Flowable] = []
    for signal in signals:
        blocks.append(
            KeepTogether(
                [
                    Paragraph(clean(signal.signal), styles["h3"]),
                    Paragraph(clean(signal.why_it_matters), styles["body"]),
                    Paragraph(f"Confidence: {clean(signal.confidence.value)}", styles["small"]),
                    Spacer(1, 3),
                ]
            )
        )
    if not blocks:
        blocks.append(Paragraph("No account signals were generated.", styles["body"]))
    return blocks


def use_case_block(use_case: UseCase, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    flowables: list[Flowable] = [
        KeepTogether(
            [
                Paragraph(f"{use_case.rank}. {clean(use_case_display_title(use_case))}", styles["h2"]),
                Paragraph(clean(use_case.account_trigger), styles["small"]),
            ]
        ),
        labelled_paragraph("Problem", use_case.problem_narrative or use_case.problem, styles),
        labelled_paragraph("OPSWAT Solution", use_case.solution_narrative or use_case.deployment_hypothesis, styles),
        labelled_paragraph("Customer Value", use_case.business_value_narrative or use_case.business_value, styles),
    ]
    if use_case.conversation_starter:
        flowables.append(Paragraph(f'"{clean(use_case.conversation_starter)}"', styles["quote"]))
    if use_case.opswat_products:
        flowables.append(Paragraph("Product Fit", styles["h3"]))
        flowables.append(product_table(use_case, styles))
    if use_case.implementation_flow:
        flowables.append(Paragraph("Implementation Flow", styles["h3"]))
        flowables.append(bullet_list(use_case.implementation_flow, styles))
    if use_case.delivery_experience:
        flowables.append(Paragraph("Relevant Delivery Experience", styles["h3"]))
        for example in use_case.delivery_experience:
            flowables.append(
                KeepTogether(
                    [
                        Paragraph(clean(example.title), styles["small_bold"]),
                        Paragraph(clean(example.relevance), styles["small"]),
                        Paragraph(clean(example.outcome), styles["small"]),
                    ]
                )
            )
    if use_case.discovery_questions:
        flowables.append(Paragraph("Discovery Questions", styles["h3"]))
        flowables.append(bullet_list(use_case.discovery_questions, styles))
    flowables.append(Spacer(1, 8))
    return flowables


def product_table(use_case: UseCase, styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            Paragraph("Product", styles["small_bold"]),
            Paragraph("Why it fits", styles["small_bold"]),
            Paragraph("Capabilities", styles["small_bold"]),
        ]
    ]
    for product in use_case.opswat_products:
        rows.append(
            [
                Paragraph(clean(product.product or product.slug), styles["small"]),
                Paragraph(clean(product.fit_reason), styles["small"]),
                Paragraph(clean(", ".join(product.capabilities_used) or "To confirm"), styles["small"]),
            ]
        )
    table = Table(rows, colWidths=[36 * mm, 78 * mm, 42 * mm], hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), OPSWAT_LIGHT),
                ("TEXTCOLOR", (0, 0), (-1, 0), OPSWAT_DARK),
                ("BOX", (0, 0), (-1, -1), 0.5, OPSWAT_LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, OPSWAT_LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def buyer_blocks(buyers: Iterable[BuyerPersona], styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    blocks: list[Flowable] = []
    for buyer in buyers:
        items: list[Flowable] = [
            Paragraph(clean(buyer.persona), styles["h3"]),
            Paragraph(clean(buyer.message_angle), styles["body"]),
        ]
        if buyer.likely_concerns:
            items.append(bullet_list(buyer.likely_concerns, styles))
        blocks.append(KeepTogether(items))
    if not blocks:
        blocks.append(Paragraph("No buyer map was generated.", styles["body"]))
    return blocks


def evidence_blocks(evidence_items: Iterable[Evidence], styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    blocks: list[Flowable] = []
    for item in evidence_items:
        url = str(item.source_url)
        link = f'<link href="{html_escape(url)}" color="#C8102E">{html_escape(item.source_title)}</link>'
        blocks.append(
            KeepTogether(
                [
                    Paragraph(clean(item.claim), styles["h3"]),
                    Paragraph(f"{link}<br/>Confidence: {clean(item.confidence.value)}", styles["small"]),
                    Spacer(1, 3),
                ]
            )
        )
    if not blocks:
        blocks.append(Paragraph("No research evidence was generated.", styles["body"]))
    return blocks


def labelled_paragraph(label: str, value: str, styles: dict[str, ParagraphStyle]) -> Flowable:
    return KeepTogether([Paragraph(clean(label), styles["h3"]), Paragraph(clean(value), styles["body"])])


def bullet_list(items: Iterable[str], styles: dict[str, ParagraphStyle]) -> Flowable:
    values = [item for item in items if item]
    if not values:
        return Paragraph("To confirm in discovery.", styles["body"])
    return ListFlowable(
        [ListItem(Paragraph(clean(item), styles["body"]), leftIndent=8) for item in values],
        bulletType="bullet",
        start="circle",
        leftIndent=12,
        bulletFontName="Helvetica",
        bulletFontSize=5,
        bulletColor=OPSWAT_RED,
    )


def draw_page_branding(canvas, document, account_name: str) -> None:  # type: ignore[no-untyped-def]
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(OPSWAT_RED)
    canvas.rect(0, height - 10 * mm, width, 10 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(16 * mm, height - 6.6 * mm, "OPSWAT")
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(width - 16 * mm, height - 6.3 * mm, "Account Map")

    canvas.setStrokeColor(OPSWAT_LINE)
    canvas.setLineWidth(0.4)
    canvas.line(16 * mm, 13 * mm, width - 16 * mm, 13 * mm)
    canvas.setFillColor(OPSWAT_MID)
    canvas.setFont("Helvetica", 7)
    footer = f"{account_name} | OPSWAT customer opportunity brief"
    canvas.drawString(16 * mm, 8.5 * mm, footer[:95])
    canvas.drawRightString(width - 16 * mm, 8.5 * mm, f"Page {document.page}")
    canvas.restoreState()


def format_generated_at(value: datetime) -> str:
    return value.strftime("%d %b %Y")


def use_case_display_title(use_case: UseCase) -> str:
    if use_case.title.strip():
        return use_case.title.strip()
    text = f"{use_case.problem} {use_case.account_trigger}".lower()
    if "remote access" in text:
        return "Secure vendor remote access into OT"
    if ("removable" in text or "media" in text) and (
        "file" in text or "document" in text or "vendor" in text or "supplier" in text or "contractor" in text
    ):
        return "Secure file and removable-media ingress into OT"
    if ("file" in text or "document" in text) and (
        "supplier" in text or "vendor" in text or "partner" in text or "contractor" in text
    ):
        return "Secure supplier and partner file exchange"
    if "cloud" in text and ("storage" in text or "file" in text):
        return "Secure cloud storage and content inspection"
    if "ot" in text or "operational technology" in text:
        return "Strengthen OT cyber-resilience controls"
    return f"Priority OPSWAT opportunity {use_case.rank}"


def clean(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "To confirm in discovery."
    return html_escape(text).replace("\n", "<br/>")
