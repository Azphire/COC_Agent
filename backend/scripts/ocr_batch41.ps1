param([Parameter(Mandatory=$true)][string]$Directory)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrLine, Windows.Foundation, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-Result($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait()
    return $task.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'No installed Windows OCR language' }
$results = @()
foreach ($file in Get-ChildItem -LiteralPath $Directory -Filter 'page-*.png' | Sort-Object Name) {
    $storage = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($file.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await-Result ($storage.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $ocr = [Windows.Media.Ocr.OcrResult](Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]))
            $recognizedText = [string]$ocr.Text
            $lines = @($ocr.Lines | ForEach-Object { ([Windows.Media.Ocr.OcrLine]$_).Text })
            $results += @{ physical_page = [int]($file.BaseName -replace 'page-', ''); image = $file.Name;
                language = $engine.RecognizerLanguage.LanguageTag; text = $recognizedText;
                lines = $lines;
                reviewed = $false; method = 'Windows.Media.Ocr' }
            Write-Output ($file.Name + ' ' + $recognizedText.Length + ' pixels=' + $bitmap.PixelWidth + 'x' + $bitmap.PixelHeight)
        } finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
}
$destination = Join-Path $Directory 'ocr-raw.json'
[IO.File]::WriteAllText($destination, (ConvertTo-Json -InputObject $results -Depth 10), [Text.UTF8Encoding]::new($false))
