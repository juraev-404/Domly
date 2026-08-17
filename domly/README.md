# Domly

Domly is a Django real-estate marketplace for publishing, moderating, finding,
and discussing property listings.

## Current stack

- Python 3.12
- Django 5.2
- Pillow
- Tailwind CSS 3.4 with a local CLI build
- SQLite for local development

The project is planned to use PostgreSQL in production.

Production configuration and Ubuntu deployment steps are documented in
[`docs/deployment.md`](docs/deployment.md). Production must use
`DJANGO_SETTINGS_MODULE=domly.settings_prod`; the default settings module is
development-only.

## Local setup

From the directory containing `manage.py`:

```bash
python -m venv .venv
```

Activate the environment, then install dependencies and prepare the database:

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Frontend assets

Tailwind is compiled locally; the site does not load the Tailwind Play CDN.
The generated `static/css/app.css` file is committed so Django can serve and
collect it even before Node.js is installed on a server.

Install the pinned frontend dependency and rebuild CSS after changing utility
classes in templates or JavaScript:

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm run build:css
```

During active interface work, use `pnpm run watch:css` in a separate terminal.
Production deployment should run `pnpm run build:css` and then
`python manage.py collectstatic --noinput`.

Registration, email-change, and password-reset codes are sent through Django's
email backend. Development prints emails to the console. Configure an SMTP
backend and domain sender through environment variables before production.

Phone fields remain optional for a possible future return of phone verification,
but the current product does not require SMS.

## Email delivery

The default development backend prints the full email, including its six-digit
code, in the terminal running Django. Production SMTP configuration is read from:

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DEFAULT_FROM_EMAIL=Domly <no-reply@your-domain.tj>
EMAIL_HOST=smtp.provider.example
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_TIMEOUT=10
```

For SMTP on port 465, use `EMAIL_USE_SSL=true` and `EMAIL_USE_TLS=false`.

Configure SPF, DKIM, and DMARC for the sender domain before public launch. Never
commit SMTP credentials to Git.

## SEO and legal settings

Public catalog, listing, help, and legal pages expose canonical and Open Graph
metadata. Django serves `/sitemap.xml` and `/robots.txt`; private account, chat,
authentication, and moderation pages are marked `noindex`.

Set the real operator information before public launch:

```text
LEGAL_OPERATOR_NAME=Full name or registered business name
LEGAL_OPERATOR_ADDRESS=Full legal/contact address
LEGAL_OPERATOR_REGISTRATION_ID=Business registration details
LEGAL_OPERATOR_TAX_ID=Tax identification number
LEGAL_DATA_PROTECTION_CERTIFICATE=Certificate number, issuer, and date
LEGAL_CONTACT_EMAIL=support@domly.site
```

The defaults remain placeholders and must not be treated as verified operator
details. Have a Tajikistan-qualified lawyer verify the final operator status,
certificate requirements, and texts before public launch, and make sure the
contact mailbox exists.

As of 2026-08-17, the authorized personal-data authority is the Agency for
Innovation and Digital Technologies under the President of Tajikistan. Confirm
the current certificate procedure directly with that authority before enabling
public registration.

Run the following command once per day on the server to remove expired attempts:

```bash
python manage.py cleanup_email_codes
```

## Verification

```bash
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
```

## Local data

The SQLite database, uploaded media, local backups, environment files, and
Python caches are intentionally ignored by Git. Keep production secrets outside
the repository.
