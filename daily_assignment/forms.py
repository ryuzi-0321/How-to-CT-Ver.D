from django import forms
from .models import DailyBoard


class DailyBoardMetaForm(forms.ModelForm):
    class Meta:
        model = DailyBoard
        fields = (
            "conference",
            "night_shift", "night_shift_after", "holiday_day_shift",
            "compensatory_leave_1", "compensatory_leave_2", "compensatory_leave_3",
            "annual_leave_1", "annual_leave_2",
            "staffing_am", "staffing_pm", "free_text", "comment",
            "unassigned", "reception_leave",
        )
        widgets = {
            "conference": forms.Textarea(attrs={"rows": 3}),
            "night_shift": forms.TextInput(),
            "night_shift_after": forms.TextInput(),
            "holiday_day_shift": forms.TextInput(),
            "compensatory_leave_1": forms.TextInput(),
            "compensatory_leave_2": forms.TextInput(),
            "compensatory_leave_3": forms.TextInput(),
            "annual_leave_1": forms.Textarea(attrs={"rows": 3}),
            "annual_leave_2": forms.Textarea(attrs={"rows": 3}),
            "free_text": forms.Textarea(attrs={"rows": 3}),
            "comment": forms.Textarea(attrs={"rows": 5}),
            "unassigned": forms.Textarea(attrs={"rows": 3}),
            "reception_leave": forms.Textarea(attrs={"rows": 3}),
        }
