# The board's own view of an overnight soak, logged from the serial console.
#
# Runs alongside scripts/ble_soak.py (which sees only the host side). The board
# prints every connect/disconnect with its HCI reason, the re-advertise backoff
# lines, and each measurement push — so a link that dies is visible from both
# ends and the two logs can be lined up afterwards.
#
# PowerShell rather than Python on purpose: pyserial is not a project dependency
# and a diagnostic is not a good reason to add one.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ble_soak_serial.ps1 -Hours 8
#   powershell -ExecutionPolicy Bypass -File scripts\ble_soak_serial.ps1 -Minutes 2 -Port COM10
#
# Ctrl-C stops early; the log is flushed line by line, so an interrupted or
# power-cut run still leaves everything captured up to that point.

param(
    [double]$Hours = 0,
    [double]$Minutes = 0,
    [string]$Port = "COM10",
    [int]$BaudRate = 115200,
    [string]$Out = ".scratch\soak"
)

$duration = $Hours * 3600 + $Minutes * 60
if ($duration -le 0) { $duration = 8 * 3600 }

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $Out "serial-$stamp.log"

Write-Host "Logging $Port at $BaudRate for $([math]::Round($duration/3600,2)) h -> $logPath"
Write-Host "(Ctrl-C to stop early.)"

$port = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, None, 8, one
$port.ReadTimeout = 2000
try {
    $port.Open()
} catch {
    Write-Host "Could not open ${Port}: $($_.Exception.Message)"
    Write-Host "Another reader (a serial monitor, or an earlier run) may still hold it."
    exit 1
}

$writer = [System.IO.StreamWriter]::new($logPath, $true)
$writer.AutoFlush = $true
$sw = [Diagnostics.Stopwatch]::StartNew()
$counts = @{ connected = 0; disconnected = 0; pushed = 0; backoff = 0 }

try {
    while ($sw.Elapsed.TotalSeconds -lt $duration) {
        try {
            $line = $port.ReadLine()
        } catch {
            continue  # read timeout: the board is simply quiet right now
        }
        $writer.WriteLine("$((Get-Date).ToString('o'))`t$line")

        if ($line -match '^Connected')        { $counts.connected++ }
        elseif ($line -match '^Disconnected') { $counts.disconnected++ }
        elseif ($line -match 'pushed slot')   { $counts.pushed++ }
        elseif ($line -match 'short link')    { $counts.backoff++ }
    }
} finally {
    $port.Close()
    $summary = @(
        "",
        "--- serial soak summary ---",
        "duration      : $([math]::Round($sw.Elapsed.TotalHours,2)) h",
        "connects      : $($counts.connected)",
        "disconnects   : $($counts.disconnected)",
        "backoff lines : $($counts.backoff)   (a central connecting and dropping fast)",
        "pushes        : $($counts.pushed)",
        "log           : $logPath"
    ) -join "`n"
    $writer.WriteLine($summary)
    $writer.Close()
    Write-Host $summary
}
