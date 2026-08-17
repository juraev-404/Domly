from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_PIXELS = 40_000_000


@dataclass(frozen=True)
class ProcessedImage:
    file: ContentFile
    width: int
    height: int
    content_type: str = "image/webp"


def _load_normalized_image(uploaded_file):
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as source:
            if source.format not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError(_("Поддерживаются JPEG, PNG и WebP."))
            if getattr(source, "is_animated", False):
                raise ValidationError(_("Анимированные изображения не поддерживаются."))
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValidationError(_("Разрешение фотографии слишком большое."))

            source.load()
            normalized = ImageOps.exif_transpose(source)
            has_alpha = "A" in normalized.getbands()
            return normalized.convert("RGBA" if has_alpha else "RGB")
    except ValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError(_("Не удалось обработать изображение.")) from exc
    finally:
        try:
            uploaded_file.seek(0)
        except (AttributeError, OSError):
            pass


def _encode_webp(image, *, max_dimensions, quality, filename):
    prepared = image.copy()
    prepared.thumbnail(max_dimensions, Image.Resampling.LANCZOS, reducing_gap=3.0)

    output = BytesIO()
    prepared.save(
        output,
        format="WEBP",
        quality=quality,
        method=6,
        exact=True,
    )
    output.seek(0)
    return ProcessedImage(
        file=ContentFile(output.read(), name=filename),
        width=prepared.width,
        height=prepared.height,
    )


def _webp_name(original_name, *, suffix=""):
    stem = Path(original_name or "image").stem[:40] or "image"
    safe_stem = "".join(character for character in stem if character.isalnum() or character in "-_")
    safe_stem = safe_stem or "image"
    return f"{safe_stem}-{uuid4().hex}{suffix}.webp"


def process_listing_image(uploaded_file, original_name):
    image = _load_normalized_image(uploaded_file)
    token = uuid4().hex
    stem = Path(original_name or "image").stem[:40] or "image"
    safe_stem = "".join(character for character in stem if character.isalnum() or character in "-_")
    safe_stem = safe_stem or "image"
    main = _encode_webp(
        image,
        max_dimensions=(2400, 2400),
        quality=84,
        filename=f"{safe_stem}-{token}.webp",
    )
    thumbnail = _encode_webp(
        image,
        max_dimensions=(720, 540),
        quality=78,
        filename=f"{safe_stem}-{token}-thumb.webp",
    )
    return main, thumbnail


def process_avatar(uploaded_file, original_name):
    image = _load_normalized_image(uploaded_file)
    return _encode_webp(
        image,
        max_dimensions=(512, 512),
        quality=82,
        filename=_webp_name(original_name),
    )


def process_chat_image(uploaded_file, original_name):
    image = _load_normalized_image(uploaded_file)
    return _encode_webp(
        image,
        max_dimensions=(1600, 1600),
        quality=80,
        filename=_webp_name(original_name),
    )
