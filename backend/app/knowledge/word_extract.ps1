param([Parameter(Mandatory=$true)][string]$InputDocument, [switch]$Structure)
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
    if ($Structure) {
        $records = [System.Collections.Generic.List[object]]::new()
        $tableEnd = -1
        $paragraphIndex = 0
        foreach ($paragraph in $readDocument.Paragraphs) {
            $range = $paragraph.Range
            if ($range.Start -lt $tableEnd) { $paragraphIndex++; continue }
            $record = @{
                text = $range.Text.TrimEnd([char]13, [char]7)
                paragraph = $paragraphIndex
                offset_start = $range.Start
                offset_end = $range.End
                physical_page = $range.Information(3)
                page_end = $range.Information(3)
                style_name = [string]$paragraph.Style.NameLocal
                outline_level = [int]$paragraph.OutlineLevel - 1
                block_type = 'paragraph'
                font_size = [double]$range.Font.Size
                bold = [int]$range.Font.Bold -eq -1
                alignment = [int]$paragraph.Alignment
                space_before = [double]$paragraph.SpaceBefore
                page_break = [int]$paragraph.PageBreakBefore -eq -1
                bookmark_names = @($range.Bookmarks | ForEach-Object { $_.Name })
            }
            $pageStartRange = $range.Duplicate
            $pageStartRange.Collapse(1)
            $record.physical_page = $pageStartRange.Information(3)
            if ($range.Tables.Count -gt 0) {
                $table = $range.Tables.Item(1)
                $tableEnd = $table.Range.End
                $rows = [System.Collections.Generic.List[object]]::new()
                foreach ($row in $table.Rows) {
                    $cells = @($row.Cells | ForEach-Object { $_.Range.Text.TrimEnd([char]13, [char]7) })
                    $rows.Add(@($cells))
                }
                $record.block_type = 'table'
                $record.table_structure = @($rows.ToArray())
                $record.text = $table.Range.Text.TrimEnd([char]13, [char]7)
                $record.offset_end = $table.Range.End
                $record.paragraph_end = $paragraphIndex + $table.Range.Paragraphs.Count - 1
            } elseif ($paragraph.OutlineLevel -lt 10) {
                $record.heading_level = [int]$paragraph.OutlineLevel
                $record.detection_source = 'outline_level'
                $record.confidence = 1.0
                $record.block_type = 'heading'
            } elseif ($record.style_name -match '^(Heading|\u6807\u9898)\s*(\d+)$') {
                $record.heading_level = [int]$Matches[2]
                $record.detection_source = 'word_style'
                $record.confidence = 1.0
                $record.block_type = 'heading'
            } elseif ($range.ListFormat.ListType -ne 0) {
                $record.block_type = 'list'
                $record.numbering = [string]$range.ListFormat.ListString
            } elseif ($record.style_name -match '^(TOC|\u76ee\u5f55)\s*(\d+)$') {
                $record.heading_level = [int]$Matches[2]
                $record.detection_source = 'toc'
                $record.confidence = 0.65
            }
            $records.Add($record)
            $paragraphIndex++
        }
        ConvertTo-Json -InputObject @($records.ToArray()) -Depth 8 -Compress
        return
    }
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
