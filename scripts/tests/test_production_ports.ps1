$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../production_ports.ps1')

function Expect-PortFailure($Mapping, $Bindings = @(), $Message = 'cannot be bound', $TargetPort = 2889) {
    $Failure = $null
    try { Assert-ProductionPort -Ports @($Mapping) -RunningBindings $Bindings -TargetPort $TargetPort }
    catch { $Failure = $_.Exception.Message }
    if (!$Failure -or !$Failure.Contains($Message)) { throw "Expected '$Message', got '$Failure'" }
}

# An unrelated listener must block startup, but an existing matching backend
# publication must allow idempotent startup without trying to steal its port.
$Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$Listener.Server.ExclusiveAddressUse = $true
try {
    $Listener.Start()
    $Port = $Listener.LocalEndpoint.Port
    $Mapping = [pscustomobject]@{ protocol = 'tcp'; target = 2889; host_ip = '127.0.0.1'; published = "$Port" }
    Expect-PortFailure $Mapping
    Assert-ProductionPort -Ports @($Mapping) -RunningBindings @([pscustomobject]@{ HostIp = '127.0.0.1'; HostPort = "$Port" })
    Expect-PortFailure $Mapping @([pscustomobject]@{ HostIp = '127.0.0.2'; HostPort = "$Port" })
    Expect-PortFailure $Mapping @([pscustomobject]@{ HostIp = '127.0.0.1'; HostPort = '0' })
    $Mapping.target = 80
    Expect-PortFailure $Mapping @() 'cannot be bound' 80
    Assert-ProductionPort -Ports @($Mapping) -TargetPort 80 -RunningBindings @([pscustomobject]@{ HostIp = '127.0.0.1'; HostPort = "$Port" })
    $Mapping.target = 2889
} finally { $Listener.Stop() }
Assert-ProductionPort -Ports @($Mapping)
# The successful probe must release its listener immediately.
Assert-ProductionPort -Ports @($Mapping)
foreach ($Invalid in @('0', '65536', 'not-a-port')) {
    $Mapping.published = $Invalid
    Expect-PortFailure $Mapping @() 'between 1 and 65535'
}
Write-Output 'Production port tests passed: occupied, available, released, owned, mismatched and invalid endpoints.'
