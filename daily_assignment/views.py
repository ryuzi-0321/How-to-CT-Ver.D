import json
import random
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.test import RequestFactory
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    import jpholiday as _jpholiday
    _JPHOLIDAY_IMPORT_ERROR = ""
except Exception as exc:
    _jpholiday = None
    _JPHOLIDAY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

from .forms import DailyBoardMetaForm
from .models import (
    AssignmentArea, AssignmentCell, CompensatoryLeaveRecord,
    CompensatoryLeaveRule, DailyAreaActivation, DailyBoard, DutyCalendarDay, DutyCalendarDayHistory, Staff, StaffFreeHistory,
    StaffAvoidArea, StaffWeeklyTarget, WeeklyAbsence, DedicatedAreaBackup, MeetingPreset, ReceptionStaff, MainRotationOverride,
    DutyRotationVersion, DutyRotationVersionMember, DailyCommentTemplate, DailyCommentTemplateValue,
)

ROW_DEFINITIONS = [
    ("0730", "7:30"), ("0830", "8:30"), ("0900", "9:00"),
    ("1000", "10:00"), ("1100", "11:00"), ("1200", "12:00"),
    ("1300", "13:00"), ("1400", "14:00"), ("1500", "15:00"),
    ("1600", "16:00"), ("main", "主担当"), ("sub", "Sub"),
]
ROW_KEYS = {key for key, _ in ROW_DEFINITIONS}


def _parse_date(raw):
    if not raw:
        return timezone.localdate()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise Http404("日付の形式が正しくありません") from exc


def _board_context(board_date):
    board, _ = DailyBoard.objects.get_or_create(board_date=board_date)
    areas = list(AssignmentArea.objects.filter(is_active=True))
    cells = {
        (cell.area_id, cell.row_key, cell.slot_index): cell.value
        for cell in board.cells.select_related("area").all()
    }
    area_groups = []
    current_category = None
    group = None
    for area in areas:
        if area.category != current_category:
            group = {"category": area.category or "その他", "areas": []}
            area_groups.append(group)
            current_category = area.category
        area_rows = []
        for row_key, row_label in ROW_DEFINITIONS:
            slots = [
                {"index": i, "value": cells.get((area.id, row_key, i), "")}
                for i in range(1, area.slot_count + 1)
            ]
            area_rows.append({"key": row_key, "label": row_label, "slots": slots})
        group["areas"].append({"area": area, "rows": area_rows})
    return board, area_groups


@require_GET
def board_view(request):
    board_date = _parse_date(request.GET.get("date"))
    board, area_groups = _board_context(board_date)
    # 月間勤務カレンダー・週間不在・主担当を表示時にも自動同期する。
    _sync_calendar_to_board(board)
    _sync_absence_text_to_board(board)
    _sync_main_row(board)
    board.refresh_from_db()
    form = DailyBoardMetaForm(instance=board)
    meta_fields = (
        "conference",
        "night_shift",
        "night_shift_after",
        "holiday_day_shift",
        "compensatory_leave_1",
        "compensatory_leave_2",
        "compensatory_leave_3",
        "annual_leave_1",
        "annual_leave_2",
        "staffing_am",
        "staffing_pm",
        "free_text",
        "comment",
        "unassigned",
        "reception_leave",
    )

    has_cell_data = board.cells.exclude(value="").exists()

    has_meta_data = any(
        str(getattr(board, field, "") or "").strip()
        for field in meta_fields
    )

    has_existing_data = has_cell_data or has_meta_data

    scheduled_areas = list(
        AssignmentArea.objects.filter(
            is_active=True, auto_assignment_mode="scheduled"
        ).order_by("display_order", "id")
    )
    activation_map = {
        item.area_id: item.is_enabled
        for item in board.area_activations.filter(area__in=scheduled_areas)
    }
    scheduled_area_settings = [
        {"area": area, "enabled": activation_map.get(area.id, False)}
        for area in scheduled_areas
    ]

    return render(request, "daily_assignment/board.html", {
        "board": board,
        "board_date": board_date,
        "previous_date": board_date - timedelta(days=1),
        "next_date": board_date + timedelta(days=1),
        "today": timezone.localdate(),
        "area_groups": area_groups,
        "row_definitions": ROW_DEFINITIONS,
        "staff_names_json": json.dumps(list(Staff.objects.filter(is_active=True).values_list("name", flat=True)), ensure_ascii=False),
        "form": form,
        "scheduled_area_settings": scheduled_area_settings,
        "staff_options": Staff.objects.filter(is_active=True).order_by("display_order", "name"),
        "quick_staff_options": Staff.objects.filter(is_active=True).exclude(name="").order_by("display_order", "name"),
        "staff_work_hours": {st.name: {"start": st.work_start_time.strftime("%H:%M") if st.work_start_time else "", "end": st.work_end_time.strftime("%H:%M") if st.work_end_time else ""} for st in Staff.objects.filter(is_active=True)},
        "quick_time_slots": TimeSlot.objects.filter(is_active=True, kind="time").order_by("display_order", "id"),
        "meeting_presets": MeetingPreset.objects.filter(is_active=True).order_by("display_order", "name"),
        "quick_reception_options": ReceptionStaff.objects.filter(is_active=True).order_by("display_order", "name"),
    })


@require_POST
@transaction.atomic
def save_board(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "送信データが正しくありません。"}, status=400)

    board_date = _parse_date(payload.get("date"))
    board, _ = DailyBoard.objects.select_for_update().get_or_create(board_date=board_date)

    client_updated_at = payload.get("updated_at")
    if client_updated_at and board.updated_at:
        try:
            client_dt = datetime.fromisoformat(client_updated_at.replace("Z", "+00:00"))
            if timezone.is_naive(client_dt):
                client_dt = timezone.make_aware(client_dt)
            if abs((board.updated_at - client_dt).total_seconds()) > 1:
                return JsonResponse({
                    "ok": False,
                    "conflict": True,
                    "error": "他の人が先に更新しました。画面を再読み込みして確認してください。",
                }, status=409)
        except ValueError:
            pass

    # v3.10.6.71:
    # 手動セル編集・メタ欄編集もUndo/Redo対象にする。
    # 実際に保存を書き込む直前の状態を1履歴として保持する。
    manual_undo_snapshot = _build_board_undo_snapshot(board, "手動保存")

    meta = payload.get("meta", {})
    allowed_meta = {field.name for field in DailyBoard._meta.fields} - {"id", "board_date", "updated_at"}
    for key, value in meta.items():
        if key in allowed_meta:
            setattr(board, key, str(value or "")[:5000])
    board.save()

    active_areas = {a.id: a for a in AssignmentArea.objects.filter(is_active=True)}
    incoming = payload.get("cells", [])
    valid_keys = set()
    for item in incoming:
        try:
            area_id = int(item["area_id"])
            row_key = str(item["row_key"])
            slot_index = int(item["slot_index"])
        except (KeyError, TypeError, ValueError):
            continue
        area = active_areas.get(area_id)
        if not area or row_key not in ROW_KEYS or not 1 <= slot_index <= area.slot_count:
            continue
        value = str(item.get("value", "")).strip()[:100]
        key = (area_id, row_key, slot_index)
        valid_keys.add(key)
        AssignmentCell.objects.update_or_create(
            board=board, area=area, row_key=row_key, slot_index=slot_index,
            defaults={"value": value},
        )

    for cell in board.cells.all():
        key = (cell.area_id, cell.row_key, cell.slot_index)
        if key not in valid_keys:
            cell.delete()

    board.refresh_from_db()
    _clear_board_undo(request)
    return JsonResponse({"ok": True, "updated_at": board.updated_at.isoformat(), "message": "保存しました。"})

@require_POST
@transaction.atomic
def copy_board(request):
    target_date = _parse_date(request.POST.get("date"))
    source_date = _parse_date(request.POST.get("source_date"))

    source = DailyBoard.objects.filter(
        board_date=source_date
    ).first()

    if not source:
        if request.POST.get("check_only") == "1":
            return JsonResponse({
                "ok": False,
                "error": "コピー元の日付に配置表がありません。",
            }, status=404)

        messages.error(
            request,
            "コピー元の日付に配置表がありません。"
        )
        return redirect(
            f"{reverse('daily_assignment:board')}?date={target_date}"
        )

    target, _ = DailyBoard.objects.get_or_create(
        board_date=target_date
    )

    meta_fields = (
        "conference",
        "night_shift",
        "night_shift_after",
        "holiday_day_shift",
        "compensatory_leave_1",
        "compensatory_leave_2",
        "compensatory_leave_3",
        "annual_leave_1",
        "annual_leave_2",
        "staffing_am",
        "staffing_pm",
        "free_text",
        "comment",
        "unassigned",
        "reception_leave",
    )

    # 判定するのはコピー先 target
    target_has_cells = target.cells.exclude(
        value=""
    ).exists()

    target_has_meta = any(
        str(getattr(target, field, "") or "").strip()
        for field in meta_fields
    )

    target_has_existing_data = (
        target_has_cells or target_has_meta
    )

    # コピー先に既存データがあるかだけ確認する
    if request.POST.get("check_only") == "1":
        return JsonResponse({
            "ok": True,
            "has_existing_data": target_has_existing_data,
        })

    overwrite = request.POST.get("overwrite") == "1"

    if target_has_existing_data and not overwrite:
        messages.error(
            request,
            "コピー先には既にデータがあります。"
        )
        return redirect(
            f"{reverse('daily_assignment:board')}?date={target_date}"
        )

    target.cells.all().delete()

    for field in meta_fields:
        setattr(
            target,
            field,
            getattr(source, field, "")
        )

    target.save()

    AssignmentCell.objects.bulk_create([
        AssignmentCell(
            board=target,
            area=cell.area,
            row_key=cell.row_key,
            slot_index=cell.slot_index,
            value=cell.value,
        )
        for cell in source.cells.all()
    ])

    messages.success(
        request,
        f"{source_date:%Y/%m/%d} の内容をコピーしました。"
    )

    return redirect(
        f"{reverse('daily_assignment:board')}?date={target_date}"
    )

@require_POST
@transaction.atomic
def clear_board(request):
    board_date = _parse_date(request.POST.get("date"))
    board = DailyBoard.objects.filter(board_date=board_date).first()
    if board:
        board.cells.all().delete()
        for field in ("conference", "night_shift", "night_shift_after", "holiday_day_shift", "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3", "annual_leave_1", "annual_leave_2", "staffing_am", "staffing_pm", "free_text", "comment", "unassigned", "reception_leave"):
            setattr(board, field, "")
        board.save()
    messages.success(request, "配置表を空にしました。")
    return redirect(f"{reverse('daily_assignment:board')}?date={board_date}")


@require_GET
def export_excel(request):
    board_date = _parse_date(request.GET.get("date"))
    board, area_groups = _board_context(board_date)
    wb = Workbook()
    ws = wb.active
    ws.title = board_date.strftime("%Y-%m-%d")
    dark = PatternFill("solid", fgColor="17324D")
    light = PatternFill("solid", fgColor="EAF1F7")
    white_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="7890A5")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells("A1:F1")
    ws["A1"] = f"当日配置表　{board_date:%Y年%m月%d日}"
    ws["A1"].font = Font(size=16, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    row = 3
    ws.cell(row=row, column=1, value="時間")
    col = 2
    area_columns = []
    for group in area_groups:
        for item in group["areas"]:
            area = item["area"]
            start = col
            end = col + area.slot_count - 1
            if end > start:
                ws.merge_cells(start_row=row, start_column=start, end_row=row, end_column=end)
            ws.cell(row=row, column=start, value=f"{area.category} {area.name}".strip())
            area_columns.append((area, start, end))
            col = end + 1
    max_col = max(1, col - 1)
    for c in range(1, max_col + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = dark; cell.font = white_font; cell.border = border; cell.alignment = Alignment(horizontal="center")

    cell_map = {(c.area_id, c.row_key, c.slot_index): c.value for c in board.cells.all()}
    for row_key, row_label in ROW_DEFINITIONS:
        row += 1
        ws.cell(row=row, column=1, value=row_label)
        ws.cell(row=row, column=1).fill = dark
        ws.cell(row=row, column=1).font = white_font
        ws.cell(row=row, column=1).border = border
        for area, start, end in area_columns:
            for idx, c in enumerate(range(start, end + 1), start=1):
                cell = ws.cell(row=row, column=c, value=cell_map.get((area.id, row_key, idx), ""))
                cell.fill = light; cell.border = border; cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    row += 2
    meta_items = [
        ("会議等", board.conference), ("2交代勤務", board.two_shift),
        ("年休①", board.annual_leave_1), ("年休②", board.annual_leave_2),
        ("人数状況 午前", board.staffing_am), ("人数状況 午後", board.staffing_pm),
        ("Free", board.free_text), ("コメント", board.comment),
        ("配置未定", board.unassigned), ("受付（年休）", board.reception_leave),
    ]
    for label, value in meta_items:
        ws.cell(row=row, column=1, value=label).fill = dark
        ws.cell(row=row, column=1).font = white_font
        ws.cell(row=row, column=1).border = border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=max(2, max_col))
        out = ws.cell(row=row, column=2, value=value)
        out.border = border; out.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    ws.freeze_panes = "B4"
    ws.column_dimensions["A"].width = 14
    for c in range(2, max_col + 1):
        ws.column_dimensions[get_column_letter(c)].width = 13
    for r in range(4, 4 + len(ROW_DEFINITIONS)):
        ws.row_dimensions[r].height = 28
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    output = BytesIO()
    wb.save(output)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="assignment_{board_date:%Y%m%d}.xlsx"'
    return response

# =========================================================
# スタッフ・配置場所設定
# ログイン不要
# =========================================================

@require_GET
def settings_view(request):
    """
    スタッフと配置場所の設定画面を表示する。
    使用停止中のデータも設定画面には表示する。
    """
    staff_list = Staff.objects.all().order_by(
        "display_order",
        "name",
    )

    area_list = AssignmentArea.objects.all().order_by(
        "display_order",
        "id",
    )

    return render(
        request,
        "daily_assignment/settings.html",
        {
            "staff_list": staff_list,
            "area_list": area_list,
            "active_area_list": AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"),
            "comp_rules": CompensatoryLeaveRule.objects.all(),
            "weekday_choices": CompensatoryLeaveRule.WEEKDAY_CHOICES,
            "day_type_choices": CompensatoryLeaveRule.DAY_TYPE_CHOICES,
            "duty_type_choices": CompensatoryLeaveRule.DUTY_TYPE_CHOICES,
        },
    )


@require_POST
def staff_add(request):
    """
    スタッフを新規登録する。
    """
    name = request.POST.get("name", "").strip()
    display_order_raw = request.POST.get("display_order", "0").strip()
    is_active = request.POST.get("is_active") == "on"
    duty_rotation_enabled = request.POST.get("duty_rotation_enabled") == "on"
    duty_rotation_order_raw = request.POST.get("duty_rotation_order", "0").strip()
    work_start_raw = request.POST.get("work_start_time", "").strip()
    work_end_raw = request.POST.get("work_end_time", "").strip()

    if not name:
        messages.error(request, "スタッフ名を入力してください。")
        return redirect("daily_assignment:settings")

    try:
        display_order = int(display_order_raw or 0)
        if display_order < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "表示順は0以上の整数で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        duty_rotation_order = int(duty_rotation_order_raw or 0)
        if duty_rotation_order < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "勤務ローテーション順は0以上の整数で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        work_start_time = datetime.strptime(work_start_raw, "%H:%M").time() if work_start_raw else None
        work_end_time = datetime.strptime(work_end_raw, "%H:%M").time() if work_end_raw else None
        if work_start_time and work_end_time and work_start_time > work_end_time:
            raise ValueError
    except ValueError:
        messages.error(request, "勤務可能時間は開始≦終了になるように入力してください。")
        return redirect("daily_assignment:settings")

    if Staff.objects.filter(name=name).exists():
        messages.error(request, f"「{name}」は既に登録されています。")
        return redirect("daily_assignment:settings")

    Staff.objects.create(
        name=name,
        display_order=display_order,
        is_active=is_active,
        duty_rotation_enabled=duty_rotation_enabled,
        duty_rotation_order=duty_rotation_order,
        work_start_time=work_start_time,
        work_end_time=work_end_time,
    )

    messages.success(request, f"スタッフ「{name}」を追加しました。")
    return redirect("daily_assignment:settings")


@require_POST
def staff_update(request, staff_id):
    """
    登録済みスタッフを更新する。
    """
    staff = get_object_or_404(Staff, pk=staff_id)

    name = request.POST.get("name", "").strip()
    display_order_raw = request.POST.get("display_order", "0").strip()
    is_active = request.POST.get("is_active") == "on"
    duty_rotation_enabled = request.POST.get("duty_rotation_enabled") == "on"
    duty_rotation_order_raw = request.POST.get("duty_rotation_order", "0").strip()
    work_start_raw = request.POST.get("work_start_time", "").strip()
    work_end_raw = request.POST.get("work_end_time", "").strip()

    if not name:
        messages.error(request, "スタッフ名を入力してください。")
        return redirect("daily_assignment:settings")

    try:
        display_order = int(display_order_raw or 0)
        if display_order < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "表示順は0以上の整数で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        duty_rotation_order = int(duty_rotation_order_raw or 0)
        if duty_rotation_order < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "勤務ローテーション順は0以上の整数で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        work_start_time = datetime.strptime(work_start_raw, "%H:%M").time() if work_start_raw else None
        work_end_time = datetime.strptime(work_end_raw, "%H:%M").time() if work_end_raw else None
        if work_start_time and work_end_time and work_start_time > work_end_time:
            raise ValueError
    except ValueError:
        messages.error(request, "勤務可能時間は開始≦終了になるように入力してください。")
        return redirect("daily_assignment:settings")

    duplicate = Staff.objects.filter(name=name).exclude(pk=staff.pk)

    if duplicate.exists():
        messages.error(request, f"「{name}」は既に登録されています。")
        return redirect("daily_assignment:settings")

    staff.name = name
    staff.display_order = display_order
    staff.is_active = is_active
    staff.duty_rotation_enabled = duty_rotation_enabled
    staff.duty_rotation_order = duty_rotation_order
    staff.work_start_time = work_start_time
    staff.work_end_time = work_end_time
    staff.save()

    messages.success(request, f"スタッフ「{name}」を更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def staff_assignment_rule_update(request, staff_id):
    """通常配置の自動作成ルールをスタッフ単位で更新する。"""
    staff = get_object_or_404(Staff, pk=staff_id)

    def area_from_post(name):
        raw = request.POST.get(name, "").strip()
        if not raw:
            return None
        try:
            return AssignmentArea.objects.get(pk=int(raw))
        except (ValueError, AssignmentArea.DoesNotExist):
            return None

    try:
        weekly_target_count = int(request.POST.get("weekly_target_count", "0") or 0)
        if weekly_target_count < 0 or weekly_target_count > 7:
            raise ValueError
    except ValueError:
        messages.error(request, "週の目安回数は0〜7で入力してください。")
        return redirect("daily_assignment:settings")

    staff.auto_assignment_enabled = request.POST.get("auto_assignment_enabled") == "on"
    staff.preferred_area_1 = area_from_post("preferred_area_1")
    staff.preferred_area_2 = area_from_post("preferred_area_2")
    staff.preferred_area_3 = area_from_post("preferred_area_3")
    staff.avoid_area = area_from_post("avoid_area")
    staff.weekly_target_area = area_from_post("weekly_target_area")
    staff.weekly_target_count = weekly_target_count
    staff.save(update_fields=[
        "auto_assignment_enabled",
        "preferred_area_1", "preferred_area_2", "preferred_area_3",
        "avoid_area", "weekly_target_area", "weekly_target_count",
    ])

    messages.success(request, f"{staff.name}さんの自動配置ルールを更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def staff_delete(request, staff_id):
    """
    スタッフを削除する。

    配置表のセルにはスタッフとの外部キーがなく、
    氏名を文字列として保存しているため削除可能。
    過去の配置表に入力された氏名は残る。
    """
    staff = get_object_or_404(Staff, pk=staff_id)
    staff_name = staff.name
    staff.delete()

    messages.success(request, f"スタッフ「{staff_name}」を削除しました。")
    return redirect("daily_assignment:settings")


@require_POST
def area_add(request):
    """
    配置場所を新規登録する。
    """
    category = request.POST.get("category", "").strip()
    name = request.POST.get("name", "").strip()
    slot_count_raw = request.POST.get("slot_count", "1").strip()
    auto_assignment_count_raw = request.POST.get("auto_assignment_count", "1").strip()
    minimum_assignment_count_raw = request.POST.get("minimum_assignment_count", "0").strip()
    assignment_priority_raw = request.POST.get("assignment_priority", "3").strip()
    overlap_assignment_mode = request.POST.get("overlap_assignment_mode", "never").strip()
    auto_assignment_mode = request.POST.get("auto_assignment_mode", "daily").strip()
    display_order_raw = request.POST.get("display_order", "0").strip()
    is_active = request.POST.get("is_active") == "on"

    if not name:
        messages.error(request, "配置場所名を入力してください。")
        return redirect("daily_assignment:settings")

    try:
        slot_count = int(slot_count_raw or 1)
        if not 1 <= slot_count <= 10:
            raise ValueError
    except ValueError:
        messages.error(
            request,
            "入力列数は1〜10の整数で入力してください。",
        )
        return redirect("daily_assignment:settings")

    try:
        auto_assignment_count = int(auto_assignment_count_raw or 0)
        if not 0 <= auto_assignment_count <= slot_count:
            raise ValueError
    except ValueError:
        messages.error(request, "自動配置人数は0〜入力列数の範囲で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        minimum_assignment_count = int(minimum_assignment_count_raw or 0)
        if not 0 <= minimum_assignment_count <= auto_assignment_count:
            raise ValueError
    except ValueError:
        messages.error(request, "最低必要人数は0〜自動配置人数の範囲で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        assignment_priority = int(assignment_priority_raw or 3)
        if not 1 <= assignment_priority <= 5:
            raise ValueError
    except ValueError:
        messages.error(request, "配置優先度は1〜5で入力してください。")
        return redirect("daily_assignment:settings")

    valid_overlap_modes = {value for value, _ in AssignmentArea.OVERLAP_ASSIGNMENT_MODE_CHOICES}
    if overlap_assignment_mode not in valid_overlap_modes:
        messages.error(request, "重複配置の設定が正しくありません。")
        return redirect("daily_assignment:settings")

    valid_modes = {value for value, _ in AssignmentArea.AUTO_ASSIGNMENT_MODE_CHOICES}
    if auto_assignment_mode not in valid_modes:
        messages.error(request, "自動配置方式が正しくありません。")
        return redirect("daily_assignment:settings")

    try:
        display_order = int(display_order_raw or 0)
        if display_order < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "表示順は0以上の整数で入力してください。")
        return redirect("daily_assignment:settings")

    if AssignmentArea.objects.filter(
        category=category,
        name=name,
    ).exists():
        messages.error(
            request,
            f"「{category} / {name}」は既に登録されています。",
        )
        return redirect("daily_assignment:settings")

    AssignmentArea.objects.create(
        category=category,
        name=name,
        slot_count=slot_count,
        auto_assignment_count=auto_assignment_count,
        minimum_assignment_count=minimum_assignment_count,
        assignment_priority=assignment_priority,
        overlap_assignment_mode=overlap_assignment_mode,
        auto_assignment_mode=auto_assignment_mode,
        display_order=display_order,
        is_active=is_active,
    )

    messages.success(request, f"配置場所「{name}」を追加しました。")
    return redirect("daily_assignment:settings")


@require_POST
def area_update(request, area_id):
    """
    登録済みの配置場所を更新する。
    """
    area = get_object_or_404(AssignmentArea, pk=area_id)

    category = request.POST.get("category", "").strip()
    name = request.POST.get("name", "").strip()
    slot_count_raw = request.POST.get("slot_count", "1").strip()
    auto_assignment_count_raw = request.POST.get("auto_assignment_count", "1").strip()
    minimum_assignment_count_raw = request.POST.get("minimum_assignment_count", "0").strip()
    assignment_priority_raw = request.POST.get("assignment_priority", "3").strip()
    overlap_assignment_mode = request.POST.get("overlap_assignment_mode", area.overlap_assignment_mode).strip()
    auto_assignment_mode = request.POST.get("auto_assignment_mode", "daily").strip()
    display_order_raw = request.POST.get("display_order", "0").strip()
    is_active = request.POST.get("is_active") == "on"

    if not name:
        messages.error(request, "配置場所名を入力してください。")
        return redirect("daily_assignment:settings")

    try:
        slot_count = int(slot_count_raw or 1)
        if not 1 <= slot_count <= 10:
            raise ValueError
    except ValueError:
        messages.error(
            request,
            "入力列数は1〜10の整数で入力してください。",
        )
        return redirect("daily_assignment:settings")

    try:
        auto_assignment_count = int(auto_assignment_count_raw or 0)
        if not 0 <= auto_assignment_count <= slot_count:
            raise ValueError
    except ValueError:
        messages.error(request, "自動配置人数は0〜入力列数の範囲で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        minimum_assignment_count = int(minimum_assignment_count_raw or 0)
        if not 0 <= minimum_assignment_count <= auto_assignment_count:
            raise ValueError
    except ValueError:
        messages.error(request, "最低必要人数は0〜自動配置人数の範囲で入力してください。")
        return redirect("daily_assignment:settings")

    try:
        assignment_priority = int(assignment_priority_raw or 3)
        if not 1 <= assignment_priority <= 5:
            raise ValueError
    except ValueError:
        messages.error(request, "配置優先度は1〜5で入力してください。")
        return redirect("daily_assignment:settings")

    valid_overlap_modes = {value for value, _ in AssignmentArea.OVERLAP_ASSIGNMENT_MODE_CHOICES}
    if overlap_assignment_mode not in valid_overlap_modes:
        messages.error(request, "重複配置の設定が正しくありません。")
        return redirect("daily_assignment:settings")

    valid_modes = {value for value, _ in AssignmentArea.AUTO_ASSIGNMENT_MODE_CHOICES}
    if auto_assignment_mode not in valid_modes:
        messages.error(request, "自動配置方式が正しくありません。")
        return redirect("daily_assignment:settings")

    try:
        display_order = int(display_order_raw or 0)
        if display_order < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "表示順は0以上の整数で入力してください。")
        return redirect("daily_assignment:settings")

    duplicate = AssignmentArea.objects.filter(
        category=category,
        name=name,
    ).exclude(pk=area.pk)

    if duplicate.exists():
        messages.error(
            request,
            f"「{category} / {name}」は既に登録されています。",
        )
        return redirect("daily_assignment:settings")

    # 入力列数を減らす場合、範囲外になる既存セルを削除する
    if slot_count < area.slot_count:
        AssignmentCell.objects.filter(
            area=area,
            slot_index__gt=slot_count,
        ).delete()

    area.category = category
    area.name = name
    area.slot_count = slot_count
    area.auto_assignment_count = auto_assignment_count
    area.minimum_assignment_count = minimum_assignment_count
    area.assignment_priority = assignment_priority
    area.overlap_assignment_mode = overlap_assignment_mode
    area.auto_assignment_mode = auto_assignment_mode
    area.display_order = display_order
    area.is_active = is_active
    area.save()

    messages.success(request, f"配置場所「{name}」を更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def area_delete(request, area_id):
    """
    配置場所を削除する。

    過去の配置表で使われている場合は物理削除せず、
    使用停止に切り替える。
    """
    area = get_object_or_404(AssignmentArea, pk=area_id)
    area_name = str(area)

    if AssignmentCell.objects.filter(area=area).exists():
        area.is_active = False
        area.save(update_fields=["is_active"])

        messages.warning(
            request,
            (
                f"配置場所「{area_name}」は過去の配置表で使用されているため、"
                "削除せず使用停止にしました。"
            ),
        )
    else:
        area.delete()
        messages.success(
            request,
            f"配置場所「{area_name}」を削除しました。",
        )

    return redirect("daily_assignment:settings")


# =========================================================
# 夜勤・休日日勤・代休の自動入力
# =========================================================


def _jpholiday_status():
    """日本の祝日判定ライブラリの状態を返す。"""
    return _jpholiday, _jpholiday is not None


def _is_japanese_holiday(target_date):
    """日本の祝日を判定する。jpholiday未導入時はFalse。"""
    if _jpholiday is None:
        return False
    return bool(_jpholiday.is_holiday(target_date))


def _japanese_holiday_name(target_date):
    """祝日名。表示用。"""
    if _jpholiday is None:
        return ""
    return _jpholiday.is_holiday_name(target_date) or ""


def _natural_day_type(target_date):
    """カレンダー個別設定を使わない、元々の日付区分。祝日を土日より優先する。"""
    if _is_japanese_holiday(target_date):
        return "holiday"
    if target_date.weekday() == 5:
        return "saturday"
    if target_date.weekday() == 6:
        return "sunday"
    return None


def _effective_day_type(target_date, calendar_day=None):
    """
    休日判定を一か所に集約する。
    normal は休日を明示的に解除、holiday/closed は曜日に関係なく休日。
    auto/未登録は土・日・日本の祝日を自動判定する。
    """
    if calendar_day is None:
        calendar_day = DutyCalendarDay.objects.filter(duty_date=target_date).only("day_mode").first()
    if calendar_day:
        if calendar_day.day_mode == "normal":
            return None
        if calendar_day.day_mode in ("holiday", "closed"):
            return "holiday"
    return _natural_day_type(target_date)


def _day_type(target_date):
    """代休ルール等で使う勤務日の種類。共通の休日判定を使用する。"""
    return _effective_day_type(target_date)


def _calendar_is_holiday_duty(target_date, calendar_day=None):
    """休日日勤を必要とする日か。土日・祝日・手動休日・臨時休業を共通判定。"""
    return _effective_day_type(target_date, calendar_day) is not None


def _nearest_normal_workday(target_date, direction):
    """代休日が休日に当たった時の候補日。土日祝・手動休日・臨時休業を避ける。"""
    step = 1 if direction >= 0 else -1
    current = target_date + timedelta(days=step)
    for _ in range(31):
        if not _calendar_is_holiday_duty(current):
            return current
        current += timedelta(days=step)
    return target_date


def _specific_comp_force(calendar_day, duty_type):
    mapping = {
        "holiday_day": "force_holiday_day_compensatory_leave",
        "night": "force_night_compensatory_leave",
        "night_after": "force_night_after_compensatory_leave",
    }
    field = mapping.get(duty_type)
    return bool(calendar_day.force_compensatory_leave or (field and getattr(calendar_day, field, False)))


def _rotation_staff(target_date=None):
    """対象月時点の勤務ローテーション。月別版が無ければ従来のスタッフ設定を使用。"""
    target = target_date or timezone.localdate()
    month = target.replace(day=1)
    version = DutyRotationVersion.objects.filter(effective_month__lte=month).order_by("-effective_month").first()
    if version:
        member_ids = list(version.members.order_by("rotation_order", "staff__display_order", "staff__name").values_list("staff_id", flat=True))
        staff_map = Staff.objects.in_bulk(member_ids)
        return [staff_map[i] for i in member_ids if i in staff_map and staff_map[i].is_active]
    return list(Staff.objects.filter(is_active=True, duty_rotation_enabled=True).order_by("duty_rotation_order", "display_order", "name"))


def _staff_available_for_row(staff, row_key):
    """スタッフの通常勤務可能時間内ならTrue。特殊行は制限しない。"""
    if not row_key or not str(row_key).isdigit() or len(str(row_key)) != 4:
        return True
    key = str(row_key)
    try:
        minute = int(key[:2]) * 60 + int(key[2:])
    except ValueError:
        return True
    if staff.work_start_time:
        start = staff.work_start_time.hour * 60 + staff.work_start_time.minute
        if minute < start:
            return False
    if staff.work_end_time:
        end = staff.work_end_time.hour * 60 + staff.work_end_time.minute
        if minute > end:
            return False
    return True


def _calculate_comp_leave_date(source_date, rule):
    if rule.offset_mode == "days":
        return source_date + timedelta(days=max(1, rule.days_after))

    monday = source_date - timedelta(days=source_date.weekday())
    return monday + timedelta(
        weeks=max(0, rule.weeks_after),
        days=rule.target_weekday,
    )


def _add_name_to_comp_leave(board, staff_name):
    fields = (
        "compensatory_leave_1",
        "compensatory_leave_2",
        "compensatory_leave_3",
    )
    existing = [str(getattr(board, field, "") or "").strip() for field in fields]
    if staff_name in existing:
        return True

    for field in fields:
        if not str(getattr(board, field, "") or "").strip():
            setattr(board, field, staff_name)
            board.save(update_fields=[field, "updated_at"])
            return True
    return False


def _remove_name_from_comp_leave_board(target_date, staff_name):
    """他の代休予約が無ければ、既存DailyBoardの代休欄から名前を取り除く。"""
    if CompensatoryLeaveRecord.objects.filter(
        target_date=target_date, staff_name=staff_name
    ).exists():
        return
    board = DailyBoard.objects.filter(board_date=target_date).first()
    if not board:
        return
    changed = []
    for field in ("compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3"):
        if str(getattr(board, field, "") or "").strip() == staff_name:
            setattr(board, field, "")
            changed.append(field)
    if changed:
        board.save(update_fields=changed + ["updated_at"])


def _clear_comp_records_for_source(source_date):
    """勤務日の再作成前に、その勤務日由来の代休予約を安全に消す。"""
    records = list(CompensatoryLeaveRecord.objects.filter(source_date=source_date))
    touched = [(r.target_date, r.staff_name) for r in records]
    CompensatoryLeaveRecord.objects.filter(source_date=source_date).delete()
    for target_date, staff_name in touched:
        _remove_name_from_comp_leave_board(target_date, staff_name)


def _schedule_comp_leave(source_date, duty_type, staff_name, write_board=False, rule_date=None, allow_inactive=False):
    rule_date = rule_date or source_date
    day_type = _day_type(rule_date)
    if not day_type or not staff_name:
        return None, None

    rule_qs = CompensatoryLeaveRule.objects.filter(
        day_type=day_type,
        duty_type=duty_type,
    )
    if not allow_inactive:
        rule_qs = rule_qs.filter(is_active=True)
    rule = rule_qs.first()
    if not rule:
        return None, "代休ルール未設定"

    target_date = _calculate_comp_leave_date(rule_date, rule)

    if write_board:
        target_board, _ = DailyBoard.objects.get_or_create(board_date=target_date)
        if not _add_name_to_comp_leave(target_board, staff_name):
            return target_date, "代休枠が3つとも埋まっています"

    CompensatoryLeaveRecord.objects.update_or_create(
        source_date=source_date,
        duty_type=duty_type,
        staff_name=staff_name,
        defaults={"target_date": target_date},
    )
    return target_date, None


def _sync_calendar_comp_records(calendar_day):
    """勤務カレンダー1日分から代休予約を同期する。スタッフ別の個別追加を維持する。

    Ver.3.9.5.2:
    「明け」の代休ルールは、夜勤入り日ではなく実際の明け日で判定する。
    そのため金曜夜勤→土曜明けのように、夜勤入り日が平日でも翌日が土日祝なら
    土曜/日曜/祝日の「明け」ルールを適用する。
    """
    source_date = calendar_day.duty_date
    existing = {(r.duty_type, r.staff_name): r for r in CompensatoryLeaveRecord.objects.filter(source_date=source_date)}

    # 「この日だけ代休なし」は、この夜勤から派生する明け分も含めて全て抑止する。
    if calendar_day.no_compensatory_leave:
        touched = [(r.target_date, r.staff_name) for r in existing.values()]
        CompensatoryLeaveRecord.objects.filter(source_date=source_date).delete()
        for target_date, staff_name in touched:
            _remove_name_from_comp_leave_board(target_date, staff_name)
        return []

    source_is_holiday = _calendar_is_holiday_duty(source_date, calendar_day)
    next_day = source_date + timedelta(days=1)
    next_day_type = _day_type(next_day)

    desired = []
    # 休日日勤と夜勤入り分は「入り日」が休日のときだけ対象。
    if source_is_holiday and calendar_day.holiday_day_shift:
        desired.append(("holiday_day", calendar_day.holiday_day_shift.name))
    if calendar_day.night_shift:
        if source_is_holiday:
            desired.append(("night", calendar_day.night_shift.name))

        # 明け分は「翌日（実際の明け日）」が土日祝等かどうかで判定する。
        night_after_rules = CompensatoryLeaveRule.objects.filter(
            day_type=next_day_type, duty_type="night_after"
        ) if next_day_type else CompensatoryLeaveRule.objects.none()
        if not _specific_comp_force(calendar_day, "night_after"):
            night_after_rules = night_after_rules.filter(is_active=True)
        if next_day_type and night_after_rules.exists():
            desired.append(("night_after", calendar_day.night_shift.name))
    desired_keys = set(desired)

    for key, record in list(existing.items()):
        if key not in desired_keys:
            old_target, staff_name = record.target_date, record.staff_name
            record.delete()
            _remove_name_from_comp_leave_board(old_target, staff_name)

    warnings = []
    for duty_type, staff_name in desired:
        record = existing.get((duty_type, staff_name))
        if record and record.is_manual_override:
            continue
        target_date, warning = _schedule_comp_leave(
            source_date, duty_type, staff_name, write_board=False,
            rule_date=(source_date + timedelta(days=1) if duty_type == "night_after" else source_date),
            allow_inactive=_specific_comp_force(calendar_day, duty_type),
        )
        if warning:
            if _specific_comp_force(calendar_day, duty_type):
                warnings.append(f"{staff_name}：{warning}")
            elif record and not record.is_manual_override:
                old_target = record.target_date
                record.delete()
                _remove_name_from_comp_leave_board(old_target, staff_name)
        elif target_date:
            CompensatoryLeaveRecord.objects.filter(source_date=source_date, duty_type=duty_type, staff_name=staff_name).update(is_manual_override=False)
    return warnings


def _rotation_neighbor(staff, offset=1, target_date=None):
    rotation = _rotation_staff(target_date)
    if not rotation:
        return None
    ids = [item.id for item in rotation]
    if staff and staff.id in ids:
        return rotation[(ids.index(staff.id) + offset) % len(rotation)]
    return rotation[0]


def _calendar_snapshot(calendar_day):
    comp_records = list(CompensatoryLeaveRecord.objects.filter(
        source_date=calendar_day.duty_date
    ).values("duty_type", "staff_name", "target_date", "is_manual_override"))
    return {
        "day_mode": calendar_day.day_mode,
        "night_shift_id": calendar_day.night_shift_id,
        "holiday_day_shift_id": calendar_day.holiday_day_shift_id,
        "no_compensatory_leave": calendar_day.no_compensatory_leave,
        "force_compensatory_leave": calendar_day.force_compensatory_leave,
        "force_holiday_day_compensatory_leave": calendar_day.force_holiday_day_compensatory_leave,
        "force_night_compensatory_leave": calendar_day.force_night_compensatory_leave,
        "force_night_after_compensatory_leave": calendar_day.force_night_after_compensatory_leave,
        "comp_records": [
            {**r, "target_date": r["target_date"].isoformat()} for r in comp_records
        ],
    }


def _save_calendar_undo(calendar_day):
    DutyCalendarDayHistory.objects.update_or_create(
        duty_date=calendar_day.duty_date,
        defaults={"snapshot": _calendar_snapshot(calendar_day)},
    )


def _apply_manual_comp_date(calendar_day, duty_type, raw_date):
    staff = calendar_day.holiday_day_shift if duty_type == "holiday_day" else calendar_day.night_shift
    if not staff or calendar_day.no_compensatory_leave:
        return

    # 明け分だけは夜勤入り日ではなく、翌日の休日判定を使う。
    relevant_date = calendar_day.duty_date + timedelta(days=1) if duty_type == "night_after" else calendar_day.duty_date
    if not _calendar_is_holiday_duty(relevant_date):
        return

    requested = None
    if raw_date:
        try:
            requested = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            requested = None

    record = CompensatoryLeaveRecord.objects.filter(
        source_date=calendar_day.duty_date, duty_type=duty_type, staff_name=staff.name
    ).first()

    rule_date = calendar_day.duty_date + timedelta(days=1) if duty_type == "night_after" else calendar_day.duty_date
    rule_day_type = _day_type(rule_date)
    rule_qs = CompensatoryLeaveRule.objects.filter(
        day_type=rule_day_type, duty_type=duty_type
    ) if rule_day_type else CompensatoryLeaveRule.objects.none()
    if not _specific_comp_force(calendar_day, duty_type):
        rule_qs = rule_qs.filter(is_active=True)
    rule = rule_qs.first()
    automatic_date = _calculate_comp_leave_date(rule_date, rule) if rule else None

    # 手動日付が指定されていれば、ルールがOFF/未設定でも直接1件作成できる。
    if requested is not None and record is None:
        CompensatoryLeaveRecord.objects.create(
            source_date=calendar_day.duty_date,
            duty_type=duty_type,
            staff_name=staff.name,
            target_date=requested,
            is_manual_override=True,
        )
        return

    if record is None:
        _schedule_comp_leave(
            calendar_day.duty_date, duty_type, staff.name, write_board=False,
            rule_date=rule_date,
            allow_inactive=_specific_comp_force(calendar_day, duty_type),
        )
        record = CompensatoryLeaveRecord.objects.filter(
            source_date=calendar_day.duty_date, duty_type=duty_type, staff_name=staff.name
        ).first()
    if not record:
        return

    if requested is None:
        requested = automatic_date or record.target_date

    old_target = record.target_date
    record.target_date = requested
    record.is_manual_override = automatic_date is None or requested != automatic_date
    record.save(update_fields=["target_date", "is_manual_override"])
    if old_target != requested:
        _remove_name_from_comp_leave_board(old_target, staff.name)


def _calendar_comp_names(target_date):
    """指定日に予定されている代休者名を重複なしで最大3名返す。"""
    names = []
    for name in CompensatoryLeaveRecord.objects.filter(
        target_date=target_date
    ).order_by("source_date", "id").values_list("staff_name", flat=True):
        if name and name not in names:
            names.append(name)
        if len(names) >= 3:
            break
    return names


@require_POST
@transaction.atomic
def auto_duty(request):
    """
    当日配置表の勤務欄へ、月間勤務カレンダーで確定済みの内容を転記する。
    この処理ではローテーション計算を行わない。
    """
    board_date = _parse_date(request.POST.get("date"))
    overwrite = request.POST.get("overwrite") == "1"
    board, _ = DailyBoard.objects.select_for_update().get_or_create(
        board_date=board_date
    )

    calendar_day = DutyCalendarDay.objects.select_related(
        "night_shift", "holiday_day_shift"
    ).filter(duty_date=board_date).first()
    if not calendar_day:
        return JsonResponse({
            "ok": False,
            "error": (
                "この日の勤務カレンダーがまだ作成されていません。"
                "「月間勤務表」で夜勤ローテーションを作成してください。"
            ),
        }, status=400)

    previous_calendar = DutyCalendarDay.objects.select_related("night_shift").filter(
        duty_date=board_date - timedelta(days=1)
    ).first()

    night_name = calendar_day.night_shift.name if calendar_day.night_shift else ""
    holiday_day_name = (
        calendar_day.holiday_day_shift.name
        if calendar_day.holiday_day_shift and _calendar_is_holiday_duty(board_date, calendar_day)
        else ""
    )
    after_name = (
        previous_calendar.night_shift.name
        if previous_calendar and previous_calendar.night_shift
        else ""
    )
    comp_names = _calendar_comp_names(board_date)

    if overwrite or not board.night_shift.strip():
        board.night_shift = night_name
    if overwrite or not board.night_shift_after.strip():
        board.night_shift_after = after_name
    if overwrite or not board.holiday_day_shift.strip():
        board.holiday_day_shift = holiday_day_name

    comp_fields = (
        "compensatory_leave_1",
        "compensatory_leave_2",
        "compensatory_leave_3",
    )
    if overwrite:
        for index, field in enumerate(comp_fields):
            setattr(board, field, comp_names[index] if index < len(comp_names) else "")
    else:
        existing = [str(getattr(board, field, "") or "").strip() for field in comp_fields]
        for name in comp_names:
            if name in existing:
                continue
            for index, field in enumerate(comp_fields):
                if not existing[index]:
                    setattr(board, field, name)
                    existing[index] = name
                    break

    board.save()
    board.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "night_shift": board.night_shift,
        "night_shift_after": board.night_shift_after,
        "holiday_day_shift": board.holiday_day_shift,
        "compensatory_leave_1": board.compensatory_leave_1,
        "compensatory_leave_2": board.compensatory_leave_2,
        "compensatory_leave_3": board.compensatory_leave_3,
        "updated_at": board.updated_at.isoformat(),
        "warnings": [],
        "message": "月間勤務カレンダーから勤務欄を反映しました。",
    })


# =========================================================
# 通常配置の自動作成
# =========================================================

AUTO_ASSIGN_ROW_KEYS = [
    "0730", "0830", "0900", "1000", "1100",
    "1200", "1300", "1400", "1500", "1600",
]


def _names_marked_unavailable(board):
    """勤務・休暇欄から「終日」通常配置できないスタッフだけを返す。"""
    fields = (
        "night_shift", "night_shift_after", "holiday_day_shift",
        "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
        "annual_leave_1", "annual_leave_2", "reception_leave", "unassigned",
    )
    text = "\n".join(str(getattr(board, field, "") or "") for field in fields)

    # v3.10.6.52:
    # AM休・PM休・時間指定休は年休欄に名前が表示されても終日不在ではない。
    # いったん通常配置候補に残し、配置後に該当時間帯だけ外す。
    partial_names = set(
        WeeklyAbsence.objects.filter(
            absence_date=board.board_date,
            staff__is_active=True,
        ).exclude(period="full").values_list("staff__name", flat=True)
    )
    partial_names.update(
        entry["staff"].name
        for entry in _parse_quick_meta_absences(board)
        if entry["period"] != "full"
    )

    unavailable = {
        staff.name
        for staff in Staff.objects.filter(is_active=True)
        if staff.name and staff.name in text and staff.name not in partial_names
    }

    # 終日休は表示文字列に依存せず明示的に除外する。
    unavailable.update(_full_day_absence_names(board.board_date))
    unavailable.update(
        entry["staff"].name
        for entry in _parse_quick_meta_absences(board)
        if entry["period"] == "full"
    )
    return unavailable


def _assignment_history(board_date, staffs, areas):
    """現在週の回数と直近28日の配置日数をまとめて返す。"""
    monday = board_date - timedelta(days=board_date.weekday())
    week_end = monday + timedelta(days=6)
    recent_start = board_date - timedelta(days=27)

    staff_names = [staff.name for staff in staffs]
    area_ids = [area.id for area in areas]

    cells = AssignmentCell.objects.filter(
        board__board_date__gte=recent_start,
        board__board_date__lte=board_date,
        row_key__in=AUTO_ASSIGN_ROW_KEYS,
        value__in=staff_names,
        area_id__in=area_ids,
    ).values("board__board_date", "area_id", "value")

    recent_days = defaultdict(set)
    weekly_days = defaultdict(set)
    for item in cells:
        day = item["board__board_date"]
        key = (item["value"], item["area_id"])
        recent_days[key].add(day)
        if monday <= day <= week_end:
            weekly_days[key].add(day)

    return (
        {key: len(days) for key, days in recent_days.items()},
        {key: len(days) for key, days in weekly_days.items()},
    )


def _slot_score(staff, area, recent_counts, weekly_counts, rng):
    """スタッフと配置場所の相性を数値化する。高いほど優先。"""
    dedicated_id = getattr(staff, "dedicated_area_id", None)
    if dedicated_id and area.id != dedicated_id:
        return None
    avoid_ids = getattr(staff, "_avoid_area_ids", set())
    if area.id in avoid_ids:
        return None

    score = rng.random() * 2.0
    preferred_ids = [
        staff.preferred_area_1_id, staff.preferred_area_2_id,
        staff.preferred_area_3_id, staff.preferred_area_4_id,
    ]
    weights = (220, 120, 70, 40)
    for idx, area_id in enumerate(preferred_ids):
        if area.id == area_id:
            score += weights[idx]
            break

    weekly_targets = getattr(staff, "_weekly_target_map", {})
    target_count = weekly_targets.get(area.id, 0)
    if target_count > 0:
        done = weekly_counts.get((staff.name, area.id), 0)
        remaining = target_count - done
        if remaining > 0:
            score += 320 + (remaining * 25)
        else:
            # v3.10.6.48:
            # 週回数達成後は、その対象配置へさらに入り続けるのを強く抑える。
            # 配置禁止にはしないため、ほかに候補がいない人員不足時は使用できる。
            # 配置場所優先度(+最大5000)より大きくして、週回数超過を通常時は避ける。
            score -= 6200

    backup_priority = getattr(staff, "_backup_priority_map", {}).get(area.id)
    if backup_priority:
        # 専任本人が終日不在のときは、専任代理を通常候補（Free優先を含む）より明確に優先する。
        # 代理1 → 代理2 → ... の順序も維持する。
        score += max(5200 - (backup_priority * 100), 4000)
    if area.id in getattr(staff, "_main_area_ids", set()):
        score += 30  # 主担当は弱めの優先。週回数や優先①〜④より下。

    # 同じ人が同じ高優先配置へ連日偏りすぎないよう、週内回数を強めに減点。
    # 優先①～④や週回数目標は維持しつつ、候補が同程度ならローテーションを優先する。
    score -= recent_counts.get((staff.name, area.id), 0) * 12
    score -= weekly_counts.get((staff.name, area.id), 0) * 55
    return score


@require_POST
def set_daily_area_activation(request):
    """予定時のみの配置場所を、その日の自動配置対象にするか保存する。"""
    board_date = _parse_date(request.POST.get("date"))
    try:
        area_id = int(request.POST.get("area_id", ""))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "配置場所が正しくありません。"}, status=400)

    area = get_object_or_404(
        AssignmentArea, pk=area_id, is_active=True, auto_assignment_mode="scheduled"
    )
    enabled = request.POST.get("enabled") == "1"
    board, _ = DailyBoard.objects.get_or_create(board_date=board_date)
    activation, _ = DailyAreaActivation.objects.get_or_create(board=board, area=area)
    activation.is_enabled = enabled
    activation.save(update_fields=["is_enabled"])
    return JsonResponse({"ok": True, "area_id": area.id, "enabled": enabled})


def _parse_quick_meta_absences(board):
    """当日配置表の年休・受付・会議等クイック入力を構造化して返す。"""
    staffs = list(Staff.objects.filter(is_active=True).order_by("-name"))
    entries = []
    sources = (("annual", board.annual_leave_1 or ""), ("reception", board.reception_leave or ""), ("meeting", board.conference or ""))
    for kind, text in sources:
        # Ver.3.10.6.7: 週間予定は内部マーカーで管理するが、画面には文言を表示しない。
        # 旧データの「【週間予定】」と新しい不可視マーカーの両方に対応する。
        for _weekly_marker in ("【週間予定】", "\u2063"):
            text = text.split(_weekly_marker)[0]
        for raw in re.split(r"[\n、]+", text):
            line = raw.strip()
            if not line:
                continue
            staff = next((st for st in staffs if st.name and st.name in line), None)
            if not staff:
                continue
            if "1日" in line or line == staff.name:
                period = "full"; slot_key = None
            elif "AM休" in line or re.search(r"(?:^|\s)AM(?:$|\s)", line, re.I):
                period = "am"; slot_key = None
            elif "PM休" in line or re.search(r"(?:^|\s)PM(?:$|\s)", line, re.I):
                period = "pm"; slot_key = None
            else:
                m = re.search(r"(\d{1,2}):(\d{2})", line)
                if m:
                    label = f"{int(m.group(1))}:{m.group(2)}"
                    slot = TimeSlot.objects.filter(is_active=True, kind="time", label=label).first()
                    period = "time"; slot_key = slot.key if slot else None
                else:
                    # 旧来の名前だけ入力も終日扱いを維持。
                    period = "full"; slot_key = None
            entries.append({"kind": kind, "staff": staff, "period": period, "slot_key": slot_key, "raw": line})
    return entries


def _quick_entry_covers_row(entry, row_key):
    slots = list(TimeSlot.objects.filter(is_active=True, kind="time").order_by("display_order", "id"))
    keys = [s.key for s in slots]
    if row_key not in keys:
        return False
    idx = keys.index(row_key)
    if entry["period"] == "full": return True
    if entry["period"] == "am":
        lunch_idx = next((i for i,s in enumerate(slots) if s.is_lunch_highlight), len(keys)//2)
        return idx <= lunch_idx
    if entry["period"] == "pm":
        lunch_idx = next((i for i,s in enumerate(slots) if s.is_lunch_highlight), len(keys)//2)
        return idx > lunch_idx
    if entry["period"] == "time" and entry.get("slot_key") in keys:
        target = keys.index(entry["slot_key"])
        # 年休・受付の時刻指定は「その時刻から休み」、会議等はその時間だけ。
        return idx == target if entry["kind"] == "meeting" else idx >= target
    return False


def _apply_quick_meta_absences(board):
    entries = [e for e in _parse_quick_meta_absences(board) if e["period"] != "full"]
    if not entries:
        return []
    warnings = []
    free_names = [x.strip() for x in re.split(r"[、,\n]+", board.free_text or "") if x.strip()]
    for entry in entries:
        affected = [c for c in board.cells.filter(value=entry["staff"].name).select_related("area") if _quick_entry_covers_row(entry, c.row_key)]
        if not affected:
            continue
        replacement = None
        for name in list(free_names):
            st = Staff.objects.filter(name=name, is_active=True, auto_assignment_enabled=True).select_related("dedicated_area").first()
            if st and all(_can_staff_cover_area(st, c.area) for c in affected):
                replacement = st; break
        for cell in affected:
            cell.value = replacement.name if replacement else ""
            cell.save(update_fields=["value"])
        if replacement:
            free_names.remove(replacement.name)
            StaffFreeHistory.objects.filter(staff=replacement, board_date=board.board_date).delete()
            warnings.append(f"{entry['staff'].name}さんの{entry['raw']}を反映し、{replacement.name}さんをFreeから補充しました。")
        else:
            warnings.append(f"{entry['staff'].name}さんの{entry['raw']}を反映しました。補充できない枠は空欄です。")
    board.free_text = "、".join(free_names)
    board.save(update_fields=["free_text", "updated_at"])
    return warnings


@require_POST
@transaction.atomic
def auto_assignment(request):
    board_date = _parse_date(request.POST.get("date"))
    rebuild = request.POST.get("rebuild") == "1"

    board, _ = DailyBoard.objects.select_for_update().get_or_create(
        board_date=board_date
    )
    all_areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    scheduled_enabled_ids = set(
        DailyAreaActivation.objects.filter(
            board=board, is_enabled=True
        ).values_list("area_id", flat=True)
    )
    areas = [
        area for area in all_areas
        if area.auto_assignment_count > 0
        and (
            area.auto_assignment_mode == "daily"
            or (
                area.auto_assignment_mode == "scheduled"
                and area.id in scheduled_enabled_ids
            )
        )
    ]
    staffs = list(
        Staff.objects.filter(is_active=True, auto_assignment_enabled=True)
        .select_related(
            "preferred_area_1", "preferred_area_2", "preferred_area_3",
            "avoid_area", "weekly_target_area",
        )
        .order_by("display_order", "name")
    )

    if not staffs:
        return JsonResponse({
            "ok": False,
            "error": "通常配置の自動作成対象スタッフが登録されていません。設定画面で対象をONにしてください。",
        }, status=400)
    if not areas:
        return JsonResponse({
            "ok": False,
            "error": "本日の自動配置対象となる配置場所がありません。予定時のみの場所は「本日の予定」をONにしてください。",
        }, status=400)

    # 作り直す場合でも主担当・Subは触らず、時間帯の通常配置だけを消す。
    if rebuild:
        board.cells.filter(row_key__in=AUTO_ASSIGN_ROW_KEYS).delete()

    unavailable = _names_marked_unavailable(board)

    # 空欄だけモードでは、既に手入力されている人を別場所へ重複配置しない。
    existing_cells = list(board.cells.filter(row_key__in=AUTO_ASSIGN_ROW_KEYS).exclude(value=""))
    already_assigned = {cell.value.strip() for cell in existing_cells if cell.value.strip()}

    candidates = [
        staff for staff in staffs
        if staff.name not in unavailable
        and (rebuild or staff.name not in already_assigned)
    ]

    # 入力列数と自動配置人数は別管理。
    # 例：SPECTは入力2枠でも自動配置人数1なら、通常は1名だけ自動入力する。
    occupied_slots = set()
    if not rebuild:
        for cell in existing_cells:
            occupied_slots.add((cell.area_id, cell.slot_index))

    slots = []
    for area in areas:
        occupied_for_area = {
            slot_index for area_id, slot_index in occupied_slots
            if area_id == area.id
        }
        additional_needed = max(area.auto_assignment_count - len(occupied_for_area), 0)
        if additional_needed <= 0:
            continue

        empty_slot_indexes = [
            slot_index for slot_index in range(1, area.slot_count + 1)
            if (area.id, slot_index) not in occupied_slots
        ]
        for slot_index in empty_slot_indexes[:additional_needed]:
            slots.append((area, slot_index))

    recent_counts, weekly_counts = _assignment_history(board_date, candidates, areas)
    rng = random.Random(board_date.toordinal())

    assignments = []
    remaining_slots = list(slots)

    # 週目標が不足している人 → 優先配置あり → その他、の順で処理する。
    def staff_priority(staff):
        deficit = 0
        if staff.weekly_target_area_id and staff.weekly_target_count > 0:
            done = weekly_counts.get((staff.name, staff.weekly_target_area_id), 0)
            deficit = max(staff.weekly_target_count - done, 0)
        has_preference = int(any([
            staff.preferred_area_1_id, staff.preferred_area_2_id, staff.preferred_area_3_id
        ]))
        return (-deficit, -has_preference, staff.display_order, staff.name)

    for staff in sorted(candidates, key=staff_priority):
        best = None
        for area, slot_index in remaining_slots:
            score = _slot_score(staff, area, recent_counts, weekly_counts, rng)
            if score is None:
                continue
            if best is None or score > best[0]:
                best = (score, area, slot_index)
        if best is None:
            continue

        _, area, slot_index = best
        remaining_slots.remove((area, slot_index))
        assignments.append((staff, area, slot_index))

        # 以降のスタッフ判定にも今回分を軽く反映する。
        recent_counts[(staff.name, area.id)] = recent_counts.get((staff.name, area.id), 0) + 1
        weekly_counts[(staff.name, area.id)] = weekly_counts.get((staff.name, area.id), 0) + 1

    # 選ばれた人をその日の時間帯すべてへ入力する。
    for staff, area, slot_index in assignments:
        for row_key in AUTO_ASSIGN_ROW_KEYS:
            cell, _ = AssignmentCell.objects.get_or_create(
                board=board,
                area=area,
                row_key=row_key,
                slot_index=slot_index,
            )
            if rebuild or not cell.value.strip():
                cell.value = staff.name
                cell.save(update_fields=["value"])

    board.save()  # updated_atを更新
    board.refresh_from_db()

    # v3.10.6.52:
    # AM休・PM休・時間指定休は終日候補から外さず、
    # いったん通常配置したあと該当時間帯だけ配置を外す。
    # これにより AM休ならPM、PM休ならAMの配置を残せる。
    partial_absence_warnings = _apply_partial_absences(board)
    board.refresh_from_db()

    cells_payload = [
        {
            "area_id": cell.area_id,
            "row_key": cell.row_key,
            "slot_index": cell.slot_index,
            "value": cell.value,
        }
        for cell in board.cells.filter(row_key__in=AUTO_ASSIGN_ROW_KEYS)
    ]

    assigned_names = {staff.name for staff, _, _ in assignments}
    not_assigned = [staff.name for staff in candidates if staff.name not in assigned_names]
    warnings = list(partial_absence_warnings)
    if not_assigned:
        warnings.append("配置枠が足りない、または苦手配置条件により未配置: " + "、".join(not_assigned))
    if unavailable:
        warnings.append("勤務・休暇欄のため通常配置から除外: " + "、".join(sorted(unavailable)))
    if unfilled_minimum:
        shortage_names = "、".join(f"{area.name}({idx})" for area, idx in unfilled_minimum)
        warnings.append("最低必要人数を確保できない配置があります: " + shortage_names)
    if unfilled_normal:
        warnings.append("人員不足のため通常人数まで埋めていない配置があります。")

    return JsonResponse({
        "ok": True,
        "cells": cells_payload,
        "updated_at": board.updated_at.isoformat(),
        "assigned_count": len(assignments),
        "warnings": warnings,
        "message": f"勤務情報を同期し、通常配置を{len(assignments)}名分、自動作成しました。",
    })


# =========================================================
# 代休ルール設定
# =========================================================

def _rule_values(request):
    day_type = request.POST.get("day_type", "").strip()
    duty_type = request.POST.get("duty_type", "").strip()
    offset_mode = request.POST.get("offset_mode", "weekday").strip()
    try:
        weeks_after = max(0, int(request.POST.get("weeks_after", "1") or 1))
        target_weekday = int(request.POST.get("target_weekday", "0") or 0)
        days_after = max(1, int(request.POST.get("days_after", "1") or 1))
    except ValueError as exc:
        raise ValueError("代休ルールの数値が正しくありません。") from exc

    valid_days = {value for value, _ in CompensatoryLeaveRule.DAY_TYPE_CHOICES}
    valid_duties = {value for value, _ in CompensatoryLeaveRule.DUTY_TYPE_CHOICES}
    valid_modes = {value for value, _ in CompensatoryLeaveRule.OFFSET_MODE_CHOICES}
    if day_type not in valid_days or duty_type not in valid_duties:
        raise ValueError("勤務日または勤務種類が正しくありません。")
    if offset_mode not in valid_modes or not 0 <= target_weekday <= 6:
        raise ValueError("代休日の設定が正しくありません。")

    return {
        "day_type": day_type,
        "duty_type": duty_type,
        "offset_mode": offset_mode,
        "weeks_after": weeks_after,
        "target_weekday": target_weekday,
        "days_after": days_after,
        "is_active": request.POST.get("is_active") == "on",
    }


@require_POST
def comp_rule_add(request):
    try:
        values = _rule_values(request)
        _, created = CompensatoryLeaveRule.objects.get_or_create(
            day_type=values.pop("day_type"),
            duty_type=values.pop("duty_type"),
            defaults=values,
        )
        if not created:
            messages.error(request, "同じ勤務日・勤務種類のルールが既にあります。")
        else:
            messages.success(request, "代休ルールを追加しました。")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("daily_assignment:settings")


@require_POST
def comp_rule_update(request, rule_id):
    rule = get_object_or_404(CompensatoryLeaveRule, pk=rule_id)
    try:
        values = _rule_values(request)
        duplicate = CompensatoryLeaveRule.objects.filter(
            day_type=values["day_type"],
            duty_type=values["duty_type"],
        ).exclude(pk=rule.pk)
        if duplicate.exists():
            raise ValueError("同じ勤務日・勤務種類のルールが既にあります。")
        for key, value in values.items():
            setattr(rule, key, value)
        rule.save()
        messages.success(request, "代休ルールを更新しました。")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("daily_assignment:settings")


@require_POST
def comp_rule_delete(request, rule_id):
    rule = get_object_or_404(CompensatoryLeaveRule, pk=rule_id)
    rule.delete()
    messages.success(request, "代休ルールを削除しました。")
    return redirect("daily_assignment:settings")


# =========================================================
# Ver.3 時間枠・レイアウト・時間連動ルール
# =========================================================
from .models import TimeSlot, AreaTimeRule, DerivedAssignmentRule, LunchBreakEntry, HorizontalMergeRule


def _active_time_slots(include_special=True):
    qs = TimeSlot.objects.filter(is_active=True).order_by("display_order", "id")
    if not include_special:
        qs = qs.filter(kind="time")
    return list(qs)


def _time_rule_map(areas, time_slots):
    rules = AreaTimeRule.objects.filter(area__in=areas, time_slot__in=time_slots)
    return {(rule.area_id, rule.time_slot_id): rule.mode for rule in rules}


def _area_row_layout(area, time_slots, rule_map):
    """各時間枠について、表示/rowspan/保存先キーを計算する。"""
    rows = []
    i = 0
    while i < len(time_slots):
        slot = time_slots[i]
        mode = rule_map.get((area.id, slot.id), "normal")
        if mode == "hidden":
            rows.append({"slot": slot, "show": True, "hidden": True, "rowspan": 1, "owner_key": None})
            i += 1
            continue
        if mode == "merge_continue":
            # 設定ミスで開始が無い場合は通常セルとして扱う。
            rows.append({"slot": slot, "show": True, "hidden": False, "rowspan": 1, "owner_key": slot.key})
            i += 1
            continue
        if mode == "merge_start":
            span = 1
            j = i + 1
            while j < len(time_slots):
                next_slot = time_slots[j]
                next_mode = rule_map.get((area.id, next_slot.id), "normal")
                if next_mode != "merge_continue":
                    break
                span += 1
                j += 1
            rows.append({"slot": slot, "show": True, "hidden": False, "rowspan": span, "owner_key": slot.key})
            for k in range(i + 1, i + span):
                rows.append({"slot": time_slots[k], "show": False, "hidden": False, "rowspan": 0, "owner_key": slot.key})
            i += span
            continue
        rows.append({"slot": slot, "show": True, "hidden": False, "rowspan": 1, "owner_key": slot.key})
        i += 1
    return rows


def _build_board_table(board, areas, time_slots):
    cells = {
        (cell.area_id, cell.row_key, cell.slot_index): cell.value
        for cell in board.cells.filter(area__in=areas)
    }
    rule_map = _time_rule_map(areas, time_slots)
    area_layouts = {area.id: _area_row_layout(area, time_slots, rule_map) for area in areas}

    # Ver.3.10.6.26:
    # 縦連結の途中に「その時刻専用の実セル」が保存されている場合は、担当交代の
    # オーバーライドとして扱い、表示上もそこで縦連結を分割する。
    # 例: ER Po が午前中は縦連結でも、11:00 の空き補充で担当が交代し、
    # 12:00 の時間連動元として新担当を残す必要がある場合。
    # これをしないと 12:00 の実セルに新担当を保存しても、過去時刻 owner の
    # rowspan に隠れて旧担当が画面に残って見える。
    for area in areas:
        layout = area_layouts[area.id]
        exact_override_keys = {
            row_key
            for (area_id, row_key, _slot_index), value in cells.items()
            if area_id == area.id and str(value or "").strip()
        }
        i = 0
        while i < len(layout):
            item = layout[i]
            if not item.get("show") or item.get("hidden") or item.get("rowspan", 1) <= 1:
                i += 1
                continue
            span = item["rowspan"]
            start = i
            end = min(len(layout), i + span)
            split_points = [
                j for j in range(start + 1, end)
                if layout[j]["slot"].key in exact_override_keys
            ]
            if not split_points:
                i = end
                continue

            boundaries = [start] + split_points + [end]
            for bidx in range(len(boundaries) - 1):
                seg_start, seg_end = boundaries[bidx], boundaries[bidx + 1]
                owner_key = layout[seg_start]["slot"].key if seg_start != start else item["owner_key"]
                layout[seg_start].update({
                    "show": True, "hidden": False,
                    "rowspan": seg_end - seg_start, "owner_key": owner_key,
                })
                for j in range(seg_start + 1, seg_end):
                    layout[j].update({
                        "show": False, "hidden": False,
                        "rowspan": 0, "owner_key": owner_key,
                    })
            i = end

    # 大分類（category）→細分類（area）の2段ヘッダー用
    category_groups = []
    for area in areas:
        category = area.category or "その他"
        if not category_groups or category_groups[-1]["category"] != category:
            category_groups.append({"category": category, "colspan": 0, "areas": []})
        category_groups[-1]["colspan"] += area.slot_count
        category_groups[-1]["areas"].append(area)

    lunch_map = {
        entry.time_slot_id: entry.value
        for entry in board.lunch_break_entries.filter(time_slot__in=time_slots)
    }

    # 主担当行では、専任配置が設定されているスタッフをその配置へ固定表示する。
    # 主担当ローテーション対象スタッフのON/OFFに関係なく、専任設定を最優先する。
    dedicated_main_names = {
        staff.dedicated_area_id: staff.name
        for staff in Staff.objects.filter(
            is_active=True, dedicated_area__in=areas
        ).exclude(dedicated_area__isnull=True).order_by("display_order", "name")
    }

    render_rows = []
    for row_index, time_slot in enumerate(time_slots):
        row_cells = []
        for area_index, area in enumerate(areas):
            layout = area_layouts[area.id][row_index]
            category = area.category or "その他"
            prev_category = (areas[area_index - 1].category or "その他") if area_index > 0 else None
            next_category = (areas[area_index + 1].category or "その他") if area_index + 1 < len(areas) else None
            for slot_index in range(1, area.slot_count + 1):
                item = {
                    "area_id": area.id,
                    "area_name": area.name,
                    "area_category": area.category or "",
                    "slot_index": slot_index,
                    "show": layout["show"],
                    "hidden": layout["hidden"],
                    "rowspan": layout["rowspan"],
                    "owner_key": layout["owner_key"],
                    "value": "",
                    "category_start": slot_index == 1 and category != prev_category,
                    "category_end": slot_index == area.slot_count and category != next_category,
                }
                if layout["show"] and not layout["hidden"] and layout["owner_key"]:
                    item["value"] = cells.get((area.id, layout["owner_key"], slot_index), "")
                item["is_dedicated_main"] = bool(
                    time_slot.kind == "main"
                    and slot_index == 1
                    and dedicated_main_names.get(area.id)
                    and item["value"] == dedicated_main_names.get(area.id)
                )
                row_cells.append(item)

        # 同じ時間帯に同じスタッフ名が複数の配置場所へ入っている場合は「兼任」として表示を強調する。
        # 時間連動ルールの「兼任（元配置を残す）」で作成された配置もここで赤字になる。
        name_counts = {}
        for item in row_cells:
            name = (item.get("value") or "").strip()
            if item.get("show") and not item.get("hidden") and name:
                name_counts[name] = name_counts.get(name, 0) + 1
        for item in row_cells:
            name = (item.get("value") or "").strip()
            item["is_concurrent"] = bool(name and name_counts.get(name, 0) > 1)
            item["lunch_merge_start"] = False
            item["lunch_merge_skip"] = False
            item["lunch_merge_span"] = 1

        # 設定された横結合ルールをすべて表示する。
        # 同じ時間帯に複数グループ（例：一般撮影・CT・MRI）があっても、それぞれ独立して結合する。
        merge_rules = list(
            HorizontalMergeRule.objects.filter(time_slot=time_slot, is_active=True)
            .select_related("start_area", "end_area", "representative_area")
            .order_by("id")
        )
        occupied_positions = set()
        prepared_merges = []
        for merge_rule in merge_rules:
            start_positions = [i for i, item in enumerate(row_cells) if item.get("area_id") == merge_rule.start_area_id]
            end_positions = [i for i, item in enumerate(row_cells) if item.get("area_id") == merge_rule.end_area_id]
            if not start_positions or not end_positions:
                continue
            first, last = min(start_positions), max(end_positions)
            if first > last:
                first, last = last, first
            # 重複する横結合はHTML上で表現できないため、先に登録されたルールを優先する。
            positions = set(range(first, last + 1))
            if positions & occupied_positions:
                continue
            occupied_positions |= positions
            prepared_merges.append((first, last, merge_rule))

        # 左から順番に適用することで、同一時刻の複数結合を安定して描画する。
        for first, last, merge_rule in sorted(prepared_merges, key=lambda x: x[0]):
            row_cells[first]["lunch_merge_start"] = True
            row_cells[first]["lunch_merge_span"] = last - first + 1
            row_cells[first]["lunch_merge_label"] = merge_rule.name
            row_cells[first]["merge_category_start"] = bool(row_cells[first].get("category_start"))
            row_cells[first]["merge_category_end"] = bool(row_cells[last].get("category_end"))
            merged_names = []
            for i in range(first, last + 1):
                item = row_cells[i]
                # Ver.3.10.6.24:
                # 横結合される時刻では、その時刻そのものに保存された値だけを通常値として採用する。
                # 縦連結の owner_key が過去時刻（例: 08:30）のセルは、12:00 の横結合へ
                # 旧担当者を持ち込む原因になるため、ここでは表示候補に含めない。
                # 横結合専用の代表セル（time_slot.key）は下の virtual_values で別途取り込む。
                if item.get("owner_key") != time_slot.key:
                    continue
                name = (item.get("value") or "").strip()
                if name and name not in merged_names:
                    merged_names.append(name)

            # 代表配置へ入れた時間連動ルールの値は「横結合専用セル」として
            # time_slot.key に保存している。縦連結の owner_key に左右されず表示する。
            virtual_values = list(AssignmentCell.objects.filter(
                board=board,
                area_id=merge_rule.representative_area_id,
                row_key=time_slot.key,
            ).exclude(value="").order_by("slot_index").values_list("value", flat=True))
            for raw_value in virtual_values:
                for name in [x.strip() for x in str(raw_value).split("、") if x.strip()]:
                    if name not in merged_names:
                        merged_names.append(name)

            # Ver.3.10.6.12: 横結合セルも通常セルと同様に手動編集できるよう、
            # 代表配置＋対象時刻を保存先としてテンプレートへ渡す。
            # 自動配置・時間連動で作られた値は従来どおり表示し、手動変更時だけ
            # representative_area / time_slot.key / slot_index=1 に保存する。
            row_cells[first]["lunch_merge_names"] = "、".join(merged_names)
            row_cells[first]["lunch_merge_edit_value"] = "、".join(
                [str(v).strip() for v in virtual_values if str(v).strip()]
            ) or row_cells[first]["lunch_merge_names"]
            row_cells[first]["lunch_merge_area_id"] = merge_rule.representative_area_id
            row_cells[first]["lunch_merge_row_key"] = time_slot.key
            row_cells[first]["lunch_merge_slot_index"] = 1
            for i in range(first + 1, last + 1):
                row_cells[i]["lunch_merge_skip"] = True

        render_rows.append({
            "time_slot": time_slot,
            "cells": row_cells,
            "lunch_value": lunch_map.get(time_slot.id, ""),
        })
    return category_groups, render_rows


def _dynamic_row_keys():
    return set(TimeSlot.objects.filter(is_active=True).values_list("key", flat=True))


@require_GET
def board_view(request):
    board_date = _parse_date(request.GET.get("date"))
    board, _ = DailyBoard.objects.get_or_create(board_date=board_date)
    _sync_calendar_to_board(board)
    _sync_absence_text_to_board(board)
    _sync_main_row(board)
    areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    time_slots = _active_time_slots(include_special=True)
    category_groups, render_rows = _build_board_table(board, areas, time_slots)
    form = DailyBoardMetaForm(instance=board)

    scheduled_areas = list(
        AssignmentArea.objects.filter(is_active=True, auto_assignment_mode="scheduled")
        .order_by("display_order", "id")
    )
    activation_map = {
        item.area_id: item.is_enabled
        for item in board.area_activations.filter(area__in=scheduled_areas)
    }
    scheduled_area_settings = [
        {"area": area, "enabled": activation_map.get(area.id, False)}
        for area in scheduled_areas
    ]

    comment_value_map = {
        item.template_id: item.value
        for item in board.comment_template_values.select_related("template").all()
    }
    comment_template_items = [
        {"template": template, "value": comment_value_map.get(template.id, "")}
        for template in DailyCommentTemplate.objects.filter(is_active=True).order_by("display_order", "id")
    ]

    return render(request, "daily_assignment/board.html", {
        "board": board,
        "board_date": board_date,
        "previous_date": board_date - timedelta(days=1),
        "next_date": board_date + timedelta(days=1),
        "today": timezone.localdate(),
        "category_groups": category_groups,
        "render_rows": render_rows,
        "staff_names_json": json.dumps(
            list(Staff.objects.filter(is_active=True).values_list("name", flat=True)),
            ensure_ascii=False,
        ),
        "form": form,
        "scheduled_area_settings": scheduled_area_settings,
        # Ver.3.8.3: 実際に使用される後段board_viewにもクイック入力候補を渡す。
        "staff_options": Staff.objects.filter(is_active=True).order_by("display_order", "name"),
        "quick_staff_options": Staff.objects.filter(is_active=True).exclude(name="").order_by("display_order", "name"),
        "staff_work_hours": {st.name: {"start": st.work_start_time.strftime("%H:%M") if st.work_start_time else "", "end": st.work_end_time.strftime("%H:%M") if st.work_end_time else ""} for st in Staff.objects.filter(is_active=True)},
        "quick_time_slots": TimeSlot.objects.filter(is_active=True, kind="time").order_by("display_order", "id"),
        "meeting_presets": MeetingPreset.objects.filter(is_active=True).order_by("display_order", "name"),
        "quick_reception_options": ReceptionStaff.objects.filter(is_active=True).order_by("display_order", "name"),
        "comment_template_items": comment_template_items,
    })


@require_POST
@transaction.atomic
def save_board(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "送信データが正しくありません。"}, status=400)

    board_date = _parse_date(payload.get("date"))
    board, _ = DailyBoard.objects.select_for_update().get_or_create(board_date=board_date)

    client_updated_at = payload.get("updated_at")
    if client_updated_at and board.updated_at:
        try:
            client_dt = datetime.fromisoformat(client_updated_at.replace("Z", "+00:00"))
            if timezone.is_naive(client_dt):
                client_dt = timezone.make_aware(client_dt)
            if abs((board.updated_at - client_dt).total_seconds()) > 1:
                return JsonResponse({
                    "ok": False, "conflict": True,
                    "error": "他の人が先に更新しました。画面を再読み込みして確認してください。",
                }, status=409)
        except ValueError:
            pass

    # v3.10.6.72:
    # 手動編集を保存する「直前」の配置表をUndo用に確保する。
    # v71ではこの変数の作成位置が抜けており、保存後の
    # _arm_board_undo() で NameError になっていた。
    manual_undo_snapshot = _build_board_undo_snapshot(board, "手動保存")

    meta = payload.get("meta", {})
    allowed_meta = {field.name for field in DailyBoard._meta.fields} - {"id", "board_date", "updated_at", "two_shift"}
    for key, value in meta.items():
        if key in allowed_meta:
            setattr(board, key, str(value or "")[:5000])
    board.save()

    # 当日コメントテンプレートの入力値は日付ごとに保存する。
    template_by_id = {t.id: t for t in DailyCommentTemplate.objects.filter(is_active=True)}
    for item in payload.get("comment_template_values", []):
        try:
            template_id = int(item.get("template_id"))
        except (TypeError, ValueError):
            continue
        template = template_by_id.get(template_id)
        if not template or template.input_type == "none":
            continue
        value = str(item.get("value", "")).strip()[:300]
        if template.input_type == "number" and value and not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return JsonResponse({"ok": False, "error": f"{template.label}は数字で入力してください。"}, status=400)
        DailyCommentTemplateValue.objects.update_or_create(
            board=board, template=template, defaults={"value": value}
        )

    active_areas = {a.id: a for a in AssignmentArea.objects.filter(is_active=True)}
    valid_row_keys = _dynamic_row_keys()
    incoming = payload.get("cells", [])
    valid_keys = set()
    for item in incoming:
        try:
            area_id = int(item["area_id"])
            row_key = str(item["row_key"])
            slot_index = int(item["slot_index"])
        except (KeyError, TypeError, ValueError):
            continue
        area = active_areas.get(area_id)
        if not area or row_key not in valid_row_keys or not 1 <= slot_index <= area.slot_count:
            continue
        value = str(item.get("value", "")).strip()[:100]
        key = (area_id, row_key, slot_index)
        valid_keys.add(key)
        AssignmentCell.objects.update_or_create(
            board=board, area=area, row_key=row_key, slot_index=slot_index,
            defaults={"value": value},
        )

    # Ver.3.10.4:
    # ブラウザから送られるのは「実際に編集できる入力セル」だけ。
    # 横結合の代表セルや時間連動ルールの仮想セルは画面上に input が無いため、
    # ここで「送信されなかったセル」を削除すると、保存だけで昼当番・Po・CT昼・MR昼などが消える。
    # 空欄にした通常セルも payload には value="" として含まれ update_or_create 済みなので、
    # 非送信セルの一括削除は行わない。これにより勤務可能時間外へ手動入力した例外配置もそのまま保持する。

    lunch_items = payload.get("lunch_entries", [])
    slot_by_key = {s.key: s for s in TimeSlot.objects.filter(is_active=True)}
    seen_lunch_ids = set()
    for item in lunch_items:
        slot = slot_by_key.get(str(item.get("row_key", "")))
        if not slot:
            continue
        value = str(item.get("value", "")).strip()[:500]
        entry, _ = LunchBreakEntry.objects.update_or_create(
            board=board, time_slot=slot, defaults={"value": value}
        )
        seen_lunch_ids.add(entry.id)
    board.lunch_break_entries.exclude(id__in=seen_lunch_ids).delete()

    board.refresh_from_db()
    _arm_board_undo(request, manual_undo_snapshot, board)
    return JsonResponse({"ok": True, "updated_at": board.updated_at.isoformat(), "message": "保存しました。"})


def _time_owner_key(area, time_slot, time_slots=None, rule_map=None):
    time_slots = time_slots or _active_time_slots(include_special=True)
    rule_map = rule_map or _time_rule_map([area], time_slots)
    layout = _area_row_layout(area, time_slots, rule_map)
    for item in layout:
        if item["slot"].id == time_slot.id:
            if item["hidden"]:
                return None
            return item["owner_key"]
    return None


def _add_lunch_name(board, time_slot, name):
    if not name:
        return
    entry, _ = LunchBreakEntry.objects.get_or_create(board=board, time_slot=time_slot)
    names = [n.strip() for n in entry.value.replace("\n", "、").split("、") if n.strip()]
    if name not in names:
        names.append(name)
        entry.value = "、".join(names)
        entry.save(update_fields=["value"])


def _apply_derived_rules(board, prefer_latest_prior=False):
    warnings = []
    cleared_virtual_merges = set()
    areas = list(AssignmentArea.objects.filter(is_active=True))
    time_slots = _active_time_slots(include_special=True)
    rule_map = _time_rule_map(areas, time_slots)
    derived_rules = list(DerivedAssignmentRule.objects.filter(is_active=True).select_related(
        "source_area", "target_area", "time_slot"
    ))
    # 同一時間帯の連動ルールは「昼休憩 → 移動 → 兼任」の順で処理する。
    # 先に休憩対象者を元配置から外して空き枠を作り、その後に移動、最後に兼任を適用する。
    # 例: Po担当を昼休憩へ出した後、必要な移動を処理し、最後にTV担当をPoへ兼任させる。
    action_order = {"break": 0, "move": 1, "add": 2}
    derived_rules.sort(key=lambda r: (r.time_slot.display_order, action_order.get(r.action, 9), r.id))

    # v3.10.6.29:
    # 再計算時に、同じ時刻の「派生先」に残っている前回結果を
    # 別ルールの「元配置」として再利用しないための判定。
    derived_target_pairs = {
        (r.target_area_id, r.time_slot_id)
        for r in derived_rules
        if r.target_area_id
    }

    # 同じ時間帯に複数の時間連動ルールがある場合、先に実行された「移動」で
    # 元セルが空になると後続ルールが同じ元配置を参照できなくなる。
    # そこで、各ルールの元配置者を「ルール適用前」の状態でスナップショットしておく。
    # これにより、同一時刻に複数の兼任・移動・昼休憩ルールを登録しても全件反映できる。
    # Ver.3.10.4:
    # 予定時のみ配置をONにした際、9:00出勤など勤務開始時刻が遅いスタッフが入ると、
    # その配置の縦連結 owner_key（例: 08:30）が勤務可能時間外のため空欄になることがある。
    # 12:00の時間連動がその空欄 owner_key だけを見ると、実際には11:00まで勤務している人がいても
    # 「元配置者なし」と判定され、昼当番・Po・CT昼・MR昼などがまとめて作られなくなる。
    # そこで、指定時刻の保存セルが空なら「直前の実データが入っている時間セル」を参照する。
    def _snapshot_names(rule, source_key):
        def names_for_key(row_key):
            values = AssignmentCell.objects.filter(
                board=board, area=rule.source_area, row_key=row_key
            ).exclude(value="").order_by("slot_index").values_list("value", flat=True)
            names = []
            for raw in values:
                for name in [x.strip() for x in str(raw).split("、") if x.strip()]:
                    if name not in names:
                        names.append(name)
            return names

        # 空き補充の直後は、横/縦連結の古い owner セルよりも
        # 「対象時刻の直前に実際に入っているスタッフ」を優先する。
        # 例: Po 11:00 を Free から差し替えた場合、12:00 の昼当番は
        # 8:30 側に残る旧Po担当ではなく、11:00 の新Po担当を引き継ぐ。
        try:
            target_index = next(i for i, slot in enumerate(time_slots) if slot.id == rule.time_slot_id)
        except StopIteration:
            return []

        if prefer_latest_prior:
            checked = set()
            for slot in reversed(time_slots[:target_index]):
                if getattr(slot, "kind", "time") != "time":
                    continue

                # Ver.3.10.6.23:
                # 空き補充は画面で指定された「その時刻の実セル」へ値を書き込むことがある。
                # ここで先に _time_owner_key() へ畳み込むと、11:00 に新担当者が入っていても
                # 縦連結 owner（例: 08:30）に残る旧担当者を拾ってしまう。
                # まず直前時刻そのものの物理セルを確認し、値が無い場合だけ owner を見る。
                exact_key = slot.key
                if exact_key not in checked:
                    checked.add(exact_key)
                    names = names_for_key(exact_key)
                    if names:
                        return names

                prior_key = _time_owner_key(rule.source_area, slot, time_slots, rule_map)
                if not prior_key or prior_key in checked:
                    continue
                checked.add(prior_key)
                names = names_for_key(prior_key)
                if names:
                    return names

        # v3.10.6.29:
        # prefer_latest_prior の再計算中、元配置自身が同時刻の別ルールの派生先なら、
        # その時刻セルは「前回の派生結果」である可能性が高い。
        # 直前時刻に元担当が見つからなかった場合も、同時刻の古い派生結果を
        # 元配置として拾わず、空として扱う。
        if not (
            prefer_latest_prior
            and (rule.source_area_id, rule.time_slot_id) in derived_target_pairs
        ):
            names = names_for_key(source_key)
            if names:
                return names

        # 通常時は保存セルが空の場合だけ、対象時刻より前を近い順に探索する。
        checked = {source_key}
        for slot in reversed(time_slots[:target_index]):
            if getattr(slot, "kind", "time") != "time":
                continue
            prior_key = _time_owner_key(rule.source_area, slot, time_slots, rule_map)
            if not prior_key or prior_key in checked:
                continue
            checked.add(prior_key)
            names = names_for_key(prior_key)
            if names:
                return names
        return []

    source_snapshot = {}
    for rule in derived_rules:
        source_key = _time_owner_key(rule.source_area, rule.time_slot, time_slots, rule_map)
        if not source_key:
            continue
        snap_key = (rule.source_area_id, rule.time_slot_id, source_key)
        if snap_key not in source_snapshot:
            source_snapshot[snap_key] = _snapshot_names(rule, source_key)

    # 空き補充では元配置者が差し替わるため、時間連動の結果セルもいったん再計算する。
    # 従来は「兼任(add)」の対象セルに旧担当者が残っていると、新担当者を空き枠へ追加するだけで
    # 旧担当者を消せなかった（例: Po 11:00差し替え後も12:00 Poに旧担当者が残る）。
    # 元配置者のスナップショットを先に保存したうえで、派生先だけをクリアしてから
    # 昼休憩→移動→兼任を作り直す。
    if prefer_latest_prior:
        cleared_targets = set()
        break_slot_ids = set()
        ordered_areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
        ordered_ids = [a.id for a in ordered_areas]

        for rule in derived_rules:
            if rule.action == "break":
                break_slot_ids.add(rule.time_slot_id)
                # prefer_latest_prior では、休憩対象者は「直前の最新配置」から取得している。
                # そのため対象時刻の元配置セルに旧担当者が残っている場合、通常の break 処理では
                # 新担当者だけを除去対象にしてしまい、旧担当者が残存する。
                # 例: Po 11:00 を差し替えた後、12:00 Po に旧Po担当が残るケース。
                # 12:00 が独立した保存セルの場合だけ、派生結果として一度クリアし、
                # 後続の move/add ルールで最新担当者を使って再構築する。
                source_key = _time_owner_key(rule.source_area, rule.time_slot, time_slots, rule_map)

                # Ver.3.10.6.22:
                # 空き補充で直前時刻の担当者が変わった場合、休憩時刻そのもの（例: 12:00 Po）に
                # 旧担当者の物理セルが残っていることがある。
                # _time_owner_key() が縦連結の owner（例: 08:30）を返す場合でも、
                # 12:00 の実セルは別に存在し得るため、owner だけを見ていると旧担当が残る。
                # ここでは owner を壊さず、休憩時刻と同じ row_key の実セルだけを必ずクリアする。
                exact_key = rule.time_slot.key
                key = ("break_exact_source", rule.source_area_id, exact_key)
                if key not in cleared_targets:
                    AssignmentCell.objects.filter(
                        board=board, area=rule.source_area, row_key=exact_key
                    ).update(value="")
                    cleared_targets.add(key)

                # 休憩時刻自体が owner の場合は従来どおり同じセルをクリア。
                # （上の exact_key と同一なので二重更新にはならない。）
                if source_key == exact_key:
                    cleared_targets.add(("break_source", rule.source_area_id, source_key))
                continue
            target = rule.target_area
            if not target:
                continue

            representative_merge = HorizontalMergeRule.objects.filter(
                time_slot=rule.time_slot, representative_area=target, is_active=True
            ).first()
            if representative_merge:
                key = ("merge", representative_merge.id, rule.time_slot_id)
                if key in cleared_targets:
                    continue
                try:
                    i0 = ordered_ids.index(representative_merge.start_area_id)
                    i1 = ordered_ids.index(representative_merge.end_area_id)
                    lo, hi = sorted((i0, i1))
                    merge_area_ids = ordered_ids[lo:hi + 1]
                    AssignmentCell.objects.filter(
                        board=board, area_id__in=merge_area_ids, row_key=rule.time_slot.key
                    ).update(value="")
                except ValueError:
                    pass
                cleared_targets.add(key)
                # 後段の通常処理で同じ結合範囲を再クリアしないよう記録。
                cleared_virtual_merges.add((representative_merge.id, rule.time_slot_id))
            else:
                target_key = _time_owner_key(target, rule.time_slot, time_slots, rule_map)
                if not target_key:
                    continue
                key = ("target", target.id, target_key)
                if key in cleared_targets:
                    continue
                AssignmentCell.objects.filter(
                    board=board, area=target, row_key=target_key
                ).update(value="")
                cleared_targets.add(key)

        # 昼休憩欄も古い担当者を残さず、その時点の元配置から再構築する。
        if break_slot_ids:
            LunchBreakEntry.objects.filter(
                board=board, time_slot_id__in=break_slot_ids
            ).update(value="")

    for rule in derived_rules:
        source_key = _time_owner_key(rule.source_area, rule.time_slot, time_slots, rule_map)
        if not source_key:
            warnings.append(f"{rule}: 元配置の時間枠が『枠なし』です。")
            continue
        source_cells = list(
            AssignmentCell.objects.filter(
                board=board, area=rule.source_area, row_key=source_key
            ).exclude(value="").order_by("slot_index")
        )
        snap_key = (rule.source_area_id, rule.time_slot_id, source_key)
        names = list(source_snapshot.get(snap_key, []))
        if not names:
            continue

        if rule.action == "break":
            for name in names:
                _add_lunch_name(board, rule.time_slot, name)
            # その時間だけ元配置から外す。
            # 同じ時刻に「別配置 → この配置へ兼任」が先に適用されている場合、
            # source_cells には後から追加されたスタッフも含まれる。
            # 休憩ルールは「ルール適用前に元配置にいたスタッフ」だけを外し、
            # 兼任・移動で新しく入ったスタッフは残す。
            if source_key != rule.time_slot.key:
                warnings.append(f"{rule}: 連結セル内の休憩なので元配置は消していません。")
            else:
                snapshot_names = set(names)
                for cell in source_cells:
                    current = (cell.value or "").strip()
                    if not current:
                        continue
                    # 通常は1セル1名だが、「、」区切りの仮想セルにも対応。
                    current_names = [x.strip() for x in current.split("、") if x.strip()]
                    remaining = [x for x in current_names if x not in snapshot_names]
                    new_value = "、".join(remaining)
                    if new_value != current:
                        cell.value = new_value
                        cell.save(update_fields=["value"])
            continue

        target = rule.target_area
        if not target:
            warnings.append(f"{rule}: 移動・兼任先が未設定です。")
            continue
        # 横結合の代表配置が指定された場合、その代表セルを結合グループの保存先として扱う。
        representative_merge = HorizontalMergeRule.objects.filter(
            time_slot=rule.time_slot, representative_area=target, is_active=True
        ).first()

        # 横結合の代表配置は、その配置場所自身の縦連結ルールよりも横結合を優先する。
        # 例: 12:00 の「3番」が通常の時間枠設定では別時間の owner_key を持っていても、
        # 横結合セルの代表として指定されている場合は必ず 12:00 の専用セルへ保存する。
        if representative_merge:
            target_key = rule.time_slot.key
        else:
            target_key = _time_owner_key(target, rule.time_slot, time_slots, rule_map)

        if not target_key:
            warnings.append(f"{rule}: 移動先の時間枠が『枠なし』です。")
            continue

        # 横結合ルールの「代表配置」は通常の物理枠ではなく、結合セルの仮想保存先として扱う。
        # 自動除外された横結合範囲には古い12時データが残ることがあるため、
        # 代表配置へ移動する際は結合範囲のその時刻だけを一度クリアしてから代表へ保存する。
        if representative_merge:
            merge_key = (representative_merge.id, rule.time_slot_id)
            if merge_key not in cleared_virtual_merges:
                ordered_areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
                ordered_ids = [a.id for a in ordered_areas]
                try:
                    i0 = ordered_ids.index(representative_merge.start_area_id)
                    i1 = ordered_ids.index(representative_merge.end_area_id)
                    lo, hi = sorted((i0, i1))
                    merge_area_ids = ordered_ids[lo:hi + 1]
                    AssignmentCell.objects.filter(
                        board=board, area_id__in=merge_area_ids, row_key=rule.time_slot.key
                    ).update(value="")
                except ValueError:
                    pass
                cleared_virtual_merges.add(merge_key)

            # 代表配置は1つの仮想セルとして扱い、空き枠判定をせず上書きする。
            # 同じ代表へ複数名を入れる設定にも対応するため、代表配置のslot_countまでは順に使用する。
            for name_index, name in enumerate(names, start=1):
                slot_index = min(name_index, max(1, target.slot_count))
                cell, _ = AssignmentCell.objects.get_or_create(
                    board=board, area=target, row_key=target_key, slot_index=slot_index
                )
                if slot_index == target.slot_count and name_index > target.slot_count and cell.value.strip():
                    existing = [x.strip() for x in cell.value.split("、") if x.strip()]
                    if name not in existing:
                        existing.append(name)
                    cell.value = "、".join(existing)
                else:
                    cell.value = name
                cell.save(update_fields=["value"])
        else:
            for name in names:
                target_cells = list(
                    AssignmentCell.objects.filter(
                        board=board, area=target, row_key=target_key
                    ).order_by("slot_index")
                )
                target_by_slot = {c.slot_index: c for c in target_cells}
                placed = False
                for slot_index in range(1, target.slot_count + 1):
                    cell = target_by_slot.get(slot_index)
                    if cell and cell.value.strip() == name:
                        placed = True
                        break
                    if not cell or not cell.value.strip():
                        cell, _ = AssignmentCell.objects.get_or_create(
                            board=board, area=target, row_key=target_key, slot_index=slot_index
                        )
                        cell.value = name
                        cell.save(update_fields=["value"])
                        placed = True
                        break
                if not placed:
                    warnings.append(f"{rule}: {target.name} の空き枠がありません（{name}）。")

        if rule.action == "move" and source_key == rule.time_slot.key:
            for cell in source_cells:
                cell.value = ""
                cell.save(update_fields=["value"])
    return warnings


def _recalculate_unassigned(board):
    active_staff = list(Staff.objects.filter(is_active=True).order_by("display_order", "name"))
    unavailable_fields = (
        "night_shift", "night_shift_after", "holiday_day_shift",
        "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
        "annual_leave_1", "annual_leave_2", "reception_leave",
    )
    unavailable_text = "\n".join(str(getattr(board, f, "") or "") for f in unavailable_fields)
    unavailable = {s.name for s in active_staff if s.name and s.name in unavailable_text}
    unavailable.update(_full_day_absence_names(board.board_date))
    # Ver.3.10.6: 当日クイック追加の終日予定（会議・出張など）も「配置未定」から除外する。
    unavailable.update(
        entry["staff"].name
        for entry in _parse_quick_meta_absences(board)
        if entry.get("period") == "full"
    )
    normal_keys = set(TimeSlot.objects.filter(is_active=True, kind="time").values_list("key", flat=True))
    assigned = set(
        board.cells.filter(row_key__in=normal_keys).exclude(value="").values_list("value", flat=True)
    )
    assigned = {name.strip() for name in assigned if name and name.strip()}
    free_names = {s.name for s in active_staff if s.name and s.name in (board.free_text or "")}
    unassigned = [s.name for s in active_staff if s.name not in unavailable and s.name not in assigned and s.name not in free_names]
    board.unassigned = "、".join(unassigned)
    board.save(update_fields=["unassigned", "updated_at"])
    return unassigned




def _sync_calendar_to_board(board):
    """月間勤務カレンダーを当日配置表の勤務欄へ同期する。"""
    duty_date = board.board_date
    cal = DutyCalendarDay.objects.select_related("night_shift", "holiday_day_shift").filter(duty_date=duty_date).first()
    prev = DutyCalendarDay.objects.select_related("night_shift").filter(duty_date=duty_date - timedelta(days=1)).first()
    comp_names = list(CompensatoryLeaveRecord.objects.filter(target_date=duty_date).order_by("source_date", "duty_type").values_list("staff_name", flat=True))
    updates = {
        "night_shift": cal.night_shift.name if cal and cal.night_shift else "",
        "holiday_day_shift": cal.holiday_day_shift.name if cal and cal.holiday_day_shift else "",
        "night_shift_after": prev.night_shift.name if prev and prev.night_shift else "",
        "compensatory_leave_1": comp_names[0] if len(comp_names) > 0 else "",
        "compensatory_leave_2": comp_names[1] if len(comp_names) > 1 else "",
        "compensatory_leave_3": comp_names[2] if len(comp_names) > 2 else "",
    }
    changed = []
    for field, value in updates.items():
        if getattr(board, field) != value:
            setattr(board, field, value)
            changed.append(field)
    if changed:
        board.save(update_fields=changed + ["updated_at"])
    return board


def _absence_covers_row(absence, row_key):
    """不在予定が指定時間行を覆うか。"""
    if absence.period == "full":
        return True
    # 現行時間枠は HHMM キーを前提。特殊行は対象外。
    if not row_key.isdigit():
        return False
    minute = int(row_key[:2]) * 60 + int(row_key[2:])
    if absence.period == "am":
        return minute < 12 * 60
    if absence.period == "pm":
        return minute >= 12 * 60
    if absence.period == "custom" and absence.start_slot and absence.end_slot:
        order = {x.id: i for i, x in enumerate(TimeSlot.objects.filter(is_active=True).order_by("display_order", "id"))}
        cur = TimeSlot.objects.filter(key=row_key).first()
        if not cur:
            return False
        a, b, c = order.get(absence.start_slot_id), order.get(absence.end_slot_id), order.get(cur.id)
        if a is None or b is None or c is None:
            return False
        lo, hi = sorted((a, b))
        return lo <= c <= hi
    return False


def _full_day_absence_names(board_date):
    return set(WeeklyAbsence.objects.filter(absence_date=board_date, period="full", staff__is_active=True).values_list("staff__name", flat=True))


def _sync_absence_text_to_board(board):
    """週間不在予定を当日表の対応欄へ反映する。"""
    absences = list(WeeklyAbsence.objects.filter(absence_date=board.board_date).select_related("staff", "start_slot", "end_slot"))
    annual, reception, other = [], [], []
    for a in absences:
        period = a.period_label
        label = a.staff.name if a.period == "full" else f"{a.staff.name}（{period}）"
        if a.kind == "annual":
            annual.append(label)
        elif a.kind == "reception":
            reception.append(label)
        else:
            other.append(f"{a.get_kind_display()}：{label}" + (f"［{a.note}］" if a.note else ""))

    # Ver.3.10.6.10: 週間予定ブロックを「開始・終了」の不可視マーカーで囲む。
    # Ver.3.10.6.7 では開始マーカー以降をすべて週間予定として扱っていたため、
    # その後にクイック追加した年休（例: 橋本 1日）が再表示時に消えることがあった。
    # 今回は週間予定だけを明示的なブロックとして置換し、前後の手動入力は必ず残す。
    marker_start = "\u2063"  # INVISIBLE SEPARATOR
    marker_end = "\u2064"    # INVISIBLE PLUS
    legacy_marker = "【週間予定】"
    changed = []

    def merged(old, values):
        old_text = old or ""
        base = old_text

        # 新形式: 開始～終了マーカーの範囲だけを取り除く。
        start = base.find(marker_start)
        if start >= 0:
            end = base.find(marker_end, start + len(marker_start))
            if end >= 0:
                before = base[:start].rstrip("\n")
                after = base[end + len(marker_end):].lstrip("\n")
                base = "\n".join(x for x in (before, after) if x).rstrip()
            else:
                # Ver.3.10.6.7 の単一不可視マーカーからの移行。
                # 旧週間予定はマーカーと同じ行にあり、後から追加した手動入力は改行後にある。
                tail = base[start + len(marker_start):]
                newline = tail.find("\n")
                before = base[:start].rstrip("\n")
                after = tail[newline + 1:].lstrip("\n") if newline >= 0 else ""
                base = "\n".join(x for x in (before, after) if x).rstrip()
        else:
            # さらに古い表示マーカー形式も同様に移行する。
            start = base.find(legacy_marker)
            if start >= 0:
                tail = base[start + len(legacy_marker):]
                newline = tail.find("\n")
                before = base[:start].rstrip("\n")
                after = tail[newline + 1:].lstrip("\n") if newline >= 0 else ""
                base = "\n".join(x for x in (before, after) if x).rstrip()

        weekly_text = "、".join(values)
        if not weekly_text:
            return base
        weekly_block = marker_start + weekly_text + marker_end
        return base + (("\n" if base else "") + weekly_block)

    annual_text = merged(board.annual_leave_1, annual)
    reception_text = merged(board.reception_leave, reception)
    conf_text = merged(board.conference, other)
    for field, value in (("annual_leave_1", annual_text), ("reception_leave", reception_text), ("conference", conf_text)):
        if getattr(board, field) != value:
            setattr(board, field, value); changed.append(field)
    if changed:
        board.save(update_fields=changed + ["updated_at"])


def _apply_weekly_absences_to_existing_board(board):
    """自動配置対象OFFのスタッフも含め、登録済みセルから週間不在を確実に外す。"""
    absences = list(WeeklyAbsence.objects.filter(absence_date=board.board_date).select_related("staff", "start_slot", "end_slot"))
    changed = 0
    for a in absences:
        qs = board.cells.filter(value=a.staff.name)
        for cell in qs:
            if a.period == "full" or _absence_covers_row(a, cell.row_key):
                cell.value = ""
                cell.save(update_fields=["value"])
                changed += 1
    return changed


def _main_rotation_mapping(board_date):
    """週単位で左へ1つずつずらす主担当配置を返す {area_id: staff}。"""
    areas = list(AssignmentArea.objects.filter(is_active=True, main_rotation_enabled=True).order_by("main_rotation_order", "display_order", "id"))
    if not areas:
        return {}
    mapping = {}
    dedicated_staff_ids = set()
    for area in areas:
        dedicated = Staff.objects.filter(is_active=True, dedicated_area=area).order_by("display_order", "name").first()
        if dedicated:
            mapping[area.id] = dedicated
            dedicated_staff_ids.add(dedicated.id)
    rotating_areas = [a for a in areas if a.id not in mapping]
    staffs = list(Staff.objects.filter(is_active=True, main_rotation_enabled=True).exclude(id__in=dedicated_staff_ids).order_by("main_rotation_order", "display_order", "name"))
    if not rotating_areas or not staffs:
        return mapping
    anchor = date(2026, 8, 10)  # 月曜日。週ごとに左へ1つ。
    monday = board_date - timedelta(days=board_date.weekday())
    auto_week_index = ((monday - anchor).days // 7)
    override = MainRotationOverride.objects.filter(week_start=monday).first()
    manual_offset = override.offset if override else 0
    week_index = (auto_week_index + manual_offset) % len(staffs)
    for i, area in enumerate(rotating_areas):
        mapping[area.id] = staffs[(i + week_index) % len(staffs)]
    return mapping


def _sync_main_row(board):
    """主担当行を必要な差分だけ同期する。

    SQLiteでは配置表表示(GET)のたびに全削除→再作成すると、別リクエストと
    競合して database is locked が起きやすい。値が変わったセルだけ更新し、
    不要になったセルだけ削除する。
    """
    mapping = _main_rotation_mapping(board.board_date)
    enabled_ids = list(
        AssignmentArea.objects.filter(main_rotation_enabled=True)
        .values_list("id", flat=True)
    )
    if not enabled_ids:
        return mapping

    existing = {
        cell.area_id: cell
        for cell in AssignmentCell.objects.filter(
            board=board, row_key="main", area_id__in=enabled_ids, slot_index=1
        )
    }

    # ローテーション対象から外れた／割当がなくなった場所だけ削除。
    remove_ids = [area_id for area_id in existing if area_id not in mapping]
    if remove_ids:
        AssignmentCell.objects.filter(
            board=board, row_key="main", area_id__in=remove_ids, slot_index=1
        ).delete()

    # 同じ値ならDBへ書き込まない。変更があるセルだけ更新／作成する。
    for area_id, staff in mapping.items():
        value = staff.name
        cell = existing.get(area_id)
        if cell is None:
            AssignmentCell.objects.create(
                board=board, area_id=area_id, row_key="main",
                slot_index=1, value=value
            )
        elif cell.value != value:
            cell.value = value
            cell.save(update_fields=["value"])
    return mapping


def _apply_partial_absences(board):
    """半日・時間指定の不在を、実セルの時刻に対して反映する。"""
    absences = list(
        WeeklyAbsence.objects.filter(absence_date=board.board_date)
        .exclude(period="full")
        .select_related("staff", "start_slot", "end_slot")
    )
    warnings = []
    free_names = [x.strip() for x in re.split(r"[、,\n]+", board.free_text or "") if x.strip()]

    def covers_actual_row(absence, row_key):
        # main等の特殊行は部分休の対象外。
        if not row_key or not row_key.isdigit():
            return False
        minute = int(row_key[:2]) * 60 + int(row_key[2:])
        if absence.period == "am":
            return minute < 12 * 60
        if absence.period == "pm":
            return minute >= 12 * 60
        if absence.period == "custom" and absence.start_slot and absence.end_slot:
            slots = list(TimeSlot.objects.filter(is_active=True).order_by("display_order", "id"))
            order = {slot.id: i for i, slot in enumerate(slots)}
            current = next((slot for slot in slots if slot.key == row_key), None)
            if not current:
                return False
            a = order.get(absence.start_slot_id)
            b = order.get(absence.end_slot_id)
            c = order.get(current.id)
            if a is None or b is None or c is None:
                return False
            lo, hi = sorted((a, b))
            return lo <= c <= hi
        return False

    for absence in absences:
        staff_cells = list(
            board.cells.filter(value=absence.staff.name)
            .exclude(row_key="main")
            .select_related("area")
        )
        affected = [
            cell for cell in staff_cells
            if covers_actual_row(absence, cell.row_key)
        ]
        if not affected:
            continue

        replacement = None
        for name in list(free_names):
            st = (
                Staff.objects.filter(
                    name=name,
                    is_active=True,
                    auto_assignment_enabled=True,
                )
                .select_related("dedicated_area")
                .first()
            )
            if not st:
                continue
            st_absences = list(
                WeeklyAbsence.objects.filter(
                    absence_date=board.board_date,
                    staff=st,
                ).select_related("start_slot", "end_slot")
            )
            if not all(_can_staff_cover_area(st, cell.area) for cell in affected):
                continue
            if not all(_staff_available_for_row(st, cell.row_key) for cell in affected):
                continue
            if any(
                covers_actual_row(st_absence, cell.row_key)
                for st_absence in st_absences
                for cell in affected
            ):
                continue
            replacement = st
            break

        for cell in affected:
            cell.value = replacement.name if replacement else ""
            cell.save(update_fields=["value"])

        if replacement:
            free_names.remove(replacement.name)
            StaffFreeHistory.objects.filter(
                staff=replacement,
                board_date=board.board_date,
            ).delete()

            # v3.10.6.61:
            # 半日休の補充に入ったFreeスタッフは、反対側の半日が未配置なら
            # 「池田（午後）」のようにFree欄へ残す。
            # これは表示用のみで、終日Free履歴には数えない。
            if absence.period in ("am", "pm"):
                if absence.period == "am":
                    other_half_label = "午後"
                    other_half_assigned = board.cells.filter(
                        value=replacement.name,
                        row_key__in=["1200", "1300", "1400", "1500", "1600"],
                    ).exists()
                else:
                    other_half_label = "午前"
                    other_half_assigned = board.cells.filter(
                        value=replacement.name,
                        row_key__in=["0730", "0830", "0900", "1000", "1100"],
                    ).exists()

                partial_free_label = f"{replacement.name}（{other_half_label}）"
                if not other_half_assigned and partial_free_label not in free_names:
                    free_names.append(partial_free_label)

            warnings.append(
                f"{absence.staff.name}さんの{absence.get_kind_display()}（{absence.get_period_display()}）を反映し、"
                f"{replacement.name}さんをFreeから補充しました。"
            )
        else:
            warnings.append(
                f"{absence.staff.name}さんの{absence.get_kind_display()}（{absence.get_period_display()}）を反映しました。"
                "補充できない時間枠は空欄です。"
            )

    board.free_text = "、".join(free_names)
    board.save(update_fields=["free_text", "updated_at"])
    return warnings


@require_POST
@transaction.atomic
def auto_assignment(request):
    board_date = _parse_date(request.POST.get("date"))
    rebuild = request.POST.get("rebuild") == "1"
    board, _ = DailyBoard.objects.select_for_update().get_or_create(board_date=board_date)
    _sync_calendar_to_board(board)
    _sync_absence_text_to_board(board)
    main_mapping = _sync_main_row(board)

    # v3.10.6.15: 配置自動作成も「一つ戻る」の対象にする。
    # 実際に処理が成功したときだけ末尾でセッションへ登録する。
    undo_snapshot = _build_board_undo_snapshot(board, "配置自動作成")

    # v3.10.6.50:
    # 土日祝・手動休日・臨時休業日は「休日日勤」の日なので、
    # 平日用の通常配置は作成しない。
    # 週間自動作成から rebuild=1 で呼ばれた場合、以前の通常配置が残っていても消去する。
    calendar_day = DutyCalendarDay.objects.filter(duty_date=board_date).first()
    if _calendar_is_holiday_duty(board_date, calendar_day):
        normal_keys = [slot.key for slot in _active_time_slots(include_special=False)]
        if rebuild:
            board.cells.filter(row_key__in=normal_keys).delete()
            board.lunch_break_entries.all().delete()

        # 休日には自動Free履歴を持ち越さない。手入力Freeだけは壊さない。
        previous_auto_free = list(
            StaffFreeHistory.objects.filter(board_date=board_date).select_related("staff")
        )
        previous_names = {x.staff.name for x in previous_auto_free}
        StaffFreeHistory.objects.filter(board_date=board_date).delete()
        manual_free = [
            x.strip()
            for x in re.split(r"[、,\n]+", board.free_text or "")
            if x.strip() and x.strip() not in previous_names
        ]
        board.free_text = "、".join(manual_free)
        _recalculate_unassigned(board)
        board.save(update_fields=["free_text", "updated_at"])
        board.refresh_from_db()

        cells_payload = [
            {"area_id": c.area_id, "row_key": c.row_key, "slot_index": c.slot_index, "value": c.value}
            for c in board.cells.filter(row_key__in=_dynamic_row_keys())
        ]
        lunch_payload = [
            {"row_key": e.time_slot.key, "value": e.value}
            for e in board.lunch_break_entries.select_related("time_slot").all()
        ]

        _arm_board_undo(request, undo_snapshot, board)
        return JsonResponse({
            "ok": True,
            "cells": cells_payload,
            "lunch_entries": lunch_payload,
            "unassigned": board.unassigned,
            "updated_at": board.updated_at.isoformat(),
            "assigned_count": 0,
            "warnings": ["休日のため通常配置は作成していません。休日日勤・夜勤のみ勤務表の設定を使用します。"],
            "message": "休日のため通常配置を作成しませんでした。",
        })

    all_areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    scheduled_enabled_ids = set(
        DailyAreaActivation.objects.filter(board=board, is_enabled=True)
        .values_list("area_id", flat=True)
    )
    areas = [
        area for area in all_areas
        if area.auto_assignment_count > 0
        and (
            area.auto_assignment_mode == "daily"
            or (area.auto_assignment_mode == "scheduled" and area.id in scheduled_enabled_ids)
        )
    ]
    staffs = list(
        Staff.objects.filter(is_active=True, auto_assignment_enabled=True)
        .select_related("dedicated_area", "preferred_area_1", "preferred_area_2", "preferred_area_3", "preferred_area_4")
        .prefetch_related("avoid_area_entries", "weekly_targets")
        .order_by("display_order", "name")
    )
    for staff in staffs:
        staff._avoid_area_ids = {x.area_id for x in staff.avoid_area_entries.all()}
        staff._weekly_target_map = {x.area_id: x.target_count for x in staff.weekly_targets.all()}
        staff._backup_priority_map = {x.area_id: x.priority for x in DedicatedAreaBackup.objects.filter(staff=staff)}
        staff._main_area_ids = {area_id for area_id, owner in main_mapping.items() if owner.id == staff.id}
    if not staffs:
        return JsonResponse({"ok": False, "error": "通常配置の自動作成対象スタッフが登録されていません。"}, status=400)
    if not areas:
        return JsonResponse({"ok": False, "error": "本日の自動配置対象となる配置場所がありません。"}, status=400)

    time_slots = _active_time_slots(include_special=False)
    normal_keys = [slot.key for slot in time_slots]
    rule_map = _time_rule_map(areas, time_slots)

    if rebuild:
        board.cells.filter(row_key__in=normal_keys).delete()
        board.lunch_break_entries.all().delete()

    # unassigned は再計算するため除外条件には含めない。
    unavailable_fields = (
        "night_shift", "night_shift_after", "holiday_day_shift",
        "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
        "annual_leave_2",
    )
    unavailable_text = "\n".join(str(getattr(board, f, "") or "") for f in unavailable_fields)

    # v3.10.6.51:
    # 週間予定から同期されたAM休/PM休を、名前が年休欄にあるという理由だけで
    # 「終日不在」にしない。終日不在は WeeklyAbsence(period=full) / quick-meta(full)
    # で別途確実に判定する。
    partial_absence_names = set(
        WeeklyAbsence.objects.filter(
            absence_date=board_date,
            staff__is_active=True,
        ).exclude(period="full").values_list("staff__name", flat=True)
    )
    partial_absence_names.update(
        e["staff"].name
        for e in _parse_quick_meta_absences(board)
        if e["period"] != "full"
    )

    unavailable = {
        st.name
        for st in Staff.objects.filter(is_active=True)
        if st.name and st.name in unavailable_text and st.name not in partial_absence_names
    }
    unavailable.update(_full_day_absence_names(board_date))
    unavailable.update(e["staff"].name for e in _parse_quick_meta_absences(board) if e["period"] == "full")
    # 専任代理は専任本人が終日配置できない日にだけ強く優先する。
    for area in areas:
        dedicated_names = set(Staff.objects.filter(is_active=True, dedicated_area=area).values_list("name", flat=True))
        if dedicated_names and not dedicated_names.issubset(unavailable):
            for staff in staffs:
                getattr(staff, "_backup_priority_map", {}).pop(area.id, None)

    # v3.10.6.16: 既存表へ「予定時のみ」を後からONにした場合の最小再配置。
    # 複数の予定配置が同時ONでも、
    #   1) 配置場所の優先度（1が最優先）
    #   2) 同優先度では最低必要人数を全場所で先に確保
    #   3) 配置可能スタッフが少ない場所を先に確保
    #   4) スタッフ側の優先①〜④・週回数等の適性
    #   5) 既存配置の変更数を最小化
    # の順で処理する。すでに予定配置へ確保したスタッフは、後続の予定配置から奪わない。
    if not rebuild and scheduled_enabled_ids:
        rebalance_recent, rebalance_weekly = _assignment_history(board_date, staffs, areas)
        rebalance_rng = random.Random(board_date.toordinal() + 610616)
        staff_by_name = {s.name: s for s in staffs}
        ordered_area_ids = [a.id for a in all_areas]

        def area_slot_primary(area, slot_index):
            values = [
                (v or "").strip()
                for v in board.cells.filter(
                    area=area, slot_index=slot_index, row_key__in=normal_keys
                ).values_list("value", flat=True)
                if (v or "").strip()
            ]
            if not values:
                return ""
            counts = defaultdict(int)
            for name in values:
                counts[name] += 1
            return sorted(counts, key=lambda n: (-counts[n], n))[0]

        def staff_can_take_area(staff, area):
            if staff.name in unavailable:
                return False
            if _slot_score(staff, area, rebalance_recent, rebalance_weekly, rebalance_rng) is None:
                return False
            layout = _area_row_layout(area, time_slots, rule_map)
            for item in layout:
                if not item["show"] or item["hidden"] or not item["owner_key"]:
                    continue
                if item["owner_key"] == "0730" and not (
                    "治療" in (area.category or "") or "治療" in area.name
                ):
                    continue
                area_time_rule = AreaTimeRule.objects.filter(
                    area=area, time_slot=item["slot"]
                ).first()
                if area_time_rule and not area_time_rule.auto_assignment_enabled:
                    continue
                skip_for_merge = False
                for merge in HorizontalMergeRule.objects.filter(
                    time_slot=item["slot"], is_active=True, exclude_from_auto_assignment=True
                ).select_related("start_area", "end_area"):
                    try:
                        a0 = ordered_area_ids.index(merge.start_area_id)
                        a1 = ordered_area_ids.index(merge.end_area_id)
                        lo, hi = sorted((a0, a1))
                        if area.id in ordered_area_ids[lo:hi + 1]:
                            skip_for_merge = True
                            break
                    except ValueError:
                        continue
                if skip_for_merge:
                    continue
                if _staff_available_for_row(staff, item["owner_key"]):
                    return True
            return False

        def write_empty_area_slot(area, slot_index, staff):
            """対象枠の空欄だけへ書き込み、実際に変更したセルIDを返す。"""
            layout = _area_row_layout(area, time_slots, rule_map)
            written_ids = []
            for item in layout:
                if not item["show"] or item["hidden"] or not item["owner_key"]:
                    continue
                if item["owner_key"] == "0730" and not (
                    "治療" in (area.category or "") or "治療" in area.name
                ):
                    continue
                area_time_rule = AreaTimeRule.objects.filter(area=area, time_slot=item["slot"]).first()
                if area_time_rule and not area_time_rule.auto_assignment_enabled:
                    continue
                skip_for_merge = False
                for merge in HorizontalMergeRule.objects.filter(
                    time_slot=item["slot"], is_active=True, exclude_from_auto_assignment=True
                ).select_related("start_area", "end_area"):
                    try:
                        a0 = ordered_area_ids.index(merge.start_area_id)
                        a1 = ordered_area_ids.index(merge.end_area_id)
                        lo, hi = sorted((a0, a1))
                        if area.id in ordered_area_ids[lo:hi + 1]:
                            skip_for_merge = True
                            break
                    except ValueError:
                        continue
                if skip_for_merge or not _staff_available_for_row(staff, item["owner_key"]):
                    continue
                cell, _ = AssignmentCell.objects.get_or_create(
                    board=board, area=area, row_key=item["owner_key"], slot_index=slot_index
                )
                if not (cell.value or "").strip():
                    cell.value = staff.name
                    cell.save(update_fields=["value"])
                    written_ids.append(cell.id)
            return written_ids

        def replace_source_staff(area, slot_index, before_name, after_name):
            cells = list(board.cells.filter(
                area=area, slot_index=slot_index, row_key__in=normal_keys, value=before_name
            ))
            if not cells:
                return []
            changed = []
            for cell in cells:
                changed.append((cell.id, cell.value))
                cell.value = after_name
                cell.save(update_fields=["value"])
            return changed

        def restore_cells(changed):
            for cell_id, old_value in changed:
                AssignmentCell.objects.filter(pk=cell_id).update(value=old_value)

        currently_assigned_names = {
            (v or "").strip()
            for v in board.cells.filter(row_key__in=normal_keys).values_list("value", flat=True)
            if (v or "").strip()
        }
        idle_staff = [
            s for s in staffs
            if s.name not in unavailable and s.name not in currently_assigned_names
        ]

        scheduled_areas = [
            a for a in areas
            if a.auto_assignment_mode == "scheduled" and a.id in scheduled_enabled_ids
        ]

        eligible_count = {
            a.id: sum(1 for s in staffs if staff_can_take_area(s, a))
            for a in scheduled_areas
        }

        # すでに予定配置へ入っている人は最初から保護する。
        reserved_staff_ids = set()
        for a in scheduled_areas:
            for idx in range(1, a.slot_count + 1):
                name = area_slot_primary(a, idx)
                st = staff_by_name.get(name)
                if st:
                    reserved_staff_ids.add(st.id)

        def target_filled_count(area):
            return sum(
                1 for idx in range(1, area.slot_count + 1)
                if area_slot_primary(area, idx)
            )

        def best_direct_options(target_area):
            options = []
            for idle in idle_staff:
                if idle.id in reserved_staff_ids or not staff_can_take_area(idle, target_area):
                    continue
                score = _slot_score(idle, target_area, rebalance_recent, rebalance_weekly, rebalance_rng)
                options.append((score, idle))
            options.sort(key=lambda x: (
                -x[0], x[1].assignment_staff_priority, x[1].display_order, x[1].name
            ))
            return options

        def best_donor_options(target_area):
            options = []
            seen_donors = set()
            for source_area in areas:
                if source_area.id == target_area.id:
                    continue
                # より高優先の配置は崩さない。同優先の予定配置も互いに奪い合わない。
                if source_area.assignment_priority < target_area.assignment_priority:
                    continue
                if (
                    source_area.auto_assignment_mode == "scheduled"
                    and source_area.id in scheduled_enabled_ids
                    and source_area.assignment_priority <= target_area.assignment_priority
                ):
                    continue
                for source_slot in range(1, source_area.slot_count + 1):
                    donor_name = area_slot_primary(source_area, source_slot)
                    donor = staff_by_name.get(donor_name)
                    if not donor or donor.id in seen_donors or donor.id in reserved_staff_ids:
                        continue
                    seen_donors.add(donor.id)
                    if not staff_can_take_area(donor, target_area):
                        continue

                    donor_score = _slot_score(
                        donor, target_area, rebalance_recent, rebalance_weekly, rebalance_rng
                    )
                    source_filled = target_filled_count(source_area)
                    can_leave_vacant = max(0, source_filled - 1) >= source_area.minimum_assignment_count
                    # v3.10.6.17: 配置場所の優先度を「実際に効く」ルールへ。
                    # 予定先の方が高優先（数値が小さい）なら、移動元が最低必要人数を
                    # 下回っても、その人を高優先配置へ引き上げることを許可する。
                    # 低優先側の不足は、この後の通常補充で可能な範囲を埋め、
                    # 人員自体が足りなければ低優先側を不足として残す。
                    source_is_lower_priority = (
                        source_area.assignment_priority > target_area.assignment_priority
                    )

                    backfills = []
                    for idle in idle_staff:
                        if idle.id in reserved_staff_ids or idle.id == donor.id:
                            continue
                        if not staff_can_take_area(idle, source_area):
                            continue
                        backfill_score = _slot_score(
                            idle, source_area, rebalance_recent, rebalance_weekly, rebalance_rng
                        )
                        backfills.append((backfill_score, idle))
                    backfills.sort(key=lambda x: (
                        -x[0], x[1].assignment_staff_priority, x[1].display_order, x[1].name
                    ))
                    # 同優先・高優先の移動元は従来どおり最低人数を守る。
                    # ただし「低優先 → 高優先」の引き上げだけは、補充不能でも許可する。
                    if not backfills and not can_leave_vacant and not source_is_lower_priority:
                        continue
                    filler = backfills[0][1] if backfills else None
                    options.append((
                        donor_score,
                        source_area.assignment_priority,
                        0 if filler else 1,  # 同程度なら空欄を作らない案を優先
                        donor, source_area, source_slot, filler,
                    ))
            # 配置場所の優先度差をスタッフ適性より先に評価する。
            # 例: target=1 なら、priority 5→4→3→2 の順で引き上げ候補を優先。
            # 同じ移動元優先度の中で、優先①〜④・週回数などの適性を比較する。
            options.sort(key=lambda x: (
                -x[1], -x[0], x[2],
                x[3].assignment_staff_priority, x[3].display_order, x[3].name,
            ))
            return options

        def fill_target_to_count(target_area, target_count):
            nonlocal_idle = None  # 可読性用ダミー。idle_staff自体は外側リストを更新する。
            while target_filled_count(target_area) < min(target_count, target_area.slot_count):
                occupied = {
                    idx for idx in range(1, target_area.slot_count + 1)
                    if area_slot_primary(target_area, idx)
                }
                empty_slots = [idx for idx in range(1, target_area.slot_count + 1) if idx not in occupied]
                if not empty_slots:
                    break
                target_slot = empty_slots[0]

                direct_options = best_direct_options(target_area)
                donor_options = best_donor_options(target_area)
                best_direct_score = direct_options[0][0] if direct_options else None
                chosen_chain = None
                if donor_options:
                    top = donor_options[0]
                    # 既配置者の適性が高い、または低優先配置から引き上げられるなら玉突き。
                    if (
                        best_direct_score is None
                        # 高優先の予定配置は、低優先配置から引き上げ可能なら
                        # Free直入れよりも配置優先度に沿った玉突きを優先する。
                        or top[4].assignment_priority > target_area.assignment_priority
                        or top[0] > best_direct_score + 5
                    ):
                        chosen_chain = top

                if chosen_chain:
                    _score, _src_priority, _vacant_rank, donor, source_area, source_slot, filler = chosen_chain
                    # 先に予定先へ書き込み、成功した場合だけ移動元を変更する。
                    written = write_empty_area_slot(target_area, target_slot, donor)
                    if written:
                        source_changes = replace_source_staff(
                            source_area, source_slot, donor.name, filler.name if filler else ""
                        )
                        if source_changes:
                            reserved_staff_ids.add(donor.id)
                            if filler:
                                idle_staff[:] = [s for s in idle_staff if s.id != filler.id]
                            continue
                        # 移動元が変えられなかった場合は予定先への仮書き込みを戻す。
                        AssignmentCell.objects.filter(id__in=written).update(value="")

                if direct_options:
                    direct = direct_options[0][1]
                    written = write_empty_area_slot(target_area, target_slot, direct)
                    if written:
                        reserved_staff_ids.add(direct.id)
                        idle_staff[:] = [s for s in idle_staff if s.id != direct.id]
                        continue
                break

        # 配置優先度ごとに処理し、同優先度では「最低必要人数」を全場所で先に確保。
        for priority in sorted({a.assignment_priority for a in scheduled_areas}):
            group = [a for a in scheduled_areas if a.assignment_priority == priority]
            group.sort(key=lambda a: (eligible_count.get(a.id, 10**6), a.display_order, a.id))

            # 第1段階: 最低必要人数
            for target_area in group:
                minimum_target = min(target_area.minimum_assignment_count, target_area.auto_assignment_count)
                fill_target_to_count(target_area, minimum_target)

            # 第2段階: 通常の自動配置人数まで
            for target_area in group:
                fill_target_to_count(target_area, target_area.auto_assignment_count)

    existing_cells = list(board.cells.filter(row_key__in=normal_keys).exclude(value=""))
    already_assigned = {cell.value.strip() for cell in existing_cells if cell.value.strip()}
    candidates = [s for s in staffs if s.name not in unavailable and (rebuild or s.name not in already_assigned)]

    occupied_slots = {(c.area_id, c.slot_index) for c in existing_cells} if not rebuild else set()

    # 人員不足時は「最低必要人数」→「通常自動配置人数」の順に枠を作る。
    # 同じ段階では assignment_priority が小さい場所を先に確保する。
    minimum_slots = []
    normal_slots = []
    for area in sorted(areas, key=lambda a: (a.assignment_priority, a.display_order, a.id)):
        occupied_for_area = {idx for aid, idx in occupied_slots if aid == area.id}
        empty = [idx for idx in range(1, area.slot_count + 1) if (area.id, idx) not in occupied_slots]
        minimum_needed = max(min(area.minimum_assignment_count, area.auto_assignment_count) - len(occupied_for_area), 0)
        normal_needed = max(area.auto_assignment_count - len(occupied_for_area) - minimum_needed, 0)
        minimum_slots.extend((area, idx) for idx in empty[:minimum_needed])
        normal_slots.extend((area, idx) for idx in empty[minimum_needed:minimum_needed + normal_needed])

    recent_counts, weekly_counts = _assignment_history(board_date, candidates, areas)
    last_free_map = {}
    for staff in candidates:
        last_free_map[staff.id] = (
            staff.free_history.filter(board_date__lt=board_date)
            .order_by("-board_date").values_list("board_date", flat=True).first()
        )
    rng = random.Random(board_date.toordinal())
    assignments = []

    # v3.10.6.53:
    # AM休 / PM休 / 時間指定休のスタッフは「終日Free」にしない。
    # まず通常配置枠へ確保し、その後に休み時間帯だけ外す。
    partial_absence_staff_ids = set(
        WeeklyAbsence.objects.filter(
            absence_date=board_date,
            staff__is_active=True,
        ).exclude(period="full").values_list("staff_id", flat=True)
    )

    def staff_priority(staff):
        deficit = 0
        for area_id, target in getattr(staff, "_weekly_target_map", {}).items():
            done = weekly_counts.get((staff.name, area_id), 0)
            deficit += max(target - done, 0)
        has_dedicated = int(bool(staff.dedicated_area_id))
        has_preference = int(any([staff.preferred_area_1_id, staff.preferred_area_2_id, staff.preferred_area_3_id, staff.preferred_area_4_id]))

        # 専任本人が終日不在で有効になっている「専任代理」は、
        # Free優先など通常のスタッフ配置優先度より先に候補へ回す。
        backup_priorities = list(getattr(staff, "_backup_priority_map", {}).values())
        active_backup_rank = min(backup_priorities) if backup_priorities else 9999
        is_active_backup = int(bool(backup_priorities))

        # v3.10.6.48:
        # 週回数目標はこの一般候補順ではなく、後段の「週回数予約」で先に確保する。
        # そのため一般配置ではスタッフ配置優先度を維持し、Free優先(4)は通常どおり後ろへ回す。
        # 同じ配置優先度の中では、まだ週回数未達の人をわずかに先にする。
        last_free = last_free_map.get(staff.id)
        last_free_rank = -(last_free.toordinal() if last_free else 0)
        has_partial_absence = int(staff.id in partial_absence_staff_ids)
        return (
            -has_dedicated,
            -is_active_backup,
            active_backup_rank,
            -has_partial_absence,
            staff.assignment_staff_priority,
            -int(deficit > 0),
            -deficit,
            last_free_rank,
            -has_preference,
            staff.display_order,
            staff.name,
        )

    ordered_candidates = sorted(candidates, key=staff_priority)

    def fill_slots(slot_pool, available_staff):
        remaining_slots = list(slot_pool)
        used_staff = set()
        for staff in available_staff:
            if not remaining_slots:
                break
            best = None
            for area, slot_index in remaining_slots:
                score = _slot_score(staff, area, recent_counts, weekly_counts, rng)
                if score is None:
                    continue
                # 場所の優先度を大きな重みで反映。1が最優先。
                score += (6 - area.assignment_priority) * 1000
                if best is None or score > best[0]:
                    best = (score, area, slot_index)
            if best is None:
                continue
            _, area, slot_index = best
            remaining_slots.remove((area, slot_index))
            assignments.append((staff, area, slot_index))
            used_staff.add(staff.id)
            recent_counts[(staff.name, area.id)] = recent_counts.get((staff.name, area.id), 0) + 1
            weekly_counts[(staff.name, area.id)] = weekly_counts.get((staff.name, area.id), 0) + 1
        return [s for s in available_staff if s.id not in used_staff], remaining_slots

    # v3.10.6.37: 専任本人が終日不在なら、通常の候補選定より前に
    # 設定済みの専任代理をその専任配置へ明示的に確保する。
    # これにより Free優先・配置場所優先度・希望配置などが代理より先に勝つのを防ぐ。
    remaining_staff = list(ordered_candidates)

    # v3.10.6.49: 専任本人を週回数予約・通常配置より前に明示的に確保する。
    # v3.10.6.48で週回数を先取りするようになったため、専任本人が並び順だけでは
    # 自分の専任配置を他スタッフに取られるケースがあった。
    def reserve_dedicated_staff(slot_pool):
        reserved = []
        for area in sorted(areas, key=lambda a: (a.assignment_priority, a.display_order, a.id)):
            dedicated_candidates = [
                staff for staff in remaining_staff
                if staff.dedicated_area_id == area.id
            ]
            if not dedicated_candidates:
                continue

            dedicated_candidates.sort(key=lambda staff: (
                staff.assignment_staff_priority,
                staff.display_order,
                staff.name,
            ))

            while dedicated_candidates:
                target_pos = next(
                    (i for i, pair in enumerate(slot_pool) if pair[0].id == area.id),
                    None,
                )
                if target_pos is None:
                    break

                chosen = None
                for staff in dedicated_candidates:
                    if _slot_score(staff, area, recent_counts, weekly_counts, rng) is not None:
                        chosen = staff
                        break
                if chosen is None:
                    break

                _target_area, slot_index = slot_pool.pop(target_pos)
                assignments.append((chosen, area, slot_index))
                remaining_staff[:] = [s for s in remaining_staff if s.id != chosen.id]
                dedicated_candidates = [s for s in dedicated_candidates if s.id != chosen.id]
                recent_counts[(chosen.name, area.id)] = recent_counts.get((chosen.name, area.id), 0) + 1
                weekly_counts[(chosen.name, area.id)] = weekly_counts.get((chosen.name, area.id), 0) + 1
                reserved.append((area.id, chosen.id))

        return reserved

    # 専任本人を最低人数枠から先に確保し、そこに枠が無ければ通常枠から確保する。
    reserved_dedicated_pairs = reserve_dedicated_staff(minimum_slots)
    reserved_dedicated_area_ids = {area_id for area_id, _staff_id in reserved_dedicated_pairs}

    # 同じ専任配置に複数専任者がいる場合もあるため、normal側にも残りの専任者を回す。
    reserve_dedicated_staff(normal_slots)

    def reserve_dedicated_backups(slot_pool):
        nonlocal_remaining = None  # 可読性用ダミー
        reserved = []
        for area in sorted(areas, key=lambda a: (a.assignment_priority, a.display_order, a.id)):
            # このarea向け代理情報が残っている = 専任本人が終日不在のときだけ有効。
            backup_candidates = [
                staff for staff in remaining_staff
                if getattr(staff, "_backup_priority_map", {}).get(area.id)
            ]
            if not backup_candidates:
                continue

            area_slots = [
                (idx, pair) for idx, pair in enumerate(slot_pool)
                if pair[0].id == area.id
            ]
            if not area_slots:
                continue

            backup_candidates.sort(key=lambda staff: (
                getattr(staff, "_backup_priority_map", {}).get(area.id, 9999),
                staff.assignment_staff_priority,
                staff.display_order,
                staff.name,
            ))

            chosen = None
            for staff in backup_candidates:
                if _slot_score(staff, area, recent_counts, weekly_counts, rng) is not None:
                    chosen = staff
                    break
            if chosen is None:
                continue

            list_index, (_target_area, slot_index) = area_slots[0]
            assignments.append((chosen, area, slot_index))
            slot_pool.pop(list_index)
            remaining_staff[:] = [s for s in remaining_staff if s.id != chosen.id]
            recent_counts[(chosen.name, area.id)] = recent_counts.get((chosen.name, area.id), 0) + 1
            weekly_counts[(chosen.name, area.id)] = weekly_counts.get((chosen.name, area.id), 0) + 1
            reserved.append((area.id, chosen.id))
        return reserved

    # 最低必要人数の枠を優先して専任代理で埋め、なければ通常枠を使う。
    reserved_backup_pairs = reserve_dedicated_backups(minimum_slots)
    reserved_backup_area_ids = {area_id for area_id, _staff_id in reserved_backup_pairs}

    # minimum側に対象枠が無かった専任配置だけnormal側で確保する。
    if normal_slots:
        for area in sorted(areas, key=lambda a: (a.assignment_priority, a.display_order, a.id)):
            if area.id in reserved_backup_area_ids:
                continue
            if not any(getattr(staff, "_backup_priority_map", {}).get(area.id) for staff in remaining_staff):
                continue
            # 対象areaだけを一時リストへ抜き出して1枠確保し、元リストから対応枠を除く。
            target_pos = next((i for i, pair in enumerate(normal_slots) if pair[0].id == area.id), None)
            if target_pos is None:
                continue
            backup_candidates = [
                staff for staff in remaining_staff
                if getattr(staff, "_backup_priority_map", {}).get(area.id)
                and _slot_score(staff, area, recent_counts, weekly_counts, rng) is not None
            ]
            if not backup_candidates:
                continue
            backup_candidates.sort(key=lambda staff: (
                getattr(staff, "_backup_priority_map", {}).get(area.id, 9999),
                staff.assignment_staff_priority,
                staff.display_order,
                staff.name,
            ))
            chosen = backup_candidates[0]
            _target_area, slot_index = normal_slots.pop(target_pos)
            assignments.append((chosen, area, slot_index))
            remaining_staff[:] = [s for s in remaining_staff if s.id != chosen.id]
            recent_counts[(chosen.name, area.id)] = recent_counts.get((chosen.name, area.id), 0) + 1
            weekly_counts[(chosen.name, area.id)] = weekly_counts.get((chosen.name, area.id), 0) + 1

    # v3.10.6.48: 週回数目標を通常配置より前に明示的に確保する。
    # 例: 「Free優先 + 2番を週3回」でも、2番の未達分だけは先に配置し、
    # それ以外の日・配置ではFree優先の性質を維持する。
    # v3.10.6.54:
    # AM休 / PM休 / 時間指定休のスタッフは、その日の「勤務できる時間帯」があるため、
    # Freeへ落とさず通常配置枠を1つ明示的に確保する。
    # 後段の _apply_partial_absences() で休み時間帯だけ外し、
    # その時間帯はFreeスタッフで補充する。
    #
    # 専任本人・専任代理は先に確保済み。
    # 週回数予約より前に部分休スタッフを確保することで、
    # 週回数対象者に全枠を先取りされて部分休スタッフが終日Freeになるのを防ぐ。
    def reserve_partial_absence_staff(slot_pool):
        reserved = []
        while True:
            partial_candidates = [
                staff for staff in remaining_staff
                if staff.id in partial_absence_staff_ids
            ]
            if not partial_candidates or not slot_pool:
                break

            best = None
            for staff in partial_candidates:
                for slot_pos, (area, slot_index) in enumerate(slot_pool):
                    score = _slot_score(staff, area, recent_counts, weekly_counts, rng)
                    if score is None:
                        continue

                    # 配置場所優先度と本人の希望・週回数等を従来どおり評価。
                    score += (6 - area.assignment_priority) * 1000
                    key = (
                        -score,
                        staff.assignment_staff_priority,
                        staff.display_order,
                        staff.name,
                        area.display_order,
                        slot_index,
                    )
                    if best is None or key < best[0]:
                        best = (key, slot_pos, staff, area, slot_index)

            if best is None:
                break

            _key, slot_pos, staff, area, slot_index = best
            assignments.append((staff, area, slot_index))
            slot_pool.pop(slot_pos)
            remaining_staff[:] = [s for s in remaining_staff if s.id != staff.id]
            recent_counts[(staff.name, area.id)] = recent_counts.get((staff.name, area.id), 0) + 1
            weekly_counts[(staff.name, area.id)] = weekly_counts.get((staff.name, area.id), 0) + 1
            reserved.append((staff.id, area.id))

        return reserved

    # 最低必要人数枠を優先し、そこに入れなかった部分休スタッフは通常枠で確保。
    reserve_partial_absence_staff(minimum_slots)
    reserve_partial_absence_staff(normal_slots)

    def reserve_weekly_targets(slot_pool):
        reserved = []
        while True:
            best = None
            for slot_pos, (area, slot_index) in enumerate(slot_pool):
                for staff in remaining_staff:
                    target = getattr(staff, "_weekly_target_map", {}).get(area.id, 0)
                    if target <= 0:
                        continue
                    done = weekly_counts.get((staff.name, area.id), 0)
                    deficit = target - done
                    if deficit <= 0:
                        continue
                    if _slot_score(staff, area, recent_counts, weekly_counts, rng) is None:
                        continue

                    # 未達が大きい人を優先。同値ならスタッフ配置優先度・表示順。
                    # Free優先でも週回数未達ならここでは対象になる。
                    key = (
                        -deficit,
                        staff.assignment_staff_priority,
                        staff.display_order,
                        staff.name,
                        area.assignment_priority,
                        area.display_order,
                        slot_index,
                    )
                    if best is None or key < best[0]:
                        best = (key, slot_pos, staff, area, slot_index)

            if best is None:
                break

            _key, slot_pos, staff, area, slot_index = best
            assignments.append((staff, area, slot_index))
            slot_pool.pop(slot_pos)
            remaining_staff[:] = [s for s in remaining_staff if s.id != staff.id]
            recent_counts[(staff.name, area.id)] = recent_counts.get((staff.name, area.id), 0) + 1
            weekly_counts[(staff.name, area.id)] = weekly_counts.get((staff.name, area.id), 0) + 1
            reserved.append((staff.id, area.id))

        return reserved

    # 最低必要人数側に対象枠があれば先に確保し、残りを通常枠から確保する。
    reserve_weekly_targets(minimum_slots)
    reserve_weekly_targets(normal_slots)

    # v3.10.6.13: 「予定時のみ」で当日ONになっている配置は、通常配置より先に確保する。
    # これにより、その配置に入れるスタッフが他の通常配置へ先に使われてしまい、
    # 予定配置だけ空くケースを防ぐ。候補が少ない予定枠から処理する。
    scheduled_slots = [
        slot for slot in (minimum_slots + normal_slots)
        if slot[0].auto_assignment_mode == "scheduled" and slot[0].id in scheduled_enabled_ids
    ]
    scheduled_slot_keys = {(area.id, idx) for area, idx in scheduled_slots}
    minimum_slots = [slot for slot in minimum_slots if (slot[0].id, slot[1]) not in scheduled_slot_keys]
    normal_slots = [slot for slot in normal_slots if (slot[0].id, slot[1]) not in scheduled_slot_keys]

    def scheduled_eligible_count(slot):
        area, _slot_index = slot
        return sum(1 for staff in ordered_candidates if _slot_score(staff, area, recent_counts, weekly_counts, rng) is not None)

    scheduled_slots.sort(key=lambda slot: (scheduled_eligible_count(slot), slot[0].assignment_priority, slot[0].display_order, slot[1]))
    unfilled_scheduled = []
    for area, slot_index in scheduled_slots:
        best = None
        for staff in remaining_staff:
            score = _slot_score(staff, area, recent_counts, weekly_counts, rng)
            if score is None:
                continue
            if best is None or score > best[0]:
                best = (score, staff)
        if best is None:
            unfilled_scheduled.append((area, slot_index))
            continue
        staff = best[1]
        assignments.append((staff, area, slot_index))
        remaining_staff = [s for s in remaining_staff if s.id != staff.id]
        recent_counts[(staff.name, area.id)] = recent_counts.get((staff.name, area.id), 0) + 1
        weekly_counts[(staff.name, area.id)] = weekly_counts.get((staff.name, area.id), 0) + 1

    remaining_staff, unfilled_minimum = fill_slots(minimum_slots, remaining_staff)
    remaining_staff, unfilled_normal = fill_slots(normal_slots, remaining_staff)

    # v3.10.6.38: 専任代理を「最終確定段階」で保証する。
    # ここまでの候補評価で代理がFreeや別配置へ流れていても、
    # 専任本人が終日不在なら、代理を専任配置へ移し、
    # その枠にいた通常スタッフは代理の元配置へ交換する。
    # 代理が未配置（Free予定）なら、その枠の通常スタッフをFree側へ戻す。
    for area in sorted(areas, key=lambda a: (a.assignment_priority, a.display_order, a.id)):
        backup_candidates = [
            s for s in candidates
            if getattr(s, "_backup_priority_map", {}).get(area.id)
            and _slot_score(s, area, recent_counts, weekly_counts, rng) is not None
        ]
        if not backup_candidates:
            continue

        backup_candidates.sort(key=lambda s: (
            getattr(s, "_backup_priority_map", {}).get(area.id, 9999),
            s.assignment_staff_priority,
            s.display_order,
            s.name,
        ))
        backup = backup_candidates[0]

        target_index = next(
            (i for i, (_s, assigned_area, _slot_idx) in enumerate(assignments)
             if assigned_area.id == area.id),
            None,
        )
        if target_index is None:
            continue

        current_staff, target_area, target_slot_index = assignments[target_index]
        if current_staff.id == backup.id:
            continue

        backup_index = next(
            (i for i, (assigned_staff, _assigned_area, _slot_idx) in enumerate(assignments)
             if assigned_staff.id == backup.id),
            None,
        )

        if backup_index is not None:
            # 代理が別配置に入っている場合は、通常スタッフと配置先を交換。
            _backup_staff, backup_area, backup_slot_index = assignments[backup_index]
            # 通常スタッフが代理の元配置へ入れない場合は無理に交換せず、
            # 代理だけ専任へ移し、通常スタッフをFreeへ戻す。
            if _slot_score(current_staff, backup_area, recent_counts, weekly_counts, rng) is not None:
                assignments[backup_index] = (current_staff, backup_area, backup_slot_index)
            else:
                assignments.pop(backup_index)
                if backup_index < target_index:
                    target_index -= 1
        else:
            # 代理がFree予定なら remaining_staff から外す。
            remaining_staff = [s for s in remaining_staff if s.id != backup.id]

        assignments[target_index] = (backup, target_area, target_slot_index)

    # 予定枠の未充足も従来の警告判定へ含める。
    unfilled_normal = unfilled_scheduled + unfilled_normal
    # 場所ごとの「枠なし」「連結」を尊重して入力する。
    for staff, area, slot_index in assignments:
        layout = _area_row_layout(area, time_slots, rule_map)
        for item in layout:
            if not item["show"] or item["hidden"] or not item["owner_key"]:
                continue
            # 7:30は治療のみ自動配置。その他の早出は手動入力する。
            if item["owner_key"] == "0730" and not ("治療" in (area.category or "") or "治療" in area.name):
                continue
            # 配置場所×時間ごとの「自動配置OFF」を尊重する。
            area_time_rule = AreaTimeRule.objects.filter(area=area, time_slot=item["slot"]).first()
            if area_time_rule and not area_time_rule.auto_assignment_enabled:
                continue
            # 横結合ルールで「自動配置対象外」の範囲は通常自動配置で埋めない。
            # 同じ時間帯に複数の横結合ルールがある場合も、すべて判定する。
            exclude_merges = HorizontalMergeRule.objects.filter(
                time_slot=item["slot"], is_active=True, exclude_from_auto_assignment=True
            ).select_related("start_area", "end_area")
            ordered_ids = [a.id for a in all_areas]
            skip_for_merge = False
            for merge in exclude_merges:
                try:
                    a0, a1 = ordered_ids.index(merge.start_area_id), ordered_ids.index(merge.end_area_id)
                    lo, hi = sorted((a0, a1))
                    if area.id in ordered_ids[lo:hi + 1]:
                        skip_for_merge = True
                        break
                except ValueError:
                    continue
            if skip_for_merge:
                continue
            # スタッフ別の通常勤務可能時間を厳守。範囲外の時間には自動配置しない。
            if not _staff_available_for_row(staff, item["owner_key"]):
                continue
            cell, _ = AssignmentCell.objects.get_or_create(
                board=board, area=area, row_key=item["owner_key"], slot_index=slot_index
            )
            if rebuild or not cell.value.strip():
                cell.value = staff.name
                cell.save(update_fields=["value"])

        # v3.10.6.57:
        # AM/PM休と縦結合セルの境界対応。
        # 例: 8:30～16:00が1つの縦結合セルで owner_key=0830 の場合、
        # AM休では0830セルを後段で消すだけだと午後の表示先そのものが無くなる。
        # 休み→勤務へ切り替わる最初の実時刻に override セルを作り、
        # _build_board_table() の既存の「担当交代でrowspan分割」機構を使って
        # 午後側を独立表示させる。
        if staff.id in partial_absence_staff_ids:
            staff_absences = list(
                WeeklyAbsence.objects.filter(
                    absence_date=board_date,
                    staff=staff,
                ).select_related("start_slot", "end_slot")
            )
            layout = _area_row_layout(area, time_slots, rule_map)
            previous_absent = None
            for item in layout:
                slot = item["slot"]
                row_key = slot.key
                if not row_key.isdigit():
                    continue
                if row_key == "0730" and not ("治療" in (area.category or "") or "治療" in area.name):
                    continue
                absent_now = any(_absence_covers_row(a, row_key) for a in staff_absences)
                if previous_absent is True and not absent_now and _staff_available_for_row(staff, row_key):
                    override_cell, _ = AssignmentCell.objects.get_or_create(
                        board=board, area=area, row_key=row_key, slot_index=slot_index
                    )
                    if rebuild or not override_cell.value.strip():
                        override_cell.value = staff.name
                        override_cell.save(update_fields=["value"])
                    break
                previous_absent = absent_now

    # 配置枠より勤務者が多い場合、残った自動配置対象スタッフをFreeへ。
    # 同じ配置優先度では直近Freeだった人を先に配置しているため、Freeは自然にローテーションする。
    assigned_staff_ids = {staff.id for staff, _, _ in assignments}
    free_staff = [s for s in candidates if s.id not in assigned_staff_ids]
    previous_auto_free = list(StaffFreeHistory.objects.filter(board_date=board_date).select_related("staff"))
    previous_names = {x.staff.name for x in previous_auto_free}
    StaffFreeHistory.objects.filter(board_date=board_date).delete()
    StaffFreeHistory.objects.bulk_create([StaffFreeHistory(staff=s, board_date=board_date) for s in free_staff])
    active_staff_names = set(
        Staff.objects.filter(is_active=True).values_list("name", flat=True)
    )
    partial_free_labels = {
        f"{name}（午前）" for name in active_staff_names
    } | {
        f"{name}（午後）" for name in active_staff_names
    }
    manual_free = [
        x.strip()
        for x in re.split(r"[、,\n]+", board.free_text or "")
        if x.strip()
        and x.strip() not in previous_names
        and x.strip() not in partial_free_labels
    ]
    merged_free = manual_free + [s.name for s in free_staff if s.name not in manual_free]
    board.free_text = "、".join(merged_free)
    board.save(update_fields=["free_text", "updated_at"])

    warnings = _apply_derived_rules(board)
    warnings.extend(_apply_partial_absences(board))
    warnings.extend(_apply_quick_meta_absences(board))
    unassigned = _recalculate_unassigned(board)
    board.refresh_from_db()

    cells_payload = [
        {"area_id": c.area_id, "row_key": c.row_key, "slot_index": c.slot_index, "value": c.value}
        for c in board.cells.filter(row_key__in=_dynamic_row_keys())
    ]
    lunch_payload = [
        {"row_key": e.time_slot.key, "value": e.value}
        for e in board.lunch_break_entries.select_related("time_slot").all()
    ]
    assigned_names = {staff.name for staff, _, _ in assignments}
    not_assigned = [s.name for s in candidates if s.name not in assigned_names]
    if not_assigned:
        warnings.append("配置枠に入らなかった人は『Free』へ反映しました: " + "、".join(not_assigned))

    # 「予定時のみ」をONにしたのに必要人数まで埋まらなかった場所は、理由を明示する。
    unfilled_by_area = defaultdict(int)
    for area, _slot_index in (unfilled_minimum + unfilled_normal):
        unfilled_by_area[area.id] += 1
    assigned_by_area = defaultdict(int)
    for _staff, area, _slot_index in assignments:
        assigned_by_area[area.id] += 1
    scheduled_area_ids = {a.id for a in areas if a.auto_assignment_mode == "scheduled"}
    for area in areas:
        if area.id not in scheduled_area_ids or area.id not in scheduled_enabled_ids:
            continue
        shortage = unfilled_by_area.get(area.id, 0)
        if shortage <= 0:
            continue
        eligible = []
        dedicated_block = 0
        avoid_block = 0
        for staff in candidates:
            if staff.dedicated_area_id and staff.dedicated_area_id != area.id:
                dedicated_block += 1
                continue
            if area.id in getattr(staff, "_avoid_area_ids", set()):
                avoid_block += 1
                continue
            eligible.append(staff)
        if not candidates:
            reason = "配置可能な勤務スタッフがいません"
        elif not eligible:
            bits = []
            if dedicated_block:
                bits.append("専任設定")
            if avoid_block:
                bits.append("『配置しない』設定")
            reason = "対象スタッフが " + "・".join(bits or ["スタッフ設定"]) + " により候補外です"
        else:
            reason = "配置可能スタッフはいますが、他の高優先度配置への割当または人数不足で埋まりませんでした"
        filled = max(area.auto_assignment_count - shortage, 0)
        warnings.append(
            f"予定ON：{area.name} は必要{area.auto_assignment_count}人中 {filled}人。{reason}。"
        )
    if unavailable:
        warnings.append("勤務・休暇欄のため通常配置から除外: " + "、".join(sorted(unavailable)))

    # v3.10.6.15: 正常終了した配置自動作成の直前状態を1回だけ戻せるようにする。
    _arm_board_undo(request, undo_snapshot, board)

    return JsonResponse({
        "ok": True,
        "cells": cells_payload,
        "lunch_entries": lunch_payload,
        "unassigned": board.unassigned,
        "updated_at": board.updated_at.isoformat(),
        "assigned_count": len(assignments),
        "warnings": warnings,
        "message": f"通常配置を{len(assignments)}名分、自動作成しました。",
    })


@require_GET
def settings_view(request):
    staff_list = Staff.objects.all().order_by("display_order", "name")
    area_list = AssignmentArea.objects.all().order_by("display_order", "id")
    time_slots = list(TimeSlot.objects.all().order_by("display_order", "id"))
    active_time_slots = [s for s in time_slots if s.is_active]
    active_areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    rules = AreaTimeRule.objects.filter(area__in=active_areas, time_slot__in=active_time_slots)
    rule_map = {(r.area_id, r.time_slot_id): r for r in rules}
    area_time_rows = []
    for area in active_areas:
        slot_items = []
        for slot in active_time_slots:
            rule = rule_map.get((area.id, slot.id))
            slot_items.append({
                "time_slot": slot,
                "mode": rule.mode if rule else "normal",
                "auto_enabled": rule.auto_assignment_enabled if rule else True,
            })
        area_time_rows.append({"area": area, "slots": slot_items})

    backup_map = defaultdict(list)
    for b in DedicatedAreaBackup.objects.select_related("staff", "area").all():
        backup_map[b.area_id].append(b)
    for area in area_list:
        area.backup_entries = backup_map.get(area.id, [])

    for staff in staff_list:
        last_free = staff.free_history.order_by("-board_date").values_list("board_date", flat=True).first()
        staff.last_free_date = last_free
        avoid_ids = set(staff.avoid_area_entries.values_list("area_id", flat=True))
        target_map = {x.area_id: x.target_count for x in staff.weekly_targets.all()}
        staff.auto_area_options = [
            {"area": area, "avoid": area.id in avoid_ids, "weekly_count": target_map.get(area.id, 0)}
            for area in active_areas
        ]

    return render(request, "daily_assignment/settings.html", {
        "staff_list": staff_list,
        "area_list": area_list,
        "active_area_list": active_areas,
        "comp_rules": CompensatoryLeaveRule.objects.all(),
        "weekday_choices": CompensatoryLeaveRule.WEEKDAY_CHOICES,
        "day_type_choices": CompensatoryLeaveRule.DAY_TYPE_CHOICES,
        "duty_type_choices": CompensatoryLeaveRule.DUTY_TYPE_CHOICES,
        "time_slots": time_slots,
        "active_time_slots": active_time_slots,
        "time_kind_choices": TimeSlot.KIND_CHOICES,
        "area_time_rows": area_time_rows,
        "area_time_mode_choices": AreaTimeRule.MODE_CHOICES,
        "horizontal_merge_rules": HorizontalMergeRule.objects.select_related("time_slot", "start_area", "end_area", "representative_area").all(),
        "derived_rules": DerivedAssignmentRule.objects.select_related("source_area", "target_area", "time_slot").all(),
        "derived_action_choices": DerivedAssignmentRule.ACTION_CHOICES,
        "meeting_presets": MeetingPreset.objects.all().order_by("display_order", "name"),
        "reception_staff_list": ReceptionStaff.objects.all().order_by("display_order", "name"),
        "comment_templates": DailyCommentTemplate.objects.all().order_by("display_order", "id"),
        "comment_template_input_choices": DailyCommentTemplate.INPUT_TYPE_CHOICES,
        "comment_template_font_choices": DailyCommentTemplate.FONT_SIZE_CHOICES,
    })


@require_POST
def comment_template_add(request):
    label = request.POST.get("label", "").strip()
    input_type = request.POST.get("input_type", "number")
    unit = request.POST.get("unit", "").strip()[:30]
    try:
        font_size = int(request.POST.get("font_size", "14") or 14)
        display_order = max(int(request.POST.get("display_order", "0") or 0), 0)
    except ValueError:
        messages.error(request, "文字サイズまたは表示順が正しくありません。")
        return redirect("daily_assignment:settings")
    if not label:
        messages.error(request, "定型コメントの表示文を入力してください。")
        return redirect("daily_assignment:settings")
    if input_type not in dict(DailyCommentTemplate.INPUT_TYPE_CHOICES):
        input_type = "none"
    if font_size not in dict(DailyCommentTemplate.FONT_SIZE_CHOICES):
        font_size = 14
    DailyCommentTemplate.objects.create(
        label=label[:120], input_type=input_type, unit=unit, font_size=font_size,
        display_order=display_order, is_active=True,
    )
    messages.success(request, f"定型コメント「{label}」を追加しました。")
    return redirect("daily_assignment:settings")


@require_POST
def comment_template_update(request, template_id):
    item = get_object_or_404(DailyCommentTemplate, pk=template_id)
    label = request.POST.get("label", "").strip()
    input_type = request.POST.get("input_type", "none")
    unit = request.POST.get("unit", "").strip()[:30]
    try:
        font_size = int(request.POST.get("font_size", item.font_size) or item.font_size)
        display_order = max(int(request.POST.get("display_order", item.display_order) or item.display_order), 0)
    except ValueError:
        messages.error(request, "文字サイズまたは表示順が正しくありません。")
        return redirect("daily_assignment:settings")
    if not label:
        messages.error(request, "定型コメントの表示文を入力してください。")
        return redirect("daily_assignment:settings")
    if input_type not in dict(DailyCommentTemplate.INPUT_TYPE_CHOICES):
        input_type = "none"
    if font_size not in dict(DailyCommentTemplate.FONT_SIZE_CHOICES):
        font_size = 14
    item.label = label[:120]
    item.input_type = input_type
    item.unit = unit
    item.font_size = font_size
    item.display_order = display_order
    item.is_active = request.POST.get("is_active") == "on"
    item.save()
    messages.success(request, f"定型コメント「{item.label}」を更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def comment_template_delete(request, template_id):
    item = get_object_or_404(DailyCommentTemplate, pk=template_id)
    label = item.label
    item.delete()
    messages.success(request, f"定型コメント「{label}」を削除しました。")
    return redirect("daily_assignment:settings")


@require_POST
def time_slot_add(request):
    key = request.POST.get("key", "").strip()
    label = request.POST.get("label", "").strip()
    try:
        order = int(request.POST.get("display_order", "0") or 0)
    except ValueError:
        order = 0
    kind = request.POST.get("kind", "time")
    if not key or not label:
        messages.error(request, "時間枠の内部キーと表示名を入力してください。")
        return redirect("daily_assignment:settings")
    if TimeSlot.objects.filter(key=key).exists():
        messages.error(request, f"時間枠キー「{key}」は既にあります。")
        return redirect("daily_assignment:settings")
    TimeSlot.objects.create(
        key=key, label=label, display_order=max(order, 0),
        kind=kind if kind in dict(TimeSlot.KIND_CHOICES) else "time",
        is_active=request.POST.get("is_active") == "on",
        is_lunch_highlight=request.POST.get("is_lunch_highlight") == "on",
    )
    messages.success(request, f"時間枠「{label}」を追加しました。")
    return redirect("daily_assignment:settings")


@require_POST
def time_slot_update(request, slot_id):
    slot = get_object_or_404(TimeSlot, pk=slot_id)
    label = request.POST.get("label", "").strip()
    if not label:
        messages.error(request, "表示名を入力してください。")
        return redirect("daily_assignment:settings")
    try:
        order = max(int(request.POST.get("display_order", "0") or 0), 0)
    except ValueError:
        order = 0
    kind = request.POST.get("kind", "time")
    slot.label = label
    slot.display_order = order
    slot.kind = kind if kind in dict(TimeSlot.KIND_CHOICES) else "time"
    slot.is_active = request.POST.get("is_active") == "on"
    slot.is_lunch_highlight = request.POST.get("is_lunch_highlight") == "on"
    slot.save()
    messages.success(request, f"時間枠「{label}」を更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def time_slot_delete(request, slot_id):
    slot = get_object_or_404(TimeSlot, pk=slot_id)
    if AssignmentCell.objects.filter(row_key=slot.key).exists() or LunchBreakEntry.objects.filter(time_slot=slot).exists():
        slot.is_active = False
        slot.save(update_fields=["is_active"])
        messages.warning(request, f"時間枠「{slot.label}」は使用済みのため使用停止にしました。")
    else:
        slot.delete()
        messages.success(request, "時間枠を削除しました。")
    return redirect("daily_assignment:settings")


@require_POST
@transaction.atomic
def area_time_rules_update(request, area_id):
    area = get_object_or_404(AssignmentArea, pk=area_id)
    valid_modes = dict(AreaTimeRule.MODE_CHOICES)
    for slot in TimeSlot.objects.filter(is_active=True):
        mode = request.POST.get(f"slot_{slot.id}", "normal")
        if mode not in valid_modes:
            mode = "normal"
        auto_enabled = request.POST.get(f"auto_{slot.id}", "on") == "on"
        if mode == "normal" and auto_enabled:
            AreaTimeRule.objects.filter(area=area, time_slot=slot).delete()
        else:
            AreaTimeRule.objects.update_or_create(
                area=area, time_slot=slot, defaults={"mode": mode, "auto_assignment_enabled": auto_enabled}
            )
    messages.success(request, f"{area.name} の時間枠設定を更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def horizontal_merge_rule_add(request):
    try:
        slot = TimeSlot.objects.get(pk=int(request.POST.get("time_slot", "")))
        start = AssignmentArea.objects.get(pk=int(request.POST.get("start_area", "")))
        end = AssignmentArea.objects.get(pk=int(request.POST.get("end_area", "")))
        representative = AssignmentArea.objects.get(pk=int(request.POST.get("representative_area", "")))
    except (ValueError, TimeSlot.DoesNotExist, AssignmentArea.DoesNotExist):
        messages.error(request, "横結合ルールの設定が正しくありません。")
        return redirect("daily_assignment:settings")
    HorizontalMergeRule.objects.create(name=request.POST.get("name", "").strip() or "横結合", time_slot=slot, start_area=start, end_area=end, representative_area=representative, exclude_from_auto_assignment=request.POST.get("exclude_from_auto_assignment") == "on", is_active=request.POST.get("is_active", "on") == "on")
    messages.success(request, "横結合ルールを追加しました。")
    return redirect("daily_assignment:settings")

@require_POST
def horizontal_merge_rule_delete(request, rule_id):
    get_object_or_404(HorizontalMergeRule, pk=rule_id).delete()
    messages.success(request, "横結合ルールを削除しました。")
    return redirect("daily_assignment:settings")

@require_POST
def derived_rule_add(request):
    try:
        source = AssignmentArea.objects.get(pk=int(request.POST.get("source_area", "")))
        slot = TimeSlot.objects.get(pk=int(request.POST.get("time_slot", "")))
    except (ValueError, AssignmentArea.DoesNotExist, TimeSlot.DoesNotExist):
        messages.error(request, "元配置または時間枠が正しくありません。")
        return redirect("daily_assignment:settings")
    action = request.POST.get("action", "break")
    target = None
    raw_target = request.POST.get("target_area", "").strip()
    if raw_target:
        try:
            target = AssignmentArea.objects.get(pk=int(raw_target))
        except (ValueError, AssignmentArea.DoesNotExist):
            target = None
    if action in ("add", "move") and not target:
        messages.error(request, "兼任・移動ルールには移動先を指定してください。")
        return redirect("daily_assignment:settings")
    DerivedAssignmentRule.objects.create(
        name=request.POST.get("name", "").strip(), source_area=source,
        time_slot=slot, action=action, target_area=target,
        is_active=request.POST.get("is_active") == "on",
    )
    messages.success(request, "時間連動ルールを追加しました。")
    return redirect("daily_assignment:settings")


@require_POST
def derived_rule_update(request, rule_id):
    rule = get_object_or_404(DerivedAssignmentRule, pk=rule_id)
    try:
        rule.source_area = AssignmentArea.objects.get(pk=int(request.POST.get("source_area", "")))
        rule.time_slot = TimeSlot.objects.get(pk=int(request.POST.get("time_slot", "")))
    except (ValueError, AssignmentArea.DoesNotExist, TimeSlot.DoesNotExist):
        messages.error(request, "元配置または時間枠が正しくありません。")
        return redirect("daily_assignment:settings")
    rule.name = request.POST.get("name", "").strip()
    rule.action = request.POST.get("action", "break")
    raw_target = request.POST.get("target_area", "").strip()
    rule.target_area = AssignmentArea.objects.filter(pk=raw_target).first() if raw_target else None
    if rule.action in ("add", "move") and not rule.target_area:
        messages.error(request, "兼任・移動ルールには移動先を指定してください。")
        return redirect("daily_assignment:settings")
    rule.is_active = request.POST.get("is_active") == "on"
    rule.save()
    messages.success(request, "時間連動ルールを更新しました。")
    return redirect("daily_assignment:settings")


@require_POST
def derived_rule_delete(request, rule_id):
    rule = get_object_or_404(DerivedAssignmentRule, pk=rule_id)
    rule.delete()
    messages.success(request, "時間連動ルールを削除しました。")
    return redirect("daily_assignment:settings")


@require_POST
@transaction.atomic
def clear_board(request):
    board_date = _parse_date(request.POST.get("date"))
    board = DailyBoard.objects.filter(board_date=board_date).first()
    if board:
        board.cells.all().delete()
        board.lunch_break_entries.all().delete()
        for field in (
            "conference", "night_shift", "night_shift_after", "holiday_day_shift",
            "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
            "annual_leave_1", "annual_leave_2", "staffing_am", "staffing_pm",
            "free_text", "comment", "unassigned", "reception_leave",
        ):
            setattr(board, field, "")
        board.save()
    messages.success(request, "配置表を空にしました。")
    return redirect(f"{reverse('daily_assignment:board')}?date={board_date}")


@require_POST
@transaction.atomic
def copy_board(request):
    target_date = _parse_date(request.POST.get("date"))
    source_date = _parse_date(request.POST.get("source_date"))
    source = DailyBoard.objects.filter(board_date=source_date).first()
    if not source:
        if request.POST.get("check_only") == "1":
            return JsonResponse({"ok": False, "error": "コピー元の日付に配置表がありません。"}, status=404)
        messages.error(request, "コピー元の日付に配置表がありません。")
        return redirect(f"{reverse('daily_assignment:board')}?date={target_date}")
    target, _ = DailyBoard.objects.get_or_create(board_date=target_date)
    meta_fields = (
        "conference", "night_shift", "night_shift_after", "holiday_day_shift",
        "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
        "annual_leave_1", "annual_leave_2", "staffing_am", "staffing_pm",
        "free_text", "comment", "unassigned", "reception_leave",
    )
    has_existing = target.cells.exclude(value="").exists() or target.lunch_break_entries.exclude(value="").exists() or any(
        str(getattr(target, f, "") or "").strip() for f in meta_fields
    )
    if request.POST.get("check_only") == "1":
        return JsonResponse({"ok": True, "has_existing_data": has_existing})
    if has_existing and request.POST.get("overwrite") != "1":
        messages.error(request, "コピー先には既にデータがあります。")
        return redirect(f"{reverse('daily_assignment:board')}?date={target_date}")

    # v3.10.6.73: 日付コピーもUndo/Redoの1操作として扱う。
    # コピー元ではなく、書き換えられるコピー先の直前状態を保存する。
    copy_undo_snapshot = _build_board_undo_snapshot(target, "日付コピー")

    target.cells.all().delete()
    target.lunch_break_entries.all().delete()
    for f in meta_fields:
        setattr(target, f, getattr(source, f, ""))
    target.save()
    AssignmentCell.objects.bulk_create([
        AssignmentCell(board=target, area=c.area, row_key=c.row_key, slot_index=c.slot_index, value=c.value)
        for c in source.cells.all()
    ])
    LunchBreakEntry.objects.bulk_create([
        LunchBreakEntry(board=target, time_slot=e.time_slot, value=e.value)
        for e in source.lunch_break_entries.all()
    ])
    target.refresh_from_db()
    _arm_board_undo(request, copy_undo_snapshot, target)
    messages.success(request, f"{source_date:%Y/%m/%d} の内容をコピーしました。")
    return redirect(f"{reverse('daily_assignment:board')}?date={target_date}")


def _assignment_history(board_date, staffs, areas):
    """Ver.3: 設定画面の時間枠を使って履歴を数える。"""
    monday = board_date - timedelta(days=board_date.weekday())
    week_end = monday + timedelta(days=6)
    recent_start = board_date - timedelta(days=27)
    staff_names = [staff.name for staff in staffs]
    area_ids = [area.id for area in areas]
    row_keys = list(TimeSlot.objects.filter(is_active=True, kind="time").values_list("key", flat=True))
    cells = AssignmentCell.objects.filter(
        board__board_date__gte=recent_start,
        board__board_date__lte=board_date,
        row_key__in=row_keys,
        value__in=staff_names,
        area_id__in=area_ids,
    ).values("board__board_date", "area_id", "value")
    recent_days = defaultdict(set)
    weekly_days = defaultdict(set)
    for item in cells:
        day = item["board__board_date"]
        key = (item["value"], item["area_id"])
        recent_days[key].add(day)
        if monday <= day <= week_end:
            weekly_days[key].add(day)
    return (
        {key: len(days) for key, days in recent_days.items()},
        {key: len(days) for key, days in weekly_days.items()},
    )





def _legacy_month_shift_symbol(board, staff_name):
    """移行前のDailyBoardしかない日を月間表へ表示するための互換処理。"""
    if not board:
        return ""
    symbols = []
    if staff_name and staff_name == (board.night_shift or "").strip():
        symbols.append("▼")
    if staff_name and staff_name == (board.night_shift_after or "").strip():
        symbols.append("△")
    if staff_name and staff_name == (board.holiday_day_shift or "").strip():
        symbols.append("日")
    comp_names = {
        (board.compensatory_leave_1 or "").strip(),
        (board.compensatory_leave_2 or "").strip(),
        (board.compensatory_leave_3 or "").strip(),
    }
    if staff_name in comp_names:
        symbols.append("休")
    return " ".join(symbols)


def _calendar_month_shift_symbol(
    target_date,
    staff_name,
    calendar_map,
    comp_by_date,
    legacy_boards,
):
    calendar_day = calendar_map.get(target_date)
    if not calendar_day:
        return _legacy_month_shift_symbol(legacy_boards.get(target_date), staff_name)

    symbols = []
    if calendar_day.night_shift and calendar_day.night_shift.name == staff_name:
        symbols.append("▼")

    previous = calendar_map.get(target_date - timedelta(days=1))
    if previous and previous.night_shift and previous.night_shift.name == staff_name:
        symbols.append("△")
    elif not previous:
        legacy_previous = legacy_boards.get(target_date - timedelta(days=1))
        if legacy_previous and (legacy_previous.night_shift or "").strip() == staff_name:
            symbols.append("△")

    if (
        _calendar_is_holiday_duty(target_date, calendar_day)
        and calendar_day.holiday_day_shift
        and calendar_day.holiday_day_shift.name == staff_name
    ):
        symbols.append("日")

    if staff_name in comp_by_date.get(target_date, set()):
        symbols.append("休")

    return " ".join(symbols)


def _month_bounds(raw_month=""):
    try:
        first_day = (
            datetime.strptime(raw_month, "%Y-%m").date().replace(day=1)
            if raw_month
            else timezone.localdate().replace(day=1)
        )
    except ValueError:
        first_day = timezone.localdate().replace(day=1)

    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1)
    last_day = next_month - timedelta(days=1)
    previous_month = (first_day - timedelta(days=1)).replace(day=1)
    return first_day, last_day, previous_month, next_month


def _comp_target_context(source_date, duty_type, prefix):
    record = CompensatoryLeaveRecord.objects.filter(source_date=source_date, duty_type=duty_type).order_by("id").first()
    if not record:
        return {f"{prefix}_date": "", f"{prefix}_is_holiday": False, f"{prefix}_prev": "", f"{prefix}_next": ""}
    target = record.target_date
    is_holiday = _calendar_is_holiday_duty(target)
    return {
        f"{prefix}_date": target.strftime("%Y-%m-%d"),
        f"{prefix}_is_holiday": is_holiday,
        f"{prefix}_prev": _nearest_normal_workday(target, -1).strftime("%Y-%m-%d") if is_holiday else "",
        f"{prefix}_next": _nearest_normal_workday(target, 1).strftime("%Y-%m-%d") if is_holiday else "",
    }


@require_GET
def monthly_duty_view(request):
    first_day, last_day, previous_month, next_month = _month_bounds(
        request.GET.get("month", "")
    )

    # 祝日判定が無効なまま静かに土日だけで動かないよう、画面で明示する。
    _, holiday_lib_available = _jpholiday_status()
    if not holiday_lib_available:
        detail = f"（{_JPHOLIDAY_IMPORT_ERROR}）" if _JPHOLIDAY_IMPORT_ERROR else ""
        messages.warning(
            request,
            "日本の祝日判定ライブラリ jpholiday を読み込めませんでした。"
            f"{detail} サーバーを起動しているPython環境を確認してください。",
        )

    # Ver.3.8.9: 月末をまたぐ夜勤・明け・代休を追いやすいよう、前後7日も表示する。
    display_start = first_day - timedelta(days=7)
    display_end = last_day + timedelta(days=7)

    calendar_days = list(
        DutyCalendarDay.objects.filter(
            duty_date__range=(display_start - timedelta(days=1), display_end)
        ).select_related("night_shift", "holiday_day_shift")
    )
    calendar_map = {item.duty_date: item for item in calendar_days}

    legacy_boards = {
        b.board_date: b
        for b in DailyBoard.objects.filter(
            board_date__range=(display_start - timedelta(days=1), display_end)
        )
    }

    comp_by_date = defaultdict(set)
    comp_detail_map = defaultdict(list)
    comp_records = list(CompensatoryLeaveRecord.objects.filter(
        target_date__range=(display_start, display_end)
    ).order_by("target_date", "staff_name", "source_date", "duty_type"))
    duty_type_labels = {
        "holiday_day": "休日日勤",
        "night": "夜勤",
        "night_after": "明け",
    }
    for record in comp_records:
        staff_name = (record.staff_name or "").strip()
        if not staff_name:
            continue
        comp_by_date[record.target_date].add(staff_name)
        if record.duty_type == "night_after":
            related_date = record.source_date + timedelta(days=1)
            detail = f"{related_date.month}/{related_date.day} 明け（{record.source_date.month}/{record.source_date.day}夜勤）の代休"
        else:
            detail = f"{record.source_date.month}/{record.source_date.day} {duty_type_labels.get(record.duty_type, record.duty_type)}の代休"
        if record.is_manual_override:
            detail += "（代休日を手動変更）"
        comp_detail_map[(record.target_date, staff_name)].append(detail)

    rotation_staff = _rotation_staff(first_day)
    rotation_ids = {staff.id for staff in rotation_staff}
    normal_staff = list(
        Staff.objects.filter(is_active=True).exclude(id__in=rotation_ids).order_by("display_order", "name")
    )
    staff_list = rotation_staff + normal_staff
    staff_options = list(
        Staff.objects.filter(is_active=True).order_by(
            "duty_rotation_order", "display_order", "name"
        )
    )
    total_active_staff = len(staff_options)
    rotation_version = DutyRotationVersion.objects.filter(effective_month__lte=first_day).order_by("-effective_month").first()
    rotation_version_month = rotation_version.effective_month if rotation_version else None
    rotation_member_ids = {st.id for st in rotation_staff}
    rotation_editor_rows = []
    for st in Staff.objects.filter(is_active=True).order_by("display_order", "name"):
        order = next((i + 1 for i, x in enumerate(rotation_staff) if x.id == st.id), st.duty_rotation_order or 0)
        rotation_editor_rows.append({"staff": st, "enabled": st.id in rotation_member_ids, "order": order})

    # 週間配置作成で登録した「年休」を日別人員集計へ加える。
    # 終日=1、AM/PM=0.5、時間指定は人数からは引かず詳細だけ表示する。
    absence_map = defaultdict(list)
    absence_by_staff_date = {}
    for absence in WeeklyAbsence.objects.filter(
        absence_date__range=(display_start, display_end),
        kind="annual",
        staff__is_active=True,
    ).select_related("staff", "start_slot", "end_slot"):
        if absence.period == "full":
            weight = 1.0
            marker = "〇"
        elif absence.period == "am":
            weight = 0.5
            marker = "AM"
        elif absence.period == "pm":
            weight = 0.5
            marker = "PM"
        else:
            weight = 0.0
            marker = "時"
        item = {
            "staff_name": absence.staff.name,
            "weight": weight,
            "marker": marker,
            "label": f"{absence.staff.name} 年休（{absence.period_label}）",
            "period": absence.period,
        }
        absence_map[absence.absence_date].append(item)
        absence_by_staff_date[(absence.staff_id, absence.absence_date)] = item

    days = []
    current = display_start
    while current <= display_end:
        calendar_day = calendar_map.get(current)
        effective_holiday = _calendar_is_holiday_duty(current, calendar_day)
        natural_type = _natural_day_type(current)
        day_mode = calendar_day.day_mode if calendar_day else "auto"
        is_target_month = first_day <= current <= last_day
        days.append({
            "date": current,
            "day": current.day,
            "month": current.month,
            "display_day": str(current.day) if is_target_month else f"{current.month}/{current.day}",
            "weekday": "月火水木金土日"[current.weekday()],
            "is_target_month": is_target_month,
            "is_saturday": current.weekday() == 5 and not effective_holiday,
            "is_sunday_or_holiday": effective_holiday,
            "is_custom_holiday": bool(calendar_day and calendar_day.day_mode == "holiday"),
            "is_closed": bool(calendar_day and calendar_day.day_mode == "closed"),
            "day_mode": day_mode,
            "day_mode_label": calendar_day.get_day_mode_display() if calendar_day else "自動判定（土日祝）",
            "no_compensatory_leave": bool(calendar_day and calendar_day.no_compensatory_leave),
            "force_compensatory_leave": bool(calendar_day and calendar_day.force_compensatory_leave),
            "force_holiday_day_compensatory_leave": bool(calendar_day and calendar_day.force_holiday_day_compensatory_leave),
            "force_night_compensatory_leave": bool(calendar_day and calendar_day.force_night_compensatory_leave),
            "force_night_after_compensatory_leave": bool(calendar_day and calendar_day.force_night_after_compensatory_leave),
            "night_staff_name": calendar_day.night_shift.name if calendar_day and calendar_day.night_shift else "",
            "holiday_staff_name": calendar_day.holiday_day_shift.name if calendar_day and calendar_day.holiday_day_shift else "",
            "night_staff_id": calendar_day.night_shift_id if calendar_day else "",
            "holiday_staff_id": calendar_day.holiday_day_shift_id if calendar_day else "",
            "has_calendar": bool(calendar_day),
            "natural_day_type": natural_type or "normal",
            "holiday_name": _japanese_holiday_name(current),
            "can_undo": DutyCalendarDayHistory.objects.filter(duty_date=current).exists(),
            **_comp_target_context(current, "holiday_day", "holiday_comp"),
            **_comp_target_context(current, "night", "night_comp"),
            **_comp_target_context(current, "night_after", "night_after_comp"),
        })
        current += timedelta(days=1)

    rows = []
    normal_section_started = False
    # 日別の勤務除外者を、記号を作るのと同時に集計する。
    unavailable_names_by_date = defaultdict(set)
    holiday_work_names_by_date = defaultdict(set)
    for staff in staff_list:
        totals = {"night": 0, "holiday_day": 0, "comp": 0}
        cells = []
        for day in days:
            symbol = _calendar_month_shift_symbol(day["date"], staff.name, calendar_map, comp_by_date, legacy_boards)
            # 右端の合計は前後7日を含めず、選択月だけ。
            if day["is_target_month"]:
                if "▼" in symbol:
                    totals["night"] += 1
                if "日" in symbol:
                    totals["holiday_day"] += 1
                if "休" in symbol:
                    totals["comp"] += 1
            if any(mark in symbol for mark in ("▼", "△", "休")):
                unavailable_names_by_date[day["date"]].add(staff.name)
            if "日" in symbol:
                holiday_work_names_by_date[day["date"]].add(staff.name)
            comp_tooltip = " / ".join(comp_detail_map.get((day["date"], staff.name), []))
            annual = absence_by_staff_date.get((staff.id, day["date"]))
            annual_marker = annual["marker"] if annual else ""
            annual_tooltip = annual["label"] if annual else ""
            cells.append({
                "symbol": symbol,
                "comp_tooltip": comp_tooltip,
                "annual_marker": annual_marker,
                "annual_tooltip": annual_tooltip,
                **day,
            })

        is_rotation = staff.id in rotation_ids
        section_start = False
        if not is_rotation and not normal_section_started:
            section_start = True
            normal_section_started = True
        rows.append({
            "staff": staff,
            "cells": cells,
            "totals": totals,
            "is_rotation": is_rotation,
            "section_start": section_start,
        })

    daily_summaries = []
    for day in days:
        date = day["date"]
        duty_unavailable = set(unavailable_names_by_date.get(date, set()))
        leave_total = float(len(comp_by_date.get(date, set())))
        leave_details = [f"代休 {name}" for name in sorted(comp_by_date.get(date, set()))]
        annual_deduction = 0.0
        annual_items = absence_map.get(date, [])
        for item in annual_items:
            leave_details.append(item["label"])
            # 代休等ですでに1人除外されている場合は二重に引かない。
            if item["staff_name"] not in duty_unavailable:
                annual_deduction += item["weight"]
                leave_total += item["weight"]

        # 土日祝・手動休日・臨時休業は「全員が通常日勤」ではなく、
        # 月間勤務表で「日（休日日勤）」が付いているスタッフだけを日勤人数として数える。
        if day["is_sunday_or_holiday"] or day["is_custom_holiday"] or day["is_closed"]:
            holiday_workers = set(holiday_work_names_by_date.get(date, set()))
            holiday_count = float(len(holiday_workers))
            for item in annual_items:
                if item["staff_name"] in holiday_workers:
                    holiday_count -= item["weight"]
            daytime_count = max(0.0, holiday_count)
            daytime_tooltip = "休日日勤のみを集計"
        else:
            daytime_count = max(0.0, float(total_active_staff - len(duty_unavailable)) - annual_deduction)
            daytime_tooltip = "総スタッフから夜勤・明け・代休・年休を差し引いた人数"

        daily_summaries.append({
            **day,
            "daytime_count": int(daytime_count) if daytime_count.is_integer() else daytime_count,
            "daytime_tooltip": daytime_tooltip,
            "leave_total": int(leave_total) if leave_total.is_integer() else leave_total,
            "leave_tooltip": " / ".join(leave_details),
        })

    return render(request, "daily_assignment/monthly_duty.html", {
        "first_day": first_day,
        "last_day": last_day,
        "display_start": display_start,
        "display_end": display_end,
        "previous_month": previous_month,
        "next_month": next_month,
        "days": days,
        "daily_summaries": daily_summaries,
        "rows": rows,
        "rotation_staff": rotation_staff,
        "staff_options": staff_options,
        "rotation_editor_rows": rotation_editor_rows,
        "rotation_version_month": rotation_version_month,
        "table_colspan": len(days) + 4,
    })


@require_POST
@transaction.atomic
def duty_rotation_version_save(request):
    """表示月から適用する勤務ローテーション構成をスナップショット保存する。"""
    first_day, _, _, _ = _month_bounds(request.POST.get("month", ""))
    rows = []
    for st in Staff.objects.filter(is_active=True).order_by("display_order", "name"):
        if request.POST.get(f"rotation_staff_{st.id}") != "on":
            continue
        raw = (request.POST.get(f"rotation_order_{st.id}") or "0").strip()
        try:
            order = int(raw)
        except ValueError:
            order = 0
        rows.append((order, st.display_order, st.name, st))
    if not rows:
        messages.error(request, "勤務ローテーション対象を1人以上選択してください。")
        return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    version, _ = DutyRotationVersion.objects.get_or_create(effective_month=first_day)
    version.members.all().delete()
    DutyRotationVersionMember.objects.bulk_create([
        DutyRotationVersionMember(version=version, staff=row[3], rotation_order=i + 1)
        for i, row in enumerate(rows)
    ])
    messages.success(request, f"{first_day:%Y年%m月}から適用する勤務メンバー・順番を保存しました。過去月は変更されません。")
    return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")


@require_POST
@transaction.atomic
def duty_rotation_version_delete(request):
    first_day, _, _, _ = _month_bounds(request.POST.get("month", ""))
    deleted, _ = DutyRotationVersion.objects.filter(effective_month=first_day).delete()
    if deleted:
        messages.success(request, f"{first_day:%Y年%m月}からの勤務メンバー変更を解除しました。")
    return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")


@require_POST
@transaction.atomic
def duty_calendar_generate(request):
    """指定した開始スタッフから、月間勤務カレンダーをローテーション順に作成する。"""
    first_day, last_day, _, _ = _month_bounds(request.POST.get("month", ""))

    try:
        start_staff_id = int(request.POST.get("start_staff_id", ""))
    except (TypeError, ValueError):
        start_staff_id = 0

    rotation = _rotation_staff(first_day)
    if not rotation:
        messages.error(request, "勤務ローテーション対象のスタッフが登録されていません。")
        return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")

    rotation_ids = [staff.id for staff in rotation]
    if start_staff_id not in rotation_ids:
        messages.error(request, "開始スタッフを勤務ローテーション対象者から選んでください。")
        return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")

    mode = request.POST.get("generate_mode", "month")
    if mode == "from_date":
        try:
            start_date = datetime.strptime(
                request.POST.get("start_date", ""), "%Y-%m-%d"
            ).date()
        except ValueError:
            start_date = first_day
        if not first_day <= start_date <= last_day:
            messages.error(request, "作り直し開始日は表示中の月から選んでください。")
            return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")
    else:
        start_date = first_day

    current_index = rotation_ids.index(start_staff_id)
    warning_messages = []
    current = start_date

    while current <= last_day:
        calendar_day, _ = DutyCalendarDay.objects.select_for_update().get_or_create(
            duty_date=current
        )
        holiday_duty = _calendar_is_holiday_duty(current, calendar_day)

        if holiday_duty:
            calendar_day.holiday_day_shift = rotation[current_index]
            current_index = (current_index + 1) % len(rotation)
            calendar_day.night_shift = rotation[current_index]
            current_index = (current_index + 1) % len(rotation)
        else:
            calendar_day.holiday_day_shift = None
            calendar_day.night_shift = rotation[current_index]
            current_index = (current_index + 1) % len(rotation)

        calendar_day.save()
        warning_messages.extend(_sync_calendar_comp_records(calendar_day))
        board, _ = DailyBoard.objects.get_or_create(board_date=current)
        _sync_calendar_to_board(board)
        current += timedelta(days=1)

    if warning_messages:
        messages.warning(
            request,
            "勤務カレンダーは作成しましたが、代休設定に確認事項があります：" +
            " / ".join(warning_messages[:8])
        )
    else:
        messages.success(
            request,
            (
                f"{start_date:%Y/%m/%d} から {last_day:%Y/%m/%d} まで、"
                "勤務ローテーションを作成しました。"
            ),
        )

    return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={first_day:%Y-%m}")


@require_POST
@transaction.atomic
def duty_calendar_day_update(request):
    """日付ごとの区分・担当・代休日を手動修正する。直前状態は1段階だけ戻せる。"""
    duty_date = _parse_date(request.POST.get("duty_date"))
    calendar_day, _ = DutyCalendarDay.objects.select_for_update().get_or_create(
        duty_date=duty_date
    )
    _save_calendar_undo(calendar_day)

    day_mode = request.POST.get("day_mode", "auto")
    if day_mode not in dict(DutyCalendarDay.DAY_MODE_CHOICES):
        day_mode = "auto"

    def _staff_or_none(raw):
        if not raw:
            return None
        try:
            staff_id = int(raw)
        except (TypeError, ValueError):
            return None
        return Staff.objects.filter(pk=staff_id, is_active=True).first()

    night_staff = _staff_or_none(request.POST.get("night_staff_id"))
    holiday_staff = _staff_or_none(request.POST.get("holiday_staff_id"))

    calendar_day.day_mode = day_mode
    calendar_day.no_compensatory_leave = request.POST.get("no_compensatory_leave") in ("1", "on")
    calendar_day.force_compensatory_leave = False
    calendar_day.force_holiday_day_compensatory_leave = request.POST.get("force_holiday_day_compensatory_leave") in ("1", "on")
    calendar_day.force_night_compensatory_leave = request.POST.get("force_night_compensatory_leave") in ("1", "on")
    calendar_day.force_night_after_compensatory_leave = request.POST.get("force_night_after_compensatory_leave") in ("1", "on")
    if calendar_day.force_holiday_day_compensatory_leave or calendar_day.force_night_compensatory_leave or calendar_day.force_night_after_compensatory_leave:
        calendar_day.no_compensatory_leave = False
    calendar_day.night_shift = night_staff

    holiday_duty = _calendar_is_holiday_duty(duty_date, calendar_day)
    if holiday_duty:
        # 通常日から休日扱い/自動判定へ戻した時、休日日勤が空なら自動補完。
        if holiday_staff is None:
            holiday_staff = _rotation_neighbor(night_staff, 1, duty_date)
        calendar_day.holiday_day_shift = holiday_staff
    else:
        calendar_day.holiday_day_shift = None

    # Ver.3.10.3:
    # 通常日から「自動判定（土日祝）」へ戻した際、休日日勤は復活しても
    # その休日日勤由来の代休が表示・当日配置表へ反映されないケースを防ぐ。
    # 変更前の代休先も控えておき、最終的な代休レコード確定後に
    # 元日と新旧すべての代休先DailyBoardを同期する。
    old_comp_targets = set(
        CompensatoryLeaveRecord.objects.filter(source_date=duty_date)
        .values_list("target_date", flat=True)
    )

    calendar_day.save()
    warnings = _sync_calendar_comp_records(calendar_day)

    if not calendar_day.no_compensatory_leave and holiday_duty:
        _apply_manual_comp_date(calendar_day, "holiday_day", request.POST.get("holiday_comp_date", ""))
        _apply_manual_comp_date(calendar_day, "night", request.POST.get("night_comp_date", ""))
        _apply_manual_comp_date(calendar_day, "night_after", request.POST.get("night_after_comp_date", ""))

    # 手動代休日の適用後を最終状態として再同期する。
    # manual override は _sync_calendar_comp_records 側で維持される。
    warnings.extend(_sync_calendar_comp_records(calendar_day))

    board, _ = DailyBoard.objects.get_or_create(board_date=duty_date)
    _sync_calendar_to_board(board)

    new_comp_targets = set(
        CompensatoryLeaveRecord.objects.filter(source_date=duty_date)
        .values_list("target_date", flat=True)
    )
    for target_date in sorted(old_comp_targets | new_comp_targets):
        target_board, _ = DailyBoard.objects.get_or_create(board_date=target_date)
        _sync_calendar_to_board(target_board)

    if warnings:
        messages.warning(request, " / ".join(warnings))
    else:
        messages.success(request, f"{duty_date:%Y/%m/%d} の勤務カレンダー設定を保存しました。")
    return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={duty_date:%Y-%m}")


@require_POST
@transaction.atomic
def duty_calendar_day_undo(request):
    """指定日の直前の勤務カレンダー状態へ1回だけ戻す。"""
    duty_date = _parse_date(request.POST.get("duty_date"))
    history = DutyCalendarDayHistory.objects.select_for_update().filter(duty_date=duty_date).first()
    if not history:
        messages.warning(request, "この日に戻せる直前の変更はありません。")
        return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={duty_date:%Y-%m}")

    calendar_day, _ = DutyCalendarDay.objects.select_for_update().get_or_create(duty_date=duty_date)
    snapshot = history.snapshot or {}
    calendar_day.day_mode = snapshot.get("day_mode", "auto")
    calendar_day.night_shift_id = snapshot.get("night_shift_id")
    calendar_day.holiday_day_shift_id = snapshot.get("holiday_day_shift_id")
    calendar_day.no_compensatory_leave = bool(snapshot.get("no_compensatory_leave", False))
    calendar_day.force_compensatory_leave = bool(snapshot.get("force_compensatory_leave", False))
    calendar_day.force_holiday_day_compensatory_leave = bool(snapshot.get("force_holiday_day_compensatory_leave", snapshot.get("force_compensatory_leave", False)))
    calendar_day.force_night_compensatory_leave = bool(snapshot.get("force_night_compensatory_leave", snapshot.get("force_compensatory_leave", False)))
    calendar_day.force_night_after_compensatory_leave = bool(snapshot.get("force_night_after_compensatory_leave", snapshot.get("force_compensatory_leave", False)))
    calendar_day.save()
    board, _ = DailyBoard.objects.get_or_create(board_date=duty_date)
    _sync_calendar_to_board(board)

    old_records = list(CompensatoryLeaveRecord.objects.filter(source_date=duty_date))
    old_touched = [(r.target_date, r.staff_name) for r in old_records]
    CompensatoryLeaveRecord.objects.filter(source_date=duty_date).delete()
    for item in snapshot.get("comp_records", []):
        try:
            target_date = datetime.strptime(item.get("target_date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        CompensatoryLeaveRecord.objects.create(
            source_date=duty_date,
            target_date=target_date,
            duty_type=item.get("duty_type", "night"),
            staff_name=item.get("staff_name", ""),
            is_manual_override=bool(item.get("is_manual_override", False)),
        )
    for target_date, staff_name in old_touched:
        _remove_name_from_comp_leave_board(target_date, staff_name)
    history.delete()
    messages.success(request, f"{duty_date:%Y/%m/%d} をひとつ前の状態に戻しました。")
    return redirect(f"{reverse('daily_assignment:monthly_duty')}?month={duty_date:%Y-%m}")


@require_GET
def export_excel(request):
    board_date = _parse_date(request.GET.get("date"))
    board, _ = DailyBoard.objects.get_or_create(board_date=board_date)
    areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    time_slots = _active_time_slots(include_special=True)
    rule_map = _time_rule_map(areas, time_slots)
    cell_map = {(c.area_id, c.row_key, c.slot_index): c.value for c in board.cells.all()}
    lunch_map = {e.time_slot_id: e.value for e in board.lunch_break_entries.all()}

    wb = Workbook()
    ws = wb.active
    ws.title = board_date.strftime("%Y-%m-%d")
    dark = PatternFill("solid", fgColor="17324D")
    darker = PatternFill("solid", fgColor="10273B")
    light = PatternFill("solid", fgColor="EAF1F7")
    lunch_fill = PatternFill("solid", fgColor="FFF3BF")
    gray = PatternFill("solid", fgColor="E5E7EB")
    white_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="7890A5")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    total_cols = 1 + sum(a.slot_count for a in areas) + 1
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(total_cols, 2))
    ws.cell(1, 1, f"当日配置表　{board_date:%Y年%m月%d日}")
    ws.cell(1, 1).font = Font(size=16, bold=True)
    ws.cell(1, 1).alignment = Alignment(horizontal="center")

    # 2段ヘッダー
    ws.merge_cells(start_row=3, start_column=1, end_row=4, end_column=1)
    ws.cell(3, 1, "時間")
    col = 2
    area_cols = []
    current_category = None
    category_start = None
    for idx, area in enumerate(areas):
        if current_category != (area.category or "その他"):
            if current_category is not None:
                ws.merge_cells(start_row=3, start_column=category_start, end_row=3, end_column=col - 1)
                ws.cell(3, category_start, current_category)
            current_category = area.category or "その他"
            category_start = col
        start = col
        end = col + area.slot_count - 1
        if end > start:
            ws.merge_cells(start_row=4, start_column=start, end_row=4, end_column=end)
        ws.cell(4, start, area.name)
        area_cols.append((area, start, end))
        col = end + 1
    if current_category is not None:
        ws.merge_cells(start_row=3, start_column=category_start, end_row=3, end_column=col - 1)
        ws.cell(3, category_start, current_category)
    ws.cell(3, col, "勤務")
    ws.cell(4, col, "昼休憩")
    lunch_col = col

    for r in (3, 4):
        for c in range(1, lunch_col + 1):
            cell = ws.cell(r, c)
            cell.fill = darker if r == 3 else dark
            cell.font = white_font
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center")

    row = 5
    area_layouts = {a.id: _area_row_layout(a, time_slots, rule_map) for a in areas}
    for row_index, slot in enumerate(time_slots):
        ws.cell(row, 1, slot.label)
        ws.cell(row, 1).fill = dark
        ws.cell(row, 1).font = white_font
        ws.cell(row, 1).border = border
        ws.cell(row, 1).alignment = Alignment(horizontal="center", vertical="center")
        for area, start, end in area_cols:
            layout = area_layouts[area.id][row_index]
            for slot_index, c in enumerate(range(start, end + 1), start=1):
                cell = ws.cell(row, c)
                cell.border = border
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                if layout["hidden"]:
                    cell.fill = gray
                elif not layout["show"]:
                    # 前のrowspanに相当するためExcelでも上のセルと縦結合する。
                    cell.fill = light
                else:
                    cell.value = cell_map.get((area.id, layout["owner_key"], slot_index), "")
                    cell.fill = lunch_fill if slot.is_lunch_highlight else light
        lunch_cell = ws.cell(row, lunch_col, lunch_map.get(slot.id, "") if slot.kind == "time" else "")
        lunch_cell.border = border
        lunch_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        lunch_cell.fill = lunch_fill if slot.is_lunch_highlight else light
        row += 1

    # 画面の縦結合（時間枠設定）をExcelにも反映する。
    # _area_row_layout() の rowspan をそのまま使うため、別の結合判定は行わない。
    first_data_row = 5
    for area, start, end in area_cols:
        layouts = area_layouts[area.id]
        for row_index, layout in enumerate(layouts):
            span = int(layout.get("rowspan") or 0)
            if not layout.get("show") or layout.get("hidden") or span <= 1:
                continue
            merge_start_row = first_data_row + row_index
            merge_end_row = merge_start_row + span - 1
            for c in range(start, end + 1):
                ws.merge_cells(
                    start_row=merge_start_row, start_column=c,
                    end_row=merge_end_row, end_column=c,
                )
                merged_cell = ws.cell(merge_start_row, c)
                merged_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    row += 2
    meta_items = [
        ("会議等", board.conference), ("夜勤", board.night_shift), ("明け", board.night_shift_after),
        ("休日日勤", board.holiday_day_shift), ("代休①", board.compensatory_leave_1),
        ("代休②", board.compensatory_leave_2), ("代休③", board.compensatory_leave_3),
        ("年休①", board.annual_leave_1), ("年休②", board.annual_leave_2),
        ("人数状況 午前", board.staffing_am), ("人数状況 午後", board.staffing_pm),
        ("Free", board.free_text), ("コメント", board.comment),
        ("配置未定", board.unassigned), ("受付（年休）", board.reception_leave),
    ]
    for label, value in meta_items:
        ws.cell(row, 1, label).fill = dark
        ws.cell(row, 1).font = white_font
        ws.cell(row, 1).border = border
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=max(2, lunch_col))
        out = ws.cell(row, 2, value)
        out.border = border
        out.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    ws.freeze_panes = "B5"
    ws.column_dimensions["A"].width = 12
    for c in range(2, lunch_col + 1):
        ws.column_dimensions[get_column_letter(c)].width = 12
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    output = BytesIO()
    wb.save(output)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="assignment_{board_date:%Y%m%d}.xlsx"'
    return response


# =========================================================
# Ver.3.2 トップページ用：本日の配置表SVGプレビュー
# =========================================================
@require_GET
def board_preview_svg(request):
    """当日の配置表を軽量なSVG画像として返す。ファイル保存はしない。"""
    from html import escape

    board_date = _parse_date(request.GET.get("date"))
    board = DailyBoard.objects.filter(board_date=board_date).first()
    areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    time_slots = _active_time_slots(include_special=True)

    if not areas or not time_slots:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="260" viewBox="0 0 900 260"><rect width="100%" height="100%" fill="#f8fafc"/><text x="450" y="125" text-anchor="middle" font-family="sans-serif" font-size="22" fill="#64748b">配置表の設定がありません</text></svg>'
        return HttpResponse(svg, content_type="image/svg+xml; charset=utf-8")

    cells = {}
    if board:
        cells = {(c.area_id, c.row_key, c.slot_index): c.value for c in board.cells.filter(area__in=areas)}

    rule_map = _time_rule_map(areas, time_slots)
    layouts = {a.id: _area_row_layout(a, time_slots, rule_map) for a in areas}
    time_w, slot_w, header_h1, header_h2, row_h = 74, 92, 32, 34, 38
    total_slots = sum(a.slot_count for a in areas)
    width = time_w + total_slots * slot_w
    height = header_h1 + header_h2 + len(time_slots) * row_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    out.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    out.append('<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif}.h{fill:#fff;font-weight:700}.n{fill:#20364d;font-size:13px}.small{font-size:12px}</style>')
    out.append(f'<rect x="0" y="0" width="{time_w}" height="{header_h1+header_h2}" fill="#17324d" stroke="#cbd5e1"/>')
    out.append(f'<text class="h" x="{time_w/2}" y="{(header_h1+header_h2)/2+5}" text-anchor="middle" font-size="14">時間</text>')

    x=time_w; current_cat=None; cat_start=x; cat_width=0
    for area in areas:
        aw=area.slot_count*slot_w; cat=area.category or "その他"
        if current_cat is None: current_cat=cat; cat_start=x; cat_width=0
        if cat != current_cat:
            out.append(f'<rect x="{cat_start}" y="0" width="{cat_width}" height="{header_h1}" fill="#17324d" stroke="#cbd5e1"/>')
            out.append(f'<text class="h" x="{cat_start+cat_width/2}" y="21" text-anchor="middle" font-size="14">{escape(current_cat)}</text>')
            current_cat=cat; cat_start=x; cat_width=0
        out.append(f'<rect x="{x}" y="{header_h1}" width="{aw}" height="{header_h2}" fill="#284b63" stroke="#cbd5e1"/>')
        out.append(f'<text class="h small" x="{x+aw/2}" y="{header_h1+21}" text-anchor="middle">{escape(area.name)}</text>')
        cat_width += aw; x += aw
    if current_cat is not None:
        out.append(f'<rect x="{cat_start}" y="0" width="{cat_width}" height="{header_h1}" fill="#17324d" stroke="#cbd5e1"/>')
        out.append(f'<text class="h" x="{cat_start+cat_width/2}" y="21" text-anchor="middle" font-size="14">{escape(current_cat)}</text>')

    for ri, ts in enumerate(time_slots):
        y=header_h1+header_h2+ri*row_h; lunch=bool(ts.is_lunch_highlight)
        time_fill='#9a7a19' if lunch else ('#284b63' if ts.kind!='time' else '#17324d')
        out.append(f'<rect x="0" y="{y}" width="{time_w}" height="{row_h}" fill="{time_fill}" stroke="#cbd5e1"/>')
        out.append(f'<text class="h small" x="{time_w/2}" y="{y+24}" text-anchor="middle">{escape(ts.label)}</text>')
        x=time_w
        for area in areas:
            layout=layouts[area.id][ri]
            for si in range(1, area.slot_count+1):
                if not layout["show"]:
                    x += slot_w; continue
                cell_h=row_h*max(1,layout["rowspan"]); fill='#fff6cc' if lunch else ('#eef3f8' if ts.kind!='time' else '#f8fbff')
                if layout["hidden"]: fill='#e5e7eb'
                out.append(f'<rect x="{x}" y="{y}" width="{slot_w}" height="{cell_h}" fill="{fill}" stroke="#cbd5e1"/>')
                if not layout["hidden"] and layout["owner_key"]:
                    val=cells.get((area.id,layout["owner_key"],si),'')
                    if val:
                        out.append(f'<text class="n" x="{x+slot_w/2}" y="{y+cell_h/2+5}" text-anchor="middle">{escape(val[:18])}</text>')
                x += slot_w
    out.append('</svg>')
    response=HttpResponse(''.join(out),content_type="image/svg+xml; charset=utf-8")
    response['Cache-Control']='no-store, max-age=0'
    return response


@require_POST
@transaction.atomic
def settings_bulk_save(request):
    """設定画面の既存レコードを一括検証し、1トランザクションで保存する。"""
    is_json = request.content_type == "application/json"
    if is_json:
        try:
            post_data = json.loads(request.body.decode("utf-8"))
            if not isinstance(post_data, dict):
                raise ValueError
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return JsonResponse({"ok": False, "error": "送信データが正しくありません。"}, status=400)
    else:
        post_data = request.POST
    staffs = list(Staff.objects.all().order_by("id"))
    areas = list(AssignmentArea.objects.all().order_by("id"))
    comp_rules = list(CompensatoryLeaveRule.objects.all().order_by("id"))
    time_slots = list(TimeSlot.objects.all().order_by("id"))
    merge_rules = list(HorizontalMergeRule.objects.all().order_by("id"))
    derived_rules = list(DerivedAssignmentRule.objects.all().order_by("id"))
    comment_templates = list(DailyCommentTemplate.objects.all().order_by("id"))

    area_by_id = {a.id: a for a in areas}
    slot_by_id = {slot.id: slot for slot in time_slots}
    valid_area_modes = {value for value, _ in AssignmentArea.AUTO_ASSIGNMENT_MODE_CHOICES}
    valid_overlap_modes = {value for value, _ in AssignmentArea.OVERLAP_ASSIGNMENT_MODE_CHOICES}
    valid_comp_days = {value for value, _ in CompensatoryLeaveRule.DAY_TYPE_CHOICES}
    valid_comp_duties = {value for value, _ in CompensatoryLeaveRule.DUTY_TYPE_CHOICES}
    valid_comp_offsets = {value for value, _ in CompensatoryLeaveRule.OFFSET_MODE_CHOICES}
    valid_time_kinds = {value for value, _ in TimeSlot.KIND_CHOICES}
    valid_area_time_modes = {value for value, _ in AreaTimeRule.MODE_CHOICES}
    valid_derived_actions = {value for value, _ in DerivedAssignmentRule.ACTION_CHOICES}
    valid_comment_input_types = {value for value, _ in DailyCommentTemplate.INPUT_TYPE_CHOICES}
    valid_comment_font_sizes = {value for value, _ in DailyCommentTemplate.FONT_SIZE_CHOICES}

    def parse_int(raw, label, minimum=None, maximum=None):
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{label}は整数で入力してください。")
        if minimum is not None and value < minimum:
            raise ValueError(f"{label}は{minimum}以上で入力してください。")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label}は{maximum}以下で入力してください。")
        return value

    def parse_work_time(raw, owner, label):
        raw = str(raw or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%H:%M").time()
        except ValueError:
            raise ValueError(f"{owner}さんの{label}が正しくありません。")

    def parse_area_ref(raw, owner, label, allow_blank=True):
        raw = str(raw or "").strip()
        if not raw and allow_blank:
            return None
        try:
            area = area_by_id[int(raw)]
        except (ValueError, KeyError):
            raise ValueError(f"{owner}の{label}が正しくありません。")
        return area

    def parse_slot_ref(raw, owner, label):
        try:
            slot = slot_by_id[int(str(raw or '').strip())]
        except (ValueError, KeyError):
            raise ValueError(f"{owner}の{label}が正しくありません。")
        return slot

    try:
        # 1) スタッフ基本・勤務ローテーション + 自動配置ルール
        staff_changes = []
        seen_staff_names = set()
        for staff in staffs:
            p = f"staff_{staff.id}_"
            name = post_data.get(p + "name", staff.name).strip()
            if not name:
                raise ValueError("スタッフ名を空欄にはできません。")
            if name in seen_staff_names:
                raise ValueError(f"スタッフ名「{name}」が重複しています。")
            seen_staff_names.add(name)
            avoid_ids = []
            weekly_targets = []
            for area in areas:
                if post_data.get(f"{p}avoid_{area.id}") == "on":
                    avoid_ids.append(area.id)
                raw_count = post_data.get(f"{p}weekly_{area.id}_count", "0")
                count = parse_int(raw_count or 0, f"{name}さんの{area.name}週回数", 0, 7)
                if count > 0:
                    weekly_targets.append((area.id, count))
            staff_changes.append({
                "staff": staff,
                "name": name,
                "display_order": parse_int(post_data.get(p + "display_order", staff.display_order), f"{name}さんの表示順", 0),
                "work_start_time": parse_work_time(post_data.get(p + "work_start_time", staff.work_start_time.strftime("%H:%M") if staff.work_start_time else ""), name, "勤務開始"),
                "work_end_time": parse_work_time(post_data.get(p + "work_end_time", staff.work_end_time.strftime("%H:%M") if staff.work_end_time else ""), name, "勤務終了"),
                "duty_rotation_order": parse_int(post_data.get(p + "duty_rotation_order", staff.duty_rotation_order), f"{name}さんの勤務順", 0),
                "duty_rotation_enabled": post_data.get(p + "duty_rotation_enabled") == "on",
                "main_rotation_enabled": post_data.get(p + "main_rotation_enabled") == "on",
                "main_rotation_order": parse_int(post_data.get(p + "main_rotation_order", staff.main_rotation_order), f"{name}さんの主担当順", 0),
                "is_active": post_data.get(p + "is_active") == "on",
                "auto_assignment_enabled": post_data.get(p + "auto_assignment_enabled") == "on",
                "assignment_staff_priority": parse_int(post_data.get(p + "assignment_staff_priority", staff.assignment_staff_priority), f"{name}さんの配置優先度", 1, 4),
                "dedicated_area": parse_area_ref(post_data.get(p + "dedicated_area"), name, "専任"),
                "preferred_area_1": parse_area_ref(post_data.get(p + "preferred_area_1"), name, "優先①"),
                "preferred_area_2": parse_area_ref(post_data.get(p + "preferred_area_2"), name, "優先②"),
                "preferred_area_3": parse_area_ref(post_data.get(p + "preferred_area_3"), name, "優先③"),
                "preferred_area_4": parse_area_ref(post_data.get(p + "preferred_area_4"), name, "優先④"),
                "avoid_ids": avoid_ids,
                "weekly_targets": weekly_targets,
            })

        for item in staff_changes:
            if item["work_start_time"] and item["work_end_time"] and item["work_start_time"] > item["work_end_time"]:
                raise ValueError(f"{item['name']}さんの勤務可能時間は開始≦終了にしてください。")

        # 2) 配置場所
        area_changes = []
        seen_area_names = set()
        for area in areas:
            p = f"area_{area.id}_"
            category = post_data.get(p + "category", area.category).strip()
            name = post_data.get(p + "name", area.name).strip()
            if not name:
                raise ValueError(f"配置場所ID {area.id} の配置場所名を入力してください。")
            key = (category, name)
            if key in seen_area_names:
                raise ValueError(f"「{category} / {name}」が重複しています。")
            seen_area_names.add(key)
            slot_count = parse_int(post_data.get(p + "slot_count", area.slot_count), f"{name}の入力列数", 1, 10)
            auto_count = parse_int(post_data.get(p + "auto_assignment_count", area.auto_assignment_count), f"{name}の自動配置人数", 0, slot_count)
            min_count = parse_int(post_data.get(p + "minimum_assignment_count", area.minimum_assignment_count), f"{name}の最低必要人数", 0, auto_count)
            priority = parse_int(post_data.get(p + "assignment_priority", area.assignment_priority), f"{name}の配置優先度", 1, 5)
            overlap_mode = post_data.get(p + "overlap_assignment_mode", area.overlap_assignment_mode).strip()
            if overlap_mode not in valid_overlap_modes:
                raise ValueError(f"{name}の重複配置設定が正しくありません。")
            mode = post_data.get(p + "auto_assignment_mode", area.auto_assignment_mode).strip()
            if mode not in valid_area_modes:
                raise ValueError(f"{name}の自動配置方式が正しくありません。")
            area_changes.append({
                "area": area, "category": category, "name": name, "slot_count": slot_count,
                "auto_assignment_count": auto_count, "minimum_assignment_count": min_count,
                "assignment_priority": priority, "overlap_assignment_mode": overlap_mode, "auto_assignment_mode": mode,
                "display_order": parse_int(post_data.get(p + "display_order", area.display_order), f"{name}の表示順", 0),
                "main_rotation_enabled": post_data.get(p + "main_rotation_enabled") == "on",
                "main_rotation_order": parse_int(post_data.get(p + "main_rotation_order", area.main_rotation_order), f"{name}の主担当順", 0),
                "is_active": post_data.get(p + "is_active") == "on",
            })

        # 3) 土日・祝日の代休ルール
        comp_changes = []
        seen_comp_pairs = set()
        for rule in comp_rules:
            p = f"comp_{rule.id}_"
            day_type = post_data.get(p + "day_type", rule.day_type).strip()
            duty_type = post_data.get(p + "duty_type", rule.duty_type).strip()
            offset_mode = post_data.get(p + "offset_mode", rule.offset_mode).strip()
            if day_type not in valid_comp_days or duty_type not in valid_comp_duties or offset_mode not in valid_comp_offsets:
                raise ValueError("代休ルールの選択項目が正しくありません。")
            pair = (day_type, duty_type)
            if pair in seen_comp_pairs:
                raise ValueError("同じ勤務日・勤務種類の代休ルールが重複しています。")
            seen_comp_pairs.add(pair)
            comp_changes.append({
                "rule": rule, "day_type": day_type, "duty_type": duty_type, "offset_mode": offset_mode,
                "weeks_after": parse_int(post_data.get(p + "weeks_after", rule.weeks_after), "代休ルールの何週後", 0),
                "target_weekday": parse_int(post_data.get(p + "target_weekday", rule.target_weekday), "代休曜日", 0, 6),
                "days_after": parse_int(post_data.get(p + "days_after", rule.days_after), "代休ルールの何日後", 1),
                "is_active": post_data.get(p + "is_active") == "on",
            })

        # 4) 時間枠
        slot_changes = []
        for slot in time_slots:
            p = f"slot_{slot.id}_"
            label = post_data.get(p + "label", slot.label).strip()
            kind = post_data.get(p + "kind", slot.kind).strip()
            if not label:
                raise ValueError(f"時間枠「{slot.key}」の表示名を入力してください。")
            if kind not in valid_time_kinds:
                raise ValueError(f"時間枠「{label}」の種類が正しくありません。")
            slot_changes.append({
                "slot": slot, "label": label, "kind": kind,
                "display_order": parse_int(post_data.get(p + "display_order", slot.display_order), f"{label}の表示順", 0),
                "is_active": post_data.get(p + "is_active") == "on",
                "is_lunch_highlight": post_data.get(p + "is_lunch_highlight") == "on",
            })

        # 5) 配置場所ごとの時間枠（現在画面に表示されている組合せ）
        area_time_changes = []
        for area in [a for a in areas if a.is_active]:
            for slot in [t for t in time_slots if t.is_active]:
                field = f"area_time_{area.id}_{slot.id}"
                if field not in post_data:
                    continue
                mode = post_data.get(field, "normal")
                if mode not in valid_area_time_modes:
                    raise ValueError(f"{area.name} / {slot.label} の時間枠設定が正しくありません。")
                auto_field = f"area_auto_{area.id}_{slot.id}"
                auto_enabled = post_data.get(auto_field) == "on"
                area_time_changes.append((area, slot, mode, auto_enabled))

        # 6) 横結合ルール
        merge_changes = []
        for rule in merge_rules:
            p = f"hmerge_{rule.id}_"
            name = post_data.get(p + "name", rule.name).strip() or "横結合"
            slot = parse_slot_ref(post_data.get(p + "time_slot", rule.time_slot_id), name, "時間")
            start = parse_area_ref(post_data.get(p + "start_area", rule.start_area_id), name, "開始配置", allow_blank=False)
            end = parse_area_ref(post_data.get(p + "end_area", rule.end_area_id), name, "終了配置", allow_blank=False)
            representative = parse_area_ref(post_data.get(p + "representative_area", rule.representative_area_id), name, "代表配置", allow_blank=False)
            merge_changes.append({"rule": rule, "name": name, "time_slot": slot, "start_area": start, "end_area": end, "representative_area": representative, "exclude_from_auto_assignment": post_data.get(p + "exclude") == "on", "is_active": post_data.get(p + "is_active") == "on"})

        # 7) 時間連動ルール
        derived_changes = []
        for rule in derived_rules:
            p = f"derived_{rule.id}_"
            name = post_data.get(p + "name", rule.name).strip()
            source = parse_area_ref(post_data.get(p + "source_area", rule.source_area_id), name or f"ルール{rule.id}", "元配置", allow_blank=False)
            slot = parse_slot_ref(post_data.get(p + "time_slot", rule.time_slot_id), name or f"ルール{rule.id}", "時間")
            action = post_data.get(p + "action", rule.action).strip()
            if action not in valid_derived_actions:
                raise ValueError(f"{name or '時間連動ルール'}の動作が正しくありません。")
            target = parse_area_ref(post_data.get(p + "target_area", rule.target_area_id or ""), name or f"ルール{rule.id}", "兼任・移動先")
            if action in ("add", "move") and target is None:
                raise ValueError(f"{name or '時間連動ルール'}は兼任・移動先を指定してください。")
            if action == "break":
                target = None
            derived_changes.append({
                "rule": rule, "name": name, "source_area": source, "time_slot": slot,
                "action": action, "target_area": target,
                "is_active": post_data.get(p + "is_active") == "on",
            })

        # 8) 当日配置表・定型コメント
        comment_template_changes = []
        for template in comment_templates:
            p = f"comment_{template.id}_"
            label = str(post_data.get(p + "label", template.label) or "").strip()
            if not label:
                raise ValueError("定型コメントの表示文を空欄にはできません。")
            input_type = str(post_data.get(p + "input_type", template.input_type) or "").strip()
            if input_type not in valid_comment_input_types:
                raise ValueError(f"定型コメント「{label}」の入力タイプが正しくありません。")
            font_size = parse_int(post_data.get(p + "font_size", template.font_size), f"定型コメント「{label}」の文字サイズ")
            if font_size not in valid_comment_font_sizes:
                raise ValueError(f"定型コメント「{label}」の文字サイズが正しくありません。")
            comment_template_changes.append({
                "template": template,
                "label": label[:120],
                "input_type": input_type,
                "unit": str(post_data.get(p + "unit", template.unit) or "").strip()[:30],
                "font_size": font_size,
                "display_order": parse_int(post_data.get(p + "display_order", template.display_order), f"定型コメント「{label}」の表示順", 0),
                "is_active": post_data.get(p + "is_active") == "on",
            })

    except ValueError as exc:
        if is_json:
            return JsonResponse({"ok": False, "error": f"保存できませんでした：{exc}"}, status=400)
        messages.error(request, f"保存できませんでした：{exc}")
        return redirect("daily_assignment:settings")

    # 全項目の検証完了後にまとめて保存。ここから先はトランザクション内。
    for item in staff_changes:
        staff = item.pop("staff")
        avoid_ids = item.pop("avoid_ids")
        weekly_targets = item.pop("weekly_targets")
        for field, value in item.items():
            setattr(staff, field, value)
        staff.save()
        StaffAvoidArea.objects.filter(staff=staff).exclude(area_id__in=avoid_ids).delete()
        for area_id in avoid_ids:
            StaffAvoidArea.objects.get_or_create(staff=staff, area_id=area_id)
        StaffWeeklyTarget.objects.filter(staff=staff).delete()
        StaffWeeklyTarget.objects.bulk_create([
            StaffWeeklyTarget(staff=staff, area_id=area_id, target_count=count)
            for area_id, count in weekly_targets
        ])

    for item in area_changes:
        area = item.pop("area")
        new_slot_count = item["slot_count"]
        if new_slot_count < area.slot_count:
            AssignmentCell.objects.filter(area=area, slot_index__gt=new_slot_count).delete()
        for field, value in item.items():
            setattr(area, field, value)
        area.save()

    for item in comp_changes:
        rule = item.pop("rule")
        for field, value in item.items():
            setattr(rule, field, value)
        rule.save()

    for item in slot_changes:
        slot = item.pop("slot")
        for field, value in item.items():
            setattr(slot, field, value)
        slot.save()

    for area, slot, mode, auto_enabled in area_time_changes:
        # 通常表示かつ自動ONならレコード不要。それ以外は設定を保持する。
        if mode == "normal" and auto_enabled:
            AreaTimeRule.objects.filter(area=area, time_slot=slot).delete()
        else:
            AreaTimeRule.objects.update_or_create(
                area=area, time_slot=slot,
                defaults={"mode": mode, "auto_assignment_enabled": auto_enabled},
            )

    for item in merge_changes:
        rule = item.pop("rule")
        for field, value in item.items():
            setattr(rule, field, value)
        rule.save()

    for item in derived_changes:
        rule = item.pop("rule")
        for field, value in item.items():
            setattr(rule, field, value)
        rule.save()

    for item in comment_template_changes:
        template = item.pop("template")
        for field, value in item.items():
            setattr(template, field, value)
        template.save()

    if is_json:
        return JsonResponse({"ok": True, "message": "スタッフ・勤務・配置・代休・時間ルール・定型コメントをすべて上書き保存しました。"})
    messages.success(request, "スタッフ・勤務・配置・代休・時間ルール・定型コメントをすべて上書き保存しました。")
    return redirect("daily_assignment:settings")



def _week_bounds(raw=""):
    base = _parse_date(raw) if raw else timezone.localdate()
    monday = base - timedelta(days=base.weekday())
    return monday, monday + timedelta(days=6)


@require_GET
def weekly_planner_view(request):
    monday, sunday = _week_bounds(request.GET.get("week", ""))
    # Ver.3.9.8.4: 週間配置作成は平日（月～金）のみ表示・集計する。
    # 長期休暇の期間登録では従来どおり日曜まで指定できるよう sunday は保持する。
    friday = monday + timedelta(days=4)
    days = [monday + timedelta(days=i) for i in range(5)]
    staffs = list(Staff.objects.filter(is_active=True).order_by("display_order", "name"))
    absences = list(WeeklyAbsence.objects.filter(absence_date__range=(monday, friday)).select_related("staff", "start_slot", "end_slot"))
    absence_map = {(a.staff_id, a.absence_date): a for a in absences}
    rows = []
    for staff in staffs:
        rows.append({"staff": staff, "days": [{"date": d, "absence": absence_map.get((staff.id, d))} for d in days]})

    # 週間配置サマリー：昼休憩ハイライト時間（通常12:00）は通常配置回数から除外し、
    # 時間連動による昼当番・移動は「昼」として日数だけ別集計する。
    summary = []
    lunch_keys = set(TimeSlot.objects.filter(is_active=True, kind="time", is_lunch_highlight=True).values_list("key", flat=True))
    all_time_keys = set(TimeSlot.objects.filter(is_active=True, kind="time").values_list("key", flat=True))
    if not lunch_keys:
        lunch_keys = {key for key in all_time_keys if key == "1200"}
    normal_summary_keys = list(all_time_keys - lunch_keys)
    for staff in staffs:
        area_days = defaultdict(set)
        lunch_days = set()
        free_days = set(StaffFreeHistory.objects.filter(staff=staff, board_date__range=(monday, friday)).values_list("board_date", flat=True))
        for item in AssignmentCell.objects.filter(
            board__board_date__range=(monday, friday), value=staff.name, row_key__in=normal_summary_keys,
        ).values("board__board_date", "area__name", "area__category"):
            label = f"{item['area__category']} / {item['area__name']}" if item["area__category"] else item["area__name"]
            area_days[label].add(item["board__board_date"])
        if lunch_keys:
            lunch_days.update(AssignmentCell.objects.filter(
                board__board_date__range=(monday, friday), value=staff.name, row_key__in=lunch_keys,
            ).values_list("board__board_date", flat=True))
        summary.append({
            "staff": staff, "areas": sorted([(k, len(v)) for k, v in area_days.items()]),
            "lunch_count": len(lunch_days), "free_count": len(free_days),
        })

    # 予定時のみ配置を週間画面で先に指定できるようにする。
    scheduled_areas = list(
        AssignmentArea.objects.filter(is_active=True, auto_assignment_mode="scheduled")
        .order_by("assignment_priority", "display_order", "id")
    )
    existing_boards = {
        b.board_date: b
        for b in DailyBoard.objects.filter(board_date__range=(monday, friday))
    }
    activation_map = set()
    if existing_boards:
        activation_map = set(
            DailyAreaActivation.objects.filter(
                board__in=existing_boards.values(), is_enabled=True
            ).values_list("board__board_date", "area_id")
        )
    scheduled_rows = [
        {
            "area": area,
            "days": [
                {"date": d, "enabled": (d, area.id) in activation_map}
                for d in days
            ],
        }
        for area in scheduled_areas
    ]

    main_override = MainRotationOverride.objects.filter(week_start=monday).first()
    main_map = _main_rotation_mapping(monday)
    main_rows = []
    for area in AssignmentArea.objects.filter(is_active=True, main_rotation_enabled=True).order_by("main_rotation_order", "display_order", "id"):
        st = main_map.get(area.id)
        dedicated = bool(st and st.dedicated_area_id == area.id)
        main_rows.append({"area": area, "staff": st, "dedicated": dedicated})

    return render(request, "daily_assignment/weekly_planner.html", {
        "monday": monday, "friday": friday, "sunday": sunday, "days": days, "rows": rows,
        "previous_week": monday - timedelta(days=7), "next_week": monday + timedelta(days=7),
        "time_slots": TimeSlot.objects.filter(is_active=True, kind="time").order_by("display_order", "id"),
        "kind_choices": [choice for choice in WeeklyAbsence.KIND_CHOICES if choice[0] != "reception"], "period_choices": WeeklyAbsence.PERIOD_CHOICES,
        "summary": summary, "main_rows": main_rows,
        "scheduled_rows": scheduled_rows,
        "staff_options": staffs,
        "main_rotation_manual_offset": main_override.offset if main_override else 0,
        "main_rotation_is_manual": bool(main_override and main_override.offset),
    })


@require_GET
def area_staff_matrix_view(request):
    """配置場所ごとに、配置可能スタッフ・設定週回数・今週実績を逆引き表示する。"""
    monday, _ = _week_bounds(request.GET.get("week", ""))
    friday = monday + timedelta(days=4)
    previous_week = monday - timedelta(days=7)
    next_week = monday + timedelta(days=7)

    areas = list(AssignmentArea.objects.filter(is_active=True).order_by("display_order", "id"))
    if not areas:
        return render(request, "daily_assignment/area_staff_matrix.html", {
            "areas": [], "selected_area": None, "rows": [], "monday": monday, "friday": friday,
            "previous_week": previous_week, "next_week": next_week,
            "include_unavailable": request.GET.get("include_unavailable") == "1",
        })

    try:
        area_id = int(request.GET.get("area") or areas[0].id)
    except (TypeError, ValueError):
        area_id = areas[0].id
    selected_area = next((a for a in areas if a.id == area_id), areas[0])

    include_unavailable = request.GET.get("include_unavailable") == "1"
    staffs = list(
        Staff.objects.filter(is_active=True)
        .select_related("dedicated_area", "preferred_area_1", "preferred_area_2", "preferred_area_3", "preferred_area_4")
        .prefetch_related("avoid_area_entries", "weekly_targets")
        .order_by("display_order", "name")
    )

    avoid_map = {
        staff.id: {item.area_id for item in staff.avoid_area_entries.all()}
        for staff in staffs
    }
    target_map = {
        staff.id: {item.area_id: item.target_count for item in staff.weekly_targets.all()}
        for staff in staffs
    }
    backup_map = {
        item.staff_id: item.priority
        for item in DedicatedAreaBackup.objects.filter(area=selected_area).select_related("staff")
    }

    # 週間配置作成のサマリーと同じ考え方で、昼休憩色の時間帯は通常配置回数から除外する。
    time_qs = TimeSlot.objects.filter(is_active=True, kind="time")
    lunch_keys = set(time_qs.filter(is_lunch_highlight=True).values_list("key", flat=True))
    all_time_keys = set(time_qs.values_list("key", flat=True))
    if not lunch_keys and "1200" in all_time_keys:
        lunch_keys = {"1200"}
    count_keys = list(all_time_keys - lunch_keys)

    weekly_days = defaultdict(set)
    if staffs and count_keys:
        for item in AssignmentCell.objects.filter(
            board__board_date__range=(monday, friday),
            area=selected_area,
            row_key__in=count_keys,
            value__in=[st.name for st in staffs],
        ).values("value", "board__board_date"):
            weekly_days[item["value"]].add(item["board__board_date"])

    rows = []
    for staff in staffs:
        available = True
        unavailable_reason = ""
        relation = "通常配置可能"
        relation_rank = 50

        if not staff.auto_assignment_enabled:
            available = False
            unavailable_reason = "通常配置の自動作成対象がOFF"
            relation = "自動対象外"
            relation_rank = 90
        elif staff.dedicated_area_id == selected_area.id:
            relation = "専任"
            relation_rank = 0
        elif staff.dedicated_area_id and staff.dedicated_area_id != selected_area.id:
            available = False
            unavailable_reason = f"{staff.dedicated_area.name} の専任"
            relation = "他配置の専任"
            relation_rank = 91
        elif selected_area.id in avoid_map.get(staff.id, set()):
            available = False
            unavailable_reason = "「配置しない」に設定"
            relation = "配置不可"
            relation_rank = 92
        elif staff.id in backup_map:
            relation = f"専任代理{backup_map[staff.id]}"
            relation_rank = 5 + backup_map[staff.id]
        elif staff.preferred_area_1_id == selected_area.id:
            relation = "優先①"
            relation_rank = 10
        elif staff.preferred_area_2_id == selected_area.id:
            relation = "優先②"
            relation_rank = 20
        elif staff.preferred_area_3_id == selected_area.id:
            relation = "優先③"
            relation_rank = 30
        elif staff.preferred_area_4_id == selected_area.id:
            relation = "優先④"
            relation_rank = 40

        weekly_target = target_map.get(staff.id, {}).get(selected_area.id, 0)
        weekly_actual = len(weekly_days.get(staff.name, set()))
        if available or include_unavailable:
            rows.append({
                "staff": staff,
                "available": available,
                "unavailable_reason": unavailable_reason,
                "relation": relation,
                "relation_rank": relation_rank,
                "weekly_target": weekly_target,
                "weekly_actual": weekly_actual,
            })

    rows.sort(key=lambda x: (
        0 if x["available"] else 1,
        x["relation_rank"],
        x["staff"].assignment_staff_priority,
        x["staff"].display_order,
        x["staff"].name,
    ))

    return render(request, "daily_assignment/area_staff_matrix.html", {
        "areas": areas,
        "selected_area": selected_area,
        "rows": rows,
        "monday": monday,
        "friday": friday,
        "previous_week": previous_week,
        "next_week": next_week,
        "include_unavailable": include_unavailable,
        "available_count": sum(1 for r in rows if r["available"]),
        "unavailable_count": sum(1 for r in rows if not r["available"]),
    })


@require_POST
@transaction.atomic
def main_rotation_shift(request):
    """指定週の主担当ローテーションを手動で1つ進める／戻す／自動へ戻す。"""
    monday, _ = _week_bounds(request.POST.get("week", ""))
    action = (request.POST.get("action") or "").strip()
    if action == "reset":
        MainRotationOverride.objects.filter(week_start=monday).delete()
        messages.success(request, "主担当を自動ローテーションに戻しました。")
    elif action in {"next", "previous"}:
        delta = 1 if action == "next" else -1
        obj, _ = MainRotationOverride.objects.get_or_create(week_start=monday, defaults={"offset": 0})
        obj.offset += delta
        # 0へ戻った場合は自動状態と同じなのでレコードを削除する。
        if obj.offset == 0:
            obj.delete()
        else:
            obj.save(update_fields=["offset", "updated_at"])
        messages.success(request, "主担当ローテーションを1つ進めました。" if delta > 0 else "主担当ローテーションを1つ戻しました。")
    else:
        messages.error(request, "主担当ローテーションの操作が正しくありません。")
    return redirect(f"{reverse('daily_assignment:weekly_planner')}?week={monday.isoformat()}#main-rotation")


@require_POST
@transaction.atomic
def weekly_absence_save(request):
    absence_date = _parse_date(request.POST.get("absence_date"))
    staff = get_object_or_404(Staff, pk=request.POST.get("staff_id"), is_active=True)
    kind = request.POST.get("kind", "annual")
    period = request.POST.get("period", "full")
    if kind not in dict(WeeklyAbsence.KIND_CHOICES): kind = "annual"
    if period not in dict(WeeklyAbsence.PERIOD_CHOICES): period = "full"
    start = TimeSlot.objects.filter(pk=request.POST.get("start_slot_id"), is_active=True).first() if request.POST.get("start_slot_id") else None
    end = TimeSlot.objects.filter(pk=request.POST.get("end_slot_id"), is_active=True).first() if request.POST.get("end_slot_id") else None
    obj, _ = WeeklyAbsence.objects.update_or_create(
        staff=staff, absence_date=absence_date,
        defaults={"kind": kind, "period": period, "start_slot": start, "end_slot": end, "note": request.POST.get("note", "").strip()[:120]},
    )
    board, _ = DailyBoard.objects.get_or_create(board_date=absence_date)
    _sync_calendar_to_board(board); _sync_absence_text_to_board(board); _apply_weekly_absences_to_existing_board(board); _sync_main_row(board)
    return JsonResponse({"ok": True, "id": obj.id, "label": f"{obj.get_kind_display()}・{obj.period_label}"})


@require_POST
@transaction.atomic
def weekly_absence_bulk(request):
    """週間配置作成から、終日の年休・出張などを期間指定で一括登録する。"""
    staff = get_object_or_404(Staff, pk=request.POST.get("staff_id"), is_active=True)
    start_date = _parse_date(request.POST.get("start_date"))
    end_date = _parse_date(request.POST.get("end_date"))
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    if (end_date - start_date).days > 62:
        return JsonResponse({"ok": False, "error": "期間は63日以内で指定してください。"}, status=400)
    kind = request.POST.get("kind", "annual")
    if kind not in dict(WeeklyAbsence.KIND_CHOICES) or kind == "reception":
        kind = "annual"
    note = (request.POST.get("note") or "").strip()[:120]
    count = 0
    current = start_date
    while current <= end_date:
        WeeklyAbsence.objects.update_or_create(
            staff=staff, absence_date=current,
            defaults={"kind": kind, "period": "full", "start_slot": None, "end_slot": None, "note": note},
        )
        board, _ = DailyBoard.objects.get_or_create(board_date=current)
        _sync_calendar_to_board(board)
        _sync_absence_text_to_board(board)
        _apply_weekly_absences_to_existing_board(board)
        _sync_main_row(board)
        count += 1
        current += timedelta(days=1)
    return JsonResponse({"ok": True, "count": count, "message": f"{staff.name}さんの予定を{count}日分登録しました。"})


@require_POST
@transaction.atomic
def weekly_absence_delete(request):
    absence = get_object_or_404(WeeklyAbsence, pk=request.POST.get("absence_id"))
    duty_date = absence.absence_date
    absence.delete()
    board, _ = DailyBoard.objects.get_or_create(board_date=duty_date)
    _sync_absence_text_to_board(board)
    return JsonResponse({"ok": True})


def _can_staff_cover_area(staff, area):
    if staff.dedicated_area_id and staff.dedicated_area_id != area.id:
        return False
    if StaffAvoidArea.objects.filter(staff=staff, area=area).exists():
        return False
    return True


def _append_unique_meta_line(current, line):
    """同じ欠員情報を重複登録せず、メタ欄へ1行追加する。"""
    current = str(current or "").strip()
    line = str(line or "").strip()
    if not line:
        return current
    lines = [x.strip() for x in current.splitlines() if x.strip()]
    if line in lines:
        return current
    return current + ("\n" if current else "") + line


def _parse_reallocation_period(value):
    """欠員再配置モーダルの期間指定を WeeklyAbsence 用へ変換する。"""
    value = str(value or "full").strip()
    if value in {"full", "am", "pm"}:
        return value, None, None

    if value.startswith("time:"):
        key = value.split(":", 1)[1].strip()
        start = TimeSlot.objects.filter(is_active=True, kind="time", key=key).first()
        end = TimeSlot.objects.filter(is_active=True, kind="time").order_by("-display_order", "-id").first()
        if start and end:
            return "custom", start, end

    return "full", None, None


def _reallocation_period_covers_row(period_value, row_key):
    """欠員再配置で選択した期間が row_key を含むか。時刻指定はその時刻以降。"""
    period, start, end = _parse_reallocation_period(period_value)
    if period == "full":
        return True

    slots = list(TimeSlot.objects.filter(is_active=True, kind="time").order_by("display_order", "id"))
    order = {s.key: i for i, s in enumerate(slots)}
    idx = order.get(row_key)
    if idx is None:
        return False

    if period == "am":
        lunch_idx = next((i for i, s in enumerate(slots) if s.is_lunch_highlight), len(slots) // 2)
        return idx <= lunch_idx
    if period == "pm":
        lunch_idx = next((i for i, s in enumerate(slots) if s.is_lunch_highlight), len(slots) // 2)
        return idx > lunch_idx
    if period == "custom" and start and end:
        a, z = order.get(start.key), order.get(end.key)
        if a is None or z is None:
            return False
        lo, hi = sorted((a, z))
        return lo <= idx <= hi
    return False


def _register_reallocation_absence(board, staff, reason, period_value="full"):
    """欠員再配置で選んだ理由・時間帯を WeeklyAbsence に登録して当日表へ同期する。"""
    reason = (reason or "annual").strip()
    period, start, end = _parse_reallocation_period(period_value)

    # 同じ内容をクイック追加済みなら二重表示させない。
    quick_entries = [e for e in _parse_quick_meta_absences(board) if e["staff"].id == staff.id]
    duplicate_quick = False
    for e in quick_entries:
        if period == "full" and e["period"] == "full":
            duplicate_quick = True
        elif period == "am" and e["period"] == "am":
            duplicate_quick = True
        elif period == "pm" and e["period"] == "pm":
            duplicate_quick = True
        elif period == "custom" and e["period"] == "time" and start and e.get("slot_key") == start.key:
            duplicate_quick = True
        if duplicate_quick:
            break
    if duplicate_quick:
        return

    kind_map = {
        "annual": "annual",
        "business": "trip",
        "meeting": "meeting",
        "absence": "other",
    }
    kind = kind_map.get(reason, "other")

    # v3.10.6.64:
    # 欠員再配置から休暇を追加するとき、既存の同日休暇を不用意に上書きしない。
    # 安全に自動統合できる AM + PM（または PM + AM）だけ終日へ統合する。
    # それ以外の組み合わせは既存休暇を保持し、休暇変更画面での確認に任せる。
    existing = WeeklyAbsence.objects.filter(
        staff=staff,
        absence_date=board.board_date,
    ).first()

    if existing:
        complementary_halves = {existing.period, period} == {"am", "pm"}
        same_period = existing.period == period

        if complementary_halves:
            # 既存と今回が同じ休暇種別ならその種別を維持。
            # 種別が異なる場合は、既存情報を壊さないため既存種別を優先する。
            existing.period = "full"
            existing.start_slot = None
            existing.end_slot = None
            if existing.kind == kind:
                existing.kind = kind
            existing.note = "欠員再配置から午前・午後休を終日へ統合"
            existing.save(update_fields=[
                "kind", "period", "start_slot", "end_slot", "note"
            ])
        elif same_period:
            # 同じ時間帯の再登録は実質変更なし。既存データを維持する。
            pass
        elif existing.period == "full":
            # すでに終日休なら追加登録は不要。
            pass
        else:
            # v3.10.6.65:
            # custom＋AM/PM は WeeklyAbsence 1件では正確に表現できない。
            # 既存の時間休はそのまま残し、追加した半日年休だけを
            # 当日表の「手動入力部分」に補助登録する。
            # ここは _parse_quick_meta_absences() でも認識されるため、
            # 表示だけでなく再配置・勤務可能判定でも追加休暇として扱われる。
            if (
                existing.period == "custom"
                and period in ("am", "pm")
                and kind == "annual"
            ):
                extra_label = f"{staff.name} {'AM休' if period == 'am' else 'PM休'}"
                marker_start = "\u2063"
                marker_end = "\u2064"
                current = board.annual_leave_1 or ""

                # 週間予定ブロックの前側（手動入力部分）だけ確認し、
                # 同じ補助休暇を二重追加しない。
                manual_part = current.split(marker_start, 1)[0].rstrip("\n")
                manual_items = [
                    x.strip()
                    for x in re.split(r"[、\n]+", manual_part)
                    if x.strip()
                ]
                if extra_label not in manual_items:
                    # 週間予定ブロックはそのまま保持し、その直前へ追加する。
                    if marker_start in current:
                        before, weekly_tail = current.split(marker_start, 1)
                        before = before.rstrip("\n")
                        before = before + (("\n" if before else "") + extra_label)
                        board.annual_leave_1 = before + "\n" + marker_start + weekly_tail
                    else:
                        board.annual_leave_1 = current.rstrip("\n") + (
                            ("\n" if current.rstrip("\n") else "") + extra_label
                        )
                    board.save(update_fields=["annual_leave_1", "updated_at"])
            # それ以外の組み合わせは既存休暇を上書きしない。
    else:
        WeeklyAbsence.objects.create(
            staff=staff,
            absence_date=board.board_date,
            kind=kind,
            period=period,
            start_slot=start,
            end_slot=end,
            note="欠員再配置から登録",
        )

    _sync_absence_text_to_board(board)


def _shortage_cells_for_absent(board, absent_staff, time_keys):
    """
    Ver.3.10.6: 先に年休・会議等を登録して元配置が空になった場合、
    最低必要人数を割った空きセルから欠員箇所を復元する。
    複数配置が不足しているときは、欠員者本人の専任/優先配置に最も近い配置を選ぶ。
    """
    candidates = []
    areas = list(AssignmentArea.objects.filter(is_active=True).order_by("assignment_priority", "display_order", "id"))
    for area in areas:
        minimum = max(0, int(area.minimum_assignment_count or 0))
        if minimum <= 0:
            continue
        rows = list(
            board.cells.filter(area=area, row_key__in=time_keys)
            .values_list("row_key", flat=True).distinct()
        )
        for row_key in rows:
            row_cells = list(board.cells.filter(area=area, row_key=row_key).order_by("slot_index"))
            filled = [c for c in row_cells if (c.value or "").strip()]
            missing = max(0, minimum - len(filled))
            if missing <= 0:
                continue
            blank = [c for c in row_cells if not (c.value or "").strip()]
            for cell in blank[:missing]:
                candidates.append(cell)

    if not candidates:
        return []

    # 欠員者本人にとって自然な配置を優先する。
    pref_ids = [
        absent_staff.preferred_area_1_id, absent_staff.preferred_area_2_id,
        absent_staff.preferred_area_3_id, absent_staff.preferred_area_4_id,
    ]
    def area_fit(area_id):
        if absent_staff.dedicated_area_id == area_id:
            return 1000
        for idx, aid in enumerate(pref_ids):
            if aid == area_id:
                return (400, 300, 200, 100)[idx]
        return 0

    best_area_id = sorted(
        {c.area_id for c in candidates},
        key=lambda aid: (-area_fit(aid), next(c.area.assignment_priority for c in candidates if c.area_id == aid), next(c.area.display_order for c in candidates if c.area_id == aid), aid),
    )[0]
    return [c for c in candidates if c.area_id == best_area_id]


@require_POST
def weekly_reallocation_proposals(request):
    """急な欠員に対し、優先度の高い配置を守るため最大2段の玉突き再配置案を返す。"""
    duty_date = _parse_date(request.POST.get("date"))
    absent_staff = get_object_or_404(
        Staff.objects.select_related("dedicated_area", "preferred_area_1", "preferred_area_2", "preferred_area_3", "preferred_area_4"),
        pk=request.POST.get("staff_id"),
    )
    board = get_object_or_404(DailyBoard, board_date=duty_date)
    time_keys = set(TimeSlot.objects.filter(is_active=True, kind="time").values_list("key", flat=True))
    absence_period = str(request.POST.get("absence_period", "full") or "full")

    affected = []
    specs_raw = (request.POST.get("affected_specs") or "").strip()
    if specs_raw:
        try:
            specs = json.loads(specs_raw)
        except Exception:
            specs = []
        for spec in specs if isinstance(specs, list) else []:
            try:
                area_id = int(spec.get("area_id"))
                slot_index = int(spec.get("slot_index", 1))
            except (TypeError, ValueError, AttributeError):
                continue
            row_key = str(spec.get("row_key", ""))
            if row_key not in time_keys or not _reallocation_period_covers_row(absence_period, row_key):
                continue
            cell = board.cells.filter(
                area_id=area_id, row_key=row_key, slot_index=slot_index
            ).select_related("area").first()
            if cell:
                affected.append(cell)
    if not affected:
        affected = [
            c for c in
            board.cells.filter(value=absent_staff.name, row_key__in=time_keys)
            .select_related("area")
            .order_by("row_key", "area__assignment_priority", "area__display_order", "slot_index")
            if _reallocation_period_covers_row(absence_period, c.row_key)
        ]
    if not affected:
        # Ver.3.10.6: 先にクイック追加して配置から消えた場合でも、
        # 最低必要人数を割った空きセルから欠員箇所を復元して再配置できるようにする。
        has_absence = bool(
            WeeklyAbsence.objects.filter(absence_date=duty_date, staff=absent_staff).exists()
            or any(e["staff"].id == absent_staff.id for e in _parse_quick_meta_absences(board))
        )
        if has_absence:
            affected = [
                c for c in _shortage_cells_for_absent(board, absent_staff, time_keys)
                if _reallocation_period_covers_row(absence_period, c.row_key)
            ]
    if not affected:
        return JsonResponse({
            "ok": False,
            "error": f"{absent_staff.name}さんの元配置または最低必要人数を割った欠員箇所が見つかりません。",
        }, status=400)

    # 同一セルの重複を除去。
    unique = {}
    for c in affected:
        unique[c.id] = c
    affected = list(unique.values())

    unavailable_fields = (
        "night_shift", "night_shift_after", "holiday_day_shift",
        "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
        "annual_leave_1", "annual_leave_2", "reception_leave",
    )
    unavailable_text = "\n".join(str(getattr(board, f, "") or "") for f in unavailable_fields)
    full_unavailable = {
        x.name for x in Staff.objects.filter(is_active=True)
        if x.name and x.name in unavailable_text
    }
    full_unavailable |= _full_day_absence_names(duty_date) | {absent_staff.name}

    day_absences = list(
        WeeklyAbsence.objects.filter(absence_date=duty_date).select_related("staff", "start_slot", "end_slot")
    )
    quick_entries = _parse_quick_meta_absences(board)

    def row_available(staff, row_key):
        if staff.name in full_unavailable:
            return False
        if not _staff_available_for_row(staff, row_key):
            return False
        for absence in day_absences:
            if absence.staff_id == staff.id and _absence_covers_row(absence, row_key):
                return False
        for entry in quick_entries:
            if entry["staff"].id == staff.id and _quick_entry_covers_row(entry, row_key):
                return False
        return True

    active = list(
        Staff.objects.filter(is_active=True, auto_assignment_enabled=True)
        .select_related("dedicated_area", "preferred_area_1", "preferred_area_2", "preferred_area_3", "preferred_area_4")
        .order_by("assignment_staff_priority", "display_order", "name")
    )
    staff_by_name = {s.name: s for s in active}
    free_labels = [x.strip() for x in re.split(r"[、,\n]+", board.free_text or "") if x.strip()]
    unassigned_names = [x.strip() for x in re.split(r"[、,\n]+", board.unassigned or "") if x.strip()]

    full_free_names = {x for x in free_labels if x in staff_by_name}
    partial_free_periods = defaultdict(set)
    for label in free_labels:
        m = re.fullmatch(r"(.+?)（(午前|午後)）", label)
        if m:
            staff_name, half_label = m.groups()
            if staff_name in staff_by_name:
                partial_free_periods[staff_name].add("am" if half_label == "午前" else "pm")

    full_idle_names = full_free_names | set(unassigned_names)
    idle_names = set(full_idle_names)

    def staff_is_idle_for_cells(staff, cells):
        if staff.name in full_idle_names:
            return True
        periods = partial_free_periods.get(staff.name, set())
        if not periods:
            return False
        return all(
            any(_reallocation_period_covers_row(period, c.row_key) for period in periods)
            for c in cells
        )

    idle_staff = [
        s for s in active
        if s.name not in full_unavailable
        and (s.name in full_idle_names or s.name in partial_free_periods)
    ]

    target_priority = min(c.area.assignment_priority for c in affected)
    target_area_ids = {c.area_id for c in affected}
    backup_ids = set(
        DedicatedAreaBackup.objects.filter(area_id__in=target_area_ids).values_list("staff_id", flat=True)
    )

    # v3.10.6.40: 欠員者本人がその配置の専任者なら、
    # 欠員再配置でも「代理1 → 代理2 → 通常候補」を最優先にする。
    # 通常スタッフの欠員時には代理設定を強制しない。
    dedicated_backup_priority = {}
    if absent_staff.dedicated_area_id and absent_staff.dedicated_area_id in target_area_ids:
        dedicated_backup_priority = {
            staff_id: priority
            for staff_id, priority in DedicatedAreaBackup.objects.filter(
                area_id=absent_staff.dedicated_area_id
            ).values_list("staff_id", "priority")
        }

    def backup_priority_for(staff):
        return dedicated_backup_priority.get(staff.id)

    def fit_score(staff, target_cells):
        """欠員場所への適性。専任・代理・優先①～④を評価。"""
        score = 0
        prefs = [staff.preferred_area_1_id, staff.preferred_area_2_id, staff.preferred_area_3_id, staff.preferred_area_4_id]
        weights = (80, 50, 30, 15)
        for c in target_cells:
            if staff.dedicated_area_id == c.area_id:
                score += 160
            if staff.id in backup_ids:
                score += 100
            for idx, aid in enumerate(prefs):
                if aid == c.area_id:
                    score += weights[idx]
                    break
        return score

    def can_cover_cells(staff, cells):
        return all(
            row_available(staff, c.row_key) and _can_staff_cover_area(staff, c.area)
            for c in cells
        )

    def change_for(cell, before, after):
        return {
            "cell_id": cell.id,
            "area": cell.area.name,
            "row_key": cell.row_key,
            "slot_index": cell.slot_index,
            "before": before,
            "after": after,
        }

    proposals = []
    seen_signatures = set()

    def duplicate_assignment_warnings(changes):
        """再配置案を反映した結果、同じ時間帯に同一スタッフが複数配置へ入る場合を警告する。"""
        # 変更後の仮想セル状態を作る。
        values = {
            c.id: (c.value or "").strip()
            for c in board.cells.filter(row_key__in=time_keys).select_related("area")
        }
        cell_map = {
            c.id: c
            for c in board.cells.filter(row_key__in=time_keys).select_related("area")
        }
        for change in changes:
            try:
                cid = int(change.get("cell_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if cid in values:
                values[cid] = str(change.get("after") or "").strip()

        # 実際に覆う時間行ごとに、スタッフの配置先を集計する。
        # この警告判定は再配置案生成の早い段階でも呼ばれるため、
        # ここで独立して時間カバレッジを構築する。
        warning_slots = [s for s in _active_time_slots(include_special=False) if s.key in time_keys]
        warning_areas = list(AssignmentArea.objects.filter(is_active=True))
        warning_rule_map = _time_rule_map(warning_areas, warning_slots)
        warning_coverage = defaultdict(lambda: defaultdict(set))
        for area in warning_areas:
            layout = _area_row_layout(area, warning_slots, warning_rule_map)
            for item in layout:
                owner_key = item.get("owner_key")
                if not owner_key or item.get("hidden"):
                    continue
                warning_coverage[area.id][owner_key].add(item["slot"].key)

        by_staff_row = defaultdict(lambda: defaultdict(list))
        for cid, name in values.items():
            if not name:
                continue
            cell = cell_map.get(cid)
            if not cell:
                continue
            covered = warning_coverage.get(cell.area_id, {}).get(cell.row_key, {cell.row_key})
            for row_key in covered:
                by_staff_row[name][row_key].append(cell)

        warnings = []
        seen = set()
        slot_labels = {
            s.key: s.label
            for s in TimeSlot.objects.filter(is_active=True, kind="time")
        }

        # 時間連動ルールの「兼任（元配置を残す）」で意図的に発生する重複は警告対象外。
        # row_key ごとに、許可されている source_area / target_area の組を保持する。
        allowed_overlap_pairs = defaultdict(set)
        for rule in DerivedAssignmentRule.objects.filter(
            is_active=True,
            action="add",
            target_area__isnull=False,
            time_slot__kind="time",
        ).select_related("time_slot"):
            pair = frozenset((rule.source_area_id, rule.target_area_id))
            if len(pair) == 2:
                allowed_overlap_pairs[rule.time_slot.key].add(pair)

        for name, rows in by_staff_row.items():
            for row_key, cells in rows.items():
                # 同一エリア内の複数slotは別枠として扱う。
                # 異なるエリアの組み合わせのうち、時間連動ルールで許可されていない組だけ警告する。
                area_by_id = {}
                for c in cells:
                    area_by_id.setdefault(c.area_id, c.area.name)
                area_ids = list(area_by_id)
                if len(area_ids) < 2:
                    continue

                unexpected_pairs = []
                for i in range(len(area_ids)):
                    for j in range(i + 1, len(area_ids)):
                        pair = frozenset((area_ids[i], area_ids[j]))
                        if pair in allowed_overlap_pairs.get(row_key, set()):
                            continue
                        unexpected_pairs.append((area_ids[i], area_ids[j]))

                if not unexpected_pairs:
                    continue

                # 警告には、意図しない重複に関係する配置だけを表示する。
                unexpected_area_ids = []
                for a, b in unexpected_pairs:
                    if a not in unexpected_area_ids:
                        unexpected_area_ids.append(a)
                    if b not in unexpected_area_ids:
                        unexpected_area_ids.append(b)
                area_names = [area_by_id[aid] for aid in unexpected_area_ids]

                key = (name, row_key, tuple(sorted(unexpected_area_ids)))
                if key in seen:
                    continue
                seen.add(key)
                warnings.append(
                    f"{name}さんが {slot_labels.get(row_key, row_key)} に "
                    + "・".join(area_names)
                    + " の重複配置になります"
                )
        return warnings

    def proposal_overlap_policy(changes):
        """
        再配置案が新たに作る重複配置を、配置場所ごとの設定で評価する。

        - never: その案は候補から除外
        - shortage: 重複なし案より大きく後回し
        - always: 許可するが、重複なし案より後回し

        時間連動ルール(action=add)で意図された兼任は評価対象外。
        """
        cells = list(
            board.cells.filter(row_key__in=time_keys).select_related("area")
        )
        values = {c.id: (c.value or "").strip() for c in cells}
        cell_map = {c.id: c for c in cells}

        # 変更後の値と、「今回新しく人を入れるセル」を作る。
        newly_assigned = []
        for change in changes:
            try:
                cid = int(change.get("cell_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if cid not in values:
                continue
            before = values[cid]
            after = str(change.get("after") or "").strip()
            values[cid] = after
            if after and after != before:
                newly_assigned.append((cell_map[cid], after))

        if not newly_assigned:
            return False, 0

        warning_slots = [s for s in _active_time_slots(include_special=False) if s.key in time_keys]
        warning_areas = list(AssignmentArea.objects.filter(is_active=True))
        warning_rule_map = _time_rule_map(warning_areas, warning_slots)
        warning_coverage = defaultdict(lambda: defaultdict(set))
        for area in warning_areas:
            layout = _area_row_layout(area, warning_slots, warning_rule_map)
            for item in layout:
                owner_key = item.get("owner_key")
                if not owner_key or item.get("hidden"):
                    continue
                warning_coverage[area.id][owner_key].add(item["slot"].key)

        allowed_overlap_pairs = defaultdict(set)
        for rule in DerivedAssignmentRule.objects.filter(
            is_active=True,
            action="add",
            target_area__isnull=False,
            time_slot__kind="time",
        ).select_related("time_slot"):
            pair = frozenset((rule.source_area_id, rule.target_area_id))
            if len(pair) == 2:
                allowed_overlap_pairs[rule.time_slot.key].add(pair)

        # 変更後の「staff / 実時間 / area」を集計。
        by_staff_row = defaultdict(lambda: defaultdict(set))
        for cid, name in values.items():
            if not name:
                continue
            cell = cell_map[cid]
            covered = warning_coverage.get(cell.area_id, {}).get(cell.row_key, {cell.row_key})
            for row_key in covered:
                by_staff_row[name][row_key].add(cell.area_id)

        penalty = 0
        checked = set()
        for target_cell, staff_name in newly_assigned:
            covered = warning_coverage.get(target_cell.area_id, {}).get(
                target_cell.row_key, {target_cell.row_key}
            )
            for row_key in covered:
                for other_area_id in by_staff_row[staff_name][row_key]:
                    if other_area_id == target_cell.area_id:
                        continue
                    pair = frozenset((target_cell.area_id, other_area_id))
                    if pair in allowed_overlap_pairs.get(row_key, set()):
                        continue
                    key = (staff_name, row_key, target_cell.area_id, other_area_id)
                    if key in checked:
                        continue
                    checked.add(key)

                    mode = target_cell.area.overlap_assignment_mode
                    if mode == "never":
                        return True, 0

                    # AM/PM限定は、昼休憩色の時間枠を境界として判定する。
                    # 既存のAM休/PM休と同じ定義（AMは昼枠を含む、PMはその次から）。
                    if mode in {"am_only", "pm_only"}:
                        ordered_slots = warning_slots
                        row_index = {s.key: i for i, s in enumerate(ordered_slots)}
                        current_idx = row_index.get(row_key)
                        lunch_idx = next(
                            (i for i, s in enumerate(ordered_slots) if s.is_lunch_highlight),
                            len(ordered_slots) // 2,
                        )
                        if current_idx is None:
                            return True, 0
                        if mode == "am_only" and current_idx > lunch_idx:
                            return True, 0
                        if mode == "pm_only" and current_idx <= lunch_idx:
                            return True, 0
                        penalty = max(penalty, 100000)
                    elif mode == "shortage":
                        penalty = max(penalty, 200000)
                    else:  # always
                        penalty = max(penalty, 100000)

        return False, penalty

    def add_proposal(title, replacement, changes, rank, detail):
        signature = tuple(sorted((x["cell_id"], x["after"]) for x in changes))
        if signature in seen_signatures:
            return

        overlap_blocked, overlap_penalty = proposal_overlap_policy(changes)
        if overlap_blocked:
            return

        seen_signatures.add(signature)
        proposals.append({
            "title": title,
            "replacement": replacement,
            "changes": changes,
            "change_count": len(changes),
            "rank": rank + overlap_penalty,
            "detail": detail,
            "warnings": duplicate_assignment_warnings(changes),
        })

    # 1) Free / 配置未定から直接補充。適性の高い人から候補化。
    direct_candidates = [
        s for s in idle_staff
        if staff_is_idle_for_cells(s, affected) and can_cover_cells(s, affected)
    ]
    direct_candidates.sort(key=lambda s: (
        0 if backup_priority_for(s) is not None else 1,
        backup_priority_for(s) if backup_priority_for(s) is not None else 9999,
        -fit_score(s, affected),
        s.assignment_staff_priority, s.display_order, s.name,
    ))
    for candidate in direct_candidates[:4]:
        source = "Free" if (
            candidate.name in full_free_names or candidate.name in partial_free_periods
        ) else "配置未定"
        changes = [change_for(c, absent_staff.name, candidate.name) for c in affected]
        backup_priority = backup_priority_for(candidate)
        if backup_priority is not None:
            add_proposal(
                f"専任代理{backup_priority}で補充",
                candidate.name,
                changes,
                -100000 + backup_priority,
                f"専任者{absent_staff.name}さんの欠員のため、設定済みの代理{backup_priority} {candidate.name}さんを最優先で補充します。",
            )
        else:
            add_proposal(
                f"{source}から直接補充",
                candidate.name,
                changes,
                0,
                f"欠員場所（優先度{target_priority}）を、ほかの配置を動かさず補充します。",
            )

    # 2) 既配置者を欠員場所へ動かす。
    # Ver.3.10.5:
    # 縦結合セルでは、保存されているrow_key（代表時刻）が配置ごとに異なることがある。
    # そのためrow_keyの完全一致ではなく「そのセルが実際に覆っている時間帯」で移動元を判定する。
    # 例: TVの09:00代表セルとPET/CTの08:30代表セルが、どちらも同じ午前帯を覆う場合。
    # 移動元が最低必要人数を割らなければ、そのまま1人移動。割る場合はFree/未定から玉突き補充。
    normal_slots = [s for s in _active_time_slots(include_special=False) if s.key in time_keys]
    normal_rule_map = _time_rule_map(list(AssignmentArea.objects.filter(is_active=True)), normal_slots)

    # area_id -> owner_key -> {実際に覆うrow_key...}
    coverage_by_area_owner = defaultdict(lambda: defaultdict(set))
    for area in AssignmentArea.objects.filter(is_active=True):
        layout = _area_row_layout(area, normal_slots, normal_rule_map)
        for item in layout:
            owner_key = item.get("owner_key")
            if not owner_key or item.get("hidden"):
                continue
            coverage_by_area_owner[area.id][owner_key].add(item["slot"].key)

    occupied = list(
        board.cells.filter(row_key__in=time_keys)
        .exclude(value="")
        .select_related("area")
    )
    cells_by_staff = defaultdict(list)
    for cell in occupied:
        name = (cell.value or "").strip()
        if name and name != absent_staff.name:
            cells_by_staff[name].append(cell)

    def source_cell_covering(donor_name, target):
        """targetの実時間帯を覆うdonorの通常配置セルを1つ返す。"""
        candidates = []
        for source in cells_by_staff.get(donor_name, []):
            covered_rows = coverage_by_area_owner.get(source.area_id, {}).get(source.row_key, {source.row_key})
            if target.row_key not in covered_rows:
                continue
            # 欠員先そのものは移動元にしない。
            if source.area_id == target.area_id and source.slot_index == target.slot_index:
                continue
            candidates.append(source)
        if not candidates:
            return None
        # 同時間帯に複数配置がある場合は、欠員先とは別エリアを優先。
        candidates.sort(key=lambda c: (c.area_id == target.area_id, c.area.assignment_priority, c.area.display_order, c.slot_index))
        return candidates[0]

    # v3.10.6.41:
    # 専任者本人の欠員では、代理を「Free/配置未定」や通常donor判定とは別枠で候補化する。
    # 代理が別配置に入っていても、欠員対象時間と重なる元配置だけを取り出して専任へ移す。
    # これにより「代理が1日のどこかに配置済み」という理由だけでFree候補に負けるのを防ぐ。
    if dedicated_backup_priority:
        affected_rows = {c.row_key for c in affected}
        dedicated_backups = [
            s for s in active
            if s.id in dedicated_backup_priority
            and s.name not in full_unavailable
            and any(
                row_available(s, c.row_key) and _can_staff_cover_area(s, c.area)
                for c in affected
            )
        ]
        dedicated_backups.sort(key=lambda s: (
            dedicated_backup_priority.get(s.id, 9999),
            s.assignment_staff_priority,
            s.display_order,
            s.name,
        ))

        for backup in dedicated_backups:
            priority = dedicated_backup_priority[backup.id]

            # v3.10.6.45:
            # 代理の勤務時間内だけ専任配置へ入れる。
            # 勤務時間外の欠員セルは別スタッフで補う候補を作る。
            backup_target_cells = [
                c for c in affected
                if row_available(backup, c.row_key) and _can_staff_cover_area(backup, c.area)
            ]
            uncovered_target_cells = [c for c in affected if c not in backup_target_cells]
            backup_rows = {c.row_key for c in backup_target_cells}

            if not backup_target_cells:
                continue

            # v3.10.6.66:
            # 代理の現在配置のうち、実際に代理へ任せる時間帯と重なるセルを移動元とする。
            # 同じ配置場所の別枠に代理が既に入っているケースも除外しない。
            # 例: 治療の専任本人＋代理が同じ「治療」内の別枠にいる場合、
            #     本人枠だけ代理へ変えると代理が二重表示になるため、代理の元枠も移動元として扱う。
            source_cells = []
            for source in cells_by_staff.get(backup.name, []):
                covered_rows = coverage_by_area_owner.get(source.area_id, {}).get(source.row_key, {source.row_key})
                if covered_rows & backup_rows:
                    source_cells.append(source)
            source_cells = list({c.id: c for c in source_cells}.values())

            target_changes = [
                change_for(c, absent_staff.name, backup.name)
                for c in backup_target_cells
            ]

            # 代理が勤務できない時間帯をまとめて補えるスタッフを候補化する。
            uncovered_fillers = []
            if uncovered_target_cells:
                for filler in idle_staff:
                    if filler.id == backup.id:
                        continue
                    if (
                        staff_is_idle_for_cells(filler, uncovered_target_cells)
                        and can_cover_cells(filler, uncovered_target_cells)
                    ):
                        uncovered_fillers.append(filler)
                uncovered_fillers.sort(key=lambda s: (
                    -fit_score(s, uncovered_target_cells),
                    s.assignment_staff_priority,
                    s.display_order,
                    s.name,
                ))

            # 元配置がなければ、勤務可能時間は代理、勤務時間外は別スタッフで補う。
            if not source_cells:
                if uncovered_target_cells and uncovered_fillers:
                    filler = uncovered_fillers[0]
                    changes = list(target_changes)
                    changes.extend(
                        change_for(c, absent_staff.name, filler.name)
                        for c in uncovered_target_cells
                    )
                    add_proposal(
                        f"専任代理{priority}＋時間外補充",
                        backup.name,
                        changes,
                        -200000 + priority + len(changes),
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを勤務可能時間に配置し、"
                        f"勤務時間外は{filler.name}さんで補充します。",
                    )
                else:
                    changes = list(target_changes)
                    changes.extend(
                        change_for(c, absent_staff.name, "")
                        for c in uncovered_target_cells
                    )
                    add_proposal(
                        f"専任代理{priority}で部分補充",
                        backup.name,
                        changes,
                        -200000 + priority + len(changes),
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを勤務可能時間だけ最優先で配置します。"
                        + ("勤務時間外は補充候補が見つからないため一時的に空きになります。" if uncovered_target_cells else ""),
                    )
                continue

            # 代理を抜いても元配置の最低人数を守れるなら、そのまま移動。
            source_can_spare = True
            for source in source_cells:
                count_now = board.cells.filter(
                    area=source.area, row_key=source.row_key
                ).exclude(value="").exclude(value=absent_staff.name).count()
                if max(0, count_now - 1) < source.area.minimum_assignment_count:
                    source_can_spare = False
                    break

            source_names = "、".join(sorted({c.area.name for c in source_cells}))
            if source_can_spare:
                # v3.10.6.66:
                # 代理の元枠が同じ高優先配置内にある場合は、単純に空欄へする前に
                # 低優先配置から1人を玉突きできる案を最優先で追加する。
                # 従来の「代理をそのまま移動して元枠を空欄にする案」は必ず残すため、
                # 既存ロジックのフォールバックは壊さない。
                source_priority = min(c.area.assignment_priority for c in source_cells)
                chain_candidates = []
                for donor in active:
                    if donor.id in (backup.id, absent_staff.id):
                        continue
                    if donor.name in full_unavailable or donor.name in idle_names:
                        continue
                    if not all(
                        row_available(donor, c.row_key) and _can_staff_cover_area(donor, c.area)
                        for c in source_cells
                    ):
                        continue

                    donor_source_cells = []
                    valid_chain = True
                    for source in source_cells:
                        donor_source = source_cell_covering(donor.name, source)
                        if donor_source is None:
                            valid_chain = False
                            break
                        donor_source_cells.append(donor_source)
                    if not valid_chain:
                        continue
                    donor_source_cells = list({c.id: c for c in donor_source_cells}.values())
                    if not donor_source_cells:
                        continue

                    donor_source_priority = min(c.area.assignment_priority for c in donor_source_cells)
                    # 高優先側から人を抜く玉突きは作らない。
                    if donor_source_priority < source_priority:
                        continue

                    # v3.10.6.67:
                    # donor の元配置が最低人数を守れる案を最優先する。
                    # ただし donor が「代理の元枠」より明確に低優先の配置にいる場合は、
                    # その低優先枠を一時的に空けてでも玉突きする案を許可する。
                    # 例: 治療(高)の代理元枠 ← sub(通常)の田中友さん。
                    donor_can_spare = True
                    for donor_source in donor_source_cells:
                        count_now = board.cells.filter(
                            area=donor_source.area, row_key=donor_source.row_key
                        ).exclude(value="").exclude(value=absent_staff.name).count()
                        if max(0, count_now - 1) < donor_source.area.minimum_assignment_count:
                            donor_can_spare = False
                            break

                    lower_priority_source = donor_source_priority > source_priority
                    if not donor_can_spare and not lower_priority_source:
                        continue

                    chain_candidates.append((
                        0 if donor_can_spare else 1,
                        -sum(fit_score(donor, [c]) for c in source_cells),
                        -donor_source_priority,
                        donor.assignment_staff_priority,
                        donor.display_order,
                        donor.name,
                        donor,
                        donor_source_cells,
                        donor_can_spare,
                    ))

                chain_candidates.sort(key=lambda x: x[:6])
                if chain_candidates:
                    _, _, _, _, _, _, donor, donor_source_cells, donor_can_spare = chain_candidates[0]
                    changes = list(target_changes)
                    changes.extend(change_for(c, backup.name, donor.name) for c in source_cells)
                    changes.extend(change_for(c, donor.name, "") for c in donor_source_cells)

                    if uncovered_target_cells and uncovered_fillers:
                        outside_filler = uncovered_fillers[0]
                        changes.extend(
                            change_for(c, absent_staff.name, outside_filler.name)
                            for c in uncovered_target_cells
                        )
                        extra_detail = f"代理の勤務時間外は{outside_filler.name}さんで補充します。"
                    else:
                        changes.extend(
                            change_for(c, absent_staff.name, "")
                            for c in uncovered_target_cells
                        )
                        extra_detail = (
                            "代理の勤務時間外は補充候補が見つからないため一時的に空きになります。"
                            if uncovered_target_cells else ""
                        )

                    donor_source_names = "、".join(sorted({c.area.name for c in donor_source_cells}))
                    donor_source_detail = (
                        f"{donor.name}さんの元配置（{donor_source_names}）は最低必要人数を維持します。"
                        if donor_can_spare
                        else f"{donor.name}さんの元配置（{donor_source_names}）は低優先配置のため一時的に空きになります。"
                    )
                    add_proposal(
                        f"専任代理{priority}を優先玉突き",
                        backup.name,
                        changes,
                        -210000 + priority + len(changes),
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを専任枠へ移し、"
                        f"空いた{source_names}を{donor.name}さんで補います。"
                        f"{donor_source_detail}{extra_detail}",
                    )

                # 従来案も残す。
                changes = list(target_changes)
                changes.extend(change_for(c, backup.name, "") for c in source_cells)
                if uncovered_target_cells and uncovered_fillers:
                    filler = uncovered_fillers[0]
                    changes.extend(
                        change_for(c, absent_staff.name, filler.name)
                        for c in uncovered_target_cells
                    )
                    title = f"専任代理{priority}＋時間外補充"
                    detail = (
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを{source_names}から"
                        f"勤務可能時間だけ専任配置へ移し、勤務時間外は{filler.name}さんで補充します。"
                    )
                else:
                    changes.extend(
                        change_for(c, absent_staff.name, "")
                        for c in uncovered_target_cells
                    )
                    title = f"専任代理{priority}を優先移動"
                    detail = (
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを{source_names}から"
                        f"勤務可能時間だけ専任配置へ最優先で移します。"
                        + ("勤務時間外は補充候補が見つからないため一時的に空きになります。" if uncovered_target_cells else "")
                    )
                add_proposal(
                    title,
                    backup.name,
                    changes,
                    -200000 + priority + len(changes),
                    detail,
                )
                continue

            # 元配置が最低人数を割る場合だけ、Free/配置未定から玉突き補充する。
            fillers = []
            for filler in idle_staff:
                if filler.id == backup.id:
                    continue
                if (
                    staff_is_idle_for_cells(filler, source_cells)
                    and all(
                        row_available(filler, c.row_key) and _can_staff_cover_area(filler, c.area)
                        for c in source_cells
                    )
                ):
                    fillers.append(filler)
            fillers.sort(key=lambda s: (
                -sum(fit_score(s, [c]) for c in source_cells),
                s.assignment_staff_priority,
                s.display_order,
                s.name,
            ))

            for filler in fillers[:2]:
                changes = list(target_changes)
                changes.extend(change_for(c, backup.name, filler.name) for c in source_cells)
                if uncovered_target_cells and uncovered_fillers:
                    outside_filler = uncovered_fillers[0]
                    changes.extend(
                        change_for(c, absent_staff.name, outside_filler.name)
                        for c in uncovered_target_cells
                    )
                    title = f"専任代理{priority}を玉突き＋時間外補充"
                    detail = (
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを勤務可能時間に専任配置へ移し、"
                        f"空いた{source_names}を{filler.name}さん、代理の勤務時間外を{outside_filler.name}さんで補います。"
                    )
                else:
                    changes.extend(
                        change_for(c, absent_staff.name, "")
                        for c in uncovered_target_cells
                    )
                    title = f"専任代理{priority}を玉突き移動"
                    detail = (
                        f"専任者{absent_staff.name}さんの欠員のため、代理{priority} {backup.name}さんを勤務可能時間に専任配置へ移し、"
                        f"空いた{source_names}を{filler.name}さんで補います。"
                        + ("代理の勤務時間外は補充候補が見つからないため一時的に空きになります。" if uncovered_target_cells else "")
                    )
                add_proposal(
                    title,
                    backup.name,
                    changes,
                    -200000 + priority + len(changes),
                    detail,
                )

            # v3.10.6.44:
            # 玉突き補充できるスタッフがいなくても、専任代理を最優先で専任配置へ移す案を残す。
            # 元配置は不足になるが、Freeから専任へ直接入れる案より先に提示する。
            # 不足した元配置は、その後の「空き補充」または追加の欠員再配置で補える。
            if not fillers:
                changes = list(target_changes)
                changes.extend(change_for(c, backup.name, "") for c in source_cells)
                if uncovered_target_cells and uncovered_fillers:
                    outside_filler = uncovered_fillers[0]
                    changes.extend(
                        change_for(c, absent_staff.name, outside_filler.name)
                        for c in uncovered_target_cells
                    )
                    extra_detail = f"代理の勤務時間外は{outside_filler.name}さんで補充します。"
                else:
                    changes.extend(
                        change_for(c, absent_staff.name, "")
                        for c in uncovered_target_cells
                    )
                    extra_detail = (
                        "代理の勤務時間外は補充候補が見つからないため一時的に空きになります。"
                        if uncovered_target_cells else ""
                    )
                add_proposal(
                    f"専任代理{priority}を優先移動",
                    backup.name,
                    changes,
                    -200000 + priority + len(changes),
                    f"専任者{absent_staff.name}さんの欠員を優先し、代理{priority} {backup.name}さんを{source_names}から勤務可能時間だけ専任配置へ移します。"
                    f"{source_names}は一時的に最低人数を下回るため、反映後に空き補充してください。{extra_detail}",
                )

    donor_candidates = []
    for donor in active:
        if donor.name in full_unavailable or donor.name in idle_names or donor.name == absent_staff.name:
            continue
        if not can_cover_cells(donor, affected):
            continue
        donor_cells = []
        valid = True
        for target in affected:
            source = source_cell_covering(donor.name, target)
            if source is None:
                valid = False
                break
            donor_cells.append(source)
        if not valid:
            continue
        donor_cells = list({c.id: c for c in donor_cells}.values())
        if not donor_cells:
            continue
        # 高優先配置から低優先配置へ人を抜く案は最後に回す。
        source_priority = min(c.area.assignment_priority for c in donor_cells)
        priority_penalty = max(0, target_priority - source_priority) * 1000
        backup_priority = backup_priority_for(donor)
        backup_sort = 0 if backup_priority is not None else 1
        backup_order = backup_priority if backup_priority is not None else 9999
        donor_candidates.append((backup_sort, backup_order, priority_penalty, -fit_score(donor, affected), source_priority, donor, donor_cells))

    donor_candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3], -x[4], x[5].assignment_staff_priority, x[5].display_order))
    for backup_sort, backup_order, priority_penalty, _fit, source_priority, donor, donor_cells in donor_candidates[:12]:
        # 移動元の各セルで、donorを抜いた後に最低必要人数が残るか。
        source_can_spare = True
        for source in donor_cells:
            count_now = board.cells.filter(
                area=source.area, row_key=source.row_key
            ).exclude(value="").exclude(value=absent_staff.name).count()
            if max(0, count_now - 1) < source.area.minimum_assignment_count:
                source_can_spare = False
                break

        target_changes = [change_for(c, absent_staff.name, donor.name) for c in affected]
        if source_can_spare:
            source_names = "、".join(sorted({c.area.name for c in donor_cells}))
            backup_priority = backup_priority_for(donor)
            if backup_priority is not None:
                title = f"専任代理{backup_priority}を移動"
                rank = -100000 + backup_priority + len(donor_cells)
                detail = (
                    f"専任者{absent_staff.name}さんの欠員のため、代理{backup_priority} {donor.name}さんを"
                    f"{source_names}から専任配置へ最優先で移します。"
                )
            else:
                title = "低優先配置から1人移動"
                rank = 10 + priority_penalty + len(donor_cells)
                detail = f"{source_names}は最低必要人数を維持できるため、{donor.name}さんを優先度{target_priority}の欠員へ移します。"
            add_proposal(
                title,
                donor.name,
                target_changes + [change_for(c, donor.name, "") for c in donor_cells],
                rank,
                detail,
            )
            continue

        # 移動元の最低人数を割るなら、空いている人で移動元を埋めて玉突き。
        backfills = []
        for filler in idle_staff:
            if filler.id == donor.id:
                continue
            if (
                staff_is_idle_for_cells(filler, donor_cells)
                and all(
                    row_available(filler, c.row_key) and _can_staff_cover_area(filler, c.area)
                    for c in donor_cells
                )
            ):
                backfills.append(filler)
        backfills.sort(key=lambda s: (
            -sum(fit_score(s, [c]) for c in donor_cells),
            s.assignment_staff_priority, s.display_order, s.name,
        ))
        for filler in backfills[:2]:
            source_names = "、".join(sorted({c.area.name for c in donor_cells}))
            changes = list(target_changes)
            changes.extend(change_for(c, donor.name, filler.name) for c in donor_cells)
            backup_priority = backup_priority_for(donor)
            if backup_priority is not None:
                title = f"専任代理{backup_priority}を玉突き移動"
                rank = -100000 + backup_priority + len(changes)
                detail = (
                    f"専任者{absent_staff.name}さんの欠員のため、代理{backup_priority} {donor.name}さんを専任配置へ移し、"
                    f"空いた{source_names}を{filler.name}さんで補います。"
                )
            else:
                title = "優先配置を守る玉突き再配置"
                rank = 20 + priority_penalty + len(changes)
                detail = f"{donor.name}さんを欠員へ移し、空いた{source_names}を{filler.name}さんで補います。"
            add_proposal(
                title,
                donor.name,
                changes,
                rank,
                detail,
            )

    if not proposals:
        return JsonResponse({
            "ok": False,
            "error": "優先配置を守れる再配置候補が見つかりませんでした。勤務可能時間・専任・配置しない設定を確認してください。",
        }, status=400)

    proposals.sort(key=lambda x: (x["rank"], x["change_count"], x["replacement"]))
    proposals = proposals[:3]
    for p in proposals:
        p.pop("rank", None)
    return JsonResponse({
        "ok": True,
        "absent": absent_staff.name,
        "date": duty_date.isoformat(),
        "proposals": proposals,
    })


# Ver.3.10.6.11: 欠員再配置／空き補充を1回だけ戻すためのセッションスナップショット。
# DBモデル追加は行わず、直前の自動操作だけを対象にする。
_BOARD_UNDO_SESSION_KEY = "daily_assignment_undo_stack_v2"
_BOARD_REDO_SESSION_KEY = "daily_assignment_redo_stack_v2"
_BOARD_HISTORY_LIMIT = 10
# v3.10.6.69: 欠員再配置そのものが新しく作った空きセルだけを、
# 次の「空き補充」で拾うための一時セッション。
_BOARD_REALLOCATION_VACANCIES_SESSION_KEY = "daily_assignment_reallocation_vacancies_v1"
_BOARD_UNDO_META_FIELDS = (
    "conference", "night_shift", "night_shift_after", "holiday_day_shift",
    "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
    "annual_leave_1", "annual_leave_2", "staffing_am", "staffing_pm",
    "free_text", "comment", "unassigned", "reception_leave",
)


def _build_board_undo_snapshot(board, action):
    return {
        "date": board.board_date.isoformat(),
        "action": action,
        "cells": list(board.cells.values("id", "area_id", "row_key", "slot_index", "value")),
        "meta": {name: str(getattr(board, name, "") or "") for name in _BOARD_UNDO_META_FIELDS},
        "weekly_absences": list(
            WeeklyAbsence.objects.filter(absence_date=board.board_date).values(
                "staff_id", "kind", "period", "start_slot_id", "end_slot_id", "note"
            )
        ),
        "free_history_staff_ids": list(
            StaffFreeHistory.objects.filter(board_date=board.board_date).values_list("staff_id", flat=True)
        ),
        "lunch_entries": list(
            board.lunch_break_entries.values("time_slot_id", "value")
        ),
        # v3.10.6.16: 「一つ戻る」で予定時のみのON/OFFも同時に戻す。
        "area_activations": list(
            board.area_activations.values("area_id", "is_enabled")
        ),
    }


def _history_stack_for_date(session, key, duty_date):
    """指定日の履歴だけを返す。旧形式の1件スナップショットも読み替える。"""
    raw = session.get(key, [])
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raw = []
    date_key = duty_date.isoformat()
    return [x for x in raw if isinstance(x, dict) and x.get("date") == date_key]


def _store_history_stack(session, key, stack):
    session[key] = stack[-_BOARD_HISTORY_LIMIT:]
    session.modified = True


def _arm_board_undo(request, snapshot, board):
    """操作前状態をUndoへ積み、新しい操作なのでRedo履歴を破棄する。"""
    snapshot["expected_updated_at"] = board.updated_at.isoformat() if board.updated_at else ""
    session = getattr(request, "session", None)
    if session is None:
        return

    stack = _history_stack_for_date(session, _BOARD_UNDO_SESSION_KEY, board.board_date)
    stack.append(snapshot)
    _store_history_stack(session, _BOARD_UNDO_SESSION_KEY, stack)
    session.pop(_BOARD_REDO_SESSION_KEY, None)
    session.modified = True


def _clear_board_undo(request):
    session = getattr(request, "session", None)
    if session is None:
        return
    session.pop(_BOARD_UNDO_SESSION_KEY, None)
    session.pop(_BOARD_REDO_SESSION_KEY, None)
    session.modified = True


def _restore_board_snapshot(board, snapshot):
    """DailyBoardに関係する状態をスナップショットどおり復元する。"""
    duty_date = board.board_date
    snap_cells = snapshot.get("cells") or []
    snap_ids = {int(x["id"]) for x in snap_cells if x.get("id") is not None}
    if snap_ids:
        board.cells.exclude(id__in=snap_ids).delete()
    else:
        board.cells.all().delete()

    for item in snap_cells:
        AssignmentCell.objects.update_or_create(
            board=board,
            area_id=int(item["area_id"]),
            row_key=str(item["row_key"]),
            slot_index=int(item["slot_index"]),
            defaults={"value": str(item.get("value", "") or "")[:100]},
        )

    meta = snapshot.get("meta") or {}
    for name in _BOARD_UNDO_META_FIELDS:
        if name in meta:
            setattr(board, name, str(meta.get(name, "") or ""))
    board.save(update_fields=[*_BOARD_UNDO_META_FIELDS, "updated_at"])

    WeeklyAbsence.objects.filter(absence_date=duty_date).delete()
    for item in snapshot.get("weekly_absences") or []:
        WeeklyAbsence.objects.create(
            staff_id=int(item["staff_id"]),
            absence_date=duty_date,
            kind=str(item.get("kind", "annual")),
            period=str(item.get("period", "full")),
            start_slot_id=item.get("start_slot_id"),
            end_slot_id=item.get("end_slot_id"),
            note=str(item.get("note", "") or "")[:120],
        )

    StaffFreeHistory.objects.filter(board_date=duty_date).delete()
    for staff_id in snapshot.get("free_history_staff_ids") or []:
        StaffFreeHistory.objects.get_or_create(staff_id=int(staff_id), board_date=duty_date)

    board.lunch_break_entries.all().delete()
    for item in snapshot.get("lunch_entries") or []:
        LunchBreakEntry.objects.create(
            board=board,
            time_slot_id=int(item["time_slot_id"]),
            value=str(item.get("value", "") or "")[:500],
        )

    board.area_activations.all().delete()
    for item in snapshot.get("area_activations") or []:
        DailyAreaActivation.objects.create(
            board=board,
            area_id=int(item["area_id"]),
            is_enabled=bool(item.get("is_enabled")),
        )


@require_POST
@transaction.atomic
def undo_last_auto_action(request):
    """直近10回まで、配置自動作成・欠員再配置・空き補充を順番に戻す。"""
    duty_date = _parse_date(request.POST.get("date"))
    session = request.session
    undo_stack = _history_stack_for_date(session, _BOARD_UNDO_SESSION_KEY, duty_date)
    if not undo_stack:
        return JsonResponse({"ok": False, "error": "この日付で戻せる操作がありません。"}, status=400)

    snapshot = undo_stack[-1]
    board = get_object_or_404(DailyBoard.objects.select_for_update(), board_date=duty_date)
    expected = str(snapshot.get("expected_updated_at") or "")
    current = board.updated_at.isoformat() if board.updated_at else ""
    if expected and current != expected:
        _clear_board_undo(request)
        return JsonResponse({
            "ok": False,
            "error": "履歴作成後に別の変更が保存されているため、安全のためUndo/Redo履歴をクリアしました。",
        }, status=409)

    # 戻す直前の現在状態をRedoへ保存。
    redo_snapshot = _build_board_undo_snapshot(
        board, str(snapshot.get("action") or "操作")
    )
    _restore_board_snapshot(board, snapshot)
    board.refresh_from_db()

    undo_stack.pop()

    # v3.10.6.71:
    # 復元すると board.updated_at 自体が新しくなる。
    # 次のUndo履歴が持つ expected_updated_at を復元後時刻へつなぎ直さないと、
    # 2回目の「戻る」を外部変更と誤判定してしまう。
    if undo_stack:
        undo_stack[-1]["expected_updated_at"] = board.updated_at.isoformat() if board.updated_at else ""
    _store_history_stack(session, _BOARD_UNDO_SESSION_KEY, undo_stack)

    redo_stack = _history_stack_for_date(session, _BOARD_REDO_SESSION_KEY, duty_date)
    redo_snapshot["expected_updated_at"] = board.updated_at.isoformat() if board.updated_at else ""
    redo_stack.append(redo_snapshot)
    _store_history_stack(session, _BOARD_REDO_SESSION_KEY, redo_stack)

    # 欠員再配置由来の空き候補は状態復元後に古くなるので破棄。
    session.pop(_BOARD_REALLOCATION_VACANCIES_SESSION_KEY, None)
    session.modified = True

    action = str(snapshot.get("action") or "操作")
    return JsonResponse({
        "ok": True,
        "message": f"{action}を戻しました。",
        "undo_count": len(undo_stack),
        "redo_count": len(redo_stack),
    })


@require_POST
@transaction.atomic
def redo_last_auto_action(request):
    """Undoで戻した操作を直近10回まで順番にやり直す。"""
    duty_date = _parse_date(request.POST.get("date"))
    session = request.session
    redo_stack = _history_stack_for_date(session, _BOARD_REDO_SESSION_KEY, duty_date)
    if not redo_stack:
        return JsonResponse({"ok": False, "error": "この日付で進められる操作がありません。"}, status=400)

    snapshot = redo_stack[-1]
    board = get_object_or_404(DailyBoard.objects.select_for_update(), board_date=duty_date)
    expected = str(snapshot.get("expected_updated_at") or "")
    current = board.updated_at.isoformat() if board.updated_at else ""
    if expected and current != expected:
        _clear_board_undo(request)
        return JsonResponse({
            "ok": False,
            "error": "履歴作成後に別の変更が保存されているため、安全のためUndo/Redo履歴をクリアしました。",
        }, status=409)

    # 進める直前の現在状態をUndoへ保存。
    undo_snapshot = _build_board_undo_snapshot(
        board, str(snapshot.get("action") or "操作")
    )
    _restore_board_snapshot(board, snapshot)
    board.refresh_from_db()

    redo_stack.pop()

    # Undoと同様、復元後のupdated_atを次のRedo履歴へ引き継ぐ。
    if redo_stack:
        redo_stack[-1]["expected_updated_at"] = board.updated_at.isoformat() if board.updated_at else ""
    _store_history_stack(session, _BOARD_REDO_SESSION_KEY, redo_stack)

    undo_stack = _history_stack_for_date(session, _BOARD_UNDO_SESSION_KEY, duty_date)
    undo_snapshot["expected_updated_at"] = board.updated_at.isoformat() if board.updated_at else ""
    undo_stack.append(undo_snapshot)
    _store_history_stack(session, _BOARD_UNDO_SESSION_KEY, undo_stack)

    session.pop(_BOARD_REALLOCATION_VACANCIES_SESSION_KEY, None)
    session.modified = True

    action = str(snapshot.get("action") or "操作")
    return JsonResponse({
        "ok": True,
        "message": f"{action}をやり直しました。",
        "undo_count": len(undo_stack),
        "redo_count": len(redo_stack),
    })


@require_POST
@transaction.atomic
def weekly_reallocation_apply(request):
    try:
        data = json.loads(request.body.decode("utf-8")) if request.content_type == "application/json" else request.POST
        duty_date = _parse_date(data.get("date"))
        changes = data.get("changes", [])
        absent_staff_id = data.get("absent_staff_id")
        absence_reason = str(data.get("absence_reason", "annual") or "annual")
        absence_period = str(data.get("absence_period", "full") or "full")
    except Exception:
        return JsonResponse({"ok": False, "error": "変更データが正しくありません。"}, status=400)
    board = get_object_or_404(DailyBoard.objects.select_for_update(), board_date=duty_date)
    undo_snapshot = _build_board_undo_snapshot(board, "欠員再配置")
    absent_staff = None
    if absent_staff_id:
        absent_staff = Staff.objects.filter(pk=absent_staff_id, is_active=True).first()

    # Ver.3.10.6.2: フロント側から absent_staff_id が届かなかった場合でも、
    # 再配置変更の before/after から欠員者を復元する。
    # 例: TV 安井→池田隆 / PET 池田隆→空欄 なら、after 側に残らない「安井」が欠員者。
    if absent_staff is None and isinstance(changes, list):
        before_names = {str(x.get("before", "")).strip() for x in changes if str(x.get("before", "")).strip()}
        after_names = {str(x.get("after", "")).strip() for x in changes if str(x.get("after", "")).strip()}
        missing_names = [name for name in before_names if name not in after_names]
        if len(missing_names) == 1:
            absent_staff = Staff.objects.filter(name=missing_names[0], is_active=True).first()

    for item in changes:
        try: cell_id = int(item.get("cell_id"))
        except (TypeError, ValueError): continue
        cell = board.cells.filter(pk=cell_id).first()
        if cell:
            cell.value = str(item.get("after", ""))[:100]
            cell.save(update_fields=["value"])
    # v3.10.6.28: 欠員再配置で2列以上の配置からスタッフが移動した場合、
    # 左側に空欄が残らないよう同じ配置・同じ時間セル内だけ左詰めする。
    # 例: PET [空欄][Bさん] -> [Bさん][空欄]
    changed_multi_rows = set()
    for item in changes:
        try:
            cell_id = int(item.get("cell_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        cell = board.cells.filter(pk=cell_id).select_related("area").first()
        if cell and getattr(cell.area, "slot_count", 1) > 1:
            changed_multi_rows.add((cell.area_id, cell.row_key, cell.area.slot_count))

    for area_id, row_key, slot_count in changed_multi_rows:
        row_cells = {
            c.slot_index: c
            for c in board.cells.filter(
                area_id=area_id, row_key=row_key, slot_index__lte=slot_count
            )
        }
        values = [
            (row_cells[i].value or "").strip()
            for i in range(1, slot_count + 1)
            if i in row_cells and (row_cells[i].value or "").strip()
        ]
        for i in range(1, slot_count + 1):
            cell = row_cells.get(i)
            if cell is None:
                cell = AssignmentCell.objects.create(
                    board=board, area_id=area_id, row_key=row_key, slot_index=i, value=""
                )
            new_value = values[i - 1] if i <= len(values) else ""
            if (cell.value or "") != new_value:
                cell.value = new_value
                cell.save(update_fields=["value"])

    replacement_names = {str(x.get("after", "")).strip() for x in changes if x.get("after")}
    if replacement_names:
        free = [x.strip() for x in re.split(r"[、,\n]+", board.free_text or "") if x.strip()]

        apply_slots = [s for s in _active_time_slots(include_special=False)]
        apply_areas = list(AssignmentArea.objects.filter(is_active=True))
        apply_rule_map = _time_rule_map(apply_areas, apply_slots)
        apply_coverage = defaultdict(lambda: defaultdict(set))
        for area in apply_areas:
            for layout_item in _area_row_layout(area, apply_slots, apply_rule_map):
                owner_key = layout_item.get("owner_key")
                if owner_key and not layout_item.get("hidden"):
                    apply_coverage[area.id][owner_key].add(layout_item["slot"].key)

        used_halves = defaultdict(set)
        for item in changes:
            name = str(item.get("after", "") or "").strip()
            if not name:
                continue
            try:
                cell_id = int(item.get("cell_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            cell = board.cells.filter(pk=cell_id).select_related("area").first()
            if not cell:
                continue
            covered_rows = apply_coverage.get(cell.area_id, {}).get(cell.row_key, {cell.row_key})
            if any(_reallocation_period_covers_row("am", key) for key in covered_rows):
                used_halves[name].add("午前")
            if any(_reallocation_period_covers_row("pm", key) for key in covered_rows):
                used_halves[name].add("午後")

        filtered_free = []
        for label in free:
            if label in replacement_names:
                continue
            if any(
                label == f"{name}（{half}）"
                for name, halves in used_halves.items()
                for half in halves
            ):
                continue
            filtered_free.append(label)

        board.free_text = "、".join(filtered_free)
        board.save(update_fields=["free_text", "updated_at"])
        StaffFreeHistory.objects.filter(
            board_date=duty_date,
            staff__name__in=replacement_names,
        ).delete()
    # v3.10.6.29:
    # 欠員再配置後も空き補充と同じく、前回の昼当番結果を足し増しせず
    # 直前時刻の実配置を基準に昼休憩→移動→兼任を作り直す。
    _apply_derived_rules(board, prefer_latest_prior=True)

    # v3.10.6.69:
    # 欠員再配置で「担当あり → 空欄」になった行だけを記録する。
    # 複数列は左詰め後の現在状態から空欄セルを取り直すため、
    # 元の slot_index がずれても正しい空きだけを次の「空き補充」へ渡せる。
    reallocation_vacancy_rows = set()
    for item in changes if isinstance(changes, list) else []:
        before = str(item.get("before", "") or "").strip()
        after = str(item.get("after", "") or "").strip()
        if not before or after:
            continue
        try:
            cell_id = int(item.get("cell_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        changed_cell = board.cells.filter(pk=cell_id).select_related("area").first()
        if changed_cell:
            reallocation_vacancy_rows.add((changed_cell.area_id, changed_cell.row_key))

    reallocation_vacancies = []
    for area_id, row_key in reallocation_vacancy_rows:
        area = AssignmentArea.objects.filter(pk=area_id, is_active=True).first()
        if not area:
            continue
        max_slots = max(1, int(area.auto_assignment_count or 0))
        for cell in board.cells.filter(
            area_id=area_id,
            row_key=row_key,
            slot_index__lte=max_slots,
        ).order_by("slot_index"):
            if not (cell.value or "").strip():
                reallocation_vacancies.append({
                    "area_id": area_id,
                    "row_key": row_key,
                    "slot_index": cell.slot_index,
                    "original_value": "",
                })

    session = getattr(request, "session", None)
    if session is not None:
        if reallocation_vacancies:
            session[_BOARD_REALLOCATION_VACANCIES_SESSION_KEY] = {
                "date": duty_date.isoformat(),
                "vacancies": reallocation_vacancies,
            }
        else:
            session.pop(_BOARD_REALLOCATION_VACANCIES_SESSION_KEY, None)
        session.modified = True

    # Ver.3.10.6.2: 再配置・派生ルール反映の最後に欠員理由を登録する。
    # 先に登録すると後続同期処理で表示用メタ欄が更新される環境があるため、
    # 最終状態へ確実に残してから配置未定を再計算する。
    if absent_staff:
        _register_reallocation_absence(board, absent_staff, absence_reason, absence_period)
    _recalculate_unassigned(board)
    board.refresh_from_db()
    _arm_board_undo(request, undo_snapshot, board)
    return JsonResponse({"ok": True, "message": "選択した再配置案を反映しました。"})


@require_POST
@transaction.atomic
def fill_manual_vacancies(request):
    """手動移動で空いた通常配置を、Free→低優先配置の順で最小限補充する。"""
    try:
        data = json.loads(request.body.decode("utf-8")) if request.content_type == "application/json" else request.POST
        duty_date = _parse_date(data.get("date"))
        specs = data.get("vacancies", [])
    except Exception:
        return JsonResponse({"ok": False, "error": "補充データが正しくありません。"}, status=400)

    if not isinstance(specs, list):
        return JsonResponse({"ok": False, "error": "補充データが正しくありません。"}, status=400)

    board = get_object_or_404(DailyBoard.objects.select_for_update(), board_date=duty_date)
    undo_snapshot = _build_board_undo_snapshot(board, "空き補充")
    time_slots = {x.key: x for x in TimeSlot.objects.filter(is_active=True, kind="time")}

    # v3.10.6.69:
    # フロントから手動空きが来ていない場合は、
    # 直前の「欠員再配置」が実際に作った空きだけを補充対象にする。
    # 配置表に元から存在する空欄は対象にしない。
    if not specs:
        session = getattr(request, "session", None)
        remembered = session.get(_BOARD_REALLOCATION_VACANCIES_SESSION_KEY, {}) if session is not None else {}
        if remembered.get("date") == duty_date.isoformat():
            specs = remembered.get("vacancies", []) or []

    if not specs:
        return JsonResponse({
            "ok": False,
            "error": "直前の欠員再配置で生じた補充対象の空きはありません。",
        }, status=400)

    targets = []
    seen = set()
    # v3.10.6.27: 手動で外した元担当をセル単位で覚える。
    # 2列目を消した直後に、同じ本人を「空き時間」として再投入するのを防ぐ。
    vacated_originals = {}
    for spec in specs:
        try:
            area_id = int(spec.get("area_id"))
            slot_index = int(spec.get("slot_index", 1))
            row_key = str(spec.get("row_key", ""))
        except (TypeError, ValueError, AttributeError):
            continue
        if row_key not in time_slots:
            continue
        key = (area_id, row_key, slot_index)
        if key in seen:
            continue
        seen.add(key)
        original_value = str(spec.get("original_value", "") or "").strip()
        if original_value:
            vacated_originals[key] = original_value
        cell = board.cells.filter(area_id=area_id, row_key=row_key, slot_index=slot_index).select_related("area").first()
        if cell and not (cell.value or "").strip():
            targets.append(cell)

    if not targets:
        return JsonResponse({"ok": False, "error": "指定された空きセルはすでに埋まっているか、通常配置ではありません。"}, status=400)

    unavailable_fields = (
        "night_shift", "night_shift_after", "holiday_day_shift",
        "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
        "annual_leave_1", "annual_leave_2", "reception_leave",
    )
    unavailable_text = "\n".join(str(getattr(board, f, "") or "") for f in unavailable_fields)
    full_unavailable = {
        st.name for st in Staff.objects.filter(is_active=True)
        if st.name and st.name in unavailable_text
    }
    full_unavailable |= _full_day_absence_names(duty_date)

    day_absences = list(
        WeeklyAbsence.objects.filter(absence_date=duty_date).select_related("staff", "start_slot", "end_slot")
    )
    quick_entries = _parse_quick_meta_absences(board)

    def row_available(staff, row_key):
        if staff.name in full_unavailable:
            return False
        if not _staff_available_for_row(staff, row_key):
            return False
        for absence in day_absences:
            if absence.staff_id == staff.id and _absence_covers_row(absence, row_key):
                return False
        for entry in quick_entries:
            if entry["staff"].id == staff.id and _quick_entry_covers_row(entry, row_key):
                return False
        return True

    active = list(
        Staff.objects.filter(is_active=True, auto_assignment_enabled=True)
        .select_related("dedicated_area", "preferred_area_1", "preferred_area_2", "preferred_area_3", "preferred_area_4")
        .order_by("assignment_staff_priority", "display_order", "name")
    )
    staff_by_name = {st.name: st for st in active}

    free_names = [x.strip() for x in re.split(r"[、,\n]+", board.free_text or "") if x.strip()]
    free_set = set(free_names)
    used_free = set()
    changes = []
    notes = []

    # v3.10.6.25:
    # Free欄は「1日単位の未配置者リスト」なので、一度どこかの時間帯で使うと名前が消える。
    # それだけを候補源にすると、11:00だけ配置された人を8:30-10:00で再利用できない。
    # 空き補充では配置表そのものから「対象時間帯に実際に空いているか」を判定する。
    normal_slots = [s for s in _active_time_slots(include_special=False) if s.key in time_slots]
    normal_rule_map = _time_rule_map(list(AssignmentArea.objects.filter(is_active=True)), normal_slots)

    # area_id -> owner_key -> {そのセルが実際に覆う時間row_key...}
    coverage_by_area_owner = defaultdict(lambda: defaultdict(set))
    for configured_area in AssignmentArea.objects.filter(is_active=True):
        layout = _area_row_layout(configured_area, normal_slots, normal_rule_map)
        for item in layout:
            owner_key = item.get("owner_key")
            if not owner_key or item.get("hidden"):
                continue
            coverage_by_area_owner[configured_area.id][owner_key].add(item["slot"].key)

    def covered_rows_for_cell(cell):
        return set(coverage_by_area_owner.get(cell.area_id, {}).get(cell.row_key, {cell.row_key}))

    def target_covered_rows(target):
        return set(coverage_by_area_owner.get(target.area_id, {}).get(target.row_key, {target.row_key}))

    def staff_busy_during(staff_name, covered_rows):
        """staff_name が covered_rows のどこかで既に通常配置されていれば True。"""
        if not staff_name:
            return False
        for cell in board.cells.filter(value=staff_name, row_key__in=time_slots.keys()).select_related("area"):
            if covered_rows_for_cell(cell) & covered_rows:
                return True
        return False

    def staff_available_during(staff, covered_rows):
        """勤務時間・不在設定を、対象セルが覆う全時間帯で確認する。"""
        return all(row_available(staff, rk) for rk in covered_rows)

    def sync_next_derived_source(area, from_row_key, staff):
        """
        空き補充で担当が途中交代したとき、直後の時間連動ルールが参照する
        「その時刻の元配置」も新担当へ引き継ぐ。

        Free欄の状態ではなく実配置を基準にする v3.10.6.25 の考え方に合わせ、
        最も近い将来の時間連動時刻だけを更新する。過去の縦連結 owner は触らないため、
        8:30-10:00 の旧担当を巻き戻して変更しない。
        """
        slot = time_slots.get(from_row_key)
        if not slot:
            return
        future_rules = list(
            DerivedAssignmentRule.objects.filter(
                is_active=True, source_area=area,
                time_slot__kind="time",
                time_slot__display_order__gt=slot.display_order,
            ).select_related("time_slot").order_by("time_slot__display_order", "time_slot__id", "id")
        )
        if not future_rules:
            return
        next_order = future_rules[0].time_slot.display_order
        next_rules = [r for r in future_rules if r.time_slot.display_order == next_order]
        next_slot = next_rules[0].time_slot

        # 新担当がその時刻に勤務可能でなければ引き継がない。
        if not row_available(staff, next_slot.key):
            return

        # その時刻専用セルへ保存する。縦連結 owner（例 8:30）は変更しない。
        # slot_index は空き補充された枠を優先するが、通常は1枠運用。
        override, _ = AssignmentCell.objects.get_or_create(
            board=board, area=area, row_key=next_slot.key, slot_index=1
        )
        override.value = staff.name
        override.save(update_fields=["value"])

    def fit_score(staff, area):
        prefs = [staff.preferred_area_1_id, staff.preferred_area_2_id, staff.preferred_area_3_id, staff.preferred_area_4_id]
        weights = (80, 50, 30, 15)
        score = 0
        for idx, aid in enumerate(prefs):
            if aid == area.id:
                score += weights[idx]
                break
        return score

    # 高優先配置から順に埋める。
    targets.sort(key=lambda c: (c.area.assignment_priority, c.area.display_order, time_slots[c.row_key].display_order, c.slot_index))

    for original_target in targets:
        if (original_target.value or "").strip():
            continue

        target = original_target
        row_key = target.row_key
        area = target.area
        removed_name = vacated_originals.get((area.id, row_key, target.slot_index), "")

        # v3.10.6.27: 2列以上の配置で1列目だけが空いた場合は、
        # まず右側の既存担当を左詰めする。
        # 例: RI [空欄][Bさん] -> [Bさん][空欄] としてから2列目を補充する。
        if target.slot_index == 1 and getattr(area, "slot_count", 1) > 1:
            right_cell = (
                board.cells.filter(
                    area=area, row_key=row_key, slot_index__gt=1
                ).exclude(value="").order_by("slot_index").first()
            )
            if right_cell:
                moved_name = (right_cell.value or "").strip()
                target.value = moved_name
                target.save(update_fields=["value"])
                right_cell.value = ""
                right_cell.save(update_fields=["value"])
                changes.append({
                    "area": area.name,
                    "row": time_slots[row_key].label,
                    "staff": moved_name,
                    "source": f"{right_cell.slot_index}列目→1列目",
                })
                notes.append(
                    f"{time_slots[row_key].label} {area.name}：{moved_name}さんを"
                    f"{right_cell.slot_index}列目から1列目へ移動"
                )
                target = right_cell
                # 元々1列目から外した人は、左詰め後に生じた2列目の空きにも
                # そのまま戻さない。

        covered_rows = target_covered_rows(target)

        # 1) 対象時間帯に実際に空いているスタッフから補充。
        #    現在Free欄に残っている人をまず優先し、その次に
        #    「別時間帯では配置済みだが、この時間帯は空いている人」を候補にする。
        idle_candidates = []
        for st in active:
            # ユーザーがこの空き操作で明示的に外した本人は、
            # 同じ時間帯・同じ配置へ即座に戻さない。
            if removed_name and st.name == removed_name:
                continue
            if st.dedicated_area_id is not None:
                continue
            if not _can_staff_cover_area(st, area):
                continue
            if not staff_available_during(st, covered_rows):
                continue
            if staff_busy_during(st.name, covered_rows):
                continue
            idle_candidates.append(st)

        idle_candidates.sort(key=lambda st: (
            0 if st.name in free_set else 1,
            -fit_score(st, area),
            st.assignment_staff_priority,
            st.display_order,
            st.name,
        ))

        if idle_candidates:
            st = idle_candidates[0]
            was_free = st.name in free_set
            target.value = st.name
            target.save(update_fields=["value"])
            sync_next_derived_source(area, row_key, st)
            if was_free:
                used_free.add(st.name)
            source_label = "Free" if was_free else "空き時間"
            changes.append({"area": area.name, "row": time_slots[row_key].label, "staff": st.name, "source": source_label})
            notes.append(f"{time_slots[row_key].label} {area.name} ← {st.name}さん（{source_label}）")
            continue

        # 2) 対象時間帯に空いている人がいなければ、より優先度の低い配置から移動。
        #    専任者は除外し、移動元が最低必要人数を下回る場合も除外。
        donor_options = []
        occupied = list(
            board.cells.filter(row_key__in=time_slots.keys()).exclude(value="").select_related("area")
        )
        for source in occupied:
            # 移動元セルが、欠員セルの実時間帯と重ならないなら候補外。
            if not (covered_rows_for_cell(source) & covered_rows):
                continue
            if source.area_id == area.id:
                continue
            # 数値が大きいほど低優先という既存ルールに合わせる。
            if source.area.assignment_priority <= area.assignment_priority:
                continue
            name = (source.value or "").strip()
            # 手動で外した本人を、別配置から玉突きで同じ場所へ戻すのも避ける。
            if removed_name and name == removed_name:
                continue
            st = staff_by_name.get(name)
            if not st or st.dedicated_area_id is not None:
                continue
            if not staff_available_during(st, covered_rows) or not _can_staff_cover_area(st, area):
                continue
            source_filled = board.cells.filter(area=source.area, row_key=source.row_key).exclude(value="").count()
            if max(0, source_filled - 1) < source.area.minimum_assignment_count:
                continue
            donor_options.append((
                -fit_score(st, area),
                -source.area.assignment_priority,
                st.assignment_staff_priority,
                st.display_order,
                st.name,
                st,
                source,
            ))

        donor_options.sort(key=lambda x: x[:5])
        if donor_options:
            _, _, _, _, _, st, source = donor_options[0]
            source_area_name = source.area.name
            source.value = ""
            source.save(update_fields=["value"])
            target.value = st.name
            target.save(update_fields=["value"])
            sync_next_derived_source(area, row_key, st)
            changes.append({"area": area.name, "row": time_slots[row_key].label, "staff": st.name, "source": source_area_name})
            notes.append(f"{time_slots[row_key].label} {area.name} ← {st.name}さん（{source_area_name}から移動）")
        else:
            notes.append(f"{time_slots[row_key].label} {area.name}：補充候補なし")

    if used_free:
        free_names = [name for name in free_names if name not in used_free]
        board.free_text = "、".join(free_names)
        board.save(update_fields=["free_text", "updated_at"])
        StaffFreeHistory.objects.filter(board_date=duty_date, staff__name__in=used_free).delete()

    _apply_derived_rules(board, prefer_latest_prior=True)
    _recalculate_unassigned(board)

    if not changes:
        return JsonResponse({
            "ok": False,
            "error": "補充できるスタッフが見つかりませんでした。Free、配置優先度、専任設定、勤務可能時間を確認してください。",
        }, status=400)

    remaining_specs = []
    for c in targets:
        current_value = board.cells.filter(pk=c.pk).values_list("value", flat=True).first()
        if not (current_value or "").strip():
            remaining_specs.append({
                "area_id": c.area_id,
                "row_key": c.row_key,
                "slot_index": c.slot_index,
                "original_value": "",
            })
    remaining = len(remaining_specs)

    session = getattr(request, "session", None)
    if session is not None:
        if remaining_specs:
            session[_BOARD_REALLOCATION_VACANCIES_SESSION_KEY] = {
                "date": duty_date.isoformat(),
                "vacancies": remaining_specs,
            }
        else:
            session.pop(_BOARD_REALLOCATION_VACANCIES_SESSION_KEY, None)
        session.modified = True

    message = "空き配置を補充しました。\n" + "\n".join(notes)
    if remaining:
        message += f"\n未補充: {remaining}セル"
    board.refresh_from_db()
    _arm_board_undo(request, undo_snapshot, board)
    return JsonResponse({"ok": True, "message": message, "changes": changes, "remaining": remaining})


@require_POST
def weekly_generate(request):
    """週間画面から月～金を一括作成。日別APIをサーバー側で順番に実行し、失敗日も返す。"""
    monday, _ = _week_bounds(request.POST.get("week", ""))
    dates = [monday + timedelta(days=i) for i in range(5)]
    rf = RequestFactory()
    results = []
    for duty_date in dates:
        subrequest = rf.post("/assignment/auto-assignment/", {"date": duty_date.isoformat(), "rebuild": "1"})
        response = auto_assignment(subrequest)
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except Exception:
            payload = {"ok": False, "error": "応答を解析できませんでした。"}
        results.append({"date": duty_date.isoformat(), "status": response.status_code, **payload})
        if response.status_code >= 400 or not payload.get("ok"):
            return JsonResponse({"ok": False, "error": f"{duty_date:%m/%d} の自動配置で停止しました: {payload.get('error','不明なエラー')}", "results": results}, status=400)
    return JsonResponse({"ok": True, "message": "月～金の5日分を自動作成しました。", "results": results})


@require_POST
def reception_staff_add(request):
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "受付担当者名を入力してください。")
        return redirect("daily_assignment:settings")
    try:
        order = max(int(request.POST.get("display_order", "0") or 0), 0)
    except (TypeError, ValueError):
        order = 0
    ReceptionStaff.objects.update_or_create(
        name=name, defaults={"display_order": order, "is_active": True}
    )
    messages.success(request, f"受付担当「{name}」を登録しました。")
    return redirect("daily_assignment:settings")


@require_POST
def reception_staff_delete(request, reception_id):
    obj = get_object_or_404(ReceptionStaff, pk=reception_id)
    name = obj.name
    obj.delete()
    messages.success(request, f"受付担当「{name}」を削除しました。")
    return redirect("daily_assignment:settings")


@require_POST
def meeting_preset_add(request):
    name = request.POST.get("name", "").strip()[:80]
    if not name:
        messages.error(request, "会議等の内容を入力してください。")
    else:
        try:
            order = max(0, int(request.POST.get("display_order", "0") or 0))
        except ValueError:
            order = 0
        MeetingPreset.objects.update_or_create(name=name, defaults={"display_order": order, "is_active": True})
        messages.success(request, f"会議等「{name}」を登録しました。")
    return redirect("daily_assignment:settings")


@require_POST
def meeting_preset_delete(request, preset_id):
    preset = get_object_or_404(MeetingPreset, pk=preset_id)
    preset.delete()
    messages.success(request, "会議等の登録内容を削除しました。")
    return redirect("daily_assignment:settings")


@require_POST
def dedicated_backup_add(request):
    area = get_object_or_404(AssignmentArea, pk=request.POST.get("area_id"))
    staff = get_object_or_404(Staff, pk=request.POST.get("staff_id"))
    try: priority = max(1, int(request.POST.get("priority", 1)))
    except ValueError: priority = 1
    # 同じ順位があれば後ろへずらす。
    for entry in DedicatedAreaBackup.objects.filter(area=area, priority__gte=priority).order_by("-priority"):
        entry.priority += 1; entry.save(update_fields=["priority"])
    DedicatedAreaBackup.objects.update_or_create(area=area, staff=staff, defaults={"priority": priority})
    messages.success(request, f"{area.name} の代理{priority}に {staff.name} さんを設定しました。")
    return redirect("daily_assignment:settings")


@require_POST
def dedicated_backup_delete(request, backup_id):
    obj = get_object_or_404(DedicatedAreaBackup, pk=backup_id)
    name = f"{obj.area.name} / {obj.staff.name}"
    obj.delete()
    messages.success(request, f"専任代理 {name} を削除しました。")
    return redirect("daily_assignment:settings")
