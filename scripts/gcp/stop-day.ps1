$ErrorActionPreference = "Stop"

$GcpScriptRoot = $PSScriptRoot

. (Join-Path $GcpScriptRoot "config\gcp.ps1")
. (Join-Path $GcpScriptRoot "_lib\tf-helper.ps1")

$env:GOOGLE_CLOUD_PROJECT = $GcpConfig.ProjectID
$env:GOOGLE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_CORE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_COMPUTE_REGION = $GcpConfig.Region

function Invoke-FoundationVpcConnectorDestroy {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Config
    )

    $Layer = "00-foundation"
    $LayerPath = Join-Path $Config.InfraRoot $Layer

    if (-not (Test-Path -LiteralPath $LayerPath -PathType Container)) {
        throw "Terraform layer not found: $LayerPath"
    }

    Push-Location $LayerPath
    try {
        terraform init `
            -backend-config="bucket=$($Config.StateBucket)" `
            -backend-config="prefix=gcp/$Layer" `
            -reconfigure

        if ($LASTEXITCODE -ne 0) {
            throw "terraform init failed for layer: $Layer"
        }

        $ExistingState = @(terraform state list 2>$null)
        if ($ExistingState -notcontains "google_vpc_access_connector.bookflow") {
            Write-Host "00-foundation VPC Access connector is not present. Nothing to remove."
            return
        }

        Write-Host "Destroying only google_vpc_access_connector.bookflow. Core 00-foundation resources remain active."

        terraform destroy -auto-approve -target="google_vpc_access_connector.bookflow"

        if ($LASTEXITCODE -ne 0) {
            throw "targeted terraform destroy failed for google_vpc_access_connector.bookflow"
        }
    }
    finally {
        Pop-Location
    }
}

Invoke-TerraformLayer -Config $GcpConfig -Layer "99-content" -Action "destroy"

Invoke-TerraformLayer -Config $GcpConfig -Layer "20-network-daily" -Action "destroy"

Invoke-FoundationVpcConnectorDestroy -Config $GcpConfig

Write-Host "00-foundation core resources were intentionally kept active."
