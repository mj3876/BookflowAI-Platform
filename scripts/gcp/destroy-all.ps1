$ErrorActionPreference = "Stop"
$GcpScriptRoot = $PSScriptRoot

. (Join-Path $GcpScriptRoot "config\gcp.ps1")
. (Join-Path $GcpScriptRoot "_lib\tf-helper.ps1")

$env:GOOGLE_CLOUD_PROJECT = $GcpConfig.ProjectID
$env:GOOGLE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_CORE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_COMPUTE_REGION = $GcpConfig.Region

# 1. destroy content layer
Invoke-TerraformLayer -Config $GcpConfig -Layer "99-content" -Action "destroy"

# 2. network layer (commented out - not deployed)
# Invoke-TerraformLayer -Config $GcpConfig -Layer "20-network-daily" -Action "destroy"

# 3. destroy foundation layer
Invoke-TerraformLayer -Config $GcpConfig -Layer "00-foundation" -Action "destroy"