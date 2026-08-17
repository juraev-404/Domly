from django.contrib.staticfiles.storage import StaticFilesStorage


class PublicStaticFilesStorage(StaticFilesStorage):
    """Store collected public assets with permissions readable by Nginx."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("file_permissions_mode", 0o644)
        kwargs.setdefault("directory_permissions_mode", 0o755)
        super().__init__(*args, **kwargs)
