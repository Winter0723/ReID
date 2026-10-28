# Probe the resolved Compose endpoint before starting cameras or containers.
function Assert-ProductionPort {
    param([object[]]$Ports, [object[]]$RunningBindings = @(), [int]$TargetPort = 2889, [string]$ServiceName = 'Backend')
    foreach ($Mapping in $Ports) {
        if ($Mapping.protocol -ne 'tcp' -or [int]$Mapping.target -ne $TargetPort) { continue }
        $Port = 0
        if (![int]::TryParse([string]$Mapping.published, [ref]$Port) -or $Port -lt 1 -or $Port -gt 65535) {
            throw "$ServiceName published port must be between 1 and 65535."
        }
        $Address = if ($Mapping.host_ip) { [string]$Mapping.host_ip } else { '0.0.0.0' }
        # A repeated start is valid when this project's running backend owns it.
        $Owned = @($RunningBindings | Where-Object { $_.HostIp -eq $Address -and [int]$_.HostPort -eq $Port })
        if ($Owned.Count) { continue }
        $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse($Address), $Port)
        try {
            $Listener.Server.ExclusiveAddressUse = $true
            $Listener.Start()
        } catch {
            throw "$ServiceName endpoint ${Address}:$Port cannot be bound (occupied or reserved by Windows). Check the listening process and 'netsh interface ipv4 show excludedportrange protocol=tcp'. Release the configured port and retry; normal ports are fixed at API 2889, staff 5173, visitor 5174. Stop the corresponding development server before starting its Docker service. Do not select another port. No services were started by this run."
        } finally {
            $Listener.Stop()
        }
    }
}
