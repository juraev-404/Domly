import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .locations import localize_city_name
from .models import City, Listing, ListingImage, ListingReport


INPUT_CLASS = (
    "w-full rounded-xl border border-gray-300 bg-white px-4 py-3 text-gray-900 "
    "outline-none transition placeholder:text-gray-400 focus:border-green-500 "
    "focus:ring-2 focus:ring-green-100"
)
SELECT_CLASS = f"{INPUT_CLASS} cursor-pointer"
CHECKBOX_CLASS = (
    "h-5 w-5 rounded border-gray-300 text-green-600 focus:ring-green-500"
)


class ModerationRejectForm(forms.Form):
    reason = forms.CharField(
        label=_("Причина отклонения"),
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": INPUT_CLASS,
                "rows": 4,
                "placeholder": _(
                    "Объясните автору, что нужно исправить перед повторной отправкой"
                ),
            }
        ),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise ValidationError(_("Укажите причину отклонения."))
        return reason


class ListingReportForm(forms.ModelForm):
    class Meta:
        model = ListingReport
        fields = ("reason", "details")
        labels = {
            "reason": _("Причина"),
            "details": _("Подробности"),
        }
        widgets = {
            "reason": forms.Select(attrs={"class": SELECT_CLASS}),
            "details": forms.Textarea(
                attrs={
                    "class": INPUT_CLASS,
                    "rows": 4,
                    "placeholder": _("Коротко опишите, что не так с объявлением"),
                }
            ),
        }

    def clean_details(self):
        details = self.cleaned_data.get("details", "").strip()
        if self.cleaned_data.get("reason") == ListingReport.Reason.OTHER and len(details) < 10:
            raise ValidationError(_("Для причины «Другое» добавьте подробности."))
        return details


class ListingReportReviewForm(forms.Form):
    resolution_note = forms.CharField(
        label=_("Комментарий модератора"),
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": INPUT_CLASS,
                "rows": 3,
                "placeholder": _("Кратко объясните принятое решение"),
            }
        ),
    )

    def clean_resolution_note(self):
        note = self.cleaned_data["resolution_note"].strip()
        if len(note) < 5:
            raise ValidationError(_("Добавьте короткий комментарий к решению."))
        return note


class ModerationBlockForm(forms.Form):
    class Duration:
        DAY = "1"
        WEEK = "7"
        MONTH = "30"
        PERMANENT = "permanent"
        choices = (
            (DAY, _("1 день")),
            (WEEK, _("7 дней")),
            (MONTH, _("30 дней")),
            (PERMANENT, _("Без срока")),
        )

    reason = forms.CharField(
        label=_("Причина блокировки"),
        max_length=1000,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
    )
    duration = forms.ChoiceField(
        label=_("Срок"),
        choices=Duration.choices,
        initial=Duration.WEEK,
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if len(reason) < 5:
            raise ValidationError(_("Укажите понятную причину блокировки."))
        return reason


class ModerationUnblockForm(forms.Form):
    note = forms.CharField(
        label=_("Причина разблокировки"),
        max_length=1000,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
    )

    def clean_note(self):
        note = self.cleaned_data["note"].strip()
        if len(note) < 5:
            raise ValidationError(_("Укажите причину разблокировки."))
        return note


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        images = [super(MultipleImageField, self).clean(file, initial) for file in files]

        if len(images) > 10:
            raise ValidationError(_("Можно загрузить не более 10 фотографий."))
        oversized = [image.name for image in images if image.size > 10 * 1024 * 1024]
        if oversized:
            raise ValidationError(_("Размер каждой фотографии не должен превышать 10 МБ."))
        allowed_types = {"image/jpeg", "image/png", "image/webp"}
        for image in images:
            if image.content_type not in allowed_types:
                raise ValidationError(_("Поддерживаются JPEG, PNG и WebP."))
            if image.image.width * image.image.height > 40_000_000:
                raise ValidationError(_("Разрешение фотографии слишком большое."))
        return images


class ListingCreateForm(forms.ModelForm):
    contact_phone = forms.CharField(
        label=_("Номер для связи"),
        required=False,
        help_text=_("Необязательно. Этот номер будет виден на странице объявления."),
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "autocomplete": "tel",
                "inputmode": "tel",
                "placeholder": "+992900001122",
            }
        ),
    )
    images = MultipleImageField(
        label=_("Фотографии"),
        required=False,
        help_text=_("До 10 фотографий, каждая не более 10 МБ."),
        widget=MultipleImageInput(
            attrs={
                "class": (
                    "block w-full cursor-pointer rounded-xl border border-dashed "
                    "border-gray-300 bg-gray-50 px-4 py-6 text-sm text-gray-600 "
                    "file:mr-4 file:rounded-lg file:border-0 file:bg-black "
                    "file:px-4 file:py-2 file:text-white hover:border-gray-500"
                ),
                "accept": "image/jpeg,image/png,image/webp",
            }
        ),
    )

    remove_images = forms.ModelMultipleChoiceField(queryset=ListingImage.objects.none(), required=False)

    class Meta:
        model = Listing
        fields = (
            "deal_type",
            "property_type",
            "city",
            "title",
            "description",
            "price",
            "currency",
            "is_negotiable",
            "contact_phone",
            "address",
            "latitude",
            "longitude",
            "rooms",
            "area",
            "floor",
            "total_floors",
        )
        labels = {
            "deal_type": _("Тип сделки"),
            "property_type": _("Тип недвижимости"),
            "city": _("Город"),
            "title": _("Название"),
            "description": _("Описание"),
            "price": _("Цена"),
            "currency": _("Валюта"),
            "is_negotiable": _("Торг уместен"),
            "address": _("Адрес"),
            "latitude": _("Широта"),
            "longitude": _("Долгота"),
            "rooms": _("Комнаты"),
            "area": _("Площадь, м²"),
            "floor": _("Этаж"),
            "total_floors": _("Этажей в доме"),
        }
        widgets = {
            "deal_type": forms.Select(attrs={"class": SELECT_CLASS}),
            "property_type": forms.Select(attrs={"class": SELECT_CLASS}),
            "city": forms.Select(attrs={"class": SELECT_CLASS}),
            "title": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": _("Например, 2-комнатная квартира в центре"),
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CLASS,
                    "rows": 6,
                    "placeholder": _("Опишите состояние, ремонт и важные особенности"),
                }
            ),
            "price": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": "0.01", "step": "0.01"}
            ),
            "currency": forms.Select(attrs={"class": SELECT_CLASS}),
            "is_negotiable": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}),
            "address": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": _("Улица, дом, район")}
            ),
            "latitude": forms.HiddenInput(),
            "longitude": forms.HiddenInput(),
            "rooms": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": "1", "inputmode": "numeric"}
            ),
            "area": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": "0.01", "step": "0.01"}
            ),
            "floor": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": "0", "inputmode": "numeric"}
            ),
            "total_floors": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": "1", "inputmode": "numeric"}
            ),
        }

    def __init__(self, *args, submit_action="draft", **kwargs):
        super().__init__(*args, **kwargs)
        self.submit_action = submit_action
        self.fields["city"].queryset = City.objects.filter(is_active=True).order_by("name")
        self.fields["city"].empty_label = _("Выберите город")
        self.fields["city"].label_from_instance = (
            lambda city: localize_city_name(city.name)
        )
        if self.instance.pk:
            self.fields["remove_images"].queryset = self.instance.images.all()

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if len(title) < 10:
            raise ValidationError(_("Название должно содержать не менее 10 символов."))
        return title

    def clean_description(self):
        description = self.cleaned_data["description"].strip()
        if len(description) < 30:
            raise ValidationError(_("Добавьте более подробное описание — не менее 30 символов."))
        return description

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price <= 0:
            raise ValidationError(_("Цена должна быть больше нуля."))
        return price

    def clean_contact_phone(self):
        value = self.cleaned_data.get("contact_phone", "").strip()
        if not value:
            return ""

        digits = re.sub(r"\D", "", value)
        if value.startswith("00"):
            digits = digits[2:]
        elif not value.startswith("+") and len(digits) == 9:
            digits = f"992{digits}"

        normalized = f"+{digits}"
        if not re.fullmatch(r"\+[1-9]\d{7,14}", normalized):
            raise ValidationError(
                _("Введите корректный номер, например +992900001122.")
            )
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        floor = cleaned_data.get("floor")
        total_floors = cleaned_data.get("total_floors")
        if floor is not None and total_floors is not None and floor > total_floors:
            self.add_error("floor", _("Этаж не может быть выше общего числа этажей."))
        uploaded_images = cleaned_data.get("images") or []
        removed_images = cleaned_data.get("remove_images")
        removed_count = removed_images.count() if removed_images is not None else 0
        existing_count = self.instance.images.count() if self.instance.pk else 0
        final_image_count = existing_count - removed_count + len(uploaded_images)
        if final_image_count > 10:
            self.add_error("images", _("В объявлении может быть не более 10 фотографий."))
        if self.submit_action == "publish" and final_image_count == 0:
            self.add_error("images", _("Для отправки на модерацию добавьте хотя бы одну фотографию."))

        latitude = cleaned_data.get("latitude")
        longitude = cleaned_data.get("longitude")
        if (latitude is None) != (longitude is None):
            self.add_error(
                "latitude",
                _("Точка на карте должна содержать широту и долготу."),
            )
        elif self.submit_action == "publish" and latitude is None:
            self.add_error(
                "latitude",
                _("Для отправки на модерацию укажите точное место объекта на карте."),
            )

        return cleaned_data
