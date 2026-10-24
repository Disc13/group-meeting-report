param([Parameter(Mandatory=$true)][string]$ImageDirectory,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
$taskAdapter=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
 $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function AwaitResult($Operation,[Type]$ResultType) {
 $task=$taskAdapter.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
 $task.Wait()
 return $task.Result
}
$language=[Windows.Globalization.Language]::new('zh-Hans-CN')
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if (!$engine) {throw 'Windows Simplified Chinese OCR language pack is unavailable'}
$outputPath=[IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($outputPath) | Out-Null
$files=Get-ChildItem -LiteralPath $ImageDirectory -File | Where-Object Extension -In '.jpg','.png'
$all=@()
foreach($image in $files) {
 $target=Join-Path $outputPath ($image.BaseName+'.json')
 if(Test-Path -LiteralPath $target) {$all+=Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json;continue}
 $file=AwaitResult ([Windows.Storage.StorageFile]::GetFileFromPathAsync($image.FullName)) ([Windows.Storage.StorageFile])
 $stream=AwaitResult ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
 $decoder=AwaitResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
 $bitmap=AwaitResult ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
 $result=AwaitResult ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
 $record=[ordered]@{page_id=$image.BaseName;source=$image.FullName;engine='Windows.Media.Ocr zh-Hans-CN';text=$result.Text}
 $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $target -Encoding UTF8
 $all+=$record
 $bitmap.Dispose();$stream.Dispose()
 if($all.Count % 25 -eq 0){Write-Output ('OCR '+$all.Count+'/'+$files.Count)}
}
$all | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputPath 'all.json') -Encoding UTF8
Write-Output ('OCR complete: '+$all.Count)
