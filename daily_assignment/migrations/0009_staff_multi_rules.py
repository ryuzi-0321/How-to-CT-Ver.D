from django.db import migrations, models
import django.db.models.deletion


def migrate_old_rules(apps, schema_editor):
    Staff = apps.get_model('daily_assignment', 'Staff')
    StaffWeeklyTarget = apps.get_model('daily_assignment', 'StaffWeeklyTarget')
    StaffAvoidArea = apps.get_model('daily_assignment', 'StaffAvoidArea')
    for staff in Staff.objects.all():
        if staff.avoid_area_id:
            StaffAvoidArea.objects.get_or_create(staff_id=staff.id, area_id=staff.avoid_area_id)
        if staff.weekly_target_area_id and staff.weekly_target_count:
            StaffWeeklyTarget.objects.get_or_create(
                staff_id=staff.id, area_id=staff.weekly_target_area_id,
                defaults={'target_count': staff.weekly_target_count},
            )

class Migration(migrations.Migration):
    dependencies = [('daily_assignment', '0008_duty_calendar_undo_manual_comp')]
    operations = [
        migrations.AddField(
            model_name='staff', name='preferred_area_4',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='preferred_staff_4', to='daily_assignment.assignmentarea', verbose_name='優先配置④'),
        ),
        migrations.CreateModel(
            name='StaffWeeklyTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_count', models.PositiveSmallIntegerField(default=1, verbose_name='週の目安回数')),
                ('area', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='weekly_target_entries', to='daily_assignment.assignmentarea')),
                ('staff', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='weekly_targets', to='daily_assignment.staff')),
            ],
            options={'ordering': ('staff_id','area__display_order','area_id')},
        ),
        migrations.CreateModel(
            name='StaffAvoidArea',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('area', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='avoided_staff_entries', to='daily_assignment.assignmentarea')),
                ('staff', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='avoid_area_entries', to='daily_assignment.staff')),
            ],
            options={'ordering': ('staff_id','area__display_order','area_id')},
        ),
        migrations.AddConstraint(model_name='staffweeklytarget', constraint=models.UniqueConstraint(fields=('staff','area'), name='unique_staff_weekly_target')),
        migrations.AddConstraint(model_name='staffavoidarea', constraint=models.UniqueConstraint(fields=('staff','area'), name='unique_staff_avoid_area')),
        migrations.RunPython(migrate_old_rules, migrations.RunPython.noop),
    ]
