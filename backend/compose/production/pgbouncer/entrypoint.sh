#!/bin/sh
set -e

# Verificar que las variables requeridas están definidas
if [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_PASSWORD" ]; then
    echo "Error: POSTGRES_USER and POSTGRES_PASSWORD environment variables are required"
    exit 1
fi

# Generar userlist.txt desde el template usando envsubst
echo "Generating userlist.txt from template..."
envsubst < /etc/pgbouncer/userlist.template.txt > /etc/pgbouncer/userlist.txt
envsubst < /etc/pgbouncer/pgbouncer.ini.template > /etc/pgbouncer/pgbouncer.ini
# Cambiar permisos del userlist.txt
chmod 600 /etc/pgbouncer/userlist.txt

echo "Generated userlist.txt content:"
cat /etc/pgbouncer/userlist.txt

echo "Starting PgBouncer..."

# Ejecutar PgBouncer con los argumentos pasados
exec "$@"