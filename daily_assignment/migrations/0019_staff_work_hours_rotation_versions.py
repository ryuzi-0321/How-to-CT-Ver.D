from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0018_staff_specific_compensatory_override")]

    operations = [
        migrations.AddField(model_name="staff", name="work_start_time", field=models.TimeField(blank=True, help_text="空欄なら開始時刻の制限なし", null=True, verbose_name="勤務開始時刻")),
        migrations.AddField(model_name="staff", name="work_end_time", field=models.TimeField(blank=True, help_text="空欄なら終了時刻の制限なし", null=True, verbose_name="勤務終了時刻")),
        migrations.CreateModel(
            name="DutyRotationVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("effective_month", models.DateField(help_text="必ず月初日を保存", unique=True, verbose_name="適用開始月")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name":"勤務ローテーション版","verbose_name_plural":"勤務ローテーション版","ordering":("-effective_month",)},
        ),
        migrations.CreateModel(
            name="DutyRotationVersionMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rotation_order", models.PositiveIntegerField(default=0, verbose_name="勤務順")),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="duty_rotation_versions", to="daily_assignment.staff")),
                ("version", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="members", to="daily_assignment.dutyrotationversion")),
            ],
            options={"verbose_name":"勤務ローテーション版メンバー","verbose_name_plural":"勤務ローテーション版メンバー","ordering":("rotation_order","staff__display_order","staff__name")},
        ),
        migrations.AddConstraint(model_name="dutyrotationversionmember", constraint=models.UniqueConstraint(fields=("version","staff"), name="unique_duty_rotation_version_staff")),
    ]
