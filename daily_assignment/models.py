from django.db import models


class Staff(models.Model):
    name = models.CharField("氏名", max_length=50, unique=True)
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)

    class Meta:
        ordering = ("display_order", "name")
        verbose_name = "スタッフ"
        verbose_name_plural = "スタッフ"

    def __str__(self):
        return self.name


class AssignmentArea(models.Model):
    category = models.CharField("部門", max_length=40, blank=True)
    name = models.CharField("配置場所", max_length=60)
    slot_count = models.PositiveSmallIntegerField("入力列数", default=1)
    display_order = models.PositiveIntegerField("表示順", default=0)
    is_active = models.BooleanField("使用中", default=True)

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
    two_shift = models.TextField("2交代勤務", blank=True)
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
