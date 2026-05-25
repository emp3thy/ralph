# Reproduce — BUG-deploy-rosa-irsa-2026-05-23

Run these commands from a machine with `kubectl` pointed at
`rosa-prod-eu-west-1` and `aws` configured for the target account.

## 1. Confirm the pod is crash-looping

```bash
kubectl -n service-auth get pods -l app=service-auth
kubectl -n service-auth logs -l app=service-auth --tail=50
```

Expected output: at least one pod in `CrashLoopBackOff`; the tail of
the log includes the `AccessDenied` signature shown in `BUG.md`.

## 2. Inspect the ServiceAccount annotation

```bash
kubectl -n service-auth get sa service-auth-runtime -o yaml \
  | grep eks.amazonaws.com/role-arn
```

Expected output: a single line containing the IAM role ARN.

## 3. Inspect the IAM role trust policy

```bash
aws iam get-role --role-name service-auth \
  --query 'Role.AssumeRolePolicyDocument' --output json | jq .
```

Expected output: a trust policy whose `Federated` principal lists the
EKS staging OIDC provider but not the ROSA cluster's OIDC issuer URL.

## 4. Confirm the ROSA OIDC issuer

```bash
rosa describe cluster -c rosa-prod-eu-west-1 \
  | grep -E "OIDC|Issuer"
```

Expected output: the cluster's OIDC issuer URL. Compare against the
trust policy from step 3; if it's missing, the hypothesis in
`BUG.md` is confirmed.

## Stop condition

The bug is reproduced when step 3 shows the ROSA OIDC issuer is absent
from the trust policy AND step 1 still shows `AccessDenied`.
