param(
    [string] $ProjectId = "project-8ab6bf05-54d2-4f5d-b8d",
    [string] $Region = "asia-northeast1",
    [string] $EndpointId = "bookflow-forecast-endpoint",
    [string] $ModelId = "3223031419848622080",
    [string] $MachineType = "e2-standard-2",
    [int] $MinReplicaCount = 1,
    [int] $MaxReplicaCount = 1
)

$ErrorActionPreference = "Stop"

Write-Host "Deploying model $ModelId to endpoint $EndpointId in $Region"
Write-Host "WARNING: Online serving cost starts after deployment until the model is undeployed."

gcloud ai endpoints deploy-model $EndpointId `
  --project=$ProjectId `
  --region=$Region `
  --model=$ModelId `
  --display-name="champion-20260515-v1" `
  --machine-type=$MachineType `
  --min-replica-count=$MinReplicaCount `
  --max-replica-count=$MaxReplicaCount

gcloud ai endpoints describe $EndpointId `
  --project=$ProjectId `
  --region=$Region `
  --format="json(name,displayName,deployedModels)"
