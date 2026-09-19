param([ValidateSet('Scan','Close')][string]$Mode = 'Scan')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$scope = [System.Windows.Automation.TreeScope]::Descendants
function ById([string]$id) {
    [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,$id)
}
function IsPortal($window) {
    $address = $window.FindFirst($scope,(ById 'view_1021'))
    if ($null -eq $address) { return $false }
    $value = $address.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value.Trim()
    if ($value -notmatch '^https?://') { $value = 'http://' + $value }
    $url = $null
    if (-not [Uri]::TryCreate($value,[UriKind]::Absolute,[ref]$url)) { return $false }
    return ($url.Scheme -in @('http','https') -and $url.Host -eq '10.254.241.66' -and $url.Port -in @(80,443) -and $url.AbsolutePath -match '^/portal/entry/pc/(authenticate|serviceSelection|success)(;|/|$)')
}
function SelectedTab($window) {
    foreach ($tab in $window.FindAll($scope,(ById 'view_24'))) {
        try {
            if ($tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected) { return $tab }
        } catch {}
    }
    return $null
}
$results = @()
if ($Mode -eq 'Scan') {
    $edgeIds = @(Get-Process msedge -ErrorAction SilentlyContinue | ForEach-Object Id)
    if ($edgeIds.Count -gt 0) {
        $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        foreach ($window in $windows) {
            try {
                if ($window.Current.ProcessId -notin $edgeIds -or -not (IsPortal $window)) { continue }
                $tab = SelectedTab $window
                if ($null -eq $tab) { continue }
                $results += @{handle=$window.Current.NativeWindowHandle;pid=$window.Current.ProcessId;runtime=($tab.GetRuntimeId() -join ',')}
            } catch {}
        }
    }
} else {
    $candidates = @([Console]::In.ReadToEnd() | ConvertFrom-Json)
    foreach ($candidate in $candidates) {
        try {
            $process = Get-Process -Id $candidate.pid -ErrorAction Stop
            if ($process.ProcessName -ne 'msedge') { continue }
            $window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]([long]$candidate.handle))
            if ($window.Current.ProcessId -ne $candidate.pid -or -not (IsPortal $window)) { continue }
            $tab = SelectedTab $window
            if ($null -eq $tab -or ($tab.GetRuntimeId() -join ',') -ne $candidate.runtime) { continue }
            $button = $tab.FindFirst($scope,(ById 'view_27'))
            if ($null -eq $button) { continue }
            # Check again immediately before invoking this specific tab's close button.
            if (-not (IsPortal $window)) { continue }
            if (-not $tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected) { continue }
            $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
            $results += $candidate
        } catch {}
    }
}
ConvertTo-Json -InputObject @($results) -Compress -Depth 4
