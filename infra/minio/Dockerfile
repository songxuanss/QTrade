FROM golang:1.24-bookworm AS build
ARG MINIO_COMMIT=07c3a429bfed433e49018cb0f78a52145d4bedeb
WORKDIR /src
RUN git init . && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth 1 origin "$MINIO_COMMIT" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$MINIO_COMMIT"
RUN CGO_ENABLED=0 go build -trimpath -o /out/minio .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10002 --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/doc/minio/LICENSE
COPY --from=build /src/NOTICE /usr/share/doc/minio/NOTICE
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
