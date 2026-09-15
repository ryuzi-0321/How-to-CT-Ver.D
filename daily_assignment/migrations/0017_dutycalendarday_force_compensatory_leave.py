from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0016_mainrotationoverride"),
    ]

    operations = [
        migrations.AddField(
            model_name="dutycalendarday",
            name="force_compensatory_leave",
            field=models.BooleanField(
                default=False,
                help_text="祝日の代休ルールを通常OFFにしていても、この日だけ代休を作成します。",
                verbose_name="この日だけ代休を発生",
            ),
        ),
    ]
