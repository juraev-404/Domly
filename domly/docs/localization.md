# Localization direction

Domly currently uses Russian as the source and default interface language.
The planned interface languages are Russian (`ru`), Tajik (`tg`), and English
(`en`).

When adding or changing interface text:

- keep one shared template instead of creating language-specific copies;
- mark template strings with `{% translate %}` or `{% blocktranslate %}`;
- mark Python strings with `gettext()` or `gettext_lazy()` as appropriate;
- keep user-authored listing and message content in its original language;
- use stable machine-readable codes for future API errors and translate their
  presentation separately in the web or mobile client.

Actual Tajik and English translation catalogs can be filled after each section
of the interface is stable.
