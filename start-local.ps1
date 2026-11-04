#Requires -Version 5.1
<#
    start-local.ps1 —— 本地一键启动（Windows 原生，不依赖 Docker）

    用法：在项目目录双击 start.bat（推荐，普通权限即可，不需要改全局执行策略）。
    也可以手动执行，效果与 start.bat 相同，只影响这一个进程：
        powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1

    它会做四件事：
      1. 检查 uv / npm 是否安装、.env 是否存在、MySQL 是否在跑；
      2. 新开一个窗口，先 `uv run alembic upgrade head` 再 `uv run uvicorn app.main:app --reload`；
      3. 新开一个窗口，在 frontend 目录跑 `npm run dev`；
      4. 等两个服务真的能响应之后，用默认浏览器打开 http://localhost:5173
         （比固定 sleep 更可靠：迁移慢的时候不会打开一个空白页）。

    停止服务：直接关掉那两个窗口。
#>

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# 子进程（含 alembic）的工作目录与依赖读取都按项目根目录算。
$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$BackendPort = 8000
$FrontendPort = 5173

# 中文提示在 Windows PowerShell 5.1 的控制台上容易被按 GBK 渲染成乱码。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step { param([string]$Text) Write-Host "[*] $Text" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "[+] $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "[!] $Text" -ForegroundColor Yellow }
function Write-Bad { param([string]$Text) Write-Host "[x] $Text" -ForegroundColor Red }

# 双击 start.bat 或右键「使用 PowerShell 运行」时，窗口会在脚本退出后立刻消失，
# 所以所有失败出口都先按提示打印原因，再等一次回车（start.bat 里还有一层 pause）。
function Stop-WithError {
    param([string]$Message, [string]$Hint)
    Write-Host ''
    Write-Bad $Message
    Write-Host ''
    if ($Hint) { Write-Host $Hint -ForegroundColor Yellow }
    Write-Host ''
    Read-Host '按回车键退出' | Out-Null
    exit 1
}

function Test-PortOpen {
    param([string]$ComputerName, [int]$Port, [int]$TimeoutMs = 2000)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $waited = $client.ConnectAsync($ComputerName, $Port).Wait($TimeoutMs)
        return ($waited -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-ForUrl {
    param([string]$Url, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) { return $true }
        } catch {
            # 服务还没起来时是连接拒绝，属于正常等待状态，不打印噪音。
        }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

# 子窗口用 -EncodedCommand 启动：命令文本按 UTF-16 Base64 传进去，
# 完全绕开 Windows 命令行引号/换行/编码的坑（项目路径带空格也不受影响）。
function Start-ToolWindow {
    param([string]$Title, [string]$ScriptText)
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($ScriptText))
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $ProjectRoot `
        -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) | Out-Null
    Write-Ok "已打开${Title}窗口。"
}

Write-Host ''
Write-Host '==================================================' -ForegroundColor Cyan
Write-Host '        AI 简历平台 · 本地一键启动' -ForegroundColor Cyan
Write-Host '==================================================' -ForegroundColor Cyan
Write-Host "项目目录：$ProjectRoot"
Write-Host ''

# ---------- 1. 前置检查：命令、配置文件、数据库 ----------

Write-Step '正在检查本机是否装了 uv 和 npm ...'
$missing = @()
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { $missing += 'uv' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { $missing += 'npm' }
if ($missing.Count -gt 0) {
    Stop-WithError -Message ('本机缺少命令：' + ($missing -join '、') + '。') -Hint @'
安装方法：
  uv      ：在 PowerShell 里执行  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  Node.js ：到 https://nodejs.org/zh-cn 下载 LTS 版安装（装完自带 npm）
装好后请重新打开一个 PowerShell 窗口，再运行本脚本。
'@
}
Write-Ok 'uv 和 npm 都已安装。'

$envFile = Join-Path $ProjectRoot '.env'
Write-Step "正在读取配置文件 .env ..."
if (-not (Test-Path -LiteralPath $envFile)) {
    Stop-WithError -Message '项目根目录没有 .env 文件。' -Hint @'
请在项目目录执行下面这条命令，然后按你的 MySQL 实际情况修改 DATABASE_URL 等字段：
    Copy-Item .env.example .env
'@
}
Write-Ok '.env 已找到。'

# alembic/env.py 只读进程环境变量，不读 .env，所以这里必须把 DATABASE_URL 导出，
# 否则第一条业务命令 `alembic upgrade head` 就会抛 RuntimeError 直接失败。
$dbUrl = $null
foreach ($line in (Get-Content -LiteralPath $envFile)) {
    $text = $line.Trim()
    if ($text.StartsWith('#')) { continue }
    $separator = $text.IndexOf('=')          # 连接串里 charset=utf8mb4 也带 =，只能按第一个 = 切
    if ($separator -lt 1) { continue }
    if ($text.Substring(0, $separator).Trim().ToUpper() -ne 'DATABASE_URL') { continue }
    $dbUrl = $text.Substring($separator + 1).Trim().Trim('"', "'")
    break
}
if (-not $dbUrl) {
    Stop-WithError -Message '.env 里没有配置 DATABASE_URL。' -Hint @'
请给 .env 加一行，形如：
    DATABASE_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/你的库名?charset=utf8mb4
'@
}
$env:DATABASE_URL = $dbUrl

# 从连接串里取出 host / port / 库名，只用于「检查哪个端口」和打印提示，不回显密码。
$dbHost = '127.0.0.1'
$dbPort = 3306
$dbName = ''
$authority = ((($dbUrl -replace '^[^:]+://', '') -split '[/?]')[0] -split '@')[-1]
if ($authority -match '^(?<h>[^:]+):(?<p>\d+)$') {
    $dbHost = $Matches.h
    $dbPort = [int]$Matches.p
} elseif ($authority) {
    $dbHost = $authority
}
$pathPart = (($dbUrl -split '[?]')[0] -split '/')
if ($pathPart.Count -gt 0) { $dbName = $pathPart[-1] }
Write-Ok ('DATABASE_URL 已导出给子进程（库名：{0}）' -f $dbName)

Write-Step "正在检查 MySQL 是否在 ${dbHost}:${dbPort} 上运行 ..."
if (Test-PortOpen -ComputerName $dbHost -Port $dbPort) {
    Write-Ok "MySQL ${dbHost}:${dbPort} 可以连接。"
} else {
    Stop-WithError -Message "MySQL 没有在 ${dbHost}:${dbPort} 上运行。" -Hint @'
请先启动 MySQL，再重新运行本脚本：
  方法一：按 Win+R 输入 services.msc 回车，找到 MySQL 服务（如 MySQL80）→ 右键「启动」
  方法二：在管理员 PowerShell 里执行  net start MySQL80
如果你的 MySQL 不在本机或用了别的端口，请改 .env 里的 DATABASE_URL 后重跑。
'@
}

Write-Step '正在检查 8000 / 5173 端口是否已被占用 ...'
if (Test-PortOpen -ComputerName '127.0.0.1' -Port $BackendPort -TimeoutMs 600) {
    Write-Warn "端口 $BackendPort 已经有程序在监听：可能是上一次没关掉的后端窗口。脚本仍会继续，但请先确认旧窗口状态。"
}
if (Test-PortOpen -ComputerName '127.0.0.1' -Port $FrontendPort -TimeoutMs 600) {
    Write-Warn "端口 $FrontendPort 已经有程序在监听：可能是上一次没关掉的前端窗口。"
}

# ---------- 2. 后端窗口：先迁移，再启动 API ----------

Write-Step '正在启动后端（新窗口，先同步数据库结构，再起 API 服务）...'
$backendScript = @'
Write-Host '后端窗口：正在同步数据库结构（alembic upgrade head）...' -ForegroundColor Cyan
Set-Location -LiteralPath $env:PROJECT_ROOT
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Host '数据库迁移失败，后端没有启动。请查看上面的报错（多为 MySQL 账号/库名不对），修好后关闭本窗口重跑 start-local.ps1。' -ForegroundColor Red
    Read-Host '按回车键关闭本窗口' | Out-Null
    exit 1
}
Write-Host '后端窗口：数据库结构已同步，正在启动 API 服务（http://127.0.0.1:8000）...' -ForegroundColor Cyan
uv run uvicorn app.main:app --reload
Write-Host '后端进程已退出。' -ForegroundColor Yellow
Read-Host '按回车键关闭本窗口' | Out-Null
'@

# ---------- 3. 前端窗口：npm run dev ----------

Write-Step '正在启动前端（新窗口，Vite 开发服务器）...'
$frontendScript = @'
Write-Host '前端窗口：正在启动 Vite 开发服务器...' -ForegroundColor Cyan
Set-Location -LiteralPath (Join-Path $env:PROJECT_ROOT 'frontend')
if (-not (Test-Path -LiteralPath (Join-Path (Get-Location).Path 'node_modules'))) {
    Write-Host '前端窗口：首次运行，正在安装依赖（npm install），可能需要几分钟...' -ForegroundColor Yellow
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host '依赖安装失败，前端没有启动。请检查 Node.js 是否安装、网络是否可用。' -ForegroundColor Red
        Read-Host '按回车键关闭本窗口' | Out-Null
        exit 1
    }
}
npm run dev
Write-Host '前端进程已退出。' -ForegroundColor Yellow
Read-Host '按回车键关闭本窗口' | Out-Null
'@

# Start-Process 启动的子进程继承当前进程的环境变量块，
# 所以这里赋的值就是子窗口里 alembic 实际读到的值。
$env:PROJECT_ROOT = $ProjectRoot
Start-ToolWindow -Title '后端' -ScriptText $backendScript
Start-ToolWindow -Title '前端' -ScriptText $frontendScript

# ---------- 4. 等两个服务真的起来，再打开浏览器 ----------

Write-Host ''
Write-Step '正在等待后端就绪（首次运行会先装 Python 依赖，最长等 3 分钟）...'
$backendReady = Wait-ForUrl -Url "http://127.0.0.1:$BackendPort/api/v1/health" -TimeoutSeconds 180
if ($backendReady) {
    Write-Ok "后端已就绪：http://127.0.0.1:$BackendPort"
} else {
    Write-Warn '后端 3 分钟内没有响应，请打开「后端」窗口看报错（数据库连不上是最常见原因）。'
}

Write-Step '正在等待前端就绪（最长等 1 分钟）...'
$frontendReady = Wait-ForUrl -Url "http://localhost:$FrontendPort" -TimeoutSeconds 60
if ($frontendReady) {
    Write-Ok "前端已就绪：http://localhost:$FrontendPort"
} else {
    Write-Warn '前端 1 分钟内没有响应，请打开「前端」窗口看报错。'
}

Write-Step '正在用默认浏览器打开 http://localhost:5173 ...'
Start-Process "http://localhost:$FrontendPort" | Out-Null

Write-Host ''
Write-Host '==================================================' -ForegroundColor Green
Write-Host '  启动完成' -ForegroundColor Green
Write-Host '==================================================' -ForegroundColor Green
Write-Host ('  网页入口   ：http://localhost:{0}' -f $FrontendPort)
Write-Host ('  后端健康检查：http://127.0.0.1:{0}/api/v1/health' -f $BackendPort)
Write-Host ('  接口文档   ：http://127.0.0.1:{0}/docs' -f $BackendPort)
Write-Host '  停止服务   ：直接关掉「后端」「前端」两个窗口'
if (-not ($backendReady -and $frontendReady)) {
    Write-Host ''
    Write-Warn '注意：有服务没能在超时前响应，页面可能打不开，请查看对应窗口的报错。'
}
Write-Host ''
