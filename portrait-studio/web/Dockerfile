# syntax=docker/dockerfile:1
# Standalone build of the web UI (dist/). Normally not needed: api/Dockerfile builds the UI in its own stage.
ARG NODE_IMAGE=node:22-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM scratch AS dist
COPY --from=build /web/dist /dist
