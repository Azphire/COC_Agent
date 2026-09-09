param([Parameter(Mandatory=$true)][string]$InputDocument)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$wordReader = $null
$readDocument = $null
$noSave = 0
$goPage = 1
$absolutePage = 1
try {
    $wordReader = New-Object -ComObject Word.Application
    $wordReader.Visible = $false
    $wordReader.DisplayAlerts = 0
    $wordReader.AutomationSecurity = 3
    $wordReader.Options.UpdateLinksAtOpen = $false
    $readDocument = $wordReader.Documents.Open($InputDocument, $false, $true, $false)
    $readDocument.Repaginate()
    $totalPages = $readDocument.ComputeStatistics(2)
    $pageRecords = @()
    for ($pageIndex = 1; $pageIndex -le $totalPages; $pageIndex++) {
        $pageStart = $readDocument.GoTo([ref]$goPage, [ref]$absolutePage, [ref]$pageIndex).Start
        $nextPage = $pageIndex + 1
        $pageEnd = if ($pageIndex -lt $totalPages) { $readDocument.GoTo([ref]$goPage, [ref]$absolutePage, [ref]$nextPage).Start } else { $readDocument.Content.End }
        $pageText = $readDocument.Range($pageStart, $pageEnd).Text
        $pageRecords += @{ physical_page = $pageIndex; page_label = $null; page_kind = 'word'; text = $pageText.Replace([string][char]13, "`n").Replace([string][char]7, ' ') }
    }
    ConvertTo-Json -InputObject @($pageRecords) -Depth 4 -Compress
} catch {
    [Console]::Error.WriteLine('word_extraction_failed_at_line_' + $_.InvocationInfo.ScriptLineNumber)
    exit 2
} finally {
    if ($readDocument) { $readDocument.Close([ref]$noSave); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($readDocument) }
    if ($wordReader) { $wordReader.Quit([ref]$noSave); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordReader) }
}
