$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:TF_INPUT = "0"

$root = "D:\gcp\BookFlowAI-Platform\infra\gcp"
$logDir = Join-Path $root "destroy-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "destroy-$timestamp.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logPath -Value $line
}

function Invoke-TerraformDestroy {
    param([string]$LayerPath)

    Write-Log "Starting destroy: $LayerPath"
    Push-Location $LayerPath
    try {
        Write-Log "terraform init -input=false"
        terraform init -input=false -no-color *>> $logPath

        Write-Log "terraform destroy -input=false -auto-approve -no-color"
        terraform destroy -input=false -auto-approve -no-color *>> $logPath

        Write-Log "Completed destroy: $LayerPath"
    }
    finally {
        Pop-Location
    }
}

Write-Log "BOOKFLOW GCP destroy started."
Write-Log "Target root: $root"

Invoke-TerraformDestroy (Join-Path $root "99-content")
Invoke-TerraformDestroy (Join-Path $root "20-network-daily")
Invoke-TerraformDestroy (Join-Path $root "00-foundation")

Write-Log "BOOKFLOW GCP destroy completed successfully."
