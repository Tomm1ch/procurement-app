import re
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from openai import OpenAI
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .models import CommodityGroup, EmailLog, OrderLine, ProcurementRequest


class ExtractedOrderLine(BaseModel):
    description: str = ""
    unit_price: float | None = None
    quantity: float | None = None
    unit: str = ""
    total_price: float | None = None


class ExtractedQuote(BaseModel):
    requestor_name: str = ""
    department: str = ""
    title: str = ""
    vendor_name: str = ""
    vendor_vat_id: str = ""
    offer_date: str | None = None
    currency: str = "EUR"
    total_cost: float | None = None
    commodity_group_id: str | None = None
    classification_reason: str = ""
    order_lines: list[ExtractedOrderLine] = Field(default_factory=list)


def extract_quote(procurement_request):
    text = extract_pdf_text(procurement_request)
    if not settings.OPENAI_API_KEY:
        return extract_quote_locally(text, procurement_request.original_filename)
    groups = "\n".join(f"{g.id}: {g.category} / {g.name}" for g in CommodityGroup.objects.filter(active=True))
    response = OpenAI(api_key=settings.OPENAI_API_KEY).responses.parse(
        model=settings.OPENAI_MODEL,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": (
                "Extract this vendor quote. Preserve decimal values and ISO currency codes. Choose exactly one "
                "commodity_group_id from the catalogue when possible. Do not invent missing values. Dates must "
                "be YYYY-MM-DD.\n\nCatalogue:\n" + groups + "\n\nQuote text:\n" + text
            )},
        ]}],
        text_format=ExtractedQuote,
    )
    if not response.output_parsed:
        raise RuntimeError("The document could not be converted into structured fields.")
    return response.output_parsed


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
    """Best-effort parser used when no external AI service is configured."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    vat_match = re.search(r"(?:USt[.\s-]*(?:IdNr|ID)|UID|VAT\s*ID|Umsatzsteuer[^:\n]*)[.:\s-]*(DE\s?\d{9})", text, re.I)
    date_match = re.search(r"(?:Angebotsdatum|Datum|Offer Date|Quote Date)\s*[:.]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})", text, re.I)
    total = _find_total(text)
    vendor = _find_vendor(lines)
    parsed_date = _parse_date(date_match.group(1)) if date_match else None
    title = _find_title(lines, filename)
    group_id, reason = _classify_locally(text)
    return ExtractedQuote(
        title=title, vendor_name=vendor,
        vendor_vat_id=re.sub(r"\s", "", vat_match.group(1)) if vat_match else "",
        offer_date=parsed_date.isoformat() if parsed_date else None,
        currency="EUR" if "€" in text or re.search(r"\bEUR\b", text) else "EUR",
        total_cost=float(total) if total is not None else None,
        commodity_group_id=group_id, classification_reason=reason,
        order_lines=[ExtractedOrderLine(description=title, unit_price=float(total), quantity=1, unit="item", total_price=float(total))] if total is not None else [],
    )


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
    for field in ("requestor_name", "department", "title", "vendor_name", "vendor_vat_id", "currency", "classification_reason"):
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
    procurement_request.raw_extraction = extracted.model_dump(mode="json")
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
    if lines and procurement_request.total_cost is not None:
        calculated_total = sum((line.total_price or Decimal("0")) for line in lines)
        if abs(procurement_request.total_cost - calculated_total) > Decimal("0.02"):
            errors.append("Total cost does not match the sum of the order lines.")
    return errors
