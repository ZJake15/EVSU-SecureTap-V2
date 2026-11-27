<#
.SYNOPSIS
  Long-run ("soak") check for EVSU SecureTap: records the memory and CPU use
  of the backend, the gate monitor and the launcher every minute to a CSV.

.DESCRIPTION
  Start SecureTap and open the gate monitor first, then run this in a
  PowerShell window and leave both running for the whole test - a full shift
  (6-8 hours) with people passing now and then. Ctrl+C stops it early; the
  summary still prints.

  What a healthy run looks like:
  - each program's memory levels off within the first half hour and stays
    roughly flat, instead of climbing all day (a leak);
  - its process id (pid) never changes - a new pid means that program
    stopped and the launcher started it again (a crash);
  - no "not running" rows while the gate is meant to be open.

  Reads process information only - changes nothing. Needs nothing installed:
  plain Windows PowerShell 5.1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\soak-monitor.ps1
  8 hours, a sample a minute, CSV on the Desktop.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\soak-monitor.ps1 -Hours 2 -IntervalSeconds 30
#>
param(
    [double]$Hours = 8,
    [int]$IntervalSeconds = 60,
    [string]$OutFile = ""
)

if (-not $OutFile) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $OutFile = Join-Path $desktop ("securetap-soak-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".csv")
}

# How each SecureTap program shows up in Task Manager's command line.
$programs = @(
    @{ Name = "backend";      Match = "*manage.py*runserver*" },
    @{ Name = "gate-monitor"; Match = "* main.py*" },
    @{ Name = "launcher";     Match = "*launcher.py*" }
)

$utf8 = New-Object System.Text.UTF8Encoding $false
function Write-Row([string]$line) {
    [System.IO.File]::AppendAllText($OutFile, $line + "`r`n", $utf8)
}

$cores = [Environment]::ProcessorCount
$started = Get-Date
$end = $started.AddHours($Hours)
$lastCpu = @{}     # pid -> (cpu seconds, time)
$lastPid = @{}     # program -> pid last seen
$restarts = @{}    # program -> new pids seen after the first
foreach ($program in $programs) { $restarts[$program.Name] = 0 }

[System.IO.File]::WriteAllText($OutFile, "", $utf8)
Write-Row "time,program,pid,memory_mb,private_mb,cpu_percent,threads,handles,note"
Write-Host "Recording every $IntervalSeconds s until $($end.ToString('HH:mm')) to:"
Write-Host "  $OutFile"
Write-Host "Ctrl+C stops early (the summary still prints)."

try {
    while ((Get-Date) -lt $end) {
        $now = Get-Date
        $stamp = $now.ToString("yyyy-MM-dd HH:mm:ss")
        $all = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue)
        foreach ($program in $programs) {
            $name = $program.Name
            $match = @($all | Where-Object { $_.CommandLine -like $program.Match } |
                       Sort-Object WorkingSetSize -Descending)
            if ($match.Count -eq 0) {
                Write-Row "$stamp,$name,,,,,,,not running"
                continue
            }
            $procId = [int]$match[0].ProcessId
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($null -eq $proc) {
                Write-Row "$stamp,$name,$procId,,,,,,ended while being read"
                continue
            }
            $note = ""
            if ($lastPid.ContainsKey($name) -and $lastPid[$name] -ne $procId) {
                $restarts[$name] += 1
                $note = "NEW PID - it stopped and was started again"
            }
            $lastPid[$name] = $procId

            $cpuSeconds = $proc.TotalProcessorTime.TotalSeconds
            $cpuPercent = ""
            if ($lastCpu.ContainsKey($procId)) {
                $previous = $lastCpu[$procId]
                $elapsed = ($now - $previous[1]).TotalSeconds
                if ($elapsed -gt 0) {
                    $cpuPercent = [math]::Round(100 * ($cpuSeconds - $previous[0]) / $elapsed / $cores, 1)
                }
            }
            $lastCpu[$procId] = @($cpuSeconds, $now)

            $memory = [math]::Round($proc.WorkingSet64 / 1MB, 1)
            $private = [math]::Round($proc.PrivateMemorySize64 / 1MB, 1)
            Write-Row "$stamp,$name,$procId,$memory,$private,$cpuPercent,$($proc.Threads.Count),$($proc.HandleCount),$note"
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}
finally {
    Write-Host ""
    Write-Host "Summary ($([math]::Round(((Get-Date) - $started).TotalMinutes)) minutes):"
    $rows = @(Import-Csv $OutFile)
    foreach ($program in $programs) {
        $name = $program.Name
        $mine = @($rows | Where-Object { $_.program -eq $name })
        $measured = @($mine | Where-Object { $_.memory_mb -ne "" })
        $missing = @($mine | Where-Object { $_.note -eq "not running" }).Count
        if ($measured.Count -eq 0) {
            Write-Host ("  {0,-13} never seen running" -f $name)
            continue
        }
        $memory = @($measured | ForEach-Object { [double]$_.memory_mb })
        $peak = ($memory | Measure-Object -Maximum).Maximum
        # From the 30-minute mark (models loaded, caches warm) to the end - a
        # leak shows as steady growth here.
        $settledAt = $started.AddMinutes(30)
        $settled = @($measured | Where-Object { [datetime]$_.time -ge $settledAt })
        $growth = ""
        if ($settled.Count -ge 2) {
            $growth = "; grew {0:+0.0;-0.0;0} MB after the first 30 min" -f
                ([double]$settled[-1].memory_mb - [double]$settled[0].memory_mb)
        }
        Write-Host ("  {0,-13} memory {1} -> {2} MB (peak {3}){4}; restarts {5}; not running {6} of {7} samples" -f
            $name, $memory[0], $memory[-1], $peak, $growth, $restarts[$name], $missing, $mine.Count)
    }
    Write-Host ""
    Write-Host "Healthy: memory flat after the first 30 minutes, 0 restarts, 0 'not running'."
    Write-Host "Full data: $OutFile"
}
