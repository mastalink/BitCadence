[CmdletBinding()]
param(
    [ValidateSet('Status', 'Connect', 'Stop')][string]$Action = 'Status',
    [string]$Profile = 'batoncadence'
)
$ErrorActionPreference = 'Stop'
$awsCommand = Get-Command aws -ErrorAction SilentlyContinue
$awsPath = if ($awsCommand) { $awsCommand.Source } else { 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $awsPath)) { throw 'Install AWS CLI v2 first.' }
function Invoke-LabAws {
    param([string[]]$Arguments)
    $result = & $awsPath @Arguments --profile $Profile --region us-east-1 --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw 'AWS command failed. Check your login and permissions.' }
    return ($result | ConvertFrom-Json)
}
$identity = Invoke-LabAws -Arguments @('sts', 'get-caller-identity')
if ($identity.Account -ne '314086896527') { throw 'Wrong AWS account. Expected the BatonCadence lab account 314086896527.' }
$inventory = Invoke-LabAws -Arguments @('ec2', 'describe-instances', '--filters', 'Name=tag:Project,Values=bitcadence-lab', 'Name=instance-state-name,Values=pending,running,stopping,stopped')
$nodes = @($inventory.Reservations | ForEach-Object { $_.Instances })
$summary = @($nodes | ForEach-Object {
    [pscustomobject]@{ Role = ($_.Tags | Where-Object Key -eq 'Role').Value; Instance = $_.InstanceId; State = $_.State.Name }
})
if ($Action -eq 'Status') { $summary | Format-Table -AutoSize; return }
if ($nodes.Count -ne 3 -or @($summary.Role | Sort-Object -Unique).Count -ne 3 -or @($summary | Where-Object { $_.Role -notin @('hub','worker','reviewer') }).Count) {
    throw 'Expected exactly one hub, worker and reviewer. Inspect Status before proceeding.'
}
if ($Action -eq 'Stop') {
    $runningIds = @($summary | Where-Object State -in @('running','pending') | ForEach-Object Instance)
    if ($runningIds.Count) {
        Invoke-LabAws -Arguments (@('ec2','stop-instances','--instance-ids') + $runningIds) | Out-Null
        Write-Host 'Stop requested. Waiting for all three lab machines to stop...'
    }
    Invoke-LabAws -Arguments (@('ec2','wait','instance-stopped','--instance-ids') + @($summary.Instance)) | Out-Null
    Write-Host 'All three lab machines are stopped. Retained storage and secrets still incur charges.'
    return
}
$hub = @($summary | Where-Object Role -eq 'hub')[0]
if ($hub.State -ne 'running') { throw 'Hub is not running. Deploy a fresh bounded session using the AWS private test lab workflow.' }
$pluginDir = Join-Path $env:LOCALAPPDATA 'BitCadence\tools'
if (Test-Path -LiteralPath (Join-Path $pluginDir 'session-manager-plugin.exe')) { $env:PATH = $pluginDir + ';' + $env:PATH }
if (-not (Get-Command session-manager-plugin -ErrorAction SilentlyContinue)) { throw 'Install the AWS Session Manager plugin first.' }
Write-Host 'Keep this tunnel open. Open http://127.0.0.1:18891/console in your browser.'
Write-Host 'Closing this tunnel does not stop the AWS machines.'
& $awsPath ssm start-session --profile $Profile --region us-east-1 --target $hub.Instance --document-name AWS-StartPortForwardingSession --parameters 'portNumber=18789,localPortNumber=18891'
if ($LASTEXITCODE -ne 0) { throw 'SSM tunnel failed. Check that the hub is online in Systems Manager.' }
