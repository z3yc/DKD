#Requires -Version 5.1
<#
.SYNOPSIS
    以 `.env` 为唯一凭据来源启动 Java 后端（dkd-admin）。

.DESCRIPTION
    为什么需要这个脚本（F-1 的根治，2026-10-02 实测踩到）：

    后端读的是**进程环境变量**（`application.yml` 里 `${DKD_DEEPSEEK_API_KEY}` 等 8 个占位符，
    其中 `DKD_AGENT_SERVICE_SECRET` 连默认值都没有，缺了直接起不来），而 Python 侧读的是 `.env`。
    两条链路各读各的，于是同一个 Key 在两处慢慢漂移也无人察觉——实测结果：

        Windows 用户级 DKD_DEEPSEEK_API_KEY = ...48a8（对 api.deepseek.com 返回 401）
        .env 里的同一变量              = ...feb2（HTTP 200 有效）

    Java 进程于是拿着失效密钥跑，`/manage/ai/diagnose` 直接 401 —— 而这恰恰是
    `agent.enabled=false` 时**唯一**的降级目标，"降级后主业务零影响"的承诺当场落空。
    报错还指向"鉴权失败"，与真因（环境变量漂移）相距很远。

    本脚本做三件事：
      1. 从 `.env` 读取全部 `DKD_*` 变量注入**本次启动的子进程**（Java 与 Python 由此同源）；
      2. 默认**检查**用户级环境变量是否与 `.env` 漂移并报告（`-PersistEnv` 才真正写回）；
      3. 前台/后台启动，并把 stdout/stderr 落到 `target/`，便于失败时定位。

.PARAMETER PersistEnv
    把 `.env` 的值写回**用户级**环境变量，消除漂移（影响此后所有新进程/IDE）。
    注意：`DKD_JWT_SECRET` 写回后新起的后端会用新密钥，**已登录用户会掉线**。

.PARAMETER SkipBuild
    跳过 `mvn package`（默认会构建；注意 `maven-jar-plugin` 有 up-to-date 检查，
    旧 jar 存在时会**静默跳过打包**，故默认先删 jar 强制作废，见 F-5）。

.PARAMETER JavaExe
    指定 java.exe。默认按 DKD_JAVA_EXE → jdk-21（本机既有运行时的实际取值）→ JAVA_HOME → PATH 顺序解析。

    为什么默认不是 JAVA_HOME：本机 JAVA_HOME 指向 **jdk1.8.0_152**，而此前后端实际跑在
    **jdk-21**（实测命令行 `C:\Program Files\Java\jdk-21\bin\java.exe -jar target/dkd-admin.jar`）。
    排查问题时换 JDK 会多引入一个变量（TLS 实现/默认 cipher 都可能变），故这里保持与既有运行时一致，
    并把「JAVA_HOME 与实跑 JDK 不一致」显式打印出来供人工决定。

.EXAMPLE
    pwsh -File docs/scripts/start-backend.ps1
    pwsh -File docs/scripts/start-backend.ps1 -PersistEnv
#>
[CmdletBinding()]
param(
    [switch]$PersistEnv,
    [switch]$SkipBuild,
    [string]$JavaExe = ''
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envFile = Join-Path $repoRoot '.env'
$moduleDir = Join-Path $repoRoot 'dkd-parent\dkd-admin'
$jarPath = Join-Path $moduleDir 'target\dkd-admin.jar'

# 与 application.yml 中**无默认值**的占位符一一对应（少一个后端就起不来）
$requiredKeys = @(
    'DKD_DB_PASSWORD',
    'DKD_REDIS_PASSWORD',
    'DKD_DRUID_PASSWORD',
    'DKD_JWT_SECRET',
    'DKD_OSS_ACCESS_KEY',
    'DKD_OSS_SECRET_KEY',
    'DKD_DEEPSEEK_API_KEY',
    'DKD_AGENT_SERVICE_SECRET'
)

if (-not (Test-Path $envFile)) {
    throw "未找到 $envFile —— 凭据来源缺失，拒绝以「环境里的旧值」启动（那正是 F-1 的成因）"
}

# ---- 1. 解析 .env（不打印值，只报尾号便于比对）--------------------------------
$pairs = [ordered]@{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $k, $v = $t.Split('=', 2)
    $pairs[$k.Trim()] = $v.Trim()
}

$missing = $requiredKeys | Where-Object { -not $pairs.Contains($_) -or -not $pairs[$_] }
if ($missing) {
    throw "以下必需变量在 .env 中缺失或为空：$($missing -join ', ')"
}

function Get-Tail([string]$value) {
    if (-not $value) { return '(empty)' }
    if ($value.Length -le 4) { return '****' }
    return '****' + $value.Substring($value.Length - 4)
}

# ---- 2. 漂移检测（F-1 的哨兵：同一变量两处不一致时必须说出来）------------------
Write-Host '== 环境一致性检查（.env vs 用户级环境变量）=='
$drifted = @()
foreach ($k in $requiredKeys) {
    $userVal = [Environment]::GetEnvironmentVariable($k, 'User')
    $envVal = $pairs[$k]
    if ($userVal -eq $envVal) {
        Write-Host ("  OK      {0,-26} {1}" -f $k, (Get-Tail $envVal))
    }
    else {
        $drifted += $k
        Write-Host ("  DRIFT   {0,-26} .env={1}  用户级={2}" -f $k, (Get-Tail $envVal), (Get-Tail $userVal))
    }
}

if ($drifted.Count -gt 0) {
    if ($PersistEnv) {
        foreach ($k in $drifted) {
            [Environment]::SetEnvironmentVariable($k, $pairs[$k], 'User')
        }
        Write-Host "  -> 已将 $($drifted.Count) 项漂移变量按 .env 写回用户级（DKD_JWT_SECRET 变更会使在线用户掉线）"
    }
    else {
        Write-Warning "检测到 $($drifted.Count) 项漂移：$($drifted -join ', ')。本次启动一律以 .env 为准（子进程注入）；想一并修正用户级请加 -PersistEnv"
    }
}

# ---- 3. 注入本次启动的环境 ------------------------------------------------------
foreach ($k in $pairs.Keys) {
    Set-Item -Path ('env:' + $k) -Value $pairs[$k]
}
Write-Host ("== 已按 .env 注入 {0} 个变量到本次启动的子进程 ==" -f $pairs.Count)

# ---- 4. 构建（默认执行；顺带规避 maven-jar-plugin 的静默跳过）-------------------
if (-not $SkipBuild) {
    Write-Host '== 构建 dkd-admin（删除旧 jar 以规避 maven-jar-plugin 的 up-to-date 静默跳过）=='
    if (Test-Path $jarPath) { Remove-Item -LiteralPath $jarPath -Force }
    Push-Location (Join-Path $repoRoot 'dkd-parent')
    try {
        & mvn -pl dkd-admin -am package
        if ($LASTEXITCODE -ne 0) { throw "mvn package 失败（exit=$LASTEXITCODE）" }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path $jarPath)) {
    throw "未找到 $jarPath —— 构建产物不存在（若用了 -SkipBuild，请确认此前已 package）"
}
$jarAge = (Get-Date) - (Get-Item $jarPath).LastWriteTime
Write-Host ("== jar: {0}（{1:N1} 分钟前生成）==" -f $jarPath, $jarAge.TotalMinutes)
if ($jarAge.TotalMinutes -gt 120) {
    Write-Warning "jar 已超过 2 小时未更新，确认不是复用旧产物（F-5）"
}

# ---- 5. 启动 -------------------------------------------------------------------
# JDK 解析顺序见 .PARAMETER JavaExe 的说明：不默认取 JAVA_HOME，避免把「换 JDK」混进故障排查
$candidates = @()
if ($JavaExe) { $candidates += $JavaExe }
if ($env:DKD_JAVA_EXE) { $candidates += $env:DKD_JAVA_EXE }
$candidates += 'C:\Program Files\Java\jdk-21\bin\java.exe'
if ($env:JAVA_HOME) { $candidates += (Join-Path $env:JAVA_HOME 'bin\java.exe') }

$javaExe = $null
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) { $javaExe = $c; break }
}
if (-not $javaExe) { $javaExe = 'java' }

Write-Host "== JDK：$javaExe"
if ($env:JAVA_HOME) {
    $jhJava = Join-Path $env:JAVA_HOME 'bin\java.exe'
    if ($jhJava -ne $javaExe) {
        Write-Warning "JAVA_HOME 指向 $jhJava，与本次使用的 $javaExe 不一致——排查问题时请确认这不是新增变量"
    }
}

$stdout = Join-Path $moduleDir 'target\boot.stdout.log'
$stderr = Join-Path $moduleDir 'target\boot.stderr.log'
Write-Host "== 启动：$javaExe -jar target/dkd-admin.jar （cwd=$moduleDir）=="

$proc = Start-Process -FilePath $javaExe `
    -ArgumentList '-jar', 'target\dkd-admin.jar' `
    -WorkingDirectory $moduleDir `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru -WindowStyle Hidden

Write-Host ("== PID {0}；日志：{1} / {2} ==" -f $proc.Id, $stdout, $stderr)

# 等端口起（最多 90s）：只看 8080 是否 LISTEN，不做业务断言
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host '== 8080 已监听；请用 docs/scripts/g1-gateway-smoke.sh 做链路自检 =='
        break
    }
    if ($proc.HasExited) {
        throw "进程已退出（exit=$($proc.ExitCode)）；见 $stderr"
    }
    Start-Sleep -Seconds 2
}
