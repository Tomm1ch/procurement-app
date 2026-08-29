from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import OrderLineFormSet, PDFUploadForm, ProcurementRequestForm, ProcurementStatusForm
from .models import OrderLine, ProcurementRequest, StatusHistory
from .permissions import is_procurement, procurement_required
from .services import apply_extraction, extract_quote, send_request_email, submission_errors


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect("requests_app:procurement_list" if is_procurement(request.user) else "requests_app:my_requests")


@login_required
def my_requests(request):
    requests = ProcurementRequest.objects.filter(requestor=request.user).select_related("commodity_group")
    return render(request, "requests_app/my_requests.html", {"requests": requests})


def upload_request(request):
    form = PDFUploadForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        document = form.cleaned_data["document"]
        procurement_request = ProcurementRequest.objects.create(
            requestor=request.user if request.user.is_authenticated else None,
            requestor_name=(request.user.get_full_name() or request.user.username) if request.user.is_authenticated else form.cleaned_data["requestor_name"],
            guest_email="" if request.user.is_authenticated else form.cleaned_data["email"],
            document=document, original_filename=document.name,
            extraction_status=ProcurementRequest.ExtractionStatus.PROCESSING,
        )
        try:
            apply_extraction(procurement_request, extract_quote(procurement_request))
            messages.success(request, "Your PDF was uploaded and the fields were extracted. Please review them before submitting.")
        except Exception as exc:
            procurement_request.extraction_status = ProcurementRequest.ExtractionStatus.FAILED
            procurement_request.extraction_error = str(exc)
            procurement_request.save(update_fields=["extraction_status", "extraction_error", "updated_at"])
            messages.warning(request, "The PDF was saved, but automatic extraction was unavailable. Please complete the fields manually.")
        if not request.user.is_authenticated:
            guest_requests = request.session.setdefault("guest_request_ids", [])
            guest_requests.append(str(procurement_request.pk))
            request.session.modified = True
        recipient = request.user.email if request.user.is_authenticated else procurement_request.guest_email
        if recipient:
            send_request_email(procurement_request, recipient, "UPLOAD", f"Upload received: {procurement_request.request_number}", "emails/upload_received.html", request)
        return redirect("requests_app:edit", pk=procurement_request.pk)
    return render(request, "requests_app/submit.html", {"form": form})


def edit_request(request, pk):
    procurement_request = get_object_or_404(ProcurementRequest, pk=pk)
    if not can_access_request(request, procurement_request):
        raise Http404
    if procurement_request.status != ProcurementRequest.Status.DRAFT:
        messages.info(request, "Submitted requests can no longer be edited.")
        return redirect("requests_app:detail", pk=pk)
    if request.method == "GET" and not procurement_request.order_lines.exists():
        OrderLine.objects.create(
            request=procurement_request,
            position=1,
            description=procurement_request.title,
            quantity=1,
            unit="item",
        )
    form = ProcurementRequestForm(request.POST or None, instance=procurement_request)
    formset = OrderLineFormSet(request.POST or None, instance=procurement_request)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save()
            for position, line in enumerate(procurement_request.order_lines.all(), start=1):
                if line.position != position:
                    line.position = position
                    line.save(update_fields=["position"])
        if request.POST.get("action") == "submit":
            errors = submission_errors(procurement_request)
            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                procurement_request.status = ProcurementRequest.Status.SUBMITTED
                procurement_request.submitted_at = timezone.now()
                procurement_request.save(update_fields=["status", "submitted_at", "updated_at"])
                StatusHistory.objects.create(request=procurement_request, old_status=ProcurementRequest.Status.DRAFT, new_status=ProcurementRequest.Status.SUBMITTED, changed_by=request.user if request.user.is_authenticated else None, comment="Submitted to procurement")
                recipient = request.user.email if request.user.is_authenticated else procurement_request.guest_email
                if recipient:
                    send_request_email(procurement_request, recipient, "SUBMISSION", f"Request submitted: {procurement_request.request_number}", "emails/request_submitted.html", request)
                send_request_email(procurement_request, settings.PROCUREMENT_EMAIL, "PROCUREMENT_NOTIFICATION", f"New procurement request: {procurement_request.request_number}", "emails/request_submitted.html", request)
                messages.success(request, "Your request was submitted to procurement.")
                return redirect("requests_app:detail", pk=pk)
        else:
            messages.success(request, "Draft saved.")
            return redirect("requests_app:edit", pk=pk)
    return render(request, "requests_app/edit_request.html", {"form": form, "formset": formset, "procurement_request": procurement_request})


def request_detail(request, pk):
    queryset = ProcurementRequest.objects.select_related("requestor", "commodity_group").prefetch_related("order_lines", "status_history__changed_by")
    procurement_request = get_object_or_404(queryset, pk=pk)
    if not can_access_request(request, procurement_request) and not is_procurement(request.user):
        raise Http404
    return render(request, "requests_app/request_detail.html", {"procurement_request": procurement_request})


def request_document(request, pk):
    procurement_request = get_object_or_404(ProcurementRequest, pk=pk)
    if not can_access_request(request, procurement_request) and not is_procurement(request.user):
        raise Http404
    procurement_request.document.open("rb")
    return FileResponse(procurement_request.document, content_type="application/pdf", filename=procurement_request.original_filename)


@procurement_required
def procurement_list(request):
    requests = ProcurementRequest.objects.exclude(status=ProcurementRequest.Status.DRAFT).select_related("requestor", "commodity_group")
    query, status = request.GET.get("q", "").strip(), request.GET.get("status", "").strip()
    if query:
        requests = requests.filter(Q(request_number__icontains=query) | Q(title__icontains=query) | Q(vendor_name__icontains=query) | Q(requestor_name__icontains=query))
    if status:
        requests = requests.filter(status=status)
    counts = {
        "open": ProcurementRequest.objects.filter(status=ProcurementRequest.Status.SUBMITTED).count(),
        "progress": ProcurementRequest.objects.filter(status=ProcurementRequest.Status.IN_PROGRESS).count(),
        "closed": ProcurementRequest.objects.filter(status=ProcurementRequest.Status.CLOSED).count(),
    }
    return render(request, "requests_app/procurement_list.html", {"requests": requests, "counts": counts, "query": query, "selected_status": status, "status_choices": ProcurementRequest.Status.choices[1:]})


@procurement_required
def procurement_detail(request, pk):
    procurement_request = get_object_or_404(ProcurementRequest.objects.exclude(status=ProcurementRequest.Status.DRAFT).select_related("requestor", "commodity_group").prefetch_related("order_lines", "status_history__changed_by"), pk=pk)
    form = ProcurementStatusForm(request.POST or None, initial={"status": procurement_request.status})
    if request.method == "POST" and form.is_valid():
        old_status, new_status = procurement_request.status, form.cleaned_data["status"]
        if old_status != new_status:
            procurement_request.status = new_status
            procurement_request.save(update_fields=["status", "updated_at"])
            StatusHistory.objects.create(request=procurement_request, old_status=old_status, new_status=new_status, changed_by=request.user, comment=form.cleaned_data["comment"])
            recipient = procurement_request.requestor.email if procurement_request.requestor else procurement_request.guest_email
            if recipient:
                send_request_email(procurement_request, recipient, "STATUS_CHANGE", f"Status updated: {procurement_request.request_number}", "emails/status_changed.html", request)
            messages.success(request, "Status updated and the requestor was notified.")
        return redirect("requests_app:procurement_detail", pk=pk)
    return render(request, "requests_app/procurement_detail.html", {"procurement_request": procurement_request, "status_form": form})


@procurement_required
def procurement_edit(request, pk):
    procurement_request = get_object_or_404(
        ProcurementRequest.objects.exclude(status=ProcurementRequest.Status.DRAFT), pk=pk,
    )
    if request.method == "GET" and not procurement_request.order_lines.exists():
        OrderLine.objects.create(request=procurement_request, position=1, quantity=1, unit="item")
    form = ProcurementRequestForm(request.POST or None, instance=procurement_request)
    formset = OrderLineFormSet(request.POST or None, instance=procurement_request)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        errors = []
        with transaction.atomic():
            form.save()
            formset.save()
            for position, line in enumerate(procurement_request.order_lines.all(), start=1):
                if line.position != position:
                    line.position = position
                    line.save(update_fields=["position"])
            errors = submission_errors(procurement_request)
            if errors:
                transaction.set_rollback(True)
            else:
                StatusHistory.objects.create(
                    request=procurement_request,
                    old_status=procurement_request.status,
                    new_status=procurement_request.status,
                    changed_by=request.user,
                    comment="Request details updated by procurement",
                )
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            messages.success(request, "Request details updated.")
            return redirect("requests_app:procurement_detail", pk=pk)
    return render(request, "requests_app/edit_request.html", {
        "form": form,
        "formset": formset,
        "procurement_request": procurement_request,
        "procurement_mode": True,
    })


def can_access_request(request, procurement_request):
    if request.user.is_authenticated and procurement_request.requestor_id == request.user.id:
        return True
    return str(procurement_request.pk) in request.session.get("guest_request_ids", [])
