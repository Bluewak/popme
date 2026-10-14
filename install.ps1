# POPME 설치·제거
#   설치: install.bat 더블클릭   제거: uninstall.bat 더블클릭   (직접: powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Uninstall])
#
# 설치 순서: 압축 푼 폴더인지 확인 → Python 3.11~3.13 찾기(없으면 winget으로 3.12 설치) → WebView2 확인
#   → 켜져 있는 POPME 끄기 → %LOCALAPPDATA%\POPME\venv에 패키지 설치 → 예전 설치 폴더의 설정 가져오기
#   → 설정 창 → 바로가기(시작프로그램·바탕화면) → POPME 켜기
# 기록: %LOCALAPPDATA%\POPME\install.log (문제가 생기면 이 파일을 보내 주세요)
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = Join-Path $env:LOCALAPPDATA "POPME"
# 패키지 환경은 앱 폴더가 아니라 짧은 고정 경로에: playwright 안의 깊은 파일이 260자 경로 제한에 걸리지 않게,
# 새 버전을 다른 폴더에 풀어도 다시 받지 않게
$venv = Join-Path $data "venv"
$py = Join-Path $venv "Scripts\python.exe"
$pyw = Join-Path $venv "Scripts\pythonw.exe"
# 테스트용: POPME_SHORTCUT_DIR이면 바로가기를 그 폴더에, POPME_TEST면 켜진 POPME를 끄거나 새로 켜지 않음
$lnkDir = $env:POPME_SHORTCUT_DIR
$startupLnk = if ($lnkDir) { Join-Path $lnkDir "startup.lnk" } else { Join-Path ([Environment]::GetFolderPath("Startup")) "POPME.lnk" }
$desktopLnk = if ($lnkDir) { Join-Path $lnkDir "desktop.lnk" } else { Join-Path ([Environment]::GetFolderPath("Desktop")) "POPME.lnk" }

New-Item -ItemType Directory -Force $data | Out-Null
try { Start-Transcript (Join-Path $data "install.log") | Out-Null } catch {}

function Step($msg) { Write-Host ""; Write-Host "> $msg" -ForegroundColor Cyan }

function Invoke-Native($exe, [string[]]$argv) {
    # 외부 프로그램 출력을 화면과 기록에 같이 남긴다 (PowerShell 5.1은 stderr를 오류로 바꾸므로 잠깐 Continue)
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $exe @argv 2>&1 | ForEach-Object { "$_" } | Out-Host } finally { $ErrorActionPreference = $old }
    return $LASTEXITCODE
}

function Stop-Popme {
    if ($env:POPME_TEST) { return }
    # 위젯 본체(-m popme)만. 설정 창(-m popme.setup_window)은 건드리지 않는다
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
               Where-Object { $_.CommandLine -match '-m\s+popme(\s|"|$)' })
    if ($procs.Count) {
        Write-Host "켜져 있는 POPME를 잠깐 끌게요."
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
}

function Get-LinkDir($lnk) {
    if (-not (Test-Path $lnk)) { return $null }
    return (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).WorkingDirectory
}

function Find-Python {
    # pywebview가 쓰는 pythonnet이 확인된 3.11~3.13. 3.12 → 3.13 → 3.11 순.
    # 없는 버전을 실행해 보면 빨간 오류가 찍히므로, 설치 목록(py -0p)·실제 파일만 후보로 삼는다
    $found = @{}
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $list = @(); try { $list = @(& py -0p 2>$null) } catch {}   # 5.1은 stderr가 있으면 Stop에서 예외
        foreach ($line in $list) {   # 예: " -V:3.12 *   C:\...\python.exe"
            if ("$line" -match '^\s*-(?:V:)?(3\.\d+)\S*\s+\*?\s*(\S.*\.exe)\s*$' -and -not $found[$matches[1]]) { $found[$matches[1]] = $matches[2] }
        }
    }
    $cands = @("3.12", "3.13", "3.11" | ForEach-Object { $found[$_] }) +
             @(Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object Source |
               Where-Object { $_ -notlike "*\WindowsApps\*" }) +   # Microsoft Store 바로가기용 가짜 python.exe 제외
             @("3.12", "3.13", "3.11" | ForEach-Object { "$env:LOCALAPPDATA\Programs\Python\Python$($_ -replace '\.')\python.exe" })   # winget 직후 PATH가 아직 안 바뀌었을 때
    foreach ($exe in $cands) {
        if (-not $exe -or -not (Test-Path $exe)) { continue }
        $out = & $exe -c "import sys; print(sys.executable if (3, 11) <= sys.version_info[:2] <= (3, 13) else '')"
        $out = "$out".Trim()
        if ($out -and (Test-Path $out)) { return $out }
    }
    return $null
}

function Test-WebView2 {
    $id = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    foreach ($k in "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$id", "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id",
                   "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$id") {
        $pv = (Get-ItemProperty $k -ErrorAction SilentlyContinue).pv
        if ($pv -and $pv -ne "0.0.0.0") { return $true }
    }
    return $false
}

function Uninstall-Popme {
    Step "POPME 끄기"
    Stop-Popme
    # 이 폴더를 가리키는 바로가기만 지운다 (다른 폴더에 새로 설치한 것은 그대로)
    foreach ($lnk in $startupLnk, $desktopLnk) {
        $dir = Get-LinkDir $lnk
        if ($dir -and ($dir.TrimEnd('\') -eq $root.TrimEnd('\'))) { Remove-Item $lnk -Force; Write-Host "지움: $lnk" }
    }
    # 패키지 환경은 다른 폴더의 POPME가 아직 쓰고 있지 않을 때만 지운다
    if (-not (Test-Path $startupLnk) -and (Test-Path $venv)) { Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue; Write-Host "지움: $venv" }
    $ans = Read-Host "브리핑 기록·메일 목록·전용 Chrome 로그인 같은 데이터($data)도 지울까요? (y/N)"
    if ($ans -match '^[yY]') {
        # 전용 Chrome이 켜져 있으면 프로필 파일이 잠겨 있다
        Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like "*$data*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        try { Stop-Transcript | Out-Null } catch {}
        Start-Sleep -Seconds 1
        Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue
        Write-Host "지움: $data"
    }
    Write-Host ""
    Write-Host "제거 끝. 남은 건 이 폴더뿐이에요 — 폴더째 지우면 돼요 (config.toml에 설정·메일 앱 비밀번호가 있어요)." -ForegroundColor Green
    Write-Host "설정 창에서 설치한 AI 도구(Claude Code·Codex·Gemini CLI)와 Python은 그대로 둬요."
}

function Install-Popme {
    # 압축 프로그램 안에서 바로 실행하면 임시 폴더에 풀린 일부 파일만 있다
    $tmp = [IO.Path]::GetTempPath().TrimEnd('\')
    if (-not $env:POPME_TEST -and $root.StartsWith($tmp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "압축 파일 안에서 바로 실행한 것 같아요. zip을 우클릭 → '모두 압축 풀기'로 푼 뒤, 풀린 폴더의 install.bat을 실행해 주세요."
    }
    foreach ($f in "requirements.txt", "popme\__main__.py", "config.example.toml") {
        if (-not (Test-Path (Join-Path $root $f))) { throw "$f 파일이 없어요. zip을 다시 받아 전체를 압축 풀어 주세요." }
    }
    # 인터넷에서 받은 zip의 파일 표시(MOTW)를 지워 실행할 때마다 경고가 뜨지 않게
    Get-ChildItem $root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

    Step "Python 확인"
    $base = Find-Python
    if (-not $base) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "Python 3.12가 필요해요. https://www.python.org/downloads/ 에서 설치할 때 'Add python.exe to PATH'를 체크하고, install.bat을 다시 실행해 주세요."
        }
        Write-Host "Python 3.12를 설치할게요 (몇 분 걸려요)..."
        Invoke-Native winget @("install", "--id", "Python.Python.3.12", "-e", "--scope", "user",
                               "--accept-package-agreements", "--accept-source-agreements") | Out-Null
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $base = Find-Python
        if (-not $base) { throw "Python을 설치하지 못했어요. https://www.python.org/downloads/ 에서 3.12를 설치한 뒤 install.bat을 다시 실행해 주세요." }
    }
    Write-Host "Python: $base"

    if (-not (Test-WebView2)) {
        Step "WebView2 설치 (말풍선·설정 창을 그리는 데 필요)"
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Invoke-Native winget @("install", "--id", "Microsoft.EdgeWebView2Runtime", "-e", "--accept-package-agreements", "--accept-source-agreements") | Out-Null
        }
        if (-not (Test-WebView2)) {
            Write-Host "WebView2를 확인하지 못했어요. 설정 창이 안 뜨면 https://go.microsoft.com/fwlink/p/?LinkId=2124703 에서 설치해 주세요." -ForegroundColor Yellow
        }
    }

    Stop-Popme   # 켜져 있으면 패키지 파일이 잠겨 있고, 새로 켠 POPME가 '이미 켜져 있음'으로 바로 꺼진다

    Step "필요한 패키지 설치 (처음엔 몇 분 걸려요)"
    if (Test-Path $py) {
        if ((Invoke-Native $py @("-c", "import sys")) -ne 0) {   # 만든 Python이 지워졌으면 다시 만든다
            Remove-Item -Recurse -Force $venv
        }
    }
    if (-not (Test-Path $py)) {
        if ((Invoke-Native $base @("-m", "venv", $venv)) -ne 0) { throw "Python 환경(venv)을 만들지 못했어요." }
    }
    if ((Invoke-Native $py @("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $root "requirements.txt"))) -ne 0) {
        throw "패키지 설치에 실패했어요 (위 메시지 참고). 인터넷 연결을 확인하고 다시 실행해 주세요."
    }

    # 새 폴더에 업데이트로 설치했으면 예전 폴더의 개인 설정·캐릭터를 가져온다 (이미 있으면 건드리지 않음)
    $prev = Get-LinkDir $startupLnk
    if ($prev -and ($prev.TrimEnd('\') -ne $root.TrimEnd('\')) -and (Test-Path (Join-Path $prev "config.toml")) -and
        -not (Test-Path (Join-Path $root "config.toml"))) {
        Step "예전 설치($prev)의 설정 가져오기"
        $files = @("config.toml", "character.toml") + @(Get-ChildItem (Join-Path $prev "assets") -Filter "pet*" -File -ErrorAction SilentlyContinue |
                                                        ForEach-Object { "assets\" + $_.Name })
        foreach ($f in $files) {
            $src = Join-Path $prev $f; $dst = Join-Path $root $f
            if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst; Write-Host "가져옴: $f" }
        }
    }

    Step "설정 창을 열어요 — 다 고르고 저장하면 설치가 이어져요"
    Push-Location $root
    try { & $py -m popme.setup_window } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $root "config.toml"))) {   # 창을 그냥 닫았으면 예시 설정으로 (나중에 트레이 > 설정 바꾸기)
        Copy-Item (Join-Path $root "config.example.toml") (Join-Path $root "config.toml")
        Write-Host "만듦: config.toml (설정 창을 닫아서 기본값으로. 트레이 > 설정 바꾸기에서 다시 고를 수 있어요)"
    }

    # 바로가기 아이콘(pet.ico): 캐릭터 이미지, 없으면 기본 방울. 이미지를 나중에 바꾸면 POPME가 켜질 때 다시 만든다
    $ico = Join-Path $root "assets\pet.ico"
    Push-Location $root
    try { Invoke-Native $py @("-c", "from popme import pet; pet.write_icon(pet.load_images()['idle'])") | Out-Null } finally { Pop-Location }

    Step "바로가기 만들기"
    $shell = New-Object -ComObject WScript.Shell
    foreach ($lnk in $startupLnk, $desktopLnk) {   # 로그인할 때 자동 실행, 더블클릭으로 켜기
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = $pyw
        $sc.Arguments = "-m popme"
        $sc.WorkingDirectory = $root
        if (Test-Path $ico) { $sc.IconLocation = $ico }
        $sc.Description = "POPME - 데스크톱 브리핑 비서"
        $sc.Save()
        Write-Host "만듦: $lnk"
    }

    if (-not $env:POPME_TEST) { Start-Process $pyw -ArgumentList "-m", "popme" -WorkingDirectory $root }
    Write-Host ""
    Write-Host "설치 끝! POPME를 켰어요. 화면 오른쪽 아래를 보세요." -ForegroundColor Green
    Write-Host "다음: 트레이 아이콘 우클릭 > '전용 Chrome 열기 (로그인)'에서 X·Threads 로그인 → '지금 수집'"
    Write-Host "끄기: 캐릭터 우클릭 > 종료   지우기: uninstall.bat"
}

try {
    if ($Uninstall) { Uninstall-Popme } else { Install-Popme }
} catch {
    Write-Host ""
    Write-Host "멈췄어요: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "기록: $(Join-Path $data 'install.log') — 해결이 안 되면 이 파일을 보내 주세요."
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
try { Stop-Transcript | Out-Null } catch {}
