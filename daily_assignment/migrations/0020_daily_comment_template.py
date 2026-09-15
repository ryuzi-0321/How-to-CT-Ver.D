from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0019_staff_work_hours_rotation_versions")]
    operations = [
        migrations.CreateModel(
            name="DailyCommentTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=120, verbose_name="表示文")),
                ("input_type", models.CharField(choices=[("none", "固定表示"), ("number", "数字"), ("text", "文字")], default="none", max_length=10, verbose_name="入力タイプ")),
                ("unit", models.CharField(blank=True, max_length=30, verbose_name="単位")),
                ("font_size", models.PositiveSmallIntegerField(choices=[(12, "小 12px"), (14, "標準 14px"), (16, "大 16px"), (18, "大きめ 18px"), (20, "特大 20px"), (24, "最大 24px")], default=14, verbose_name="文字サイズ")),
                ("display_order", models.PositiveIntegerField(default=0, verbose_name="表示順")),
                ("is_active", models.BooleanField(default=True, verbose_name="使用中")),
            ],
            options={"verbose_name": "当日コメントテンプレート", "verbose_name_plural": "当日コメントテンプレート", "ordering": ("display_order", "id")},
        ),
        migrations.CreateModel(
            name="DailyCommentTemplateValue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.CharField(blank=True, max_length=300, verbose_name="当日の入力値")),
                ("board", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comment_template_values", to="daily_assignment.dailyboard")),
                ("template", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="daily_values", to="daily_assignment.dailycommenttemplate")),
            ],
            options={"verbose_name": "当日コメントテンプレート入力値", "verbose_name_plural": "当日コメントテンプレート入力値"},
        ),
        migrations.AddConstraint(model_name="dailycommenttemplatevalue", constraint=models.UniqueConstraint(fields=("board", "template"), name="unique_daily_comment_template_value")),
    ]
