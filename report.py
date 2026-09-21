"""
Сборка PDF-отчёта (выручка/расходы по месяцам) для кнопки «Скачать отчёт»
в мини-приложении. Шрифт DejaVuSans нужен, потому что встроенные шрифты
reportlab не умеют в кириллицу — файлы шрифта лежат в папке fonts/.
"""
import io
import os
import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_REGISTERED = False

MONTH_RU_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                   "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
MONTH_RU_FULL = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# Те же цвета, что и в круглом дашборде "По видам вредителей" на главном
# экране мини-приложения (webapp/index.html, --pest-*), чтобы отчёт выглядел
# согласованно с приложением.
PEST_COLORS = {
    "cockroach": "#f27b21",
    "bedbug": "#0071e3",
    "flying": "#248a3d",
    "rodent": "#af52de",
    "other": "#86868b",
}


def _ensure_font():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(_FONTS_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")))
    _FONT_REGISTERED = True


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def _kpi_dashboard(values_labels: list, styles: dict) -> Table:
    """Строка "плиток"-KPI (как на главном экране приложения) — большое
    число сверху, подпись снизу. values_labels: [(value_str, label_str), ...]"""
    value_style = styles["kpi_value"]
    label_style = styles["kpi_label"]
    row_values = [Paragraph(v, value_style) for v, _ in values_labels]
    row_labels = [Paragraph(l, label_style) for _, l in values_labels]
    col_w = (170 * mm) / len(values_labels)
    table = Table([row_values, row_labels], colWidths=[col_w] * len(values_labels))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f7")),
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, colors.HexColor("#d2d2d7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _pest_donut(pest_list: list, styles: dict):
    """Круговая диаграмма по видам вредителей (проценты по количеству
    вызовов) — тот же принцип и цвета, что и в приложении на главном
    экране. Возвращает None, если считать нечего."""
    items = [i for i in (pest_list or []) if i.get("count")]
    total = sum(i["count"] for i in items)
    if not items or not total:
        return None

    drawing = Drawing(170 * mm, 45 * mm)
    pie = Pie()
    pie.x = 10
    pie.y = 5
    pie.width = 40 * mm
    pie.height = 40 * mm
    pie.data = [i["count"] for i in items]
    pie.labels = None
    pie.sideLabels = False
    pie.simpleLabels = False
    pie.slices.strokeWidth = 1
    pie.slices.strokeColor = colors.white
    for idx, item in enumerate(items):
        pie.slices[idx].fillColor = colors.HexColor(PEST_COLORS.get(item["key"], PEST_COLORS["other"]))
    drawing.add(pie)

    legend = Legend()
    legend.x = 65 * mm
    legend.y = 40 * mm
    legend.dx = 8
    legend.dy = 8
    legend.dxTextSpace = 6
    legend.deltay = 14
    legend.alignment = "right"
    legend.columnMaximum = len(items)
    legend.fontName = "DejaVuSans"
    legend.fontSize = 9
    legend.colorNamePairs = [
        (colors.HexColor(PEST_COLORS.get(item["key"], PEST_COLORS["other"])),
         f'{item["label"]} — {item["count"]} выз. ({round(item["count"] / total * 100)}%)')
        for item in items
    ]
    drawing.add(legend)
    return drawing


def build_pdf(year: int, revenue: dict, expenses: dict, by_category: dict,
              month: int | None = None, pest_list: list | None = None,
              total_requests: int = 0) -> bytes:
    """
    revenue: {month(1-12): {"cash":..,"invoice":..,"total":..}}
    expenses: {month(1-12): total_float}
    by_category: {category_name: total_float} — расходы по категориям за
        выбранный период (весь год, либо только выбранный месяц, если
        month задан — фильтрация делается на уровне вызова из app.py).
    month: None — отчёт за весь год; 1-12 — отчёт только за этот месяц.
    pest_list: [{"key":.., "label":.., "count":.., "total":..}, ...] — то
        же, что показывает круглый дашборд "По видам вредителей" в
        приложении, за тот же период.
    total_requests: суммарное количество вызовов за период (для плитки
        "Заявок итого").
    """
    _ensure_font()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleRu", parent=styles["Title"], fontName="DejaVuSans-Bold")
    normal_style = ParagraphStyle("NormalRu", parent=styles["Normal"], fontName="DejaVuSans", fontSize=9)
    h2_style = ParagraphStyle("H2Ru", parent=styles["Heading2"], fontName="DejaVuSans-Bold", fontSize=13)
    kpi_styles = {
        "kpi_value": ParagraphStyle("KpiValue", parent=styles["Normal"], fontName="DejaVuSans-Bold",
                                     fontSize=14, leading=16, textColor=colors.HexColor("#1d1d1f")),
        "kpi_label": ParagraphStyle("KpiLabel", parent=styles["Normal"], fontName="DejaVuSans",
                                     fontSize=8, textColor=colors.HexColor("#6e6e73")),
    }

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=15 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
    )
    story = []

    period_label = f"{MONTH_RU_FULL[month - 1]} {year}" if month else f"{year} год"
    story.append(Paragraph("Финансовый отчёт", title_style))
    story.append(Paragraph(
        f"{period_label} · сформирован {datetime.date.today().strftime('%d.%m.%Y')}",
        normal_style,
    ))
    story.append(Spacer(1, 14))

    empty = {"cash": 0.0, "invoice": 0.0, "total": 0.0}
    months_range = [month] if month else range(1, 13)

    total_cash = total_inv = total_rev = total_exp = total_profit = 0.0
    for m in months_range:
        rv = revenue.get(m, empty)
        exp = expenses.get(m, 0.0)
        total_cash += rv["cash"]
        total_inv += rv["invoice"]
        total_rev += rv["total"]
        total_exp += exp
        total_profit += rv["total"] - exp

    # Дашборд-плитки (как на главном экране приложения): выручка, расходы,
    # прибыль, количество заявок итого.
    dashboard = _kpi_dashboard([
        (_fmt(total_rev), "Общая выручка, MDL"),
        (_fmt(total_exp), "Расходы, MDL"),
        (_fmt(total_profit), "Прибыль, MDL"),
        (str(total_requests), "Заявок итого"),
    ], kpi_styles)
    story.append(dashboard)
    story.append(Spacer(1, 18))

    # Круглый дашборд по видам вредителей — тот же, что в приложении.
    donut = _pest_donut(pest_list, kpi_styles)
    if donut:
        story.append(Paragraph("По видам вредителей", h2_style))
        story.append(Spacer(1, 6))
        story.append(donut)
        story.append(Spacer(1, 12))

    header = ["Месяц", "Наличные", "По счёту", "Выручка", "Расходы", "Прибыль"]
    data = [header]
    for m in months_range:
        rv = revenue.get(m, empty)
        exp = expenses.get(m, 0.0)
        profit = rv["total"] - exp
        data.append([
            MONTH_RU_SHORT[m - 1],
            _fmt(rv["cash"]), _fmt(rv["invoice"]), _fmt(rv["total"]),
            _fmt(exp), _fmt(profit),
        ])

    if not month:
        data.append(["Итого", _fmt(total_cash), _fmt(total_inv), _fmt(total_rev),
                     _fmt(total_exp), _fmt(total_profit)])

    table = Table(data, colWidths=[22 * mm, 27 * mm, 27 * mm, 27 * mm, 27 * mm, 27 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "DejaVuSans-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d1d1f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f5f5f7")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d2d2d7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fafafa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 20))

    if by_category:
        story.append(Paragraph("Расходы по категориям", h2_style))
        story.append(Spacer(1, 8))
        cat_data = [["Категория", "Сумма, MDL"]]
        for cat, amount in sorted(by_category.items(), key=lambda kv: -kv[1]):
            if amount:
                cat_data.append([cat or "—", _fmt(amount)])
        cat_table = Table(cat_data, colWidths=[110 * mm, 40 * mm])
        cat_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
            ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d1d1f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d2d2d7")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(cat_table)

    doc.build(story)
    return buf.getvalue()
