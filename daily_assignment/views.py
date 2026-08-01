import json
from datetime import date, datetime, timedelta
from io import BytesIO

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .forms import DailyBoardMetaForm
from .models import AssignmentArea, AssignmentCell, DailyBoard, Staff

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
    form = DailyBoardMetaForm(instance=board)
    meta_fields = (
        "conference",
        "two_shift",
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
        "two_shift",
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
        for field in ("conference", "two_shift", "annual_leave_1", "annual_leave_2", "staffing_am", "staffing_pm", "free_text", "comment", "unassigned", "reception_leave"):
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

    if Staff.objects.filter(name=name).exists():
        messages.error(request, f"「{name}」は既に登録されています。")
        return redirect("daily_assignment:settings")

    Staff.objects.create(
        name=name,
        display_order=display_order,
        is_active=is_active,
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

    duplicate = Staff.objects.filter(name=name).exclude(pk=staff.pk)

    if duplicate.exists():
        messages.error(request, f"「{name}」は既に登録されています。")
        return redirect("daily_assignment:settings")

    staff.name = name
    staff.display_order = display_order
    staff.is_active = is_active
    staff.save()

    messages.success(request, f"スタッフ「{name}」を更新しました。")
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
