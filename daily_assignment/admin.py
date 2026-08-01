from django.contrib import admin
from .models import AssignmentArea, AssignmentCell, DailyBoard, Staff


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("name", "display_order", "is_active")
    list_editable = ("display_order", "is_active")
    search_fields = ("name",)


@admin.register(AssignmentArea)
class AssignmentAreaAdmin(admin.ModelAdmin):
    list_display = ("category", "name", "slot_count", "display_order", "is_active")
    list_editable = ("slot_count", "display_order", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("category", "name")


class AssignmentCellInline(admin.TabularInline):
    model = AssignmentCell
    extra = 0


@admin.register(DailyBoard)
class DailyBoardAdmin(admin.ModelAdmin):
    list_display = ("board_date", "updated_at")
    date_hierarchy = "board_date"
    inlines = (AssignmentCellInline,)
