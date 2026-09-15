from django.db import migrations, models
import django.db.models.deletion

def create_default_merge(apps, schema_editor):
    TimeSlot = apps.get_model("daily_assignment", "TimeSlot")
    Area = apps.get_model("daily_assignment", "AssignmentArea")
    Merge = apps.get_model("daily_assignment", "HorizontalMergeRule")
    slot = TimeSlot.objects.filter(key="1200").first()
    start = Area.objects.filter(name="2番").first()
    end = Area.objects.filter(name="画像入力室").first()
    rep = Area.objects.filter(name="3番").first()
    if slot and start and end and rep:
        Merge.objects.get_or_create(time_slot=slot, defaults={"name":"昼休憩", "start_area":start, "end_area":end, "representative_area":rep, "exclude_from_auto_assignment":True, "is_active":True})

class Migration(migrations.Migration):
    dependencies = [('daily_assignment', '0009_staff_multi_rules')]
    operations = [
        migrations.AddField(
            model_name='staff', name='dedicated_area',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dedicated_staff', to='daily_assignment.assignmentarea', verbose_name='専任配置'),
        ),
        migrations.CreateModel(
            name='HorizontalMergeRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='昼休憩', max_length=80, verbose_name='結合名')),
                ('exclude_from_auto_assignment', models.BooleanField(default=True, verbose_name='自動配置対象外')),
                ('is_active', models.BooleanField(default=True, verbose_name='使用中')),
                ('end_area', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='horizontal_merge_ends', to='daily_assignment.assignmentarea', verbose_name='終了配置')),
                ('representative_area', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='horizontal_merge_representatives', to='daily_assignment.assignmentarea', verbose_name='代表配置')),
                ('start_area', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='horizontal_merge_starts', to='daily_assignment.assignmentarea', verbose_name='開始配置')),
                ('time_slot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='horizontal_merge_rules', to='daily_assignment.timeslot', verbose_name='時間')),
            ],
            options={'verbose_name':'横結合ルール','verbose_name_plural':'横結合ルール','ordering':('time_slot__display_order','id')},
        ),
        migrations.RunPython(create_default_merge, migrations.RunPython.noop),
    ]
