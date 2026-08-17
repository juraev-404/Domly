from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from .settings import _legal_env
from .storage import PublicStaticFilesStorage


class ProductionDeploymentTests(SimpleTestCase):
    def test_public_legal_values_reject_environment_placeholders(self):
        with patch.dict(
            "os.environ",
            {"LEGAL_OPERATOR_NAME": "replace-with-real-operator-name"},
        ):
            value = _legal_env(
                "LEGAL_OPERATOR_NAME",
                "Администратор сервиса Domly",
            )

        self.assertEqual(value, "Администратор сервиса Domly")

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
