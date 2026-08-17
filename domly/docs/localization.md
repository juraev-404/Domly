# Localization direction

Domly uses Russian as the source and default interface language. Russian
(`ru`), Tajik (`tg`), and English (`en`) are enabled through Django's locale
middleware, and the selected language is stored in the `django_language`
cookie.

When adding or changing interface text:

- keep one shared template instead of creating language-specific copies;
- mark template strings with `{% translate %}` or `{% blocktranslate %}`;
- mark Python strings with `gettext()` or `gettext_lazy()` as appropriate;
- keep user-authored listing and message content in its original language;
- use stable machine-readable codes for future API errors and translate their
  presentation separately in the web or mobile client.

Translations live in `locale/<language>/LC_MESSAGES/django.po`. After changing
a catalog, compile it on Windows without GNU gettext by running:

```bash
python scripts/compile_messages.py
```

Commit both the `.po` sources and compiled `.mo` files. Do not run
`compilemessages` inside the production checkout: generated binary differences
would leave local changes and block the next fast-forward deployment.

Authentication currently uses six-digit email codes for registration, email
changes, and password recovery. Phone data remains optional for a possible
future phone-verification feature, but SMS is not part of the current flow.
