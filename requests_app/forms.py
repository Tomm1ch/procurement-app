from django import forms
from django.conf import settings
from django.forms import inlineformset_factory

from .models import OrderLine, ProcurementRequest


class PDFUploadForm(forms.Form):
    requestor_name = forms.CharField(max_length=200, required=False)
    email = forms.EmailField(required=False)
    document = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": "application/pdf,.pdf", "class": "file-input"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not user or not user.is_authenticated:
            self.fields["requestor_name"].required = True
            self.fields["email"].required = True

    def clean_document(self):
        document = self.cleaned_data["document"]
        if document.size > settings.MAX_UPLOAD_SIZE:
            raise forms.ValidationError("The PDF must be smaller than 10 MB.")
        if not document.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Please upload a PDF file.")
        signature = document.read(5)
        document.seek(0)
        if signature != b"%PDF-":
            raise forms.ValidationError("This file does not appear to be a valid PDF.")
        return document


class ProcurementRequestForm(forms.ModelForm):
    class Meta:
        model = ProcurementRequest
        fields = ("requestor_name", "department", "title", "vendor_name", "vendor_vat_id", "offer_date", "currency", "total_cost", "commodity_group")
        widgets = {
            "offer_date": forms.DateInput(attrs={"type": "date"}),
            "total_cost": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "currency": forms.TextInput(attrs={"maxlength": "3"}),
        }

    def __init__(self, *args, commodity_editable=False, requestor_details_editable=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["commodity_group"].queryset = self.fields["commodity_group"].queryset.filter(active=True)
        self.fields["commodity_group"].disabled = not commodity_editable
        if not commodity_editable:
            self.fields["commodity_group"].help_text = "Automatically assigned from the uploaded quote. Procurement can correct it after submission."
        self.fields["requestor_name"].disabled = not requestor_details_editable
        self.fields["department"].disabled = not requestor_details_editable
        for field in self.fields.values():
            field.required = False
        department_choices = [("", "Select a department")] + list(ProcurementRequest.DEPARTMENT_CHOICES)
        current_department = self.instance.department if self.instance else ""
        if current_department and current_department not in dict(department_choices):
            department_choices.insert(1, (current_department, f"{current_department} (extracted)"))
        self.fields["department"].choices = department_choices


class ProcurementStatusForm(forms.Form):
    status = forms.ChoiceField(choices=((ProcurementRequest.Status.SUBMITTED, "Open"), (ProcurementRequest.Status.IN_PROGRESS, "In progress"), (ProcurementRequest.Status.CLOSED, "Closed")))
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional note"}))


OrderLineFormSet = inlineformset_factory(
    ProcurementRequest, OrderLine,
    fields=("description", "unit_price", "quantity", "unit", "total_price"), extra=0, can_delete=True,
    widgets={
        "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
        "total_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    },
)
