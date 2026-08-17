# Domly production deployment

The repository assumes Ubuntu 24.04, the Linux user `domly`, and a repository
checkout at `/srv/domly/repo`. The Django project containing `manage.py` is in
`/srv/domly/repo/domly`. Production uses PostgreSQL, Redis, Gunicorn,
Nginx, and `domly.settings_prod`.

## 1. Packages and directories

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip postgresql redis-server nginx certbot python3-certbot-nginx
sudo install -d -o domly -g www-data -m 0750 /srv/domly
sudo install -d -o root -g domly -m 0750 /etc/domly
sudo install -d -o domly -g domly -m 0750 /var/backups/domly
git clone --branch main https://github.com/juraev-404/Domly.git /srv/domly/repo
sudo install -d -o domly -g www-data -m 0750 /srv/domly/repo/domly/media /srv/domly/repo/domly/staticfiles
python3 -m venv /srv/domly/venv
/srv/domly/venv/bin/pip install --upgrade pip
/srv/domly/venv/bin/pip install -r /srv/domly/repo/domly/requirements.txt
```

## 2. PostgreSQL

Run the following in `sudo -u postgres psql`, replacing the password with a
new random value that will also be stored in `/etc/domly/domly.env`:

```sql
CREATE ROLE domly LOGIN PASSWORD 'replace-this-password';
CREATE DATABASE domly OWNER domly;
REVOKE ALL ON DATABASE domly FROM PUBLIC;
```

Copy `.env.example` to `/etc/domly/domly.env`, replace every placeholder, quote
values containing spaces, and protect the file:

```bash
sudo cp /srv/domly/repo/domly/.env.example /etc/domly/domly.env
sudo chown root:domly /etc/domly/domly.env
sudo chmod 0640 /etc/domly/domly.env
sudoedit /etc/domly/domly.env
```

Generate `DJANGO_SECRET_KEY` on the server without sending it through chat:

```bash
/srv/domly/venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## 3. Validate and prepare Django

```bash
cd /srv/domly/repo/domly
set -a
. /etc/domly/domly.env
set +a
/srv/domly/venv/bin/python manage.py check --deploy
/srv/domly/venv/bin/python manage.py migrate
/srv/domly/venv/bin/python manage.py collectstatic --noinput
/srv/domly/venv/bin/python manage.py compilemessages
/srv/domly/venv/bin/python manage.py createsuperuser
```

Do not continue if `check --deploy` reports warnings other than `W005` and
`W021`: those two remain intentional until every subdomain is HTTPS-only and
you deliberately opt into HSTS preload. Test SMTP with Django's shell before
opening registration publicly.

Audit pinned Python packages before every release from a development machine:

```bash
python -m pip install -r requirements-dev.txt
python -m pip_audit -r requirements.txt
```

## 4. Gunicorn and maintenance

```bash
sudo cp deploy/systemd/domly.service /etc/systemd/system/
sudo cp deploy/systemd/domly-maintenance.service /etc/systemd/system/
sudo cp deploy/systemd/domly-maintenance.timer /etc/systemd/system/
sudo cp deploy/systemd/domly-backup.service /etc/systemd/system/
sudo cp deploy/systemd/domly-backup.timer /etc/systemd/system/
sudo chmod 0750 scripts/backup.sh
sudo systemctl daemon-reload
sudo systemctl enable --now redis-server domly.service domly-maintenance.timer domly-backup.timer
sudo systemctl status domly --no-pager
curl --unix-socket /run/domly/gunicorn.sock http://localhost/health/
```

## 5. Nginx and HTTPS

Install the HTTP configuration first and verify it before asking Certbot to
create the HTTPS configuration:

```bash
sudo cp deploy/nginx/domly.conf /etc/nginx/sites-available/domly
sudo ln -s /etc/nginx/sites-available/domly /etc/nginx/sites-enabled/domly
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
curl -I http://domly.site/health/
sudo certbot --nginx -d domly.site -d www.domly.site
sudo nginx -t
sudo systemctl reload nginx
curl -I https://domly.site/health/
```

Only raise `DJANGO_SECURE_HSTS_SECONDS` to `31536000` and enable subdomains
after HTTPS works reliably on every relevant hostname.

## 6. Backups and smoke test

The included timer creates local PostgreSQL and media backups under
`/var/backups/domly` and retains them for 14 days. Also copy these archives to
storage outside the server. A backup is not considered working until a restore
into a temporary database has been tested. Before launch, verify registration
email, password reset, image upload,
moderation, chat in two browsers, map location, language switching, `/robots.txt`,
and `/sitemap.xml`.

Useful diagnostics:

```bash
sudo journalctl -u domly -n 100 --no-pager
sudo systemctl list-timers domly-maintenance.timer
sudo systemctl list-timers domly-backup.timer
sudo systemctl start domly-backup.service
sudo journalctl -u domly-backup -n 50 --no-pager
sudo -u postgres pg_isready
redis-cli ping
curl -fsS https://domly.site/health/
```
