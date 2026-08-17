"""Report Russian UI messages missing from the English or Tajik catalogs."""

from __future__ import annotations

import ast
import argparse
import re
import sys
from pathlib import Path

from django.utils.translation.template import templatize

from compile_messages import read_catalog


BASE_DIR = Path(__file__).resolve().parent.parent
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
GETTEXT_RE = re.compile(
    r"gettext\(u?(?P<literal>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")\)"
)
SOURCE_DIRS = ("users", "listings", "chat", "domly")
ALLOW_IDENTICAL = {
    "tg": {"Бохтар", "Душанбе", "Истаравшан", "Тоҷикӣ", "м²"},
}
INTERACTIVE_PYTHON_FILES = (
    "users/forms.py",
    "users/views.py",
    "users/services.py",
    "listings/forms.py",
    "listings/views.py",
    "chat/forms.py",
    "chat/services.py",
    "chat/views.py",
)
INTERACTIVE_TEMPLATE_FILES = (
    "templates/users/login.html",
    "templates/users/register.html",
    "templates/users/verify.html",
    "templates/users/password_reset_request.html",
    "templates/users/password_reset_verify.html",
    "templates/users/password_reset_new.html",
    "templates/users/profile.html",
    "templates/users/public_profile.html",
    "templates/chat/conversation_detail.html",
    "templates/listings/create.html",
    "templates/listings/detail.html",
    "templates/listings/city_map.html",
)


def python_messages(paths: list[Path] | None = None) -> set[str]:
    messages: set[str] = set()
    if paths is None:
        paths = []
        for source_dir in SOURCE_DIRS:
            paths.extend((BASE_DIR / source_dir).rglob("*.py"))
    for path in paths:
        if "migrations" in path.parts or path.name.startswith("test"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "_":
                continue
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                messages.add(value.value)
    return messages


def template_messages(paths: list[Path] | None = None) -> set[str]:
    messages: set[str] = set()
    if paths is None:
        paths = list((BASE_DIR / "templates").rglob("*.html"))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        generated = templatize(source)
        for match in GETTEXT_RE.finditer(generated):
            message = ast.literal_eval(match.group("literal"))
            messages.add(message)
    return messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Audit forms, actions, and other interactive user flows only.",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Audit the application UI and help center, excluding legal documents.",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    python_paths = None
    template_paths = None
    if args.interactive:
        python_paths = [BASE_DIR / path for path in INTERACTIVE_PYTHON_FILES]
        template_paths = [BASE_DIR / path for path in INTERACTIVE_TEMPLATE_FILES]
    elif args.ui:
        template_paths = [
            path
            for path in (BASE_DIR / "templates").rglob("*.html")
            if "legal" not in path.parts
        ]
    source_messages = {
        message
        for message in python_messages(python_paths) | template_messages(template_paths)
        if CYRILLIC_RE.search(message)
    }
    failed = False
    for language in ("en", "tg"):
        catalog_path = BASE_DIR / "locale" / language / "LC_MESSAGES" / "django.po"
        catalog = read_catalog(catalog_path)
        missing = sorted(
            message
            for message in source_messages
            if not catalog.get(message)
            or (
                catalog[message] == message
                and message not in ALLOW_IDENTICAL.get(language, set())
            )
        )
        print(f"{language}: {len(missing)} missing translation(s)")
        for message in missing:
            print(f"  - {message}")
        failed = failed or bool(missing)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
