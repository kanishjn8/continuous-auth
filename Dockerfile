# syntax=docker/dockerfile:1

FROM python:3.12-slim AS backend-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY pyproject.toml ./
COPY backend ./backend
COPY ml ./ml
COPY protocol ./protocol
COPY config ./config

RUN python -m pip install --no-cache-dir ".[backend]"

EXPOSE 8765

CMD ["python", "-m", "uvicorn", "backend.app.compose:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765"]


FROM node:22-alpine AS dashboard-build

WORKDIR /workspace/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci

COPY dashboard ./
COPY config /workspace/config
RUN npm run build


FROM nginx:1.27-alpine AS dashboard-runtime

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=dashboard-build /workspace/dashboard/dist /usr/share/nginx/html

EXPOSE 8080

