from django.db import models


class Staff(models.Model):
    name = models.CharField("氏名", max_length=50, unique=True)
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)
    work_start_time = models.TimeField("勤務開始時刻", null=True, blank=True, help_text="空欄なら開始時刻の制限なし")
    work_end_time = models.TimeField("勤務終了時刻", null=True, blank=True, help_text="空欄なら終了時刻の制限なし")

    # 夜勤・休日日勤の共通ローテーション
    duty_rotation_enabled = models.BooleanField("勤務ローテーション対象", default=False)
    duty_rotation_order = models.PositiveIntegerField("勤務ローテーション順", default=0)
    main_rotation_enabled = models.BooleanField("主担当ローテーション対象", default=False)
    main_rotation_order = models.PositiveIntegerField("主担当順", default=0)

    # 通常配置の自動作成ルール
    auto_assignment_enabled = models.BooleanField("通常配置の自動作成対象", default=False)
    ASSIGNMENT_STAFF_PRIORITY_CHOICES = (
        (1, "最優先"), (2, "優先"), (3, "通常"), (4, "Free優先"),
    )
    assignment_staff_priority = models.PositiveSmallIntegerField(
        "スタッフ配置優先度", choices=ASSIGNMENT_STAFF_PRIORITY_CHOICES, default=3,
        help_text="数字が小さいほど通常配置へ入りやすく、4は人員に余裕があるときFreeになりやすくなります。",
    )
    dedicated_area = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="dedicated_staff", verbose_name="専任配置",
    )
    preferred_area_1 = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="preferred_staff_1", verbose_name="優先配置①",
    )
    preferred_area_2 = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="preferred_staff_2", verbose_name="優先配置②",
    )
    preferred_area_3 = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="preferred_staff_3", verbose_name="優先配置③",
    )
    preferred_area_4 = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="preferred_staff_4", verbose_name="優先配置④",
    )
    avoid_area = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="avoided_by_staff", verbose_name="配置しない場所",
    )
    weekly_target_area = models.ForeignKey(
        "AssignmentArea", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="weekly_target_staff", verbose_name="週回数を指定する場所",
    )
    weekly_target_count = models.PositiveSmallIntegerField(
        "週の目安回数", default=0,
    )

    class Meta:
        ordering = ("display_order", "name")
        verbose_name = "スタッフ"
        verbose_name_plural = "スタッフ"

    def __str__(self):
        return self.name


class DutyRotationVersion(models.Model):
    """指定月以降に適用する勤務ローテーション構成。過去月の並びを保持する。"""
    effective_month = models.DateField("適用開始月", unique=True, help_text="必ず月初日を保存")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-effective_month",)
        verbose_name = "勤務ローテーション版"
        verbose_name_plural = "勤務ローテーション版"

    def __str__(self):
        return f"{self.effective_month:%Y-%m}から"


class DutyRotationVersionMember(models.Model):
    version = models.ForeignKey(DutyRotationVersion, on_delete=models.CASCADE, related_name="members")
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="duty_rotation_versions")
    rotation_order = models.PositiveIntegerField("勤務順", default=0)

    class Meta:
        ordering = ("rotation_order", "staff__display_order", "staff__name")
        constraints = [models.UniqueConstraint(fields=("version", "staff"), name="unique_duty_rotation_version_staff")]
        verbose_name = "勤務ローテーション版メンバー"
        verbose_name_plural = "勤務ローテーション版メンバー"

    def __str__(self):
        return f"{self.version} {self.staff.name}"


class StaffFreeHistory(models.Model):
    """自動配置でFreeになった履歴。公平なFreeローテーションに使用する。"""
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="free_history")
    board_date = models.DateField("Free日")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("staff", "board_date"), name="unique_staff_free_history")]
        ordering = ("-board_date", "staff_id")
        verbose_name = "スタッフFree履歴"
        verbose_name_plural = "スタッフFree履歴"

    def __str__(self):
        return f"{self.board_date} {self.staff.name}"


class StaffWeeklyTarget(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="weekly_targets")
    area = models.ForeignKey("AssignmentArea", on_delete=models.CASCADE, related_name="weekly_target_entries")
    target_count = models.PositiveSmallIntegerField("週の目安回数", default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("staff", "area"), name="unique_staff_weekly_target")]
        ordering = ("staff_id", "area__display_order", "area_id")


class StaffAvoidArea(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="avoid_area_entries")
    area = models.ForeignKey("AssignmentArea", on_delete=models.CASCADE, related_name="avoided_staff_entries")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("staff", "area"), name="unique_staff_avoid_area")]
        ordering = ("staff_id", "area__display_order", "area_id")


class AssignmentArea(models.Model):
    AUTO_ASSIGNMENT_MODE_CHOICES = (
        ("daily", "毎日"),
        ("scheduled", "予定時のみ"),
        ("manual", "手動のみ"),
    )
    OVERLAP_ASSIGNMENT_MODE_CHOICES = (
        ("never", "不可"),
        ("am_only", "AMのみ可"),
        ("pm_only", "PMのみ可"),
        ("shortage", "人員不足時のみ可"),
        ("always", "常に可"),
    )

    category = models.CharField("部門", max_length=40, blank=True)
    name = models.CharField("配置場所", max_length=60)
    slot_count = models.PositiveSmallIntegerField("入力列数", default=1)
    auto_assignment_count = models.PositiveSmallIntegerField(
        "自動配置人数", default=1,
        help_text="入力列数とは別に、自動配置で埋める人数を指定します。",
    )
    auto_assignment_mode = models.CharField(
        "自動配置方式", max_length=20,
        choices=AUTO_ASSIGNMENT_MODE_CHOICES, default="daily",
    )
    minimum_assignment_count = models.PositiveSmallIntegerField(
        "最低必要人数", default=0,
        help_text="人員不足時でもできるだけ確保したい人数です。0なら最低人数の指定なし。",
    )
    assignment_priority = models.PositiveSmallIntegerField(
        "配置優先度", default=3,
        help_text="1が最優先、5が低優先です。人員不足時の配置順に使います。",
    )
    overlap_assignment_mode = models.CharField(
        "重複配置", max_length=20,
        choices=OVERLAP_ASSIGNMENT_MODE_CHOICES, default="never",
        help_text="欠員再配置で、ほかの配置と同時間帯に兼任させてよいかを指定します。時間連動ルールの兼任はこの設定とは別に許可されます。",
    )
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)
    main_rotation_enabled = models.BooleanField("主担当ローテーション対象", default=False)
    main_rotation_order = models.PositiveIntegerField("主担当順", default=0)

    class Meta:
        ordering = ("display_order", "id")
        constraints = [
            models.UniqueConstraint(fields=("category", "name"), name="unique_assignment_area")
        ]
        verbose_name = "配置場所"
        verbose_name_plural = "配置場所"

    def __str__(self):
        return f"{self.category} / {self.name}" if self.category else self.name


class DailyBoard(models.Model):
    board_date = models.DateField("日付", unique=True)
    conference = models.TextField("会議等", blank=True)

    # 旧データ互換用。新画面では専用欄を使用する。
    two_shift = models.TextField("2交代勤務（旧欄）", blank=True)

    night_shift = models.CharField("夜勤", max_length=100, blank=True)
    night_shift_after = models.CharField("明け", max_length=100, blank=True)
    holiday_day_shift = models.CharField("休日日勤", max_length=100, blank=True)
    compensatory_leave_1 = models.CharField("代休①", max_length=100, blank=True)
    compensatory_leave_2 = models.CharField("代休②", max_length=100, blank=True)
    compensatory_leave_3 = models.CharField("代休③", max_length=100, blank=True)

    annual_leave_1 = models.TextField("年休①", blank=True)
    annual_leave_2 = models.TextField("年休②", blank=True)
    staffing_am = models.CharField("人数状況・午前", max_length=50, blank=True)
    staffing_pm = models.CharField("人数状況・午後", max_length=50, blank=True)
    free_text = models.TextField("Free", blank=True)
    comment = models.TextField("コメント", blank=True)
    unassigned = models.TextField("配置未定", blank=True)
    reception_leave = models.TextField("受付（年休）", blank=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ("-board_date",)
        verbose_name = "当日配置表"
        verbose_name_plural = "当日配置表"

    def __str__(self):
        return self.board_date.strftime("%Y-%m-%d")


class DailyAreaActivation(models.Model):
    """「予定時のみ」の配置場所を、その日に自動配置対象にするか保存する。"""
    board = models.ForeignKey(
        DailyBoard, on_delete=models.CASCADE, related_name="area_activations"
    )
    area = models.ForeignKey(AssignmentArea, on_delete=models.PROTECT)
    is_enabled = models.BooleanField("本日の予定あり", default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("board", "area"),
                name="unique_daily_area_activation",
            )
        ]
        verbose_name = "当日配置場所予定"
        verbose_name_plural = "当日配置場所予定"

    def __str__(self):
        state = "ON" if self.is_enabled else "OFF"
        return f"{self.board.board_date} {self.area} {state}"


class AssignmentCell(models.Model):
    board = models.ForeignKey(DailyBoard, on_delete=models.CASCADE, related_name="cells")
    area = models.ForeignKey(AssignmentArea, on_delete=models.PROTECT)
    row_key = models.CharField("時間帯", max_length=20)
    slot_index = models.PositiveSmallIntegerField("列番号", default=1)
    value = models.CharField("担当者", max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("board", "area", "row_key", "slot_index"),
                name="unique_assignment_cell",
            )
        ]
        verbose_name = "配置セル"
        verbose_name_plural = "配置セル"

    def __str__(self):
        return f"{self.board} {self.area} {self.row_key}-{self.slot_index}: {self.value}"


class CompensatoryLeaveRule(models.Model):
    DAY_TYPE_CHOICES = (
        ("saturday", "土曜日"),
        ("sunday", "日曜日"),
        ("holiday", "祝日"),
    )
    DUTY_TYPE_CHOICES = (
        ("holiday_day", "休日日勤"),
        ("night", "夜勤"),
        ("night_after", "明け"),
    )
    OFFSET_MODE_CHOICES = (
        ("weekday", "指定週の曜日"),
        ("days", "勤務日の何日後"),
    )
    WEEKDAY_CHOICES = (
        (0, "月曜日"), (1, "火曜日"), (2, "水曜日"), (3, "木曜日"),
        (4, "金曜日"), (5, "土曜日"), (6, "日曜日"),
    )

    day_type = models.CharField("勤務日の種類", max_length=20, choices=DAY_TYPE_CHOICES)
    duty_type = models.CharField("勤務種類", max_length=20, choices=DUTY_TYPE_CHOICES)
    offset_mode = models.CharField(
        "代休日の決め方", max_length=20, choices=OFFSET_MODE_CHOICES, default="weekday"
    )
    weeks_after = models.PositiveSmallIntegerField("何週後", default=1)
    target_weekday = models.PositiveSmallIntegerField(
        "代休曜日", choices=WEEKDAY_CHOICES, default=0
    )
    days_after = models.PositiveSmallIntegerField("何日後", default=1)
    is_active = models.BooleanField("使用中", default=True)

    class Meta:
        ordering = ("day_type", "duty_type")
        constraints = [
            models.UniqueConstraint(
                fields=("day_type", "duty_type"),
                name="unique_comp_leave_rule",
            )
        ]
        verbose_name = "代休ルール"
        verbose_name_plural = "代休ルール"

    def __str__(self):
        return f"{self.get_day_type_display()}・{self.get_duty_type_display()}"


class CompensatoryLeaveRecord(models.Model):
    DUTY_TYPE_CHOICES = CompensatoryLeaveRule.DUTY_TYPE_CHOICES

    source_date = models.DateField("勤務日")
    target_date = models.DateField("代休日")
    duty_type = models.CharField("勤務種類", max_length=20, choices=DUTY_TYPE_CHOICES)
    staff_name = models.CharField("スタッフ", max_length=100)
    is_manual_override = models.BooleanField("代休日を手動変更", default=False)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        ordering = ("target_date", "source_date")
        constraints = [
            models.UniqueConstraint(
                fields=("source_date", "duty_type", "staff_name"),
                name="unique_comp_leave_record",
            )
        ]
        verbose_name = "代休予約"
        verbose_name_plural = "代休予約"

    def __str__(self):
        return f"{self.source_date} {self.staff_name} → {self.target_date}"


class DutyCalendarDay(models.Model):
    """月間勤務カレンダー上で確定した夜勤・休日日勤と日ごとの例外設定。"""

    DAY_MODE_CHOICES = (
        ("auto", "自動判定（土日祝）"),
        ("normal", "通常日"),
        ("holiday", "休日扱い"),
        ("closed", "臨時休業"),
    )

    duty_date = models.DateField("日付", unique=True)
    day_mode = models.CharField(
        "勤務区分", max_length=20, choices=DAY_MODE_CHOICES, default="auto"
    )
    night_shift = models.ForeignKey(
        Staff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendar_night_shifts",
        verbose_name="夜勤",
    )
    holiday_day_shift = models.ForeignKey(
        Staff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendar_holiday_day_shifts",
        verbose_name="休日日勤",
    )
    no_compensatory_leave = models.BooleanField(
        "代休を発生させない",
        default=False,
        help_text="祝日・年末年始など、例外的に代休を発生させない日に使用します。",
    )
    force_compensatory_leave = models.BooleanField(
        "この日だけ代休を発生",
        default=False,
        help_text="祝日の代休ルールを通常OFFにしていても、この日だけ代休を作成します。",
    )
    force_holiday_day_compensatory_leave = models.BooleanField(
        "休日日勤担当だけ代休を追加", default=False,
        help_text="祝日の代休ルールがOFFでも、この日の休日日勤担当者だけ代休を作成します。",
    )
    force_night_compensatory_leave = models.BooleanField(
        "夜勤担当だけ代休を追加", default=False,
        help_text="祝日の代休ルールがOFFでも、この日の夜勤担当者だけ代休を作成します。",
    )
    force_night_after_compensatory_leave = models.BooleanField(
        "明け担当だけ代休を追加", default=False,
        help_text="祝日の代休ルールがOFFでも、この日の夜勤に続く明け分だけ代休を作成します。",
    )
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ("duty_date",)
        verbose_name = "勤務カレンダー"
        verbose_name_plural = "勤務カレンダー"

    def __str__(self):
        return self.duty_date.strftime("%Y-%m-%d")


class DutyCalendarDayHistory(models.Model):
    """日ごとの勤務カレンダー設定を1段階だけ戻すための履歴。"""

    duty_date = models.DateField("日付", unique=True)
    snapshot = models.JSONField("直前の状態", default=dict, blank=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ("duty_date",)
        verbose_name = "勤務カレンダー変更履歴"
        verbose_name_plural = "勤務カレンダー変更履歴"

    def __str__(self):
        return f"{self.duty_date:%Y-%m-%d} undo"


class TimeSlot(models.Model):
    KIND_CHOICES = (
        ("time", "時間枠"),
        ("main", "主担当"),
        ("sub", "Sub"),
    )

    key = models.CharField("内部キー", max_length=20, unique=True)
    label = models.CharField("表示名", max_length=30)
    display_order = models.PositiveIntegerField("表示順", default=0)
    kind = models.CharField("種類", max_length=10, choices=KIND_CHOICES, default="time")
    is_active = models.BooleanField("使用中", default=True)
    is_lunch_highlight = models.BooleanField("昼休憩色", default=False)

    class Meta:
        ordering = ("display_order", "id")
        verbose_name = "時間枠"
        verbose_name_plural = "時間枠"

    def __str__(self):
        return self.label


class AreaTimeRule(models.Model):
    MODE_CHOICES = (
        ("normal", "通常"),
        ("hidden", "枠なし"),
        ("merge_start", "連結開始"),
        ("merge_continue", "連結中"),
    )

    area = models.ForeignKey(
        AssignmentArea, on_delete=models.CASCADE, related_name="time_rules"
    )
    time_slot = models.ForeignKey(
        TimeSlot, on_delete=models.CASCADE, related_name="area_rules"
    )
    mode = models.CharField("表示方法", max_length=20, choices=MODE_CHOICES, default="normal")
    auto_assignment_enabled = models.BooleanField(
        "自動配置ON", default=True,
        help_text="OFFにすると、この配置場所・時間のセルは通常自動配置で入力しません。",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("area", "time_slot"), name="unique_area_time_rule"
            )
        ]
        verbose_name = "配置場所時間ルール"
        verbose_name_plural = "配置場所時間ルール"

    def __str__(self):
        return f"{self.area} / {self.time_slot}: {self.get_mode_display()}"


class HorizontalMergeRule(models.Model):
    name = models.CharField("結合名", max_length=80, default="昼休憩")
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="horizontal_merge_rules", verbose_name="時間")
    start_area = models.ForeignKey(AssignmentArea, on_delete=models.CASCADE, related_name="horizontal_merge_starts", verbose_name="開始配置")
    end_area = models.ForeignKey(AssignmentArea, on_delete=models.CASCADE, related_name="horizontal_merge_ends", verbose_name="終了配置")
    representative_area = models.ForeignKey(AssignmentArea, on_delete=models.CASCADE, related_name="horizontal_merge_representatives", verbose_name="代表配置")
    exclude_from_auto_assignment = models.BooleanField("自動配置対象外", default=True)
    is_active = models.BooleanField("使用中", default=True)

    class Meta:
        ordering = ("time_slot__display_order", "id")
        verbose_name = "横結合ルール"
        verbose_name_plural = "横結合ルール"

    def __str__(self):
        return f"{self.time_slot} / {self.name}"


class DerivedAssignmentRule(models.Model):
    ACTION_CHOICES = (
        ("add", "兼任（元配置を残す）"),
        ("move", "移動（元配置から外す）"),
        ("break", "昼休憩"),
    )

    name = models.CharField("ルール名", max_length=80, blank=True)
    source_area = models.ForeignKey(
        AssignmentArea, on_delete=models.CASCADE, related_name="derived_source_rules",
        verbose_name="元の配置",
    )
    time_slot = models.ForeignKey(
        TimeSlot, on_delete=models.CASCADE, related_name="derived_rules",
        verbose_name="適用時間",
    )
    action = models.CharField("動作", max_length=20, choices=ACTION_CHOICES)
    target_area = models.ForeignKey(
        AssignmentArea, on_delete=models.CASCADE, null=True, blank=True,
        related_name="derived_target_rules", verbose_name="移動・兼任先",
    )
    is_active = models.BooleanField("使用中", default=True)

    class Meta:
        ordering = ("time_slot__display_order", "id")
        verbose_name = "時間連動ルール"
        verbose_name_plural = "時間連動ルール"

    def __str__(self):
        return self.name or f"{self.source_area} {self.time_slot} {self.get_action_display()}"


class MeetingPreset(models.Model):
    """当日配置表の会議等クイック入力で使う登録済み内容。"""
    name = models.CharField("会議等の内容", max_length=80, unique=True)
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)

    class Meta:
        ordering = ("display_order", "name")
        verbose_name = "会議等プリセット"
        verbose_name_plural = "会議等プリセット"

    def __str__(self):
        return self.name


class ReceptionStaff(models.Model):
    """受付クイック入力専用。通常スタッフ・自動配置・Free候補には含めない。"""
    name = models.CharField("受付担当者名", max_length=80, unique=True)
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)

    class Meta:
        ordering = ("display_order", "name")
        verbose_name = "受付担当"
        verbose_name_plural = "受付担当"

    def __str__(self):
        return self.name


class MainRotationOverride(models.Model):
    """週単位の主担当ローテーション手動補正。専任固定枠には影響しない。"""
    week_start = models.DateField("対象週（月曜）", unique=True)
    offset = models.IntegerField("手動ローテーション補正", default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-week_start",)
        verbose_name = "主担当ローテーション手動補正"
        verbose_name_plural = "主担当ローテーション手動補正"

    def __str__(self):
        return f"{self.week_start} offset={self.offset}"


class DailyCommentTemplate(models.Model):
    INPUT_TYPE_CHOICES = (("none", "固定表示"), ("number", "数字"), ("text", "文字"))
    FONT_SIZE_CHOICES = ((12, "小 12px"), (14, "標準 14px"), (16, "大 16px"), (18, "大きめ 18px"), (20, "特大 20px"), (24, "最大 24px"))
    label = models.CharField("表示文", max_length=120)
    input_type = models.CharField("入力タイプ", max_length=10, choices=INPUT_TYPE_CHOICES, default="number")
    unit = models.CharField("単位", max_length=30, blank=True)
    font_size = models.PositiveSmallIntegerField("文字サイズ", choices=FONT_SIZE_CHOICES, default=14)
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)
    class Meta:
        ordering = ("display_order", "id")
        verbose_name = "当日コメントテンプレート"
        verbose_name_plural = "当日コメントテンプレート"
    def __str__(self):
        return self.label


class DailyCommentTemplateValue(models.Model):
    board = models.ForeignKey(DailyBoard, on_delete=models.CASCADE, related_name="comment_template_values")
    template = models.ForeignKey(DailyCommentTemplate, on_delete=models.CASCADE, related_name="daily_values")
    value = models.CharField("当日の入力値", max_length=300, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("board", "template"), name="unique_daily_comment_template_value")]
        verbose_name = "当日コメントテンプレート入力値"
        verbose_name_plural = "当日コメントテンプレート入力値"
    def __str__(self):
        return f"{self.board.board_date} / {self.template.label}: {self.value}"


class LunchBreakEntry(models.Model):
    board = models.ForeignKey(
        DailyBoard, on_delete=models.CASCADE, related_name="lunch_break_entries"
    )
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT)
    value = models.TextField("昼休憩者", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("board", "time_slot"), name="unique_lunch_break_entry"
            )
        ]
        verbose_name = "昼休憩欄"
        verbose_name_plural = "昼休憩欄"

    def __str__(self):
        return f"{self.board.board_date} {self.time_slot}: {self.value}"


class WeeklyAbsence(models.Model):
    """週間配置作成で使用する年休・出張・会議などの不在予定。"""
    KIND_CHOICES = (
        ("annual", "年休"), ("reception", "受付"), ("trip", "出張"), ("meeting", "会議"), ("other", "その他"),
    )
    PERIOD_CHOICES = (
        ("full", "終日"), ("am", "午前"), ("pm", "午後"), ("custom", "時間指定"),
    )
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="weekly_absences")
    absence_date = models.DateField("不在日")
    kind = models.CharField("種類", max_length=20, choices=KIND_CHOICES, default="annual")
    period = models.CharField("時間", max_length=20, choices=PERIOD_CHOICES, default="full")
    start_slot = models.ForeignKey(TimeSlot, on_delete=models.SET_NULL, null=True, blank=True, related_name="absence_starts", verbose_name="開始時間")
    end_slot = models.ForeignKey(TimeSlot, on_delete=models.SET_NULL, null=True, blank=True, related_name="absence_ends", verbose_name="終了時間")
    note = models.CharField("メモ", max_length=120, blank=True)

    class Meta:
        ordering = ("absence_date", "staff__display_order", "staff_id")
        constraints = [models.UniqueConstraint(fields=("staff", "absence_date"), name="unique_weekly_absence_staff_date")]
        verbose_name = "週間不在予定"
        verbose_name_plural = "週間不在予定"

    @property
    def period_label(self):
        """週間表に表示する時間。時間指定では実際の時刻を表示する。"""
        if self.period == "custom":
            start = self.start_slot.label if self.start_slot else "?"
            end = self.end_slot.label if self.end_slot else start
            return start if start == end else f"{start}～{end}"
        return self.get_period_display()

    def __str__(self):
        return f"{self.absence_date} {self.staff.name} {self.get_kind_display()}"


class DedicatedAreaBackup(models.Model):
    """専任者が不在の際に優先して配置する代理者。"""
    area = models.ForeignKey(AssignmentArea, on_delete=models.CASCADE, related_name="dedicated_backups")
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="dedicated_backup_entries")
    priority = models.PositiveSmallIntegerField("代理順", default=1)

    class Meta:
        ordering = ("area__display_order", "priority", "staff__display_order")
        constraints = [
            models.UniqueConstraint(fields=("area", "staff"), name="unique_dedicated_area_backup"),
            models.UniqueConstraint(fields=("area", "priority"), name="unique_dedicated_area_backup_priority"),
        ]
        verbose_name = "専任代理"
        verbose_name_plural = "専任代理"

    def __str__(self):
        return f"{self.area} 代理{self.priority}: {self.staff.name}"
