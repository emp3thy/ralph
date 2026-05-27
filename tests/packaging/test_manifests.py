"""Structural tests for k8s manifests."""

from pathlib import Path
from typing import Any

import yaml

MANIFESTS = Path(__file__).resolve().parents[2] / "manifests"


def _load(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        return [doc for doc in yaml.safe_load_all(fh) if doc is not None]


def test_deployment_is_non_root_with_limits() -> None:
    docs = _load(MANIFESTS / "ralph-deployment.yaml")
    assert len(docs) == 1
    dep = docs[0]
    assert dep["kind"] == "Deployment"
    pod_spec = dep["spec"]["template"]["spec"]
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["runAsUser"] == 10001
    assert pod_spec["serviceAccountName"] == "ralph"
    container = pod_spec["containers"][0]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["resources"]["limits"]["cpu"]
    assert container["resources"]["limits"]["memory"]
    assert container["resources"]["requests"]["cpu"]
    assert container["resources"]["requests"]["memory"]
    assert container["readinessProbe"]["exec"]["command"][0] == "ralph-executor"
    assert "ready" in " ".join(container["readinessProbe"]["exec"]["command"])
    assert container["livenessProbe"]["exec"]["command"][0] == "ralph-executor"
    ref_names = {
        ref["configMapRef"]["name"] for ref in container["envFrom"] if "configMapRef" in ref
    }
    assert "ralph-config" in ref_names
    ref_secret = {ref["secretRef"]["name"] for ref in container["envFrom"] if "secretRef" in ref}
    assert "ralph-secrets" in ref_secret


def test_deployment_seeds_home_via_init_container() -> None:
    """Regression: BugBot PR #39 flagged that the emptyDir at
    /home/ralph wipes the baked .claude/skills/ tree at runtime.
    Fix uses an initContainer to copy the baked /home/ralph into
    the volume before the main container starts, so skills remain
    visible AND the writable home survives readOnlyRootFilesystem."""
    docs = _load(MANIFESTS / "ralph-deployment.yaml")
    pod_spec = docs[0]["spec"]["template"]["spec"]
    init = pod_spec.get("initContainers") or []
    assert len(init) == 1, f"expected 1 init container, got {len(init)}"
    seed = init[0]
    assert seed["name"] == "seed-home"
    # initContainer mounts the same emptyDir at /seed and copies the
    # baked /home/ralph contents into it.
    mounts = {m["mountPath"]: m["name"] for m in seed.get("volumeMounts", [])}
    assert mounts.get("/seed") == "home"
    assert "/home/ralph/." in " ".join(seed["command"])
    assert "/seed/" in " ".join(seed["command"])


def test_job_seeds_home_via_init_container() -> None:
    """Same regression as the deployment test — the Job manifest has
    the same emptyDir-over-/home/ralph problem; it needs the same
    seed-home initContainer."""
    docs = _load(MANIFESTS / "ralph-job.yaml")
    pod_spec = docs[0]["spec"]["template"]["spec"]
    init = pod_spec.get("initContainers") or []
    assert len(init) == 1
    assert init[0]["name"] == "seed-home"


def test_deployment_does_not_set_ralph_git_host_env() -> None:
    """RALPH_GIT_HOST is baked into the image, not set in the
    manifest. If the manifest tries to set it, ENV order rules
    mean envFrom would override it — but more importantly, the
    manifest setting it would imply runtime is the right place
    for the host decision, which contradicts the design."""
    docs = _load(MANIFESTS / "ralph-deployment.yaml")
    container = docs[0]["spec"]["template"]["spec"]["containers"][0]
    env_names = {e["name"] for e in container.get("env", [])}
    assert "RALPH_GIT_HOST" not in env_names, (
        "RALPH_GIT_HOST must not be set in the manifest; it is baked into the image at build time."
    )


def test_deployment_image_tag_comment_present() -> None:
    raw = (MANIFESTS / "ralph-deployment.yaml").read_text(encoding="utf-8")
    assert "-github" in raw and "-ado" in raw, (
        "deployment manifest must document the host-tagged image naming"
    )


def test_job_is_task_pod_shape() -> None:
    docs = _load(MANIFESTS / "ralph-job.yaml")
    assert len(docs) == 1
    job = docs[0]
    assert job["kind"] == "Job"
    spec = job["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["ttlSecondsAfterFinished"] >= 1
    pod_spec = spec["template"]["spec"]
    assert pod_spec["restartPolicy"] == "Never"
    assert pod_spec["serviceAccountName"] == "ralph"
    container = pod_spec["containers"][0]
    env = {e["name"]: e["value"] for e in container.get("env", [])}
    assert env.get("RALPH_RUN_ONCE") == "true"
    assert "--once" in container["args"]


def test_configmap_carries_expected_keys() -> None:
    docs = _load(MANIFESTS / "ralph-configmap.yaml")
    by_name = {d["metadata"]["name"]: d for d in docs}
    assert "ralph-config" in by_name
    assert "ralph-claude-settings" in by_name
    cfg = by_name["ralph-config"]["data"]
    for key in (
        "RALPH_REPO_URL",
        "RALPH_QUEUE_BRANCH",
        "RALPH_MAIN_BRANCH",
        "ANTHROPIC_MODEL",
        "RALPH_LOG_LEVEL",
    ):
        assert key in cfg, f"missing {key} in ralph-config"
    # RALPH_GIT_HOST must NOT live in the ConfigMap — it is
    # baked into the image.
    assert "RALPH_GIT_HOST" not in cfg


def test_secrets_template_covers_both_hosts() -> None:
    path = MANIFESTS / "ralph-secrets.template.yaml"
    raw = path.read_text(encoding="utf-8")
    assert "RALPH-TEMPLATE-DO-NOT-APPLY-AS-IS" in raw
    docs = _load(path)
    assert len(docs) == 1
    sec = docs[0]
    assert sec["kind"] == "Secret"
    data = sec["stringData"]
    # Shared
    assert data["ANTHROPIC_API_KEY"].startswith("__REPLACE_WITH")
    # Phase 1 (github)
    assert data["GH_TOKEN"].startswith("__REPLACE_WITH")
    assert data["GH_OWNER"].startswith("__REPLACE_WITH")
    # Phase 2 (ado)
    assert data["ADO_PAT"].startswith("__REPLACE_WITH")
    assert data["ADO_ORG_URL"].startswith("__REPLACE_WITH")
    assert data["ADO_PROJECT"].startswith("__REPLACE_WITH")
    # Documentation comment
    assert "GitHub deployments" in raw
    assert "ADO deployments" in raw


def test_rbac_binds_ralph_service_account() -> None:
    docs = _load(MANIFESTS / "ralph-rbac.yaml")
    kinds = {d["kind"]: d for d in docs}
    assert "ServiceAccount" in kinds
    assert "Role" in kinds
    assert "RoleBinding" in kinds
    sa = kinds["ServiceAccount"]
    assert sa["metadata"]["name"] == "ralph"
    binding = kinds["RoleBinding"]
    assert any(
        s.get("kind") == "ServiceAccount" and s.get("name") == "ralph" for s in binding["subjects"]
    )
