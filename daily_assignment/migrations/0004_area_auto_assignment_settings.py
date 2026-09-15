from django.db import migrations, models
import django.db.models.deletion


def copy_slot_count_to_auto_count(apps, schema_editor):
    AssignmentArea = apps.get_model("daily_assignment", "AssignmentArea")
    for area in AssignmentArea.objects.all():
        area.auto_assignment_count = area.slot_count
        area.save(update_fields=["auto_assignment_count"])


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0003_staff_auto_assignment_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignmentarea",
            name="auto_assignment_count",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="入力列数とは別に、自動配置で埋める人数を指定します。",
                verbose_name="自動配置人数",
            ),
        ),
        migrations.RunPython(copy_slot_count_to_auto_count, migrations.RunPython.noop),
        migrations.AddField(
            model_name="assignmentarea",
            name="auto_assignment_mode",
            field=models.CharField(
                choices=[
                    ("daily", "毎日"),
                    ("scheduled", "予定時のみ"),
                    ("manual", "手動のみ"),
                ],
                default="daily",
                max_length=20,
                verbose_name="自動配置方式",
            ),
        ),
        migrations.CreateModel(
            name="DailyAreaActivation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_enabled", models.BooleanField(default=False, verbose_name="本日の予定あり")),
                ("area", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="daily_assignment.assignmentarea")),
                ("board", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="area_activations", to="daily_assignment.dailyboard")),
            ],
            options={
                "verbose_name": "当日配置場所予定",
                "verbose_name_plural": "当日配置場所予定",
            },
        ),
        migrations.AddConstraint(
            model_name="dailyareaactivation",
            constraint=models.UniqueConstraint(fields=("board", "area"), name="unique_daily_area_activation"),
        ),
    ]
