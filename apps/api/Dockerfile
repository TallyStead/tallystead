# The API and Caddy share a root-owned, mode-0200 Unix administration socket.
# Splitting that controller into a dedicated least-privilege sidecar is tracked
# as the remediation; changing only this USER would silently break rollback.
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

WORKDIR /app
COPY apps/api/pyproject.toml ./pyproject.toml
# Temporary home-server build workaround for environments that intercept PyPI TLS.
# Replace with the trusted local CA before any production deployment.
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org .
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
