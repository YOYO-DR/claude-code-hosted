FROM edoburu/pgbouncer:v1.24.1-p1

# Cambiar a root temporalmente para instalar gettext
USER root

# Instalar envsubst (gettext) para el procesamiento de templates
RUN apk add --no-cache gettext \
    && addgroup -S pgbouncer \
    && adduser -S -G pgbouncer pgbouncer

# Copiar archivos de configuración y script de inicio
COPY ./compose/production/pgbouncer/pgbouncer.ini.template /etc/pgbouncer/pgbouncer.ini.template
COPY ./compose/production/pgbouncer/userlist.template.txt /etc/pgbouncer/userlist.template.txt
COPY ./compose/production/pgbouncer/entrypoint.sh /entrypoint-custom.sh

# Hacer ejecutable el script de inicio
RUN chmod +x /entrypoint-custom.sh

# Cambiar ownership de los archivos de configuración
RUN chown -R pgbouncer:pgbouncer /etc/pgbouncer

# Dar permiso sobre la carpeta de logs
RUN mkdir -p /var/log/pgbouncer
RUN chown pgbouncer:pgbouncer /var/log/pgbouncer

# Dar permiso sobre la carpeta pid de pgbouncer
RUN mkdir -p /var/run/pgbouncer
RUN chown pgbouncer:pgbouncer /var/run/pgbouncer

# Volver al usuario pgbouncer
USER pgbouncer

# Exponer puerto por defecto de PgBouncer
EXPOSE 5432

# Usar nuestro script personalizado como entrypoint
ENTRYPOINT ["/entrypoint-custom.sh"]

# Comando por defecto
CMD ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]