# The API and Caddy share a root-owned, mode-0200 Unix administration socket.
# Splitting that controller into a dedicated least-privilege sidecar is tracked
# as the remediation; changing only this USER would silently break rollback.
FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65

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
