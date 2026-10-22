<#
.SYNOPSIS
安装官方 whisper.cpp Windows CPU x64 基线；默认仅显示用法，不下载模型或 CLI。
.DESCRIPTION
在项目 tools/whisper 下安装，模型仅在显式 -DownloadModel 时下载至 models。
模型为多语言版本：base 约 148 MB，small 约 488 MB，medium 约 1.53 GB。
这只是 CPU 基线，不代表 AMD GPU、880M 或另一台 RX 9070 已完成加速验证。
不读取令牌，不覆盖已有文件，不执行远程脚本。官方 API 匿名限流时仅允许已固定校验值的 b5454。
.EXAMPLE
powershell -NoProfile -File .\tools\setup-asr.ps1 -InstallCli
.EXAMPLE
powershell -NoProfile -File .\tools\setup-asr.ps1 -DownloadModel -Model small
#>
[CmdletBinding()]
param(
    [ValidateSet('base', 'small', 'medium')][string]$Model = 'small',
    [ValidatePattern('^(v\d+\.\d+\.\d+|b\d+)$')][string]$ReleaseTag = 'b5454',
    [switch]$DownloadModel,
    [switch]$InstallCli
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.IO.Compression.FileSystem
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$toolsRoot = Join-Path $projectRoot 'tools'
$modelsRoot = Join-Path $projectRoot 'models'
$installRoot = Join-Path $toolsRoot 'whisper'
$assetName = 'whisper-bin-x64.zip'
$pinnedDigest = '6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb'
$httpHandler = [Net.Http.HttpClientHandler]::new()
$httpHandler.AllowAutoRedirect = $false
$httpHandler.UseProxy = $false
$httpClient = [Net.Http.HttpClient]::new($httpHandler)
$httpClient.Timeout = [TimeSpan]::FromHours(2)
$httpClient.DefaultRequestHeaders.UserAgent.ParseAdd('yutoAvNight-local-setup/0.1')

function Assert-ProjectPath([string]$Target) {
    $full = [IO.Path]::GetFullPath($Target)
    if (-not $full.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw '安装目标超出项目工作空间。'
    }
    $current = $full
    while ($current -and $current.Length -ge $projectRoot.Length) {
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '安装路径包含链接或重定向目录，已停止。'
        }
        $current = Split-Path -Parent $current
    }
}

function Get-CheckedResponse([string]$Url) {
    $uri = [Uri]$Url
    for ($redirect = 0; $redirect -le 8; $redirect++) {
        $hostName = $uri.DnsSafeHost.ToLowerInvariant()
        $allowed = $hostName -in @('api.github.com', 'github.com', 'release-assets.githubusercontent.com',
            'objects.githubusercontent.com', 'huggingface.co', 'cas-bridge.xethub.hf.co',
            'cdn-lfs.huggingface.co', 'cdn-lfs.hf.co', 'cdn-lfs-us-1.hf.co', 'cdn-lfs-eu-1.hf.co',
            'us.aws.cdn.hf.co', 'us.gcp.cdn.hf.co', 'cas-server.xethub.hf.co',
            'cas-server.xethub-eu.hf.co', 'transfer.xethub.hf.co', 'transfer.xethub-eu.hf.co')
        if (-not $allowed -or $uri.Scheme -ne 'https' -or $uri.UserInfo) {
            throw '下载地址不在官方 HTTPS 域名白名单内。'
        }
        $response = $httpClient.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $status = [int]$response.StatusCode
        if ($status -in @(301,302,303,307,308)) {
            $location = $response.Headers.Location
            if (-not $location) { $response.Dispose(); throw '官方服务返回无效重定向。' }
            $next = [Uri]::new($uri, $location)
            $response.Dispose()
            $uri = $next
            continue
        }
        if (-not $response.IsSuccessStatusCode) {
            $response.Dispose()
            throw "官方服务返回 HTTP $status；请稍后重试，不需要提供任何令牌。"
        }
        return $response
    }
    throw '官方服务重定向过多。'
}

function Get-OfficialJson([string]$Url) {
    $response = Get-CheckedResponse $Url
    try { return ($response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json) }
    finally { $response.Dispose() }
}

function Save-VerifiedDownload([string]$Url, [string]$Destination, [string]$Sha256) {
    Assert-ProjectPath $Destination
    if (Test-Path -LiteralPath $Destination) { throw '目标文件已存在；不会覆盖，请人工检查已有文件。' }
    if ($Sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw '缺少可信 SHA256，已停止下载。' }
    $part = $Destination + '.part'
    Assert-ProjectPath $part
    if (Test-Path -LiteralPath $part) { throw '存在上次未完成的 .part 文件，请先人工检查处理。' }
    $response = $null
    $file = $null
    $inputStream = $null
    $createdPart = $false
    try {
        $response = Get-CheckedResponse $Url
        $file = [IO.File]::Open($part, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $createdPart = $true
        $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $buffer = New-Object byte[] 1048576
        $downloaded = 0L
        $progressClock = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $readTask = $inputStream.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $readTask.Wait(30000)) { throw '下载连续 30 秒无数据，已停止；请稍后重试。' }
            $count = $readTask.GetAwaiter().GetResult()
            if ($count -eq 0) { break }
            $file.Write($buffer, 0, $count)
            $downloaded += $count
            if ($progressClock.Elapsed.TotalSeconds -ge 5) {
                Write-Host ('已接收 ' + [Math]::Round($downloaded / 1MB, 1) + ' MiB…')
                $progressClock.Restart()
            }
        }
        $file.Dispose(); $file = $null
        $actual = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash
        if ($actual -ne $Sha256) { throw 'SHA256 校验不匹配，文件不会安装。' }
        [IO.File]::Move($part, $Destination)
    }
    finally {
        if ($file) { $file.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
        # Only clean the exact partial file created by this operation; never recursive-delete.
        if ($createdPart -and (Test-Path -LiteralPath $part)) { Remove-Item -LiteralPath $part -Force }
    }
}

function Install-WhisperCli {
    Assert-ProjectPath $toolsRoot
    New-Item -ItemType Directory -Path $toolsRoot -Force | Out-Null
    if (Test-Path -LiteralPath $installRoot) {
        throw 'tools\whisper 已存在；不会覆盖，请先确认现有安装。'
    }
    $assetUrl = "https://github.com/ggml-org/whisper.cpp/releases/download/$ReleaseTag/$assetName"
    $digest = $null
    $releaseInfo = $null
    try {
        $releaseInfo = Get-OfficialJson "https://api.github.com/repos/ggml-org/whisper.cpp/releases/tags/$ReleaseTag"
    }
    catch {
        if ($ReleaseTag -ne 'b5454') { throw '无法验证指定发布的官方元数据；请稍后重试。' }
        Write-Warning '匿名 API 当前不可用，使用已核实的官方 b5454 固定资产和 SHA256。'
    }
    if ($releaseInfo) {
        $asset = @($releaseInfo.assets | Where-Object { $_.name -eq $assetName })
        if ($asset.Count -ne 1 -or $asset[0].browser_download_url -ne $assetUrl) {
            throw '该发布没有指定的 Windows CPU x64 资产。'
        }
        if ($asset[0].digest -match '^sha256:([a-fA-F0-9]{64})$') { $digest = $Matches[1] }
        if ($ReleaseTag -eq 'b5454' -and $digest -and $digest -ne $pinnedDigest) {
            throw '固定发布摘要发生变化，已停止。'
        }
    }
    if ($ReleaseTag -eq 'b5454') { $digest = $pinnedDigest }
    if (-not $digest) { throw '官方 API 未提供该资产的 SHA256；不会执行未校验二进制。' }
    $archivePath = Join-Path $toolsRoot ("whisper-$ReleaseTag-cpu-x64.zip")
    Write-Host "下载并校验官方 whisper.cpp $ReleaseTag CPU x64 基线（约 9 MB）…"
    Save-VerifiedDownload $assetUrl $archivePath $digest
    Assert-ProjectPath $installRoot
    $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        foreach ($entry in $zip.Entries) {
            $entryPath = [IO.Path]::GetFullPath((Join-Path $installRoot $entry.FullName))
            if (-not $entryPath.StartsWith($installRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'ZIP 中含有越界路径，已停止。'
            }
        }
    }
    finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $installRoot)
    $cli = @(Get-ChildItem -LiteralPath $installRoot -Filter 'whisper-cli.exe' -File -Recurse)
    if ($cli.Count -ne 1) { throw '官方压缩包没有唯一的 whisper-cli.exe，请人工检查。' }
    & $cli[0].FullName --help
    if ($LASTEXITCODE -ne 0) { throw 'CLI 帮助检查失败，可能缺少系统运行库。' }
    Write-Host ('CPU CLI 已安装：' + $cli[0].FullName)
}

function Install-WhisperModel {
    Assert-ProjectPath $modelsRoot
    New-Item -ItemType Directory -Path $modelsRoot -Force | Out-Null
    $modelFile = "ggml-$Model.bin"
    $metadata = @(Get-OfficialJson 'https://huggingface.co/api/models/ggerganov/whisper.cpp/tree/main')
    $modelEntry = @($metadata | Where-Object { $_.path -eq $modelFile })
    if ($modelEntry.Count -ne 1 -or $modelEntry[0].lfs.oid -notmatch '^[a-fA-F0-9]{64}$') {
        throw '未能从官方模型元数据取得可信 SHA256。'
    }
    Write-Host ("下载多语言 $Model 模型，约 " + [Math]::Round($modelEntry[0].size / 1MB) + ' MiB…')
    Save-VerifiedDownload "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$modelFile" (Join-Path $modelsRoot $modelFile) $modelEntry[0].lfs.oid
    Write-Host ('模型已校验并保存：' + (Join-Path $modelsRoot $modelFile))
}

try {
    if (-not $InstallCli -and -not $DownloadModel) {
        Write-Host '默认不下载。当前 FFmpeg 若含 whisper 滤镜，可留空程序路径直接使用 CPU 基线；模型仍需下载。'
        Write-Host '只下载多语言模型：.\tools\setup-asr.ps1 -DownloadModel -Model small'
        Write-Host '可选安装独立 CPU CLI：.\tools\setup-asr.ps1 -InstallCli'
        return
    }
    if ($InstallCli) { Install-WhisperCli }
    if ($DownloadModel) { Install-WhisperModel }
}
finally { $httpClient.Dispose(); $httpHandler.Dispose() }
