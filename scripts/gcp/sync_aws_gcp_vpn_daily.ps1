<#
.SYNOPSIS
  Daily AWS <-> GCP VPN sync for BOOKFLOW.

.DESCRIPTION
  This script is intended for the daily cost-saving workflow where AWS VPN/TGW
  resources are destroyed after hours and recreated in the morning while GCP
  VPN resources may also be recreated or kept.

  It does four things:
    1. Reads the GCP HA VPN gateway public IP from GCP.
    2. Deploys only the AWS Customer Gateway, TGW, and Site-to-Site VPN stacks.
       It intentionally does not deploy bookflow-60-tgw-vpc-routes because this
       project currently has existing VPC peering routes that can conflict.
    3. Reads the newly created AWS VPN tunnel outside IPs and inside BGP CIDRs.
    4. Updates infra/gcp/20-network-daily/terraform.tfvars, then optionally
       runs terraform init/plan/apply for GCP VPN resources.

  PSK values are read from terraform.tfvars and are never printed.

.EXAMPLE
  # Update AWS VPN, update GCP tfvars, show terraform plan only
  pwsh scripts/gcp/sync_aws_gcp_vpn_daily.ps1

.EXAMPLE
  # Full daily reconnect, including GCP terraform apply
  pwsh scripts/gcp/sync_aws_gcp_vpn_daily.ps1 -Apply

.EXAMPLE
  # Do not deploy AWS, only re-read current AWS VPN and update/apply GCP
  pwsh scripts/gcp/sync_aws_gcp_vpn_daily.ps1 -SkipAwsDeploy -Apply
#>

[CmdletBinding()]
param(
    [string] $ProjectId = "project-8ab6bf05-54d2-4f5d-b8d",
    [string] $GcpRegion = "asia-northeast1",
    [string] $AwsRegion = "ap-northeast-1",
    [string] $ProjectName = "bookflow",
    [string] $RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [switch] $Apply,
    [switch] $SkipAwsDeploy
)

$ErrorActionPreference = "Stop"

$AwsInfraDir = Join-Path $RepoRoot "infra\aws"
$GcpTfDir = Join-Path $RepoRoot "infra\gcp\20-network-daily"
$TfvarsPath = Join-Path $GcpTfDir "terraform.tfvars"

function Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Info($Message) {
    Write-Host "    $Message"
}

function Fail($Message) {
    throw $Message
}

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "Required command not found: $Name"
    }
}

function Invoke-Logged($File, [string[]] $ArgumentList, [switch] $AllowFailure) {
    Info "$File $($ArgumentList -join ' ')"
    & $File @ArgumentList
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        Fail "Command failed ($code): $File $($ArgumentList -join ' ')"
    }
    return $code
}

function Get-TfvarString($Name) {
    $text = Get-Content -LiteralPath $TfvarsPath -Raw
    $match = [regex]::Match($text, "(?m)^\s*$([regex]::Escape($Name))\s*=\s*""([^""]+)""")
    if (-not $match.Success) {
        Fail "Could not find string tfvar: $Name in $TfvarsPath"
    }
    return $match.Groups[1].Value
}

function Convert-CidrToInsideIps($Cidr) {
    $parts = $Cidr.Split("/")
    if ($parts.Count -ne 2 -or $parts[1] -ne "30") {
        Fail "Only /30 tunnel CIDR is supported, got: $Cidr"
    }

    $octets = $parts[0].Split(".") | ForEach-Object { [int] $_ }
    if ($octets.Count -ne 4) {
        Fail "Invalid IPv4 CIDR: $Cidr"
    }

    $base = [uint32](
        ([uint32]$octets[0] * 16777216) +
        ([uint32]$octets[1] * 65536) +
        ([uint32]$octets[2] * 256) +
        [uint32]$octets[3]
    )

    function To-Ip([uint32] $Value) {
        return "{0}.{1}.{2}.{3}" -f (($Value -shr 24) -band 255), (($Value -shr 16) -band 255), (($Value -shr 8) -band 255), ($Value -band 255)
    }

    return @{
        AwsPeerIp = To-Ip ($base + 1)
        GcpRouterCidr = "$(To-Ip ($base + 2))/30"
    }
}

function Get-GcpHaVpnIp() {
    $json = gcloud compute vpn-gateways describe bookflow-aws-ha-vpn `
        --project $ProjectId `
        --region $GcpRegion `
        --format json | ConvertFrom-Json

    if (-not $json.vpnInterfaces -or $json.vpnInterfaces.Count -lt 1) {
        Fail "GCP HA VPN gateway bookflow-aws-ha-vpn has no public interface IPs."
    }

    # Existing AWS template accepts a single Customer Gateway IP. Keep the
    # project convention and use GCP HA VPN interface 0.
    return ($json.vpnInterfaces | Where-Object { $_.id -eq 0 } | Select-Object -First 1).ipAddress
}

function Get-AwsVpnConnection() {
    $raw = aws ec2 describe-vpn-connections `
        --region $AwsRegion `
        --filters "Name=tag:Name,Values=$ProjectName-vpn-gcp" `
        --output json
    $data = $raw | ConvertFrom-Json
    if (-not $data.VpnConnections -or $data.VpnConnections.Count -lt 1) {
        Fail "No AWS VPN connection found with tag Name=$ProjectName-vpn-gcp"
    }
    return $data.VpnConnections[0]
}

function Deploy-AwsStack($StackName, $Template, [string[]] $Parameters) {
    $args = @(
        "cloudformation", "deploy",
        "--region", $AwsRegion,
        "--stack-name", $StackName,
        "--template-file", $Template,
        "--parameter-overrides"
    ) + $Parameters + @("--capabilities", "CAPABILITY_NAMED_IAM")
    Invoke-Logged "aws" $args | Out-Null
}

function Update-GcpTfvars($TunnelRows, $Psk) {
    $text = Get-Content -LiteralPath $TfvarsPath -Raw

    $awsPeerBlock = @"
aws_peer_ips = [
  "$($TunnelRows[0].OutsideIp)",
  "$($TunnelRows[1].OutsideIp)"
]
"@
    $text = [regex]::Replace(
        $text,
        '(?s)aws_peer_ips\s*=\s*\[\s*".*?"\s*,\s*".*?"\s*\]',
        [System.Text.RegularExpressions.MatchEvaluator] { param($m) $awsPeerBlock }
    )

    for ($i = 0; $i -lt 2; $i++) {
        $name = "tunnel$i"
        $row = $TunnelRows[$i]
        $pattern = "(?s)($name\s*=\s*\{.*?router_ip_cidr\s*=\s*)"".*?""(.*?peer_ip_address\s*=\s*)"".*?""(.*?shared_secret\s*=\s*)"".*?""(.*?\})"
        $text = [regex]::Replace(
            $text,
            $pattern,
            [System.Text.RegularExpressions.MatchEvaluator] {
                param($m)
                return $m.Groups[1].Value + '"' + $row.GcpRouterCidr + '"' +
                    $m.Groups[2].Value + '"' + $row.AwsPeerIp + '"' +
                    $m.Groups[3].Value + '"' + $Psk + '"' +
                    $m.Groups[4].Value
            }
        )
    }

    Copy-Item -LiteralPath $TfvarsPath -Destination "$TfvarsPath.bak" -Force
    Set-Content -LiteralPath $TfvarsPath -Value $text -Encoding UTF8
    Info "Updated terraform.tfvars (backup: terraform.tfvars.bak). PSK values were not printed."
}

function Show-Status() {
    Step "AWS VPN tunnel status"
    aws ec2 describe-vpn-connections `
        --region $AwsRegion `
        --filters "Name=tag:Name,Values=$ProjectName-vpn-gcp" `
        --query "VpnConnections[*].VgwTelemetry[*].{OutsideIp:OutsideIpAddress,Status:Status,Message:StatusMessage}" `
        --output table

    Step "GCP VPN tunnel status"
    gcloud compute vpn-tunnels list `
        --project $ProjectId `
        --format "table(name,region,status,peerIp)"

    Step "GCP Cloud Router BGP status"
    gcloud compute routers get-status bookflow-aws-cr `
        --project $ProjectId `
        --region $GcpRegion `
        --format "table(result.bgpPeerStatus[].name,result.bgpPeerStatus[].status,result.bgpPeerStatus[].ipAddress,result.bgpPeerStatus[].peerIpAddress)"
}

Require-Command aws
Require-Command gcloud
Require-Command terraform

if (-not (Test-Path -LiteralPath $TfvarsPath)) {
    Fail "terraform.tfvars not found: $TfvarsPath"
}

Step "Read GCP HA VPN and local PSK"
$gcpHaVpnIp = Get-GcpHaVpnIp
$psk = Get-TfvarString "vpn_shared_secret"
Info "GCP HA VPN interface 0 IP: $gcpHaVpnIp"
Info "PSK loaded from terraform.tfvars: ***"

if (-not $SkipAwsDeploy) {
    Step "Deploy AWS Customer Gateway"
    Deploy-AwsStack `
        -StackName "$ProjectName-10-customer-gateway" `
        -Template (Join-Path $AwsInfraDir "10-network-core\customer-gateway.yaml") `
        -Parameters @("ProjectName=$ProjectName", "GcpHaVpnIp=$gcpHaVpnIp")

    Step "Deploy AWS Transit Gateway"
    Deploy-AwsStack `
        -StackName "$ProjectName-60-tgw" `
        -Template (Join-Path $AwsInfraDir "60-network-cross-cloud\tgw.yaml") `
        -Parameters @("ProjectName=$ProjectName")

    Step "Deploy AWS Site-to-Site VPN for GCP"
    Deploy-AwsStack `
        -StackName "$ProjectName-60-vpn-site-to-site" `
        -Template (Join-Path $AwsInfraDir "60-network-cross-cloud\vpn-site-to-site.yaml") `
        -Parameters @("ProjectName=$ProjectName", "EnableGcpVpn=true", "GcpPresharedKey=$psk")
} else {
    Step "Skip AWS deploy"
}

Step "Read newly created AWS VPN tunnel data"
$vpn = Get-AwsVpnConnection
$tunnels = @()
foreach ($opt in $vpn.Options.TunnelOptions) {
    $inside = Convert-CidrToInsideIps $opt.TunnelInsideCidr
    $tunnels += [pscustomobject]@{
        OutsideIp = $opt.OutsideIpAddress
        TunnelInsideCidr = $opt.TunnelInsideCidr
        AwsPeerIp = $inside.AwsPeerIp
        GcpRouterCidr = $inside.GcpRouterCidr
    }
}

if ($tunnels.Count -ne 2) {
    Fail "Expected exactly 2 AWS VPN tunnels, got $($tunnels.Count)."
}

$tunnels = $tunnels | Sort-Object @{
    Expression = {
        if ($_.TunnelInsideCidr -eq "169.254.213.136/30") { 0 }
        elseif ($_.TunnelInsideCidr -eq "169.254.100.72/30") { 1 }
        else { 99 }
    }
}
Info "AWS tunnel outside IPs discovered:"
foreach ($t in $tunnels) {
    Info "  outside=$($t.OutsideIp), inside=$($t.TunnelInsideCidr), bgpPeer=$($t.AwsPeerIp)"
}

Step "Update GCP Terraform variables"
Update-GcpTfvars $tunnels $psk

Step "Run GCP Terraform"
Push-Location $GcpTfDir
try {
    Invoke-Logged "terraform" @("init", "-input=false") | Out-Null
    Invoke-Logged "terraform" @("plan", "-input=false", "-no-color") | Out-Null
    if ($Apply) {
        Invoke-Logged "terraform" @("apply", "-input=false", "-auto-approve", "-no-color") | Out-Null
    } else {
        Info "Apply not requested. Re-run with -Apply to update GCP VPN tunnels."
    }
}
finally {
    Pop-Location
}

Show-Status
