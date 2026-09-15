from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0012_weekly_planner_main_rotation_backup")]

    operations = [
        migrations.AddField(
            model_name="areatimerule",
            name="auto_assignment_enabled",
            field=models.BooleanField(default=True, help_text="OFFにすると、この配置場所・時間のセルは通常自動配置で入力しません。", verbose_name="自動配置ON"),
        ),
        migrations.AlterField(
            model_name="compensatoryleaverule",
            name="duty_type",
            field=models.CharField(choices=[("holiday_day", "休日日勤"), ("night", "夜勤"), ("night_after", "明け")], max_length=20, verbose_name="勤務種類"),
        ),
        migrations.AlterField(
            model_name="compensatoryleaverecord",
            name="duty_type",
            field=models.CharField(choices=[("holiday_day", "休日日勤"), ("night", "夜勤"), ("night_after", "明け")], max_length=20, verbose_name="勤務種類"),
        ),
        migrations.CreateModel(
            name="MeetingPreset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True, verbose_name="会議等の内容")),
                ("display_order", models.PositiveIntegerField(default=0, verbose_name="表示順")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
            ],
            options={"ordering": ("display_order", "name"), "verbose_name": "会議等プリセット", "verbose_name_plural": "会議等プリセット"},
        ),
    ]
