FROM nginx:stable-alpine
COPY templates/ /opt/nginx/templates/
COPY configure.sh /docker-entrypoint.d/40-configure.sh
RUN chmod +x /docker-entrypoint.d/40-configure.sh
