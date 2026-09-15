from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("daily_assignment", "0007_duty_calendar_day"),
    ]

    operations = [
        migrations.AddField(
            model_name="compensatoryleaverecord",
            name="is_manual_override",
            field=models.BooleanField(default=False, verbose_name="代休日を手動変更"),
        ),
        migrations.CreateModel(
            name="DutyCalendarDayHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("duty_date", models.DateField(unique=True, verbose_name="日付")),
                ("snapshot", models.JSONField(blank=True, default=dict, verbose_name="直前の状態")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新日時")),
            ],
            options={
                "verbose_name": "勤務カレンダー変更履歴",
                "verbose_name_plural": "勤務カレンダー変更履歴",
                "ordering": ("duty_date",),
            },
        ),
    ]
