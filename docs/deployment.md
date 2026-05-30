# Ralph Deployment Runbook

## Which phase are you deploying?

Ralph supports two git-host backends, and you must pick one before
building or deploying. The image you build is host-specific —
there is no host-agnostic image.

**Phase 1 — GitHub (at home / dogfooding):**
  Use this if Ralph will operate on GitHub repos. The image tag
  will be `ralph-executor:<ver>-github`. The skill baked in is
  `pr-github`. The auth secrets you need are `ANTHROPIC_API_KEY`,
  `GH_TOKEN`, `GH_OWNER`. Continue to the
  [Phase 1 path](#phase-1--github-deployment) below.

**Phase 2 — ADO (at work / production):**
  Use this if Ralph will operate on Azure DevOps repos. The image
  tag will be `ralph-executor:<ver>-ado`. The skill baked in is
  `pr-ado`. The auth secrets you need are `ANTHROPIC_API_KEY`,
  `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`. Continue to the
  [Phase 2 path](#phase-2--ado-deployment) below.

Both phases share the SAME Dockerfile, manifests, and runbook
steps — only the `--host` build arg and the auth secret keys
differ. The host is baked into the image at build time via the
Dockerfile's `ARG RALPH_GIT_HOST` and persisted as `ENV
RALPH_GIT_HOST`; ralph-doctor and the executor read it from
there. You CANNOT switch a deployment's host at runtime —
redeploy with a host-matching image.

## Common prerequisites (both phases)

- `docker` with BuildKit enabled.
- `kubectl` 1.30+ (or `oc` for ROSA).
- Access to the target cluster's `ralph` namespace.
- For CI: the GitHub Actions secrets `RALPH_REGISTRY`,
  `RALPH_REGISTRY_USER`, `RALPH_REGISTRY_PASSWORD`.
- Cluster preconditions: the `ralph` namespace exists; the
  cluster has PodSecurity restricted-or-baseline enforcement.

Note any Plan 7 / Plan 11 stubs still in place — the preflight
gate is meaningful only after both plans land.

## Choosing a mode (long-running Deployment vs Coder Job)

Independent of the host choice, you also pick a deployment mode:

| Question | Long-running Deployment | Coder task-pod Job |
|---|---|---|
| Are PBIs continuous or bursty? | continuous | bursty |
| Does the org use Coder workspaces? | optional | required |
| Idle compute tolerance | acceptable | unacceptable |
| Need rolling restarts? | yes | no |

Recommend Deployment for the first ROSA rollout.

---

## Phase 1 — GitHub deployment

This is the "at home" path. It is the recommended first
deployment because the Phase 1 skill (`pr-github`) is delivered
by Plan 5 and is immediately available.

### Phase 1: building the image

Local build (host suffix is required — the script exits 2 if
you forget):

    RALPH_VERSION=$(git rev-parse --short HEAD) \
    bash scripts/build_image.sh --host github

Build + push:

    RALPH_REGISTRY=123456789012.dkr.ecr.eu-west-2.amazonaws.com \
    RALPH_VERSION=$(git rev-parse --short HEAD) \
    bash scripts/build_image.sh --host github --push

CI: `.github/workflows/ralph-image.yml` builds the github image
automatically as part of its matrix.

### Phase 1: pre-deploy gate

    IMG=$(bash scripts/build_image.sh --host github)
    bash scripts/preflight.sh "$IMG"

The doctor reads `RALPH_GIT_HOST` from the image's baked env
and probes only the GitHub auth path (`GH_TOKEN`, `GH_OWNER`).
Expected exit 0. If exit 2, ralph-doctor FAILED — DO NOT
promote the image. Read the doctor JSON report on stderr.

### Phase 1: secrets handling

The Secret carries shared and host-specific keys; for Phase 1
populate ANTHROPIC_API_KEY, GH_TOKEN, GH_OWNER. Leave the
ADO_* sentinels alone (they're documented as unused for github
deployments but the manifest still carries them as placeholders).

    kubectl create secret generic ralph-secrets \
      --namespace ralph \
      --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
      --from-literal=GH_TOKEN="$GH_TOKEN" \
      --from-literal=GH_OWNER="myorg"

Alternatively, use External Secrets Operator (preferred for
production) backed by AWS Secrets Manager. Document the
ExternalSecret manifest in your service overlay. SealedSecrets
is also supported for self-hosted clusters.

### Phase 1: applying manifests

    kubectl apply -f manifests/ralph-rbac.yaml
    kubectl apply -f manifests/ralph-configmap.yaml
    sed "s|__IMAGE__|$IMG|" manifests/ralph-deployment.yaml \
      | kubectl apply -f -
    kubectl rollout status deploy/ralph-executor -n ralph

Job mode:

    sed -e "s|__IMAGE__|$IMG|" \
        -e "s|__PBI_ID__|$PBI_ID|" \
        manifests/ralph-job.yaml | kubectl apply -f -

### Phase 1: verifying the pod

    kubectl get pods -n ralph
    kubectl logs -n ralph deploy/ralph-executor --tail=50
    kubectl exec -n ralph deploy/ralph-executor -- ralph-executor health --ready
    # Confirm the host is github:
    kubectl exec -n ralph deploy/ralph-executor -- env | grep RALPH_GIT_HOST
    # Expected: RALPH_GIT_HOST=github

### Phase 1: troubleshooting

Common failures:

- ImagePullBackOff   → check RALPH_REGISTRY auth; describe pod.
- CrashLoopBackOff   → kubectl logs --previous.
- "401 Unauthorized" in logs → GH_TOKEN missing or expired;
  re-create the Secret with a fresh PAT.
- "GH_OWNER not set" → forgot to populate the Secret key.
- claude -p prompted for permission → doctor drift; rerun
  preflight and fix .claude/settings.json.

---

## Phase 2 — ADO deployment

This is the "at work" path. It requires Plans 2/5 Phase 2 to
have completed (`pr-ado` skill directory must exist on disk).

### Phase 2: building the image

Local build:

    RALPH_VERSION=$(git rev-parse --short HEAD) \
    bash scripts/build_image.sh --host ado

If you get `COPY failed: skills/pr-ado: no such file or
directory`, Phase 2 skills have not landed yet — check Plan
status in the orchestrator.

Build + push:

    RALPH_REGISTRY=123456789012.dkr.ecr.eu-west-2.amazonaws.com \
    RALPH_VERSION=$(git rev-parse --short HEAD) \
    bash scripts/build_image.sh --host ado --push

### Phase 2: pre-deploy gate

    IMG=$(bash scripts/build_image.sh --host ado)
    bash scripts/preflight.sh "$IMG"

The doctor reads `RALPH_GIT_HOST=ado` from the image's baked
env and probes the ADO auth path (`ADO_PAT`, `ADO_ORG_URL`,
`ADO_PROJECT`).

### Phase 2: secrets handling

    kubectl create secret generic ralph-secrets \
      --namespace ralph \
      --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
      --from-literal=ADO_PAT="$ADO_PAT" \
      --from-literal=ADO_ORG_URL="https://dev.azure.com/myorg" \
      --from-literal=ADO_PROJECT="myproject"

### Phase 2: applying manifests

Identical to Phase 1, but substitute the ADO-tagged image:

    sed "s|__IMAGE__|$IMG|" manifests/ralph-deployment.yaml \
      | kubectl apply -f -

### Phase 2: verifying the pod

    kubectl exec -n ralph deploy/ralph-executor -- env | grep RALPH_GIT_HOST
    # Expected: RALPH_GIT_HOST=ado

### Phase 2: troubleshooting

- "skills/pr-ado not found" at build time → Plans 3/5 Phase 2
  not yet complete.
- "401 Unauthorized" calling ADO → ADO_PAT missing, expired,
  or wrong scope; regenerate with vso.code_full.
- "Project not found" → ADO_PROJECT name mismatch.

---

## IAM and IRSA (both phases)

The ralph ServiceAccount carries an annotation slot for IRSA.
Step-by-step:

1. Create the IAM role with a trust policy for the cluster's
   OIDC provider, namespace=ralph, sa=ralph.
2. Attach a policy granting whatever AWS APIs Ralph needs
   (in v1 — none, unless using Secrets Manager).
3. After applying manifests/ralph-rbac.yaml, annotate the SA:

    kubectl annotate sa ralph -n ralph \
      eks.amazonaws.com/role-arn=arn:aws:iam::123456789012:role/ralph-executor

## Alternative: Azure Pipelines

The team's standard is GitHub Actions
(.github/workflows/ralph-image.yml). For teams using ADO
Pipelines instead, this YAML is functionally equivalent
(builds BOTH host images as separate jobs):

    # Azure Pipelines alternative to .github/workflows/ralph-image.yml.
    # Builds both host images via a matrix-equivalent strategy
    # (two parallel jobs).
    trigger:
      branches:
        include:
          - main
      paths:
        include:
          - Dockerfile
          - pyproject.toml
          - ralph_executor/*
          - skills/*
          - .claude/settings.json
          - scripts/*

    pool:
      vmImage: 'ubuntu-latest'

    variables:
      RALPH_IMAGE: ralph-executor

    jobs:
      - job: build_github
        displayName: 'Build (github)'
        steps:
          - checkout: self
            fetchDepth: 0
          # Capture the version ONCE so build + preflight + push all
          # tag the same image. Using $(Build.SourceVersion) in the
          # push step (full SHA) while the build step uses the short
          # SHA would push an untested image and discard the
          # preflighted one.
          - bash: |
              VERSION="$(git rev-parse --short HEAD)"
              echo "##vso[task.setvariable variable=RALPH_VERSION]$VERSION"
              export RALPH_VERSION="$VERSION"
              IMG=$(bash scripts/build_image.sh --host github)
              echo "##vso[task.setvariable variable=IMAGE]$IMG"
            displayName: 'Build image'
          - bash: bash scripts/preflight.sh "$(IMAGE)"
            displayName: 'Preflight (ralph-doctor)'
          - bash: bash scripts/build_image.sh --host github --push
            displayName: 'Push image'
            condition: succeeded()
            env:
              RALPH_REGISTRY: $(RALPH_REGISTRY)
              RALPH_VERSION: $(RALPH_VERSION)

      - job: build_ado
        displayName: 'Build (ado)'
        dependsOn: []
        steps:
          - checkout: self
            fetchDepth: 0
          - bash: |
              VERSION="$(git rev-parse --short HEAD)"
              echo "##vso[task.setvariable variable=RALPH_VERSION]$VERSION"
              export RALPH_VERSION="$VERSION"
              IMG=$(bash scripts/build_image.sh --host ado)
              echo "##vso[task.setvariable variable=IMAGE]$IMG"
            displayName: 'Build image'
          - bash: bash scripts/preflight.sh "$(IMAGE)"
            displayName: 'Preflight (ralph-doctor)'
          - bash: bash scripts/build_image.sh --host ado --push
            displayName: 'Push image'
            condition: succeeded()
            env:
              RALPH_REGISTRY: $(RALPH_REGISTRY)
              RALPH_VERSION: $(RALPH_VERSION)

## Runtime-staging alternative (single image, host chosen at startup)

An alternative deployment model — supported by Plan 7's
`host_select.py` — is to ship a single host-agnostic image
carrying BOTH `pr-github/` AND `pr-ado/` skill bundles, then
at pod startup let `host_select.py` read `RALPH_GIT_HOST` from
the ConfigMap and symlink the chosen skill into
`~/.claude/skills/pr/`. This is supported but is NOT the
default because:

- it adds a startup step that can fail (the staging logic);
- it weakens the image manifest (an image no longer documents
  exactly one host);
- it requires the ConfigMap to set `RALPH_GIT_HOST` (the
  build-time model treats RALPH_GIT_HOST as immutable per image);
- the auth-env-var contract becomes a runtime check rather
  than a build-time check.

To opt into runtime staging, the operator:
1. Modifies the Dockerfile to copy BOTH host bundles into
   /opt/ralph/skills/ without re-copying to /root/.claude/skills.
2. Adds RALPH_GIT_HOST to the ConfigMap (not the image ENV).
3. Configures host_select.py to run at startup via an entrypoint
   wrapper.

Plan 7 documents the contract for `host_select.py`. Operators
who want a single dual-host image should follow that path.

## Plan 7 / 11 stub assumptions

Until Plan 7 ships the real ralph-executor health/run commands
and Plan 11 ships the real doctor, the preflight gate is
meaningful only after both plans land. Refer the operator to
the orchestrator plan for status.
