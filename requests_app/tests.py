import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import CommodityGroup, OrderLine, ProcurementRequest, StatusHistory


TEST_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RequestWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = User.objects.create_user("employee", "employee@example.com", "testpass", first_name="Eva", last_name="Example")
        cls.other = User.objects.create_user("other", "other@example.com", "testpass")
        cls.procurement = User.objects.create_user("procurement", "procurement@example.com", "testpass")
        group = Group.objects.create(name="Procurement")
        cls.procurement.groups.add(group)
        cls.commodity = CommodityGroup.objects.create(id="031", category="Information Technology", name="Software")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def create_request(self, user=None, status=ProcurementRequest.Status.DRAFT):
        request = ProcurementRequest.objects.create(
            requestor=user or self.employee, requestor_name="Eva Example", department="Engineering",
            title="Developer tools", vendor_name="Software GmbH", total_cost=Decimal("100.00"),
            commodity_group=self.commodity, status=status,
            document=SimpleUploadedFile("quote.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"),
            original_filename="quote.pdf",
        )
        OrderLine.objects.create(request=request, description="Annual licence", unit_price=Decimal("100.00"), quantity=1, unit="license", total_price=Decimal("100.00"))
        return request

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("requests_app:my_requests"))
        self.assertRedirects(response, "/accounts/login/?next=/requests/")

    def test_employee_only_sees_own_requests(self):
        own = self.create_request()
        self.create_request(user=self.other)
        self.client.force_login(self.employee)
        response = self.client.get(reverse("requests_app:my_requests"))
        self.assertContains(response, own.request_number)
        self.assertEqual(response.context["requests"].count(), 1)

    def test_employee_cannot_read_another_users_document(self):
        another = self.create_request(user=self.other)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse("requests_app:document", args=[another.pk])).status_code, 404)

    def test_non_procurement_user_cannot_open_queue(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse("requests_app:procurement_list")).status_code, 403)

    def test_procurement_queue_hides_drafts(self):
        self.create_request(status=ProcurementRequest.Status.DRAFT)
        submitted = self.create_request(status=ProcurementRequest.Status.SUBMITTED)
        self.client.force_login(self.procurement)
        response = self.client.get(reverse("requests_app:procurement_list"))
        self.assertContains(response, submitted.request_number)
        self.assertEqual(response.context["requests"].count(), 1)

    def test_procurement_status_change_is_recorded_and_emailed(self):
        request = self.create_request(status=ProcurementRequest.Status.SUBMITTED)
        self.client.force_login(self.procurement)
        response = self.client.post(reverse("requests_app:procurement_detail", args=[request.pk]), {"status": "IN_PROGRESS", "comment": "Review started"})
        self.assertRedirects(response, reverse("requests_app:procurement_detail", args=[request.pk]))
        request.refresh_from_db()
        self.assertEqual(request.status, ProcurementRequest.Status.IN_PROGRESS)
        self.assertTrue(StatusHistory.objects.filter(request=request, old_status="SUBMITTED", new_status="IN_PROGRESS").exists())
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(OPENAI_API_KEY="")
    def test_valid_pdf_upload_is_saved_when_extraction_is_unavailable(self):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("requests_app:upload"), {"document": SimpleUploadedFile("offer.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf")})
        created = ProcurementRequest.objects.get()
        self.assertRedirects(response, reverse("requests_app:edit", args=[created.pk]))
        self.assertEqual(created.extraction_status, ProcurementRequest.ExtractionStatus.FAILED)
        self.assertEqual(len(mail.outbox), 1)

    def test_non_pdf_upload_is_rejected(self):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("requests_app:upload"), {"document": SimpleUploadedFile("offer.pdf", b"not a pdf", content_type="application/pdf")})
        self.assertContains(response, "does not appear to be a valid PDF")
        self.assertFalse(ProcurementRequest.objects.exists())

    def test_valid_draft_can_be_submitted_and_creates_history_and_emails(self):
        request = self.create_request()
        request.vendor_vat_id = "DE123456789"
        request.save()
        line = request.order_lines.get()
        self.client.force_login(self.employee)
        response = self.client.post(reverse("requests_app:edit", args=[request.pk]), {
            "requestor_name": request.requestor_name, "department": request.department,
            "title": request.title, "vendor_name": request.vendor_name, "vendor_vat_id": request.vendor_vat_id,
            "offer_date": "", "currency": "EUR", "total_cost": "100.00", "commodity_group": self.commodity.pk,
            "order_lines-TOTAL_FORMS": "1", "order_lines-INITIAL_FORMS": "1",
            "order_lines-MIN_NUM_FORMS": "0", "order_lines-MAX_NUM_FORMS": "1000",
            "order_lines-0-id": str(line.pk), "order_lines-0-description": line.description,
            "order_lines-0-unit_price": "100.00", "order_lines-0-quantity": "1",
            "order_lines-0-unit": "license", "order_lines-0-total_price": "100.00",
            "action": "submit",
        })
        self.assertRedirects(response, reverse("requests_app:detail", args=[request.pk]))
        request.refresh_from_db()
        self.assertEqual(request.status, ProcurementRequest.Status.SUBMITTED)
        self.assertTrue(StatusHistory.objects.filter(request=request, new_status="SUBMITTED").exists())
        self.assertEqual(len(mail.outbox), 2)
