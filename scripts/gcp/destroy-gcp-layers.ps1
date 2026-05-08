$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:TF_INPUT = "0"

$GcpScriptRoot = $PSScriptRoot

. (Join-Path $GcpScriptRoot "config\gcp.ps1")

$env:GOOGLE_CLOUD_PROJECT = $GcpConfig.ProjectID
$env:GOOGLE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_CORE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_COMPUTE_REGION = $GcpConfig.Region

$LogDir = Join-Path $GcpScriptRoot "destroy-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDir "destroy-$Timestamp.log"

function Write-Log {
    param([string] $Message)

    $Line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogPath -Value $Line
    Write-Host $Message
}

function Invoke-TerraformDestroy {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Config,

        [Parameter(Mandatory = $true)]
        [string] $Layer
    )

    $LayerPath = Join-Path $Config.InfraRoot $Layer

    if (-not (Test-Path -LiteralPath $LayerPath -PathType Container)) {
        throw "Terraform layer not found: $LayerPath"
    }

    Write-Log "Starting destroy: $Layer"
    Push-Location $LayerPath
    try {
        Write-Log "terraform init -input=false"
        terraform init `
            -input=false `
            -no-color `
            -backend-config="bucket=$($Config.StateBucket)" `
            -backend-config="prefix=gcp/$Layer" `
            -reconfigure *>> $LogPath

        if ($LASTEXITCODE -ne 0) {
            throw "terraform init failed for layer: $Layer"
        }

        Write-Log "terraform destroy -input=false -auto-approve -no-color"
        terraform destroy -input=false -auto-approve -no-color *>> $LogPath

        if ($LASTEXITCODE -ne 0) {
            throw "terraform destroy failed for layer: $Layer"
        }

        Write-Log "Completed destroy: $Layer"
    }
    finally {
        Pop-Location
    }
}

Write-Log "BOOKFLOW GCP destroy started."
Write-Log "Target root: $($GcpConfig.InfraRoot)"

Invoke-TerraformDestroy -Config $GcpConfig -Layer "99-content"
Invoke-TerraformDestroy -Config $GcpConfig -Layer "20-network-daily"
Invoke-TerraformDestroy -Config $GcpConfig -Layer "00-foundation"

Write-Log "BOOKFLOW GCP destroy completed successfully."
Write-Log "Log file: $LogPath"
