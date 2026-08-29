from django.contrib import admin

from .models import CommodityGroup, EmailLog, OrderLine, ProcurementRequest, StatusHistory


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "comment", "created_at")
    can_delete = False


@admin.register(ProcurementRequest)
class ProcurementRequestAdmin(admin.ModelAdmin):
    list_display = ("request_number", "title", "vendor_name", "requestor", "status", "created_at")
    list_filter = ("status", "extraction_status", "department", "commodity_group")
    search_fields = ("request_number", "title", "vendor_name", "requestor__email")
    inlines = (OrderLineInline, StatusHistoryInline)


@admin.register(CommodityGroup)
class CommodityGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "category", "name", "active")
    list_filter = ("category", "active")
    search_fields = ("id", "name")


admin.site.register(EmailLog)
