function Invoke-TerraformLayer {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Config,

        [Parameter(Mandatory = $true)]
        [string] $Layer,

        [ValidateSet("apply", "destroy")]
        [string] $Action = "apply"
    )

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

        terraform $Action -auto-approve

        if ($LASTEXITCODE -ne 0) {
            throw "terraform $Action failed for layer: $Layer"
        }
    }
    finally {
        Pop-Location
    }
}
