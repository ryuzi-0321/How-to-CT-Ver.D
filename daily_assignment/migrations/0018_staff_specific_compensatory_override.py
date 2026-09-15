from django.db import migrations, models


def copy_legacy_force(apps, schema_editor):
    DutyCalendarDay = apps.get_model('daily_assignment', 'DutyCalendarDay')
    DutyCalendarDay.objects.filter(force_compensatory_leave=True).update(
        force_holiday_day_compensatory_leave=True,
        force_night_compensatory_leave=True,
        force_night_after_compensatory_leave=True,
    )


class Migration(migrations.Migration):
    dependencies = [('daily_assignment', '0017_dutycalendarday_force_compensatory_leave')]
    operations = [
        migrations.AddField(
            model_name='dutycalendarday',
            name='force_holiday_day_compensatory_leave',
            field=models.BooleanField(default=False, help_text='祝日の代休ルールがOFFでも、この日の休日日勤担当者だけ代休を作成します。', verbose_name='休日日勤担当だけ代休を追加'),
        ),
        migrations.AddField(
            model_name='dutycalendarday',
            name='force_night_compensatory_leave',
            field=models.BooleanField(default=False, help_text='祝日の代休ルールがOFFでも、この日の夜勤担当者だけ代休を作成します。', verbose_name='夜勤担当だけ代休を追加'),
        ),
        migrations.AddField(
            model_name='dutycalendarday',
            name='force_night_after_compensatory_leave',
            field=models.BooleanField(default=False, help_text='祝日の代休ルールがOFFでも、この日の夜勤に続く明け分だけ代休を作成します。', verbose_name='明け担当だけ代休を追加'),
        ),
        migrations.RunPython(copy_legacy_force, migrations.RunPython.noop),
    ]
