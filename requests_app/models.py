import uuid
from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models


def request_document_path(instance, filename):
    return f"requests/{instance.id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class CommodityGroup(models.Model):
    id = models.CharField(primary_key=True, max_length=3)
    category = models.CharField(max_length=100)
    name = models.CharField(max_length=150)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.id} · {self.name}"


class ProcurementRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        CLOSED = "CLOSED", "Closed"

    class ExtractionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_number = models.CharField(max_length=24, unique=True, blank=True)
    requestor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="procurement_requests")
    guest_email = models.EmailField(blank=True)
    requestor_name = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=150, blank=True)
    title = models.CharField(max_length=250, blank=True)
    vendor_name = models.CharField(max_length=250, blank=True)
    vendor_vat_id = models.CharField(max_length=40, blank=True)
    offer_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="EUR")
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    commodity_group = models.ForeignKey(CommodityGroup, null=True, blank=True, on_delete=models.PROTECT)
    classification_reason = models.TextField(blank=True)
    document = models.FileField(upload_to=request_document_path, validators=[FileExtensionValidator(["pdf"])])
    original_filename = models.CharField(max_length=255)
    extraction_status = models.CharField(max_length=20, choices=ExtractionStatus.choices, default=ExtractionStatus.PENDING)
    extraction_error = models.TextField(blank=True)
    raw_extraction = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.request_number:
            self.request_number = f"PR-{str(self.id).split('-')[0].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.request_number} — {self.title or self.original_filename}"


class OrderLine(models.Model):
    request = models.ForeignKey(ProcurementRequest, on_delete=models.CASCADE, related_name="order_lines")
    position = models.PositiveIntegerField(default=1)
    description = models.CharField(max_length=500, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=40, blank=True)
    total_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.position}. {self.description}"


class StatusHistory(models.Model):
    request = models.ForeignKey(ProcurementRequest, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField(max_length=20, choices=ProcurementRequest.Status.choices, blank=True)
    new_status = models.CharField(max_length=20, choices=ProcurementRequest.Status.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class EmailLog(models.Model):
    request = models.ForeignKey(ProcurementRequest, on_delete=models.CASCADE, related_name="email_logs")
    recipient = models.EmailField()
    email_type = models.CharField(max_length=30)
    subject = models.CharField(max_length=250)
    successful = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
