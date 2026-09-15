from django.contrib import admin
from .models import (
    AssignmentArea, AssignmentCell, CompensatoryLeaveRecord,
    CompensatoryLeaveRule, DailyAreaActivation, DailyBoard, Staff,
)


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = (
        "name", "display_order", "is_active",
        "duty_rotation_enabled", "duty_rotation_order",
        "auto_assignment_enabled",
    )
    list_editable = (
        "display_order", "is_active",
        "duty_rotation_enabled", "duty_rotation_order",
        "auto_assignment_enabled",
    )
    search_fields = ("name",)
    list_filter = ("is_active", "duty_rotation_enabled", "auto_assignment_enabled")


@admin.register(AssignmentArea)
class AssignmentAreaAdmin(admin.ModelAdmin):
    list_display = (
        "category", "name", "slot_count", "auto_assignment_count",
        "auto_assignment_mode", "display_order", "is_active",
    )
    list_editable = (
        "slot_count", "auto_assignment_count", "auto_assignment_mode",
        "display_order", "is_active",
    )
    list_filter = ("category", "auto_assignment_mode", "is_active")
    search_fields = ("category", "name")


class AssignmentCellInline(admin.TabularInline):
    model = AssignmentCell
    extra = 0


@admin.register(DailyBoard)
class DailyBoardAdmin(admin.ModelAdmin):
    list_display = (
        "board_date", "night_shift", "night_shift_after",
        "holiday_day_shift", "updated_at",
    )
    date_hierarchy = "board_date"
    inlines = (AssignmentCellInline,)


@admin.register(CompensatoryLeaveRule)
class CompensatoryLeaveRuleAdmin(admin.ModelAdmin):
    list_display = (
        "day_type", "duty_type", "offset_mode", "weeks_after",
        "target_weekday", "days_after", "is_active",
    )
    list_editable = (
        "offset_mode", "weeks_after", "target_weekday", "days_after", "is_active",
    )


@admin.register(CompensatoryLeaveRecord)
class CompensatoryLeaveRecordAdmin(admin.ModelAdmin):
    list_display = ("source_date", "duty_type", "staff_name", "target_date")
    date_hierarchy = "target_date"
    search_fields = ("staff_name",)


@admin.register(DailyAreaActivation)
class DailyAreaActivationAdmin(admin.ModelAdmin):
    list_display = ("board", "area", "is_enabled")
    list_filter = ("is_enabled", "area")

from .models import TimeSlot, AreaTimeRule, DerivedAssignmentRule, LunchBreakEntry

@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "kind", "display_order", "is_active", "is_lunch_highlight")
    list_editable = ("display_order", "is_active", "is_lunch_highlight")

@admin.register(AreaTimeRule)
class AreaTimeRuleAdmin(admin.ModelAdmin):
    list_display = ("area", "time_slot", "mode")
    list_filter = ("mode", "time_slot")

@admin.register(DerivedAssignmentRule)
class DerivedAssignmentRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "source_area", "time_slot", "action", "target_area", "is_active")
    list_editable = ("is_active",)

@admin.register(LunchBreakEntry)
class LunchBreakEntryAdmin(admin.ModelAdmin):
    list_display = ("board", "time_slot", "value")
