FROM alpine:3.20

# Instala traefik y envsubst (parte del paquete gettext)
RUN apk add --no-cache traefik gettext curl

# Crea carpeta para certificados
RUN mkdir -p /etc/traefik/acme \
    && touch /etc/traefik/acme/acme.json \
    && chmod 600 /etc/traefik/acme/acme.json

# Copia la plantilla del archivo traefik
COPY ./compose/production/traefik/traefik.template.yml /etc/traefik/traefik.template.yml

# Comando de inicio:
# 1. Usa envsubst para reemplazar las variables en la plantilla.
# 2. Ejecuta traefik con el archivo generado.
CMD envsubst < /etc/traefik/traefik.template.yml > /etc/traefik/traefik.yml && \
    traefik --configFile=/etc/traefik/traefik.yml