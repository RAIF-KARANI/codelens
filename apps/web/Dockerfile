# The dashboard as a container (planning-v2.md D149): CodeLens Desktop runs it,
# and so can the self-hosted stack. Build context: this folder (apps/web).
#
#   docker build -t codelens-web apps/web
#
# Three stages, so the image holds only Node and the standalone server: no
# pnpm, no source, no dev dependencies.

FROM node:22-bookworm-slim AS base
# pnpm, at the version package.json's "packageManager" pins.
RUN corepack enable
ENV PNPM_HOME=/pnpm NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

# 1) Dependencies only: cached until the lockfile changes.
FROM base AS deps
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

# 2) The production build, as .next/standalone (next.config.ts).
FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_OUTPUT=standalone
RUN pnpm build

# 3) What runs: server.js and the files it traced, plus the static files it
#    serves itself (next build leaves those for a CDN).
FROM node:22-bookworm-slim AS runner
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
# The image's own unprivileged user (uid 1000).
USER node
EXPOSE 3000
CMD ["node", "server.js"]
