$ErrorActionPreference = "Stop"

# $PSScriptRoot       .
#     Join-Path       .
$GcpScriptRoot = $PSScriptRoot

#    
. (Join-Path $GcpScriptRoot "config\gcp.ps1")
. (Join-Path $GcpScriptRoot "_lib\tf-helper.ps1")

#   
$env:GOOGLE_CLOUD_PROJECT = $GcpConfig.ProjectID
$env:GOOGLE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_CORE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_COMPUTE_REGION = $GcpConfig.Region

# 1.   
Invoke-TerraformLayer -Config $GcpConfig -Layer "00-foundation"

# 2.    (AWS IP    )
# Read-Host "Check AWS peer IPs and VPN shared secret values for 20-network-daily, then press Enter to continue"
# Invoke-TerraformLayer -Config $GcpConfig -Layer "20-network-daily"

# 3.   
Invoke-TerraformLayer -Config $GcpConfig -Layer "99-content"