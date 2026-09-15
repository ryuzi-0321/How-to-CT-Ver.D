from django.db import migrations, models
import django.db.models.deletion


def create_default_rules(apps, schema_editor):
    Rule = apps.get_model("daily_assignment", "CompensatoryLeaveRule")
    defaults = [
        ("saturday", "holiday_day", "weekday", 1, 0, 1),
        ("saturday", "night", "weekday", 1, 2, 1),
        ("sunday", "holiday_day", "weekday", 1, 1, 1),
        ("sunday", "night", "weekday", 1, 3, 1),
        ("holiday", "holiday_day", "days", 1, 0, 2),
        ("holiday", "night", "days", 1, 0, 3),
    ]
    for day_type, duty_type, mode, weeks, weekday, days in defaults:
        Rule.objects.get_or_create(
            day_type=day_type,
            duty_type=duty_type,
            defaults={
                "offset_mode": mode,
                "weeks_after": weeks,
                "target_weekday": weekday,
                "days_after": days,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="duty_rotation_enabled",
            field=models.BooleanField(default=False, verbose_name="勤務ローテーション対象"),
        ),
        migrations.AddField(
            model_name="staff",
            name="duty_rotation_order",
            field=models.PositiveIntegerField(default=0, verbose_name="勤務ローテーション順"),
        ),
        migrations.AddField(model_name="dailyboard", name="night_shift", field=models.CharField(blank=True, max_length=100, verbose_name="夜勤")),
        migrations.AddField(model_name="dailyboard", name="night_shift_after", field=models.CharField(blank=True, max_length=100, verbose_name="明け")),
        migrations.AddField(model_name="dailyboard", name="holiday_day_shift", field=models.CharField(blank=True, max_length=100, verbose_name="休日日勤")),
        migrations.AddField(model_name="dailyboard", name="compensatory_leave_1", field=models.CharField(blank=True, max_length=100, verbose_name="代休①")),
        migrations.AddField(model_name="dailyboard", name="compensatory_leave_2", field=models.CharField(blank=True, max_length=100, verbose_name="代休②")),
        migrations.AddField(model_name="dailyboard", name="compensatory_leave_3", field=models.CharField(blank=True, max_length=100, verbose_name="代休③")),
        migrations.AlterField(model_name="dailyboard", name="two_shift", field=models.TextField(blank=True, verbose_name="2交代勤務（旧欄）")),
        migrations.CreateModel(
            name="CompensatoryLeaveRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day_type", models.CharField(choices=[("saturday", "土曜日"), ("sunday", "日曜日"), ("holiday", "祝日")], max_length=20, verbose_name="勤務日の種類")),
                ("duty_type", models.CharField(choices=[("holiday_day", "休日日勤"), ("night", "夜勤")], max_length=20, verbose_name="勤務種類")),
                ("offset_mode", models.CharField(choices=[("weekday", "指定週の曜日"), ("days", "勤務日の何日後")], default="weekday", max_length=20, verbose_name="代休日の決め方")),
                ("weeks_after", models.PositiveSmallIntegerField(default=1, verbose_name="何週後")),
                ("target_weekday", models.PositiveSmallIntegerField(choices=[(0, "月曜日"), (1, "火曜日"), (2, "水曜日"), (3, "木曜日"), (4, "金曜日"), (5, "土曜日"), (6, "日曜日")], default=0, verbose_name="代休曜日")),
                ("days_after", models.PositiveSmallIntegerField(default=1, verbose_name="何日後")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
            ],
            options={"verbose_name": "代休ルール", "verbose_name_plural": "代休ルール", "ordering": ("day_type", "duty_type")},
        ),
        migrations.AddConstraint(
            model_name="compensatoryleaverule",
            constraint=models.UniqueConstraint(fields=("day_type", "duty_type"), name="unique_comp_leave_rule"),
        ),
        migrations.CreateModel(
            name="CompensatoryLeaveRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_date", models.DateField(verbose_name="勤務日")),
                ("target_date", models.DateField(verbose_name="代休日")),
                ("duty_type", models.CharField(choices=[("holiday_day", "休日日勤"), ("night", "夜勤")], max_length=20, verbose_name="勤務種類")),
                ("staff_name", models.CharField(max_length=100, verbose_name="スタッフ")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="作成日時")),
            ],
            options={"verbose_name": "代休予約", "verbose_name_plural": "代休予約", "ordering": ("target_date", "source_date")},
        ),
        migrations.AddConstraint(
            model_name="compensatoryleaverecord",
            constraint=models.UniqueConstraint(fields=("source_date", "duty_type", "staff_name"), name="unique_comp_leave_record"),
        ),
        migrations.RunPython(create_default_rules, migrations.RunPython.noop),
    ]
