---
id: BUG-deploy-rosa-irsa-2026-05-23
type: bug
status: inbox
severity: critical
attempts: 0
created_at: 2026-05-23T22:41:00+00:00
updated_at: 2026-05-23T22:41:00+00:00
---

# service-auth pod on ROSA: AccessDenied calling STS via IRSA

## Failure signature

```
botocore.exceptions.ClientError: An error occurred (AccessDenied) when
calling the AssumeRoleWithWebIdentity operation: Not authorized to perform
sts:AssumeRoleWithWebIdentity
```

## Where it fires

`service_auth/aws/credentials.py:get_session` on pod startup, before the
HTTP server binds. The container restarts in a `CrashLoopBackOff`.

## Environment

- Cluster: `rosa-prod-eu-west-1`
- Namespace: `service-auth`
- ServiceAccount: `service-auth-runtime`
- Image: `service-auth:2026-05-23.1`

## What we know

- The same image runs cleanly on the EKS staging cluster, where IRSA is
  also configured.
- The ServiceAccount on ROSA has the annotation
  `eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/service-auth`.
- The role's trust policy lists the EKS staging OIDC provider but not the
  ROSA OIDC provider.

## Hypothesis (unverified)

Trust policy on the IAM role does not include the ROSA cluster's OIDC
issuer, so STS rejects the web identity token.

## What success looks like

- Pod reaches `Running` and serves traffic on `/healthz` (when that
  endpoint exists; see WI-1234).
- No `AccessDenied` lines in the first 5 minutes of pod logs.
