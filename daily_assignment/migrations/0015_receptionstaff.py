from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0014_weeklyabsence_reception_kind"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReceptionStaff",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True, verbose_name="受付担当者名")),
                ("display_order", models.PositiveIntegerField(default=0, verbose_name="表示順")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
            ],
            options={
                "verbose_name": "受付担当",
                "verbose_name_plural": "受付担当",
                "ordering": ("display_order", "name"),
            },
        ),
    ]
