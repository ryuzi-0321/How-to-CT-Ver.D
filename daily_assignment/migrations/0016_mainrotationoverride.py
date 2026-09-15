from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("daily_assignment", "0015_receptionstaff"),
    ]

    operations = [
        migrations.CreateModel(
            name="MainRotationOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_start", models.DateField(unique=True, verbose_name="対象週（月曜）")),
                ("offset", models.IntegerField(default=0, verbose_name="手動ローテーション補正")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "主担当ローテーション手動補正",
                "verbose_name_plural": "主担当ローテーション手動補正",
                "ordering": ("-week_start",),
            },
        ),
    ]
