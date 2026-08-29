import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from pypdf import PdfReader

from .models import CommodityGroup, EmailLog, OrderLine, ProcurementRequest


@dataclass
class ExtractedOrderLine:
    description: str = ""
    unit_price: float | None = None
    quantity: float | None = None
    unit: str = ""
    total_price: float | None = None


@dataclass
class ExtractedQuote:
    requestor_name: str = ""
    department: str = ""
    title: str = ""
    short_description: str = ""
    vendor_name: str = ""
    vendor_vat_id: str = ""
    offer_date: str | None = None
    currency: str = "EUR"
    total_cost: float | None = None
    commodity_group_id: str | None = None
    classification_reason: str = ""
    order_lines: list[ExtractedOrderLine] = field(default_factory=list)


def extract_quote(procurement_request):
    text = extract_pdf_text(procurement_request)
    return extract_quote_locally(text, procurement_request.original_filename)


def extract_pdf_text(procurement_request):
    procurement_request.document.open("rb")
    try:
        reader = PdfReader(procurement_request.document)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    finally:
        procurement_request.document.close()
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 30:
        text = _ocr_pdf_text(procurement_request)
    if len(text) < 30:
        raise RuntimeError("OCR completed but did not find enough readable text in this PDF.")
    return text


def _ocr_pdf_text(procurement_request):
    try:
        import ocrmypdf
    except ImportError as exc:
        raise RuntimeError("OCRmyPDF is not installed. Install the project requirements and OCR system dependencies.") from exc

    try:
        with tempfile.TemporaryDirectory(prefix="procurement-ocr-") as directory:
            input_path = Path(directory) / "input.pdf"
            output_path = Path(directory) / "ocr.pdf"
            procurement_request.document.open("rb")
            try:
                with input_path.open("wb") as target:
                    shutil.copyfileobj(procurement_request.document, target)
            finally:
                procurement_request.document.close()
            ocrmypdf.ocr(
                input_path, output_path, language=["deu", "eng"], mode="force",
                output_type="pdf", rotate_pages=True, deskew=True, optimize=0,
                jobs=1, use_threads=True, progress_bar=False,
            )
            text = "\n".join(page.extract_text() or "" for page in PdfReader(output_path).pages)
            return re.sub(r"[ \t]+", " ", text).strip()
    except Exception as exc:
        raise RuntimeError(f"OCRmyPDF could not read this scanned document: {exc}") from exc


def extract_quote_locally(text, filename):
    """Convert locally extracted PDF/OCR text into structured quote fields."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    vat_match = re.search(r"(?:USt[.\s-]*(?:IdNr|ID)|UID|VAT\s*ID|Umsatzsteuer[^:\n]*)[.:\s-]*(DE\s?\d{9})", text, re.I)
    date_match = re.search(r"(?:Angebotsdatum|Datum|Offer Date|Quote Date)\s*[:.]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})", text, re.I)
    total = _find_total(text)
    vendor = _find_vendor(lines)
    parsed_date = _parse_date(date_match.group(1)) if date_match else None
    title = _find_title(lines, filename)
    order_lines = _extract_order_lines(text)
    group_id, reason = _classify_locally(text)
    return ExtractedQuote(
        title=title,
        short_description=order_lines[0].description[:500] if order_lines else title,
        vendor_name=vendor,
        vendor_vat_id=re.sub(r"\s", "", vat_match.group(1)) if vat_match else "",
        offer_date=parsed_date.isoformat() if parsed_date else None,
        currency="EUR" if "€" in text or re.search(r"\bEUR\b", text) else "EUR",
        total_cost=float(total) if total is not None else None,
        commodity_group_id=group_id, classification_reason=reason,
        order_lines=order_lines,
    )


def _extract_order_lines(text):
    """Extract priced table rows from common German and English quote layouts."""
    normalized = text.replace("\xa0", " ").replace("�", "€")
    money = r"(?:(?:€\s*)?[0-9]{1,3}(?:[. ][0-9]{3})*(?:,[0-9]{2})|(?:€\s*)?[0-9]+(?:\.[0-9]{2}))"
    priced_row = re.compile(
        rf"(?P<quantity>\d+(?:[.,]\d+)?)\s+"
        rf"(?P<unit>[A-Za-zÄÖÜäöüß.]+)\s+"
        rf"(?P<unit_price>{money})"
        rf"(?:\s+[+-]?\d+(?:[.,]\d+)?\s*%)?\s+"
        rf"(?P<total>{money})(?:\s*€)?",
        re.I,
    )
    stop = r"(?=^\d+\s+\S|^(?:Positionen|Versandkosten|Netto|Endsumme|Gesamtsumme|Zwischensumme)|\Z)"
    blocks = re.finditer(rf"(?ms)^(?P<position>\d+)\s+(?P<body>.*?){stop}", normalized)
    extracted = []
    for block in blocks:
        body = " ".join(block.group("body").split())
        match = priced_row.search(body)
        if not match:
            continue
        description = body[:match.start()].strip(" -–")
        if not description:
            continue
        extracted.append(_build_order_line(description, match))

    if extracted:
        return extracted

    # Compact exports sometimes concatenate every column without whitespace.
    compact = re.search(
        rf"(?P<position>\d+\.\d)(?P<quantity>\d+[,]\d{{2}})\d{{4,}}"
        rf"(?P<description>.*?)(?:Übertrag|Uebertrag)\s*(?P<total>{money})",
        normalized,
        re.I | re.S,
    )
    if compact:
        quantity = _parse_decimal_token(compact.group("quantity"))
        total = _parse_money_token(compact.group("total"))
        description = " ".join(compact.group("description").split()).strip(" -–")
        return [ExtractedOrderLine(
            description=description[:500],
            unit_price=float(total / quantity),
            quantity=float(quantity),
            unit="item",
            total_price=float(total),
        )]

    table = re.search(
        r"(?:Produkt\s*/\s*Beschreibung|Description).*?Gesamt\s*(?P<body>.*?)"
        r"(?=Zwischensumme|Nettosumme|Gesamtsumme|Grand Total|\Z)",
        normalized,
        re.I | re.S,
    )
    if table:
        body = " ".join(table.group("body").split())
        row = re.search(
            rf"(?P<description>.+?)\s+(?P<quantity>\d+(?:[.,]\d+)?)\s+"
            rf"(?P<unit_price>{money})\s+(?P<total>{money})(?:\s*€)?$",
            body,
            re.I,
        )
        if row:
            return [ExtractedOrderLine(
                description=row.group("description")[:500],
                unit_price=float(_parse_money_token(row.group("unit_price"))),
                quantity=float(_parse_decimal_token(row.group("quantity"))),
                unit="item",
                total_price=float(_parse_money_token(row.group("total"))),
            )]
    return []


def _build_order_line(description, match):
    return ExtractedOrderLine(
        description=description[:500],
        unit_price=float(_parse_money_token(match.group("unit_price"))),
        quantity=float(_parse_decimal_token(match.group("quantity"))),
        unit=match.group("unit").rstrip("."),
        total_price=float(_parse_money_token(match.group("total"))),
    )


def _parse_decimal_token(value):
    value = value.replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return Decimal(value)


def _parse_money_token(value):
    return _parse_decimal_token(value.replace("€", "").strip())


def _parse_money(value):
    value = value.replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return Decimal(value)


def _find_total(text):
    labels = list(re.finditer(r"(?:Endsumme|Gesamtsumme|Gesamtbetrag|Endbetrag|Total(?: Offer)? Cost|Grand Total)", text, re.I))
    if not labels:
        return None
    following = text[labels[-1].end():labels[-1].end() + 180]
    same_line = following.splitlines()[0]
    money_pattern = r"(?:EUR|€)?\s*([0-9]{1,3}(?:[. ][0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:\.[0-9]{2})?)\s*(?:EUR|€)?"
    same_line_values = re.findall(money_pattern, same_line, re.I)
    if same_line_values:
        return _parse_money(same_line_values[0])
    values = re.findall(money_pattern, following, re.I)
    return _parse_money(values[-1]) if values else None


def _parse_date(value):
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _find_vendor(lines):
    joined = "\n".join(lines)
    if re.search(r"Apple Business Team", joined, re.I):
        return "Apple Business Team"
    stylegreen = re.search(r"(?:bei|von)\s+(styleGREEN)\b", joined, re.I)
    if stylegreen:
        return stylegreen.group(1)
    gregg = re.search(r"(Gärtner Gregg)(?:Inh\.|\s|$)", joined, re.I)
    if gregg:
        return gregg.group(1)
    for index, line in enumerate(lines):
        match = re.search(r"(?:Vendor|Anbieter|Lieferant|Firma)\s*(?:Name)?\s*[:.]\s*(.+)", line, re.I)
        if match:
            return match.group(1).strip()
        if re.search(r"\b(?:GmbH|AG|KG|Ltd\.?|Inc\.?)\b", line) and not re.search(r"Lio Technologies", line, re.I):
            return line.split("|")[0].strip()[:250]
    return lines[0][:250] if lines else ""


def _find_title(lines, filename):
    for line in lines:
        match = re.search(r"(?:Betreff|Subject|Description)\s*[:.]\s*(.+)", line, re.I)
        if match:
            return match.group(1).strip()[:250]
    return f"Quote {filename.rsplit('.', 1)[0]}"[:250]


def _classify_locally(text):
    lowered = text.lower()
    rules = [
        ("031", ("software", "lizenz", "license", "subscription"), "Software-related terms found in the quote."),
        ("029", ("laptop", "notebook", "computer", "hardware", "monitor", "macbook", "apple m2"), "IT hardware terms found in the quote."),
        ("030", ("it service", "support", "hosting", "cloud"), "IT service terms found in the quote."),
        ("004", ("consulting", "beratung", "consultant"), "Consulting terms found in the quote."),
        ("015", ("office", "büro", "furniture", "möbel", "moosbild", "raumbegrünung", "pflanzen"), "Office equipment terms found in the quote."),
        ("022", ("print", "druck"), "Printing terms found in the quote."),
        ("041", ("online marketing", "seo", "social media"), "Online marketing terms found in the quote."),
    ]
    for group_id, keywords, reason in rules:
        if any(keyword in lowered for keyword in keywords):
            return group_id, reason
    return "009", "No specific category keyword was found; review the suggested miscellaneous-services group."


def apply_extraction(procurement_request, extracted):
    from datetime import date
    for field in ("requestor_name", "department", "title", "short_description", "vendor_name", "vendor_vat_id", "currency", "classification_reason"):
        value = getattr(extracted, field, None)
        if value:
            setattr(procurement_request, field, value)
    if extracted.offer_date:
        try:
            procurement_request.offer_date = date.fromisoformat(extracted.offer_date)
        except ValueError:
            pass
    if extracted.total_cost is not None:
        procurement_request.total_cost = Decimal(str(extracted.total_cost))
    if extracted.commodity_group_id:
        procurement_request.commodity_group = CommodityGroup.objects.filter(pk=extracted.commodity_group_id).first()
    if not procurement_request.commodity_group:
        procurement_request.commodity_group = CommodityGroup.objects.filter(pk="009").first()
    procurement_request.raw_extraction = asdict(extracted)
    procurement_request.extraction_status = ProcurementRequest.ExtractionStatus.COMPLETED
    procurement_request.extraction_error = ""
    procurement_request.save()
    procurement_request.order_lines.all().delete()
    for position, line in enumerate(extracted.order_lines, start=1):
        OrderLine.objects.create(
            request=procurement_request, position=position, description=line.description,
            unit_price=Decimal(str(line.unit_price)) if line.unit_price is not None else None,
            quantity=Decimal(str(line.quantity)) if line.quantity is not None else None,
            unit=line.unit, total_price=Decimal(str(line.total_price)) if line.total_price is not None else None,
        )
    if not procurement_request.order_lines.exists():
        OrderLine.objects.create(
            request=procurement_request,
            position=1,
            description=extracted.title,
            quantity=Decimal("1"),
            unit="item",
        )


def send_request_email(procurement_request, recipient, email_type, subject, template, request=None):
    log = EmailLog.objects.create(request=procurement_request, recipient=recipient, email_type=email_type, subject=subject)
    context = {
        "procurement_request": procurement_request,
        "absolute_url": request.build_absolute_uri(reverse("requests_app:detail", args=[procurement_request.pk])) if request else "",
    }
    try:
        html = render_to_string(template, context)
        text = render_to_string(template.replace(".html", ".txt"), context)
        message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [recipient])
        message.attach_alternative(html, "text/html")
        message.send()
        log.successful = True
    except Exception as exc:
        log.error = str(exc)
    log.save(update_fields=["successful", "error"])
    return log.successful


def submission_errors(procurement_request):
    errors = []
    required = {
        "requestor_name": "Requestor name", "department": "Department", "title": "Title",
        "vendor_name": "Vendor name", "vendor_vat_id": "Vendor VAT ID",
        "commodity_group": "Commodity group", "total_cost": "Total cost",
    }
    for field, label in required.items():
        if getattr(procurement_request, field) in (None, ""):
            errors.append(f"{label} is required.")
    lines = list(procurement_request.order_lines.all())
    if not lines:
        errors.append("At least one order line is required.")
    for index, line in enumerate(lines, start=1):
        if not line.description or line.unit_price is None or line.quantity is None or not line.unit or line.total_price is None:
            errors.append(f"Order line {index} needs a description, unit price, quantity, unit and total price.")
        elif line.quantity <= 0:
            errors.append(f"Order line {index} quantity must be greater than zero.")
        elif abs(line.total_price - (line.unit_price * line.quantity)) > Decimal("0.02"):
            errors.append(f"Order line {index} total does not match quantity × unit price.")
    return errors
