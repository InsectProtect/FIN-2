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

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_REGISTERED = False

MONTH_RU_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                   "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def _ensure_font():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(_FONTS_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")))
    _FONT_REGISTERED = True


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def build_pdf(year: int, revenue: dict, expenses: dict, by_category: dict) -> bytes:
    """
    revenue: {month(1-12): {"cash":..,"invoice":..,"total":..}}
    expenses: {month(1-12): total_float}
    by_category: {category_name: total_float}
    """
    _ensure_font()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleRu", parent=styles["Title"], fontName="DejaVuSans-Bold")
    normal_style = ParagraphStyle("NormalRu", parent=styles["Normal"], fontName="DejaVuSans", fontSize=9)
    h2_style = ParagraphStyle("H2Ru", parent=styles["Heading2"], fontName="DejaVuSans-Bold", fontSize=13)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=15 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
    )
    story = []

    story.append(Paragraph("Финансовый отчёт", title_style))
    story.append(Paragraph(
        f"{year} год · сформирован {datetime.date.today().strftime('%d.%m.%Y')}",
        normal_style,
    ))
    story.append(Spacer(1, 14))

    empty = {"cash": 0.0, "invoice": 0.0, "total": 0.0}
    header = ["Месяц", "Наличные", "По счёту", "Выручка", "Расходы", "Прибыль"]
    data = [header]

    total_cash = total_inv = total_rev = total_exp = total_profit = 0.0
    for m in range(1, 13):
        rv = revenue.get(m, empty)
        exp = expenses.get(m, 0.0)
        profit = rv["total"] - exp
        data.append([
            MONTH_RU_SHORT[m - 1],
            _fmt(rv["cash"]), _fmt(rv["invoice"]), _fmt(rv["total"]),
            _fmt(exp), _fmt(profit),
        ])
        total_cash += rv["cash"]
        total_inv += rv["invoice"]
        total_rev += rv["total"]
        total_exp += exp
        total_profit += profit

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
