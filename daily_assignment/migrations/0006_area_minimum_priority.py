from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0005_time_layout_and_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignmentarea",
            name="minimum_assignment_count",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="人員不足時でもできるだけ確保したい人数です。0なら最低人数の指定なし。",
                verbose_name="最低必要人数",
            ),
        ),
        migrations.AddField(
            model_name="assignmentarea",
            name="assignment_priority",
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text="1が最優先、5が低優先です。人員不足時の配置順に使います。",
                verbose_name="配置優先度",
            ),
        ),
    ]
