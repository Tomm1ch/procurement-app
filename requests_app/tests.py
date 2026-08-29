import shutil
import tempfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import CommodityGroup, OrderLine, ProcurementRequest, StatusHistory
from .services import extract_pdf_text, extract_quote_locally


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

    def test_guest_home_redirects_to_login(self):
        response = self.client.get(reverse("requests_app:home"))
        self.assertRedirects(response, reverse("login"))

    def test_guest_can_upload_and_access_own_draft(self):
        response = self.client.post(reverse("requests_app:upload"), {
            "requestor_name": "Guest User", "email": "guest@example.com",
            "document": SimpleUploadedFile("offer.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"),
        })
        created = ProcurementRequest.objects.get()
        self.assertIsNone(created.requestor)
        self.assertEqual(created.guest_email, "guest@example.com")
        self.assertRedirects(response, reverse("requests_app:edit", args=[created.pk]))
        self.assertEqual(self.client.get(reverse("requests_app:edit", args=[created.pk])).status_code, 200)

    def test_guest_cannot_access_another_sessions_request(self):
        request = self.create_request(user=None)
        request.requestor = None
        request.guest_email = "guest@example.com"
        request.save()
        self.assertEqual(self.client.get(reverse("requests_app:edit", args=[request.pk])).status_code, 404)

    def test_edit_page_creates_real_initial_order_line_for_empty_draft(self):
        request = self.create_request()
        request.order_lines.all().delete()
        self.client.force_login(self.employee)
        response = self.client.get(reverse("requests_app:edit", args=[request.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.order_lines.count(), 1)
        self.assertEqual(response.context["formset"].initial_form_count(), 1)

    def test_guest_status_history_renders_without_a_user(self):
        request = self.create_request(status=ProcurementRequest.Status.SUBMITTED)
        request.requestor = None
        request.guest_email = "guest@example.com"
        request.save()
        StatusHistory.objects.create(
            request=request, old_status=ProcurementRequest.Status.DRAFT,
            new_status=ProcurementRequest.Status.SUBMITTED, changed_by=None,
        )
        session = self.client.session
        session["guest_request_ids"] = [str(request.pk)]
        session.save()
        response = self.client.get(reverse("requests_app:detail", args=[request.pk]))
        self.assertContains(response, "Guest requestor")

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

    def test_employee_cannot_edit_request_as_procurement(self):
        request = self.create_request(status=ProcurementRequest.Status.SUBMITTED)
        self.client.force_login(self.employee)
        response = self.client.get(reverse("requests_app:procurement_edit", args=[request.pk]))
        self.assertEqual(response.status_code, 403)

    def test_procurement_can_update_totals_and_add_order_line(self):
        request = self.create_request(status=ProcurementRequest.Status.SUBMITTED)
        request.vendor_vat_id = "DE123456789"
        request.save()
        line = request.order_lines.get()
        self.client.force_login(self.procurement)
        response = self.client.post(reverse("requests_app:procurement_edit", args=[request.pk]), {
            "requestor_name": "Tampered Name", "department": "Finance",
            "title": request.title, "vendor_name": request.vendor_name,
            "vendor_vat_id": request.vendor_vat_id, "offer_date": "", "currency": "EUR",
            "total_cost": "150.00", "commodity_group": self.commodity.pk,
            "order_lines-TOTAL_FORMS": "2", "order_lines-INITIAL_FORMS": "1",
            "order_lines-MIN_NUM_FORMS": "0", "order_lines-MAX_NUM_FORMS": "1000",
            "order_lines-0-id": str(line.pk), "order_lines-0-description": line.description,
            "order_lines-0-unit_price": "100.00", "order_lines-0-quantity": "1",
            "order_lines-0-unit": "license", "order_lines-0-total_price": "100.00",
            "order_lines-1-id": "", "order_lines-1-description": "Setup service",
            "order_lines-1-unit_price": "50.00", "order_lines-1-quantity": "1",
            "order_lines-1-unit": "service", "order_lines-1-total_price": "50.00",
        })
        self.assertRedirects(response, reverse("requests_app:procurement_detail", args=[request.pk]))
        request.refresh_from_db()
        self.assertEqual(request.total_cost, Decimal("150.00"))
        self.assertEqual(request.order_lines.count(), 2)
        self.assertEqual(request.requestor_name, "Eva Example")
        self.assertEqual(request.department, "Engineering")
        self.assertTrue(request.status_history.filter(comment="Request details updated by procurement").exists())

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

    def test_local_parser_extracts_german_quote_fields(self):
        extracted = extract_quote_locally(
            "Dream in Green GmbH\nAngebot 4120\nDatum: 28.11.23\n"
            "Moosbild 1,00 Stk. 715,26 €\nUSt.-ID: DE325240530\nEndsumme 1.847,19 €",
            "AN-4120.pdf",
        )
        self.assertEqual(extracted.vendor_name, "Dream in Green GmbH")
        self.assertEqual(extracted.vendor_vat_id, "DE325240530")
        self.assertEqual(extracted.offer_date, "2023-11-28")
        self.assertEqual(extracted.total_cost, 1847.19)
        self.assertEqual(extracted.commodity_group_id, "015")

    def test_local_parser_extracts_multiple_lines_and_keeps_document_total(self):
        extracted = extract_quote_locally(
            "Vendor GmbH\nPos. Bezeichnung Menge Einheit Preis Gesamt\n"
            "1 Moss panel 1,28 qm 559,00 715,52\n"
            "2 Edge greenery 4,80 Lfm 25,13 120,62\n"
            "3 White logo 1,00 Stk. 350,00 350,00\n"
            "Endsumme 1.546,99 EUR",
            "quote.pdf",
        )
        self.assertEqual(len(extracted.order_lines), 3)
        self.assertEqual(extracted.order_lines[0].description, "Moss panel")
        self.assertEqual(extracted.order_lines[1].quantity, 4.8)
        self.assertEqual(extracted.total_cost, 1546.99)
        self.assertEqual(extracted.short_description, "Moss panel")

    def test_local_parser_includes_explicit_alternative_quote_line(self):
        extracted = extract_quote_locally(
            "Vendor GmbH\nPos. Bezeichnung Menge Einheit Preis Gesamt\n"
            "1 Moss panel 1,00 Stk. 715,26 715,26\n"
            "2 Logo horizontal 1,00 Stk. 622,00 622,00\n"
            "3 Alternativ: Logo vertical 1,00 Stk. 430,00 430,00\n"
            "Endsumme 1.847,19 EUR",
            "quote.pdf",
        )
        self.assertEqual([line.total_price for line in extracted.order_lines], [715.26, 622.0, 430.0])
        self.assertEqual(extracted.total_cost, 1847.19)

    def test_request_form_uses_common_department_dropdown(self):
        from .forms import ProcurementRequestForm

        form = ProcurementRequestForm(instance=self.create_request())
        self.assertEqual(form.fields["department"].widget.input_type, "select")
        self.assertIn(("Human Resources", "Human Resources"), form.fields["department"].choices)
        self.assertTrue(form.fields["commodity_group"].disabled)
        self.assertIn("short_description", form.fields)
        self.assertTrue(form.fields["total_cost"].widget.attrs["readonly"])

    def test_procurement_form_can_edit_commodity_group(self):
        from .forms import ProcurementRequestForm

        form = ProcurementRequestForm(
            instance=self.create_request(),
            commodity_editable=True,
            requestor_details_editable=False,
        )
        self.assertFalse(form.fields["commodity_group"].disabled)
        self.assertTrue(form.fields["requestor_name"].disabled)
        self.assertTrue(form.fields["department"].disabled)

    @patch("requests_app.services._ocr_pdf_text", return_value="Recognized scanned document text with enough characters.")
    @patch("requests_app.services.PdfReader")
    def test_image_only_pdf_uses_ocrmypdf_fallback(self, reader, ocr_fallback):
        page = MagicMock()
        page.extract_text.return_value = ""
        reader.return_value.pages = [page]
        document = MagicMock()
        request = MagicMock(document=document)
        self.assertIn("Recognized scanned", extract_pdf_text(request))
        ocr_fallback.assert_called_once_with(request)
