$ErrorActionPreference = "Stop"

$GcpScriptRoot = $PSScriptRoot

. (Join-Path $GcpScriptRoot "config\gcp.ps1")
. (Join-Path $GcpScriptRoot "_lib\tf-helper.ps1")

$env:GOOGLE_CLOUD_PROJECT = $GcpConfig.ProjectID
$env:GOOGLE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_CORE_PROJECT = $GcpConfig.ProjectID
$env:CLOUDSDK_COMPUTE_REGION = $GcpConfig.Region

function Invoke-FoundationEssentialApply {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Config
    )

    $Layer = "00-foundation"
    $LayerPath = Join-Path $Config.InfraRoot $Layer

    if (-not (Test-Path -LiteralPath $LayerPath -PathType Container)) {
        throw "Terraform layer not found: $LayerPath"
    }

    $Targets = @(
        "google_project_service.required",
        "google_project_service.vpcaccess",
        "google_compute_network.bookflow_vpc",
        "google_compute_subnetwork.bookflow_main",
        "google_compute_firewall.bookflow_internal",
        "google_compute_global_address.private_ip_alloc",
        "google_service_networking_connection.private_vpc_connection",
        "google_storage_bucket.staging",
        "google_storage_bucket.models",
        "google_bigquery_dataset.bookflow_dw",
        "google_bigquery_table.sales_fact",
        "google_bigquery_table.inventory",
        "google_bigquery_table.forecast_results",
        "google_bigquery_table.features",
        "google_bigquery_table.training_dataset",
        "google_bigquery_table.book_master"
    )

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
        $EssentialStateNames = $Targets | Where-Object { $_ -ne "google_project_service.required" }
        $MissingEssential = @(
            $EssentialStateNames | Where-Object {
                $Expected = $_
                -not ($ExistingState | Where-Object { $_ -eq $Expected -or $_.StartsWith("$Expected[") })
            }
        )

        if ($MissingEssential.Count -eq 0) {
            Write-Host "00-foundation essential resources already exist. Skipping targeted foundation apply."
            return
        }

        Write-Host "00-foundation is missing essential resources:"
        $MissingEssential | ForEach-Object { Write-Host " - $_" }
        Write-Host "Applying essential 00-foundation targets only. This intentionally excludes google_vpc_access_connector.bookflow."

        $TargetArgs = @()
        foreach ($Target in $Targets) {
            $TargetArgs += "-target=$Target"
        }

        terraform apply -auto-approve @TargetArgs

        if ($LASTEXITCODE -ne 0) {
            throw "targeted terraform apply failed for layer: $Layer"
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-FoundationVpcConnectorApply {
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
        if ($ExistingState -contains "google_vpc_access_connector.bookflow") {
            Write-Host "00-foundation VPC Access connector already exists."
            return
        }

        Write-Host "99-content requires google_vpc_access_connector.bookflow. Applying only that dependency now."

        terraform apply -auto-approve `
            -target="google_project_service.vpcaccess" `
            -target="google_compute_network.bookflow_vpc" `
            -target="google_vpc_access_connector.bookflow"

        if ($LASTEXITCODE -ne 0) {
            throw "targeted terraform apply failed for google_vpc_access_connector.bookflow"
        }
    }
    finally {
        Pop-Location
    }
}

Invoke-FoundationEssentialApply -Config $GcpConfig

Invoke-TerraformLayer -Config $GcpConfig -Layer "20-network-daily" -Action "apply"

Invoke-FoundationVpcConnectorApply -Config $GcpConfig

Invoke-TerraformLayer -Config $GcpConfig -Layer "99-content" -Action "apply"
