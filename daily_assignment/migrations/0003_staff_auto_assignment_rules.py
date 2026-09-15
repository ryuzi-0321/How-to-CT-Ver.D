from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0002_duty_rotation_and_comp_leave"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="auto_assignment_enabled",
            field=models.BooleanField(default=False, verbose_name="通常配置の自動作成対象"),
        ),
        migrations.AddField(
            model_name="staff",
            name="preferred_area_1",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="preferred_staff_1", to="daily_assignment.assignmentarea", verbose_name="優先配置①"),
        ),
        migrations.AddField(
            model_name="staff",
            name="preferred_area_2",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="preferred_staff_2", to="daily_assignment.assignmentarea", verbose_name="優先配置②"),
        ),
        migrations.AddField(
            model_name="staff",
            name="preferred_area_3",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="preferred_staff_3", to="daily_assignment.assignmentarea", verbose_name="優先配置③"),
        ),
        migrations.AddField(
            model_name="staff",
            name="avoid_area",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="avoided_by_staff", to="daily_assignment.assignmentarea", verbose_name="配置しない場所"),
        ),
        migrations.AddField(
            model_name="staff",
            name="weekly_target_area",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="weekly_target_staff", to="daily_assignment.assignmentarea", verbose_name="週回数を指定する場所"),
        ),
        migrations.AddField(
            model_name="staff",
            name="weekly_target_count",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="週の目安回数"),
        ),
    ]
