#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

backup_dir="/var/backups/domly"
retention_days="${DOMLY_BACKUP_RETENTION_DAYS:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
database_file="$backup_dir/domly-$timestamp.dump"
media_file="$backup_dir/domly-media-$timestamp.tar.gz"
checksum_files=("$database_file")

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

mkdir -p "$backup_dir"
export PGPASSWORD="$POSTGRES_PASSWORD"
pg_dump \
    --host="${POSTGRES_HOST:-127.0.0.1}" \
    --port="${POSTGRES_PORT:-5432}" \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --format=custom \
    --file="$database_file.tmp"
mv "$database_file.tmp" "$database_file"

if [[ -d /srv/domly/repo/domly/media ]]; then
    tar --create --gzip --file="$media_file.tmp" -C /srv/domly/repo/domly media
    mv "$media_file.tmp" "$media_file"
    checksum_files+=("$media_file")
fi

sha256sum "${checksum_files[@]}" > "$backup_dir/domly-$timestamp.sha256"

find "$backup_dir" -maxdepth 1 -type f -name 'domly-*' -mtime "+$retention_days" -delete
