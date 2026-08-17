from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from .storage import PublicStaticFilesStorage


class ProductionDeploymentTests(SimpleTestCase):
    def test_public_static_storage_uses_nginx_readable_permissions(self):
        storage = PublicStaticFilesStorage()

        self.assertEqual(storage.file_permissions_mode, 0o644)
        self.assertEqual(storage.directory_permissions_mode, 0o755)

    def test_deploy_script_requires_clean_checkout_and_skips_server_compilation(self):
        script = Path(settings.BASE_DIR, "scripts", "deploy.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("status --porcelain", script)
        self.assertIn("pull --ff-only", script)
        self.assertIn("manage.py check --deploy", script)
        self.assertIn("manage.py migrate --noinput", script)
        self.assertIn("manage.py collectstatic --noinput", script)
        self.assertNotIn("compilemessages", script)
