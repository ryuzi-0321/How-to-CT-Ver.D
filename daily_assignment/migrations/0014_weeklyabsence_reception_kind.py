from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("daily_assignment", "0013_comp_after_cell_auto_meeting_preset"),
    ]

    operations = [
        migrations.AlterField(
            model_name="weeklyabsence",
            name="kind",
            field=models.CharField(
                choices=[
                    ("annual", "年休"),
                    ("reception", "受付"),
                    ("trip", "出張"),
                    ("meeting", "会議"),
                    ("other", "その他"),
                ],
                default="annual",
                max_length=20,
                verbose_name="種類",
            ),
        ),
    ]
