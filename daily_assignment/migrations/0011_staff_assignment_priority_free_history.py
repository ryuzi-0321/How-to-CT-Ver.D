from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0010_horizontal_merge_dedicated")]
    operations = [
        migrations.AddField(
            model_name="staff",
            name="assignment_staff_priority",
            field=models.PositiveSmallIntegerField(choices=[(1, "最優先"), (2, "優先"), (3, "通常"), (4, "Free優先")], default=3, help_text="数字が小さいほど通常配置へ入りやすく、4は人員に余裕があるときFreeになりやすくなります。", verbose_name="スタッフ配置優先度"),
        ),
        migrations.CreateModel(
            name="StaffFreeHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("board_date", models.DateField(verbose_name="Free日")),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="free_history", to="daily_assignment.staff")),
            ],
            options={"verbose_name":"スタッフFree履歴", "verbose_name_plural":"スタッフFree履歴", "ordering":("-board_date","staff_id")},
        ),
        migrations.AddConstraint(
            model_name="stafffreehistory",
            constraint=models.UniqueConstraint(fields=("staff","board_date"), name="unique_staff_free_history"),
        ),
    ]
