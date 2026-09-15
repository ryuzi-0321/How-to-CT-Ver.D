from django.db import migrations, models
import django.db.models.deletion


def seed_time_slots(apps, schema_editor):
    TimeSlot = apps.get_model("daily_assignment", "TimeSlot")
    defaults = [
        ("0730", "7:30", 10, "time", False),
        ("0830", "8:30", 20, "time", False),
        ("0900", "9:00", 30, "time", False),
        ("1000", "10:00", 40, "time", False),
        ("1100", "11:00", 50, "time", False),
        ("1200", "12:00", 60, "time", True),
        ("1300", "13:00", 70, "time", False),
        ("1400", "14:00", 80, "time", False),
        ("1500", "15:00", 90, "time", False),
        ("1600", "16:00", 100, "time", False),
        ("main", "主担当", 110, "main", False),
        ("sub", "Sub", 120, "sub", False),
    ]
    for key, label, order, kind, lunch in defaults:
        TimeSlot.objects.get_or_create(
            key=key,
            defaults={
                "label": label, "display_order": order, "kind": kind,
                "is_active": True, "is_lunch_highlight": lunch,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0004_area_auto_assignment_settings")]

    operations = [
        migrations.CreateModel(
            name="TimeSlot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=20, unique=True, verbose_name="内部キー")),
                ("label", models.CharField(max_length=30, verbose_name="表示名")),
                ("display_order", models.PositiveIntegerField(default=0, verbose_name="表示順")),
                ("kind", models.CharField(choices=[("time", "時間枠"), ("main", "主担当"), ("sub", "Sub")], default="time", max_length=10, verbose_name="種類")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
                ("is_lunch_highlight", models.BooleanField(default=False, verbose_name="昼休憩色")),
            ],
            options={"verbose_name": "時間枠", "verbose_name_plural": "時間枠", "ordering": ("display_order", "id")},
        ),
        migrations.CreateModel(
            name="AreaTimeRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("normal", "通常"), ("hidden", "枠なし"), ("merge_start", "連結開始"), ("merge_continue", "連結中")], default="normal", max_length=20, verbose_name="表示方法")),
                ("area", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="time_rules", to="daily_assignment.assignmentarea")),
                ("time_slot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="area_rules", to="daily_assignment.timeslot")),
            ],
            options={"verbose_name": "配置場所時間ルール", "verbose_name_plural": "配置場所時間ルール"},
        ),
        migrations.CreateModel(
            name="DerivedAssignmentRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, max_length=80, verbose_name="ルール名")),
                ("action", models.CharField(choices=[("add", "兼任（元配置を残す）"), ("move", "移動（元配置から外す）"), ("break", "昼休憩")], max_length=20, verbose_name="動作")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
                ("source_area", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="derived_source_rules", to="daily_assignment.assignmentarea", verbose_name="元の配置")),
                ("target_area", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="derived_target_rules", to="daily_assignment.assignmentarea", verbose_name="移動・兼任先")),
                ("time_slot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="derived_rules", to="daily_assignment.timeslot", verbose_name="適用時間")),
            ],
            options={"verbose_name": "時間連動ルール", "verbose_name_plural": "時間連動ルール", "ordering": ("time_slot__display_order", "id")},
        ),
        migrations.CreateModel(
            name="LunchBreakEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.TextField(blank=True, verbose_name="昼休憩者")),
                ("board", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lunch_break_entries", to="daily_assignment.dailyboard")),
                ("time_slot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="daily_assignment.timeslot")),
            ],
            options={"verbose_name": "昼休憩欄", "verbose_name_plural": "昼休憩欄"},
        ),
        migrations.AddConstraint(
            model_name="areatimerule",
            constraint=models.UniqueConstraint(fields=("area", "time_slot"), name="unique_area_time_rule"),
        ),
        migrations.AddConstraint(
            model_name="lunchbreakentry",
            constraint=models.UniqueConstraint(fields=("board", "time_slot"), name="unique_lunch_break_entry"),
        ),
        migrations.RunPython(seed_time_slots, migrations.RunPython.noop),
    ]
