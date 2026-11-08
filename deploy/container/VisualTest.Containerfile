# Isolated acceptance image. Does not replace or rebuild redactx:0.2.8.
FROM redactx:0.2.8
USER root
COPY redactx /app/redactx
COPY service /app/service
COPY web /app/web
USER redactx
