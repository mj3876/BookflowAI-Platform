# GCP Deployment Scripts

PowerShell automation for the BOOKFLOW GCP infrastructure layers. These scripts wrap Terraform layer deployment from `infra/gcp` and keep the project, region, backend bucket, and layer paths consistent across rehearsal runs.

## Directory Structure

```text
scripts/gcp/
+-- config/
|   +-- gcp.ps1
+-- _lib/
|   +-- tf-helper.ps1
+-- 0-initial/
+-- 1-daily/
+-- 2-tasks/
+-- deploy-all.ps1
+-- destroy-all.ps1
+-- README.md
```

### `config/`

Contains environment variables and GCP project settings used by all scripts.

- `gcp.ps1` defines `$GcpConfig`, including:
  - `ProjectID = "project-8ab6bf05-54d2-4f5d-b8d"`
  - `Region = "asia-northeast1"`
  - `StateBucket = "bookflow-tf-state"`
  - `InfraRoot = <repo-root>\infra\gcp`

Deployment scripts also export these values into the standard Google environment variables:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_PROJECT`
- `CLOUDSDK_CORE_PROJECT`
- `CLOUDSDK_COMPUTE_REGION`

### `_lib/`

Shared helper scripts.

- `tf-helper.ps1` provides `Invoke-TerraformLayer`.
- The helper resolves a Terraform layer under `infra/gcp`, runs `terraform init` with the configured GCS backend bucket and layer prefix, then runs either `terraform apply -auto-approve` or `terraform destroy -auto-approve`.
- This keeps layer iteration logic centralized so `deploy-all.ps1` and `destroy-all.ps1` only define ordering.

### `0-initial/`

Reserved for initial foundation setup scripts. Use this area for one-time bootstrap tasks that must happen before normal Terraform layer deployment, such as project authentication checks, state bucket validation, or local tooling setup.

### `1-daily/`

Reserved for daily infrastructure update scripts.

The pending `20-network-daily` Terraform layer belongs to this workflow. It is the bridge for the multi-cloud VPN connection to AWS and should be enabled only after the AWS-side VPN export values are available.

### `2-tasks/`

Reserved for miscellaneous utility scripts and targeted operational tasks that do not belong in the full deploy/destroy flow.

Examples:

- Re-uploading Cloud Functions source artifacts.
- Validating GCS objects.
- Running one-off Terraform plans for a specific layer.

## Main Deployment Scripts

### `deploy-all.ps1`

Runs the GCP deployment rehearsal in layer order:

```text
00-foundation -> 99-content
```

Current behavior:

1. Loads `config/gcp.ps1`.
2. Loads `_lib/tf-helper.ps1`.
3. Exports Google project and region environment variables.
4. Applies `00-foundation`.
5. Skips `20-network-daily` for now because AWS VPN peer IPs and secrets are pending.
6. Applies `99-content`.

Run from the repository root:

```powershell
.\scripts\gcp\deploy-all.ps1
```

### `destroy-all.ps1`

Runs teardown in reverse order:

```text
99-content -> 00-foundation
```

Current behavior:

1. Destroys `99-content`.
2. Skips `20-network-daily` unless it has been deployed and explicitly re-enabled.
3. Destroys `00-foundation`.

Run from the repository root:

```powershell
.\scripts\gcp\destroy-all.ps1
```

Manual cleanup note: Terraform destroy can fail if GCS buckets still contain objects. If that happens, manually remove remaining objects from the managed buckets, then rerun `destroy-all.ps1`.

## Special Implementation Notes

### Cloud Functions Source Artifacts

The `99-content` layer deploys 2nd Gen Cloud Functions from source archives in the staging bucket.

Before deploying, the following zip files must exist in:

```text
gs://project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging/functions/
```

Required objects:

```text
bookflow-bq-load.zip
bookflow-feature-assemble.zip
bookflow-vertex-invoke.zip
```

If the zip files are missing, Terraform fails with a GCS 404 during Cloud Functions deployment.

Each zip should contain only the function source files at the archive root, not the parent directory.

### Artifact Registry

2nd Gen Cloud Functions build through Cloud Build and push build artifacts to Artifact Registry.

The Docker repository below must exist:

```text
region: asia-northeast1
repository: gcf-artifacts
format: docker
```

If it is missing, Cloud Build can fail while checking push access to:

```text
asia-northeast1-docker.pkg.dev/<project-id>/gcf-artifacts/...
```

### Vertex AI Network Path

For Vertex AI private networking, use the Project Number in the network path rather than the Project ID. This is required by Google API resource naming for the relevant network attachment/private service path.

For this project:

```text
Project ID:     project-8ab6bf05-54d2-4f5d-b8d
Project Number: 476598540719
```

### Workflows Vertex AI Connector

The Workflows call to:

```text
googleapis.aiplatform.v1.projects.locations.pipelineJobs.create
```

must include the required `region` argument in addition to `parent`.

The current `infra/gcp/99-content/workflow.tf` includes:

```yaml
args:
  parent: "projects/${var.project_id}/locations/${local.region}"
  region: "${local.region}"
```

Without `region`, Workflows validation fails before the workflow can be created.

## AWS Integration

The GCP VPN layer depends on AWS deployment outputs from Yeongheon's AWS environment.

Expected handoff file:

```text
exports/aws-YYYY-MM-DD.txt
```

This file should provide the AWS-side values needed by `infra/gcp/20-network-daily/terraform.tfvars`, including:

- AWS VPC CIDR ranges.
- AWS TGW BGP ASN.
- AWS VPN tunnel public peer IPs.
- VPN shared secret or tunnel-specific PSK values.
- BGP inside tunnel CIDR details, if different from the defaults.

The `20-network-daily` layer is currently the GCP-side bridge for the multi-cloud VPN connection. Keep it disabled in `deploy-all.ps1` until the AWS VPN exports are available and verified.
