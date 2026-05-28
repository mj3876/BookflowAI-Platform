# RDS Multi-AZ Failover 시연 스크립트 (PowerShell · deploy 계정 354493396671)
#
# 사용:
#   .\rds-failover-test.ps1 check       — 현재 상태 확인 (Multi-AZ 활성, primary AZ)
#   .\rds-failover-test.ps1 baseline    — 연결 테스트 + 기준선 측정 (pod side 포함)
#   .\rds-failover-test.ps1 failover    — force-failover 트리거 + 이벤트 watch (~2~3분)
#   .\rds-failover-test.ps1 verify      — primary AZ 변경 확인 + pod 재연결 확인
#   .\rds-failover-test.ps1 all         — check → baseline → failover → verify 자동
#
# 전제조건:
#   - aws CLI v2 + kubectl 설치
#   - alpaco1 자격증명 적용 (aws sts get-caller-identity 가 354493396671 반환)
#   - bookflow-postgres MultiAZ=true (rds modify 후 ~15분 대기 완료)
#   - kubectl context = bookflow-eks
#
# 녹화 흐름:
#   - 터미널 좌측: .\rds-failover-test.ps1 failover (이벤트 + AZ + downtime)
#   - 터미널 우측: kubectl logs -n bookflow -l app=auth-pod -f (pod 연결 끊김/복구)
#   - 브라우저: Grafana Row 9 SCN-05 (RDS 연결 timeline · IOPS · CPU)
#   - 또는 AWS Console RDS dashboard

param(
    [Parameter(Position=0)]
    [ValidateSet("check","baseline","failover","verify","all","watch")]
    [string]$Action = "check"
)

$ErrorActionPreference = "Stop"
$env:Path = "C:\Program Files\Amazon\AWSCLIV2;" + $env:Path

$DB_ID = "bookflow-postgres"
$REGION = "ap-northeast-1"
$NS = "bookflow"
$POD_LABEL = "app=auth-pod"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host " $msg" -ForegroundColor Cyan
    Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
}

function Get-RdsState {
    aws rds describe-db-instances --db-instance-identifier $DB_ID --region $REGION `
      --query "DBInstances[0].{Status:DBInstanceStatus,MultiAZ:MultiAZ,AZ:AvailabilityZone,SecondaryAZ:SecondaryAvailabilityZone,Endpoint:Endpoint.Address}" `
      --output json | ConvertFrom-Json
}

function Get-RecentEvents($Minutes = 5) {
    aws rds describe-events --source-identifier $DB_ID --source-type db-instance `
      --duration $Minutes --region $REGION `
      --query "Events[].{Time:Date,Msg:Message}" --output table
}

function Do-Check {
    Write-Step "[CHECK] 현재 RDS 상태"
    $s = Get-RdsState
    Write-Host "  Instance        : $($s.Status)"
    Write-Host "  MultiAZ         : $($s.MultiAZ)" -ForegroundColor $(if ($s.MultiAZ -eq $true -or $s.MultiAZ -eq "True") { "Green" } else { "Red" })
    Write-Host "  Primary AZ      : $($s.AZ)"
    Write-Host "  Secondary AZ    : $($s.SecondaryAZ)"
    Write-Host "  Endpoint        : $($s.Endpoint)"

    if ($s.MultiAZ -ne $true -and $s.MultiAZ -ne "True") {
        Write-Host ""
        Write-Host "  ✗ MultiAZ 가 비활성. failover 불가." -ForegroundColor Red
        Write-Host "  먼저 실행:" -ForegroundColor Yellow
        Write-Host "    aws rds modify-db-instance --db-instance-identifier $DB_ID ``" -ForegroundColor Yellow
        Write-Host "      --multi-az --backup-retention-period 1 --apply-immediately" -ForegroundColor Yellow
        Write-Host "  완료 후 (~15-20분) 재실행." -ForegroundColor Yellow
        return $false
    }
    return $true
}

function Do-Baseline {
    Write-Step "[BASELINE] 연결 테스트 + pod 상태"

    Write-Host "  Pod 상태 (auth-pod):"
    kubectl get pods -n $NS -l $POD_LABEL -o wide
    Write-Host ""

    Write-Host "  최근 5분 RDS 이벤트:"
    Get-RecentEvents 5
    Write-Host ""

    $s = Get-RdsState
    Write-Host "  현재 primary AZ: $($s.AZ) · standby AZ: $($s.SecondaryAZ)"
    Write-Host ""
    Write-Host "  Pod 에서 RDS 연결 테스트 (auth-pod /health):"
    $podName = (kubectl get pods -n $NS -l $POD_LABEL -o jsonpath="{.items[0].metadata.name}")
    kubectl exec -n $NS $podName -- curl -s -o /dev/null -w "  /health HTTP %{http_code}  (latency %{time_total}s)`n" http://localhost:80/health
    Write-Host ""
    Write-Host "  ✓ baseline 측정 완료. 곧 failover 실행 → 이 시각 이후 30초~2분 동안 연결 단절."
}

function Do-Failover {
    Write-Step "[FAILOVER] force-failover 트리거"

    $before = Get-RdsState
    $primaryBefore = $before.AZ
    $secondaryBefore = $before.SecondaryAZ
    Write-Host "  전: primary=$primaryBefore · standby=$secondaryBefore"
    Write-Host ""

    # 트리거 시각 기록
    $triggerTime = Get-Date
    Write-Host "  $($triggerTime.ToString('HH:mm:ss')) → reboot-db-instance --force-failover" -ForegroundColor Yellow
    aws rds reboot-db-instance --db-instance-identifier $DB_ID --force-failover --region $REGION `
      --query "DBInstance.{Id:DBInstanceIdentifier,Status:DBInstanceStatus}" --output json
    Write-Host ""

    Write-Host "  이벤트 watch (60초 폴링 × 5회 = 5분):"
    $startTime = Get-Date
    $primaryChanged = $false
    $rebootStarted = $false
    $rebootCompleted = $false
    for ($i = 1; $i -le 30; $i++) {
        $elapsed = [int]((Get-Date) - $triggerTime).TotalSeconds
        $s = Get-RdsState
        $status = $s.Status
        $primary = $s.AZ

        $marker = ""
        if (-not $rebootStarted -and $status -ne "available") {
            $rebootStarted = $true
            $marker = " ← reboot 시작"
        }
        if ($rebootStarted -and -not $rebootCompleted -and $status -eq "available") {
            $rebootCompleted = $true
            $marker = " ← reboot 완료"
        }
        if (-not $primaryChanged -and $primary -ne $primaryBefore) {
            $primaryChanged = $true
            $marker += "  ✓ primary AZ 전환: $primaryBefore → $primary"
        }

        Write-Host ("  [+{0,3}s] status={1,-15} primary={2}{3}" -f $elapsed, $status, $primary, $marker)
        if ($primaryChanged -and $rebootCompleted -and $elapsed -gt 90) { break }
        Start-Sleep -Seconds 10
    }

    Write-Host ""
    Write-Host "  failover 이벤트:"
    Get-RecentEvents 5
}

function Do-Verify {
    Write-Step "[VERIFY] failover 후 상태 + pod 재연결"

    $s = Get-RdsState
    Write-Host "  새 primary AZ   : $($s.AZ)"
    Write-Host "  새 standby AZ   : $($s.SecondaryAZ)"
    Write-Host "  Status          : $($s.Status)"
    Write-Host ""

    Write-Host "  Pod 상태 (restart 발생했는지):"
    kubectl get pods -n $NS -l $POD_LABEL
    Write-Host ""

    Write-Host "  auth-pod /health 응답:"
    $podName = (kubectl get pods -n $NS -l $POD_LABEL -o jsonpath="{.items[0].metadata.name}")
    for ($i = 1; $i -le 5; $i++) {
        $code = kubectl exec -n $NS $podName -- curl -s -o /dev/null -w "%{http_code}" http://localhost:80/health
        $color = if ($code -eq "200") { "Green" } else { "Red" }
        Write-Host "    try $i : HTTP $code" -ForegroundColor $color
        Start-Sleep -Seconds 2
    }
    Write-Host ""

    Write-Host "  최근 10분 RDS 이벤트 (failover 전체 흐름):"
    Get-RecentEvents 10
}

function Do-Watch {
    Write-Step "[WATCH] RDS 상태 5초 간격 라이브"
    Write-Host "  Ctrl+C 로 중단" -ForegroundColor Yellow
    while ($true) {
        $s = Get-RdsState
        Write-Host ("[{0}] status={1} multiaz={2} primary={3} standby={4}" -f `
            (Get-Date -Format "HH:mm:ss"), $s.Status, $s.MultiAZ, $s.AZ, $s.SecondaryAZ)
        Start-Sleep -Seconds 5
    }
}

switch ($Action) {
    "check"    { Do-Check }
    "baseline" { if (Do-Check) { Do-Baseline } }
    "failover" { if (Do-Check) { Do-Baseline; Do-Failover; Do-Verify } }
    "verify"   { Do-Verify }
    "watch"    { Do-Watch }
    "all"      { if (Do-Check) { Do-Baseline; Do-Failover; Do-Verify } }
}
