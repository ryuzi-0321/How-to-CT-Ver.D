from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("daily_assignment", "0020_daily_comment_template")]
    operations = [
        migrations.AlterField(
            model_name="dailycommenttemplate",
            name="input_type",
            field=models.CharField(choices=[("none", "固定表示"), ("number", "数字"), ("text", "文字")], default="number", max_length=10, verbose_name="入力タイプ"),
        ),
    ]
