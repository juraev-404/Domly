from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import City, Listing, ListingImage


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
            raise ValidationError("Можно загрузить не более 10 фотографий.")
        oversized = [image.name for image in images if image.size > 10 * 1024 * 1024]
        if oversized:
            raise ValidationError("Размер каждой фотографии не должен превышать 10 МБ.")
        return images


class ListingCreateForm(forms.ModelForm):
    images = MultipleImageField(
        label="Фотографии",
        required=False,
        help_text="До 10 фотографий, каждая не более 10 МБ.",
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
            "address",
            "rooms",
            "area",
            "floor",
            "total_floors",
        )
        labels = {
            "deal_type": "Тип сделки",
            "property_type": "Тип недвижимости",
            "city": "Город",
            "title": "Название",
            "description": "Описание",
            "price": "Цена",
            "currency": "Валюта",
            "is_negotiable": "Торг уместен",
            "address": "Адрес",
            "rooms": "Комнаты",
            "area": "Площадь, м²",
            "floor": "Этаж",
            "total_floors": "Этажей в доме",
        }
        widgets = {
            "deal_type": forms.Select(attrs={"class": SELECT_CLASS}),
            "property_type": forms.Select(attrs={"class": SELECT_CLASS}),
            "city": forms.Select(attrs={"class": SELECT_CLASS}),
            "title": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Например, 2-комнатная квартира в центре",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CLASS,
                    "rows": 6,
                    "placeholder": "Опишите состояние, ремонт и важные особенности",
                }
            ),
            "price": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": "0.01", "step": "0.01"}
            ),
            "currency": forms.Select(attrs={"class": SELECT_CLASS}),
            "is_negotiable": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}),
            "address": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Улица, дом, район"}
            ),
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
        self.fields["city"].empty_label = "Выберите город"
        if self.instance.pk:
            self.fields["remove_images"].queryset = self.instance.images.all()

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if len(title) < 10:
            raise ValidationError("Название должно содержать не менее 10 символов.")
        return title

    def clean_description(self):
        description = self.cleaned_data["description"].strip()
        if len(description) < 30:
            raise ValidationError("Добавьте более подробное описание — не менее 30 символов.")
        return description

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price <= 0:
            raise ValidationError("Цена должна быть больше нуля.")
        return price

    def clean(self):
        cleaned_data = super().clean()
        floor = cleaned_data.get("floor")
        total_floors = cleaned_data.get("total_floors")
        if floor is not None and total_floors is not None and floor > total_floors:
            self.add_error("floor", "Этаж не может быть выше общего числа этажей.")
        uploaded_images = cleaned_data.get("images") or []
        removed_images = cleaned_data.get("remove_images")
        removed_count = removed_images.count() if removed_images is not None else 0
        existing_count = self.instance.images.count() if self.instance.pk else 0
        final_image_count = existing_count - removed_count + len(uploaded_images)
        if final_image_count > 10:
            self.add_error("images", "В объявлении может быть не более 10 фотографий.")
        if self.submit_action == "publish" and final_image_count == 0:
            self.add_error("images", "Для отправки на модерацию добавьте хотя бы одну фотографию.")
        return cleaned_data
