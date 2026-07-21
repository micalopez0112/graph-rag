"""
generate_pdf.py
---------------
Generates a realistic "Power Plant RDS PP Code Reference Manual" PDF
with multiple tables of equipment codes, fault codes, and maintenance intervals.

RDS-PP = Reference Designation System for Power Plants (IEC 61346 / EN 81346).
It is the standard naming system used in power plant engineering to uniquely
identify components.

Run: python data/generate_pdf.py
Output: data/power_plant_rds_pp_codes.pdf
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, PageBreak, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import os

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "power_plant_rds_pp_codes.pdf")


# ---------------------------------------------------------------------------
# DATA TABLES
# ---------------------------------------------------------------------------

# Table 1 – Main Equipment RDS-PP Codes
EQUIPMENT_CODES = [
    ["RDS-PP Code",   "Component Name",               "System",       "Location",      "Rated Value",        "Status"],
    ["PP-ENG-001",    "Diesel Prime Mover",            "Generation",   "Engine Room",   "600 kW / 1500 rpm",  "Operational"],
    ["PP-ALT-001",    "Synchronous Alternator",        "Generation",   "Engine Room",   "750 kVA / 400V",     "Operational"],
    ["PP-AVR-001",    "Automatic Voltage Regulator",   "Excitation",   "Control Panel", "MX341",              "Operational"],
    ["PP-GOV-001",    "Electronic Speed Governor",     "Control",      "Engine Room",   "EG3P",               "Operational"],
    ["PP-MCB-001",    "Main Generator CB",             "Protection",   "Switchgear Rm", "400A / 36kA",        "Operational"],
    ["PP-TRF-001",    "Step-Up Transformer",           "Distribution", "Yard",          "630 kVA 400/11kV",   "Operational"],
    ["PP-BUS-001",    "11kV Main Bus Bar",             "Distribution", "Switchgear Rm", "11kV / 600A",        "Operational"],
    ["PP-GCP-001",    "Generator Control Panel",       "Control",      "Control Room",  "InteliGen NT",       "Operational"],
    ["PP-LFA-001",    "Feeder A – Essential Loads",    "Distribution", "LV Switchboard","200 kW",             "Operational"],
    ["PP-LFB-001",    "Feeder B – Industrial Loads",   "Distribution", "LV Switchboard","400 kW",             "Operational"],
]

# Table 2 – Fault / Alarm Codes
FAULT_CODES = [
    ["Fault Code", "Description",                    "Associated RDS-PP", "Severity", "Action Required",                              "Reset Type"],
    ["F-001",      "High Coolant Temperature",       "PP-ENG-001",        "Critical", "Stop engine. Check coolant level & radiator.", "Manual"],
    ["F-002",      "Low Oil Pressure",               "PP-ENG-001",        "Critical", "Stop engine immediately. Check oil level.",    "Manual"],
    ["F-003",      "Engine Overspeed",               "PP-ENG-001",        "Critical", "Emergency stop. Check governor.",             "Manual"],
    ["F-004",      "Engine Underspeed",              "PP-ENG-001",        "Warning",  "Check fuel supply and governor setpoint.",    "Auto"],
    ["F-005",      "High Winding Temperature",       "PP-ALT-001",        "Critical", "Reduce load. Check ventilation.",             "Manual"],
    ["F-006",      "Generator Overvoltage",          "PP-ALT-001",        "Critical", "Check AVR. Trip main breaker.",               "Manual"],
    ["F-007",      "Generator Undervoltage",         "PP-ALT-001",        "Warning",  "Check AVR setpoint and excitation.",         "Auto"],
    ["F-008",      "Generator Overcurrent",          "PP-ALT-001",        "Critical", "Reduce load or trip non-essential feeders.", "Manual"],
    ["F-009",      "AVR Fault",                      "PP-AVR-001",        "Critical", "Replace AVR or switch to manual excitation.", "Manual"],
    ["F-010",      "Excitation Loss",                "PP-AVR-001",        "Critical", "Check AVR connections. Trip generator.",     "Manual"],
    ["F-011",      "Governor Fault",                 "PP-GOV-001",        "Critical", "Stop engine. Inspect governor actuator.",     "Manual"],
    ["F-012",      "Main Breaker Trip",              "PP-MCB-001",        "Critical", "Identify fault before re-closing.",          "Manual"],
    ["F-013",      "Transformer High Oil Temp",      "PP-TRF-001",        "Critical", "Reduce load. Check oil cooling system.",      "Manual"],
    ["F-014",      "Buchholz Relay Trip",            "PP-TRF-001",        "Critical", "Take transformer out of service. Test oil.", "Manual"],
    ["F-015",      "Control Panel Power Loss",       "PP-GCP-001",        "Critical", "Check UPS and panel supply fuses.",          "Auto"],
    ["F-016",      "Communication Fault (Modbus)",   "PP-GCP-001",        "Warning",  "Check network cables and device addresses.", "Auto"],
    ["F-017",      "Feeder A Overcurrent Trip",      "PP-LFA-001",        "Critical", "Identify overloaded essential circuits.",    "Manual"],
    ["F-018",      "Feeder B Overcurrent Trip",      "PP-LFB-001",        "Warning",  "Shed non-essential industrial loads.",       "Auto"],
]

# Table 3 – Preventive Maintenance Schedule
MAINTENANCE_CODES = [
    ["Maint. Code", "Task Description",                   "RDS-PP Code", "Interval",       "Duration (h)", "Skill Level"],
    ["M-001",       "Engine oil & filter change",         "PP-ENG-001",  "250 hours",      "2",            "Technician"],
    ["M-002",       "Engine coolant check & top-up",      "PP-ENG-001",  "50 hours",       "0.5",          "Operator"],
    ["M-003",       "Fuel filter replacement",            "PP-ENG-001",  "500 hours",      "1",            "Technician"],
    ["M-004",       "Air filter inspection/replacement",  "PP-ENG-001",  "500 hours",      "1",            "Technician"],
    ["M-005",       "Drive belt inspection",              "PP-ENG-001",  "500 hours",      "1",            "Technician"],
    ["M-006",       "Engine coolant flush & replace",     "PP-ENG-001",  "2000 hours",     "3",            "Technician"],
    ["M-007",       "Valve clearance adjustment",         "PP-ENG-001",  "1000 hours",     "4",            "Engineer"],
    ["M-008",       "Alternator winding insulation test", "PP-ALT-001",  "Annual",         "2",            "Engineer"],
    ["M-009",       "Alternator bearing lubrication",     "PP-ALT-001",  "1000 hours",     "1",            "Technician"],
    ["M-010",       "Slip ring & brush inspection",       "PP-ALT-001",  "500 hours",      "1",            "Technician"],
    ["M-011",       "AVR setpoint verification",          "PP-AVR-001",  "Annual",         "1",            "Engineer"],
    ["M-012",       "Governor response test",             "PP-GOV-001",  "Annual",         "2",            "Engineer"],
    ["M-013",       "Main breaker functional test",       "PP-MCB-001",  "Annual",         "1",            "Engineer"],
    ["M-014",       "Transformer oil sampling & test",    "PP-TRF-001",  "Annual",         "2",            "Engineer"],
    ["M-015",       "Transformer oil level check",        "PP-TRF-001",  "Monthly",        "0.25",         "Operator"],
    ["M-016",       "Bus bar torque check",               "PP-BUS-001",  "Annual",         "2",            "Engineer"],
    ["M-017",       "Control panel battery test (UPS)",   "PP-GCP-001",  "6 months",       "1",            "Technician"],
    ["M-018",       "Full load test run",                 "ALL",         "Monthly",        "2",            "Engineer"],
]

# Table 4 – Operating Parameters & Setpoints
OPERATING_PARAMS = [
    ["Parameter",                    "RDS-PP Code", "Normal Range",       "Warning Threshold",  "Trip Threshold",     "Unit"],
    ["Engine Speed",                 "PP-ENG-001",  "1480 – 1520",        "< 1450 or > 1560",   "< 1400 or > 1650",   "rpm"],
    ["Engine Coolant Temperature",   "PP-ENG-001",  "70 – 90",            "> 95",               "> 105",              "°C"],
    ["Engine Oil Pressure",          "PP-ENG-001",  "3.5 – 6.0",          "< 2.5",              "< 1.5",              "bar"],
    ["Engine Exhaust Temperature",   "PP-ENG-001",  "350 – 480",          "> 520",              "> 600",              "°C"],
    ["Generator Voltage (L-L)",      "PP-ALT-001",  "396 – 404",          "< 380 or > 420",     "< 360 or > 440",     "V"],
    ["Generator Frequency",          "PP-ALT-001",  "49.5 – 50.5",        "< 48.5 or > 51.5",   "< 47.0 or > 52.0",  "Hz"],
    ["Generator Current (rated)",    "PP-ALT-001",  "0 – 360",            "> 380",              "> 420",              "A"],
    ["Generator Winding Temp",       "PP-ALT-001",  "< 100",              "> 120",              "> 145",              "°C"],
    ["Power Factor",                 "PP-ALT-001",  "0.75 – 1.0",         "< 0.70",             "< 0.60",             "pf"],
    ["AVR Excitation Voltage",       "PP-AVR-001",  "20 – 60",            "> 65",               "> 75",               "V DC"],
    ["Transformer Oil Temp",         "PP-TRF-001",  "< 70",               "> 80",               "> 95",               "°C"],
    ["Transformer Load",             "PP-TRF-001",  "0 – 90",             "> 95",               "> 110",              "% of rating"],
    ["Bus Bar Voltage",              "PP-BUS-001",  "10.7 – 11.3",        "< 10.2 or > 11.8",   "< 9.5 or > 12.5",   "kV"],
]


# ---------------------------------------------------------------------------
# PDF BUILDER
# ---------------------------------------------------------------------------

def build_table(data, col_widths, header_bg=colors.HexColor("#1a3a5c")):
    """Build a styled ReportLab Table from a 2D list."""
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        # Header row
        ("BACKGROUND",   (0, 0), (-1, 0),  header_bg),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("ALIGN",        (0, 0), (-1, 0),  "CENTER"),
        ("BOTTOMPADDING",(0, 0), (-1, 0),  6),
        ("TOPPADDING",   (0, 0), (-1, 0),  6),
        # Data rows
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 7),
        ("ALIGN",        (0, 1), (0, -1),  "CENTER"),   # code column centred
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 4),
        # Alternating row colours
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f7")]),
        # Grid
        ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#b0bec5")),
        ("BOX",          (0, 0), (-1, -1), 1,   colors.HexColor("#1a3a5c")),
    ])
    table.setStyle(style)
    return table


def generate_pdf(output_path: str):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"],
        fontSize=18, textColor=colors.HexColor("#1a3a5c"),
        spaceAfter=6, alignment=TA_CENTER
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontSize=10, textColor=colors.HexColor("#546e7a"),
        spaceAfter=16, alignment=TA_CENTER
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontSize=12, textColor=colors.white,
        backColor=colors.HexColor("#1a3a5c"),
        borderPad=6, spaceBefore=18, spaceAfter=8,
        leftIndent=-10, rightIndent=-10, alignment=TA_LEFT
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=8.5, textColor=colors.HexColor("#37474f"),
        spaceAfter=8, leading=13
    )

    story = []

    # ── Cover / Header ──────────────────────────────────────────────────────
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("SMALL DIESEL POWER PLANT", title_style))
    story.append(Paragraph("RDS-PP Component & Fault Code Reference Manual", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a3a5c")))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(
        "Document No.: PP-REF-2024-001 &nbsp;&nbsp;|&nbsp;&nbsp; Revision: A &nbsp;&nbsp;|&nbsp;&nbsp; "
        "Date: 2024-01-15 &nbsp;&nbsp;|&nbsp;&nbsp; Classification: Internal",
        ParagraphStyle("Meta", parent=styles["Normal"], fontSize=7.5,
                       textColor=colors.HexColor("#78909c"), alignment=TA_CENTER)
    ))
    story.append(Spacer(1, 0.6 * cm))

    # ── Introduction ─────────────────────────────────────────────────────────
    story.append(Paragraph("1. Introduction", section_style))
    story.append(Paragraph(
        "This document provides the Reference Designation System for Power Plants (RDS-PP) coding scheme "
        "for all major components of the small diesel-driven power plant. RDS-PP is based on the IEC 61346 / "
        "EN 81346 international standard and provides a hierarchical, function-oriented naming convention that "
        "uniquely identifies every component in the plant. "
        "The tables in this manual are the primary reference for maintenance work orders, fault diagnosis, "
        "spare parts ordering, and integration with the plant's SCADA and Asset Management systems.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Plant Rated Capacity:</b> 600 kW (750 kVA) &nbsp;&nbsp; "
        "<b>Fuel:</b> Diesel &nbsp;&nbsp; "
        "<b>Output Voltage:</b> 400V / 11kV &nbsp;&nbsp; "
        "<b>Frequency:</b> 50 Hz",
        body_style
    ))

    # ── Table 1 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("2. Main Equipment RDS-PP Codes", section_style))
    story.append(Paragraph(
        "The following table lists all major plant components with their RDS-PP codes. "
        "These codes are used on all engineering drawings, nameplates, and maintenance records.",
        body_style
    ))
    page_w = A4[0] - 3 * cm
    t1_widths = [2.4*cm, 3.8*cm, 2.4*cm, 2.4*cm, 3.2*cm, 2.2*cm]
    story.append(build_table(EQUIPMENT_CODES, t1_widths))
    story.append(Spacer(1, 0.5 * cm))

    # ── Table 2 ──────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("3. Fault & Alarm Code Reference", section_style))
    story.append(Paragraph(
        "All alarms and protective trips generated by the Generator Control Panel (PP-GCP-001) are assigned "
        "a unique fault code. The table below defines each fault, its associated component, severity level, "
        "and the immediate action required by the operator or technician.",
        body_style
    ))
    t2_widths = [1.6*cm, 4.5*cm, 2.4*cm, 1.7*cm, 4.8*cm, 1.5*cm]
    story.append(build_table(FAULT_CODES, t2_widths, header_bg=colors.HexColor("#b71c1c")))
    story.append(Spacer(1, 0.5 * cm))

    # ── Table 3 ──────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("4. Preventive Maintenance Schedule", section_style))
    story.append(Paragraph(
        "Preventive maintenance tasks are assigned maintenance codes (M-xxx) and linked to the relevant "
        "RDS-PP component code. All tasks must be logged in the plant's Computerised Maintenance Management "
        "System (CMMS) using these codes. Intervals are based on manufacturer recommendations and IEC 60034 "
        "maintenance guidelines.",
        body_style
    ))
    t3_widths = [1.7*cm, 5.0*cm, 2.4*cm, 2.0*cm, 2.0*cm, 2.4*cm]
    story.append(build_table(MAINTENANCE_CODES, t3_widths, header_bg=colors.HexColor("#1b5e20")))
    story.append(Spacer(1, 0.5 * cm))

    # ── Table 4 ──────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("5. Operating Parameters & Protection Setpoints", section_style))
    story.append(Paragraph(
        "The following table defines the normal operating ranges and protection thresholds for each "
        "monitored parameter. Warning thresholds trigger an alarm in PP-GCP-001; trip thresholds cause "
        "an automatic protective shutdown to prevent equipment damage.",
        body_style
    ))
    t4_widths = [3.8*cm, 2.4*cm, 2.4*cm, 2.6*cm, 2.6*cm, 1.6*cm]
    story.append(build_table(OPERATING_PARAMS, t4_widths, header_bg=colors.HexColor("#4a148c")))
    story.append(Spacer(1, 0.5 * cm))

    # ── Notes ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("6. Notes & Revision History", section_style))
    notes_data = [
        ["Rev", "Date",       "Author",          "Description"],
        ["A",   "2024-01-15", "M. Garcia",       "Initial release – all tables for PP-REF-2024-001"],
        ["–",   "–",          "–",               "–"],
    ]
    story.append(build_table(notes_data, [1.5*cm, 2.5*cm, 4*cm, 8.5*cm]))
    story.append(Spacer(1, 0.8 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#90a4ae")))
    story.append(Paragraph(
        "© 2024 Plant Engineering Department. This document is for internal use only. "
        "Reproduction without authorisation is prohibited.",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7,
                       textColor=colors.HexColor("#90a4ae"), alignment=TA_CENTER)
    ))

    doc.build(story)
    print(f"✅ PDF generated: {output_path}")


if __name__ == "__main__":
    generate_pdf(OUTPUT_PATH)
