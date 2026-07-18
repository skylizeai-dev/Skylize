# syntax=docker/dockerfile:1.7
# OPA guardrail policy engine — Railway deploy image.
#
# The policy bundle (../../policy) is baked in at build time: this is a
# staging-simplicity choice, not a bundle server. Shipping a policy change
# means rebuilding/redeploying this image. Pinned to the same OPA release
# used in infra/docker-compose.yml so local and deployed evaluation match.
#
# Build context must be the repo root (Railway root directory = "/"), so this
# Dockerfile is referenced by path rather than living next to a self-contained
# context — see infra/opa/railway.json's build.dockerfilePath.
FROM openpolicyagent/opa:1.18.2

COPY policy /policies

EXPOSE 8181

ENTRYPOINT ["/opa"]
CMD ["run", "--server", "--addr=0.0.0.0:8181", "--log-level=info", "/policies"]
