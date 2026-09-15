from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0011_staff_assignment_priority_free_history")]
    operations = [
        migrations.AddField(model_name="staff", name="main_rotation_enabled", field=models.BooleanField(default=False, verbose_name="主担当ローテーション対象")),
        migrations.AddField(model_name="staff", name="main_rotation_order", field=models.PositiveIntegerField(default=0, verbose_name="主担当順")),
        migrations.AddField(model_name="assignmentarea", name="main_rotation_enabled", field=models.BooleanField(default=False, verbose_name="主担当ローテーション対象")),
        migrations.AddField(model_name="assignmentarea", name="main_rotation_order", field=models.PositiveIntegerField(default=0, verbose_name="主担当順")),
        migrations.CreateModel(name="WeeklyAbsence", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("absence_date", models.DateField(verbose_name="不在日")),
            ("kind", models.CharField(choices=[("annual","年休"),("trip","出張"),("meeting","会議"),("other","その他")], default="annual", max_length=20, verbose_name="種類")),
            ("period", models.CharField(choices=[("full","終日"),("am","午前"),("pm","午後"),("custom","時間指定")], default="full", max_length=20, verbose_name="時間")),
            ("note", models.CharField(blank=True, max_length=120, verbose_name="メモ")),
            ("end_slot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="absence_ends", to="daily_assignment.timeslot", verbose_name="終了時間")),
            ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="weekly_absences", to="daily_assignment.staff")),
            ("start_slot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="absence_starts", to="daily_assignment.timeslot", verbose_name="開始時間")),
        ], options={"ordering": ("absence_date","staff__display_order","staff_id"), "verbose_name":"週間不在予定", "verbose_name_plural":"週間不在予定"}),
        migrations.AddConstraint(model_name="weeklyabsence", constraint=models.UniqueConstraint(fields=("staff","absence_date"), name="unique_weekly_absence_staff_date")),
        migrations.CreateModel(name="DedicatedAreaBackup", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("priority", models.PositiveSmallIntegerField(default=1, verbose_name="代理順")),
            ("area", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dedicated_backups", to="daily_assignment.assignmentarea")),
            ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dedicated_backup_entries", to="daily_assignment.staff")),
        ], options={"ordering": ("area__display_order","priority","staff__display_order"), "verbose_name":"専任代理", "verbose_name_plural":"専任代理"}),
        migrations.AddConstraint(model_name="dedicatedareabackup", constraint=models.UniqueConstraint(fields=("area","staff"), name="unique_dedicated_area_backup")),
        migrations.AddConstraint(model_name="dedicatedareabackup", constraint=models.UniqueConstraint(fields=("area","priority"), name="unique_dedicated_area_backup_priority")),
    ]
