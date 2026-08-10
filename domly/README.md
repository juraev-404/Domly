# Domly

Domly is a Django real-estate marketplace for publishing, moderating, finding,
and discussing property listings.

## Current stack

- Python 3.12
- Django 5.2
- Pillow
- SQLite for local development

The project is planned to use PostgreSQL in production.

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

Registration SMS codes are written to the development log while `DEBUG=True`.
A real SMS provider must be configured before production deployment.

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
