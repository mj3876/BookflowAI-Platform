$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path

$GcpConfig = @{
    ProjectID   = "project-8ab6bf05-54d2-4f5d-b8d"
    Region      = "asia-northeast1"
    StateBucket = "bookflow-tf-state"
    InfraRoot   = Join-Path $RepoRoot "infra\gcp"
}
