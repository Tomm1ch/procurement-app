import base64
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from openai import OpenAI
from pydantic import BaseModel, Field

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
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured. You can still enter all fields manually.")
    groups = "\n".join(f"{g.id}: {g.category} / {g.name}" for g in CommodityGroup.objects.filter(active=True))
    procurement_request.document.open("rb")
    encoded = base64.b64encode(procurement_request.document.read()).decode("ascii")
    procurement_request.document.close()
    response = OpenAI(api_key=settings.OPENAI_API_KEY).responses.parse(
        model=settings.OPENAI_MODEL,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": (
                "Extract this vendor quote. Preserve decimal values and ISO currency codes. Choose exactly one "
                "commodity_group_id from the catalogue when possible. Do not invent missing values. Dates must "
                "be YYYY-MM-DD.\n\nCatalogue:\n" + groups
            )},
            {"type": "input_file", "filename": procurement_request.original_filename, "file_data": f"data:application/pdf;base64,{encoded}"},
        ]}],
        text_format=ExtractedQuote,
    )
    if not response.output_parsed:
        raise RuntimeError("The document could not be converted into structured fields.")
    return response.output_parsed


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
