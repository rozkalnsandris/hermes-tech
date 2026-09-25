FROM ghcr.io/gohugoio/hugo:v0.164.0 AS build
WORKDIR /src
COPY site ./site
RUN hugo --source /src/site \
    --destination /out \
    --cleanDestinationDir \
    --minify \
    --noBuildLock \
    --panicOnWarning

FROM nginxinc/nginx-unprivileged:1.29.1-alpine
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /out /usr/share/nginx/html
EXPOSE 8080
