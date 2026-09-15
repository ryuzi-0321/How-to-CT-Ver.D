from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0006_area_minimum_priority"),
    ]

    operations = [
        migrations.CreateModel(
            name="DutyCalendarDay",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("duty_date", models.DateField(unique=True, verbose_name="日付")),
                ("day_mode", models.CharField(
                    choices=[
                        ("auto", "自動判定（土日祝）"),
                        ("normal", "通常日"),
                        ("holiday", "休日扱い"),
                        ("closed", "臨時休業"),
                    ],
                    default="auto",
                    max_length=20,
                    verbose_name="勤務区分",
                )),
                ("no_compensatory_leave", models.BooleanField(
                    default=False,
                    help_text="祝日・年末年始など、例外的に代休を発生させない日に使用します。",
                    verbose_name="代休を発生させない",
                )),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新日時")),
                ("holiday_day_shift", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="calendar_holiday_day_shifts",
                    to="daily_assignment.staff",
                    verbose_name="休日日勤",
                )),
                ("night_shift", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="calendar_night_shifts",
                    to="daily_assignment.staff",
                    verbose_name="夜勤",
                )),
            ],
            options={
                "verbose_name": "勤務カレンダー",
                "verbose_name_plural": "勤務カレンダー",
                "ordering": ("duty_date",),
            },
        ),
    ]
