from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="AssignmentArea",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(blank=True, max_length=40, verbose_name="部門")),
                ("name", models.CharField(max_length=60, verbose_name="配置場所")),
                ("slot_count", models.PositiveSmallIntegerField(default=1, verbose_name="入力列数")),
                ("display_order", models.PositiveIntegerField(default=0, verbose_name="表示順")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
            ],
            options={"verbose_name": "配置場所", "verbose_name_plural": "配置場所", "ordering": ("display_order", "id")},
        ),
        migrations.CreateModel(
            name="DailyBoard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("board_date", models.DateField(unique=True, verbose_name="日付")),
                ("conference", models.TextField(blank=True, verbose_name="会議等")),
                ("two_shift", models.TextField(blank=True, verbose_name="2交代勤務")),
                ("annual_leave_1", models.TextField(blank=True, verbose_name="年休①")),
                ("annual_leave_2", models.TextField(blank=True, verbose_name="年休②")),
                ("staffing_am", models.CharField(blank=True, max_length=50, verbose_name="人数状況・午前")),
                ("staffing_pm", models.CharField(blank=True, max_length=50, verbose_name="人数状況・午後")),
                ("free_text", models.TextField(blank=True, verbose_name="Free")),
                ("comment", models.TextField(blank=True, verbose_name="コメント")),
                ("unassigned", models.TextField(blank=True, verbose_name="配置未定")),
                ("reception_leave", models.TextField(blank=True, verbose_name="受付（年休）")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新日時")),
            ],
            options={"verbose_name": "当日配置表", "verbose_name_plural": "当日配置表", "ordering": ("-board_date",)},
        ),
        migrations.CreateModel(
            name="Staff",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=50, unique=True, verbose_name="氏名")),
                ("display_order", models.PositiveIntegerField(default=0, verbose_name="表示順")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
            ],
            options={"verbose_name": "スタッフ", "verbose_name_plural": "スタッフ", "ordering": ("display_order", "name")},
        ),
        migrations.CreateModel(
            name="AssignmentCell",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("row_key", models.CharField(max_length=20, verbose_name="時間帯")),
                ("slot_index", models.PositiveSmallIntegerField(default=1, verbose_name="列番号")),
                ("value", models.CharField(blank=True, max_length=100, verbose_name="担当者")),
                ("area", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="daily_assignment.assignmentarea")),
                ("board", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cells", to="daily_assignment.dailyboard")),
            ],
            options={"verbose_name": "配置セル", "verbose_name_plural": "配置セル"},
        ),
        migrations.AddConstraint(
            model_name="assignmentarea",
            constraint=models.UniqueConstraint(fields=("category", "name"), name="unique_assignment_area"),
        ),
        migrations.AddConstraint(
            model_name="assignmentcell",
            constraint=models.UniqueConstraint(fields=("board", "area", "row_key", "slot_index"), name="unique_assignment_cell"),
        ),
    ]
