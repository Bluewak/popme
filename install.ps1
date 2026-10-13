# POPME 설치: 패키지 설치 + 설정 창 + 로그인 시 자동 실행 + 바탕화면 바로가기
# 실행: install.bat 더블클릭 (또는 powershell -ExecutionPolicy Bypass -File .\install.ps1)
# 우클릭 > "PowerShell에서 실행"은 PC 실행 정책에 막혀 창이 바로 닫힐 수 있다 → install.bat이 정책을 이번 실행에만 풀어 준다
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

try {
    # Python 3.11+ 확인 (Microsoft Store 바로가기용 가짜 python.exe는 버전을 못 낸다)
    $ok = $null
    try { $ok = & python -c "import sys; print(sys.version_info >= (3, 11))" 2>$null } catch {}
    if ("$ok".Trim() -ne "True") {
        throw "Python 3.11 이상이 필요해요. PowerShell에서 winget install Python.Python.3.12 → 창을 새로 열고 다시 실행해 주세요."
    }

    Write-Host "필요한 패키지 설치 중... (처음엔 몇 분 걸려요)"
    python -m pip install -q --disable-pip-version-check -r "$root\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw "패키지 설치에 실패했어요 (위 메시지 참고). 인터넷 연결을 확인하고 다시 실행해 주세요." }

    # 설정 창: 준비물 확인 → 브리핑 AI(설치·로그인) → 캘린더 → 메일 → 날씨·시각. config.toml이 있으면 그 값으로 채워서 열림
    Write-Host "설정 창을 열어요. 다 고르고 저장하면 설치가 이어져요."
    Push-Location $root
    python -m popme.setup_window
    Pop-Location
    if (-not (Test-Path "$root\config.toml")) {   # 창을 그냥 닫았으면 예시 설정으로 (나중에 트레이 > 설정 바꾸기)
        Copy-Item "$root\config.example.toml" "$root\config.toml"
        Write-Host "만듦: config.toml (설정 창을 닫아서 기본값으로. 트레이 > 설정 바꾸기에서 다시 고를 수 있어요)"
    }

    # 캐릭터 이미지가 있으면 바로가기·트레이 아이콘(pet.ico) 만들기
    if ((Test-Path "$root\assets\pet.png") -and -not (Test-Path "$root\assets\pet.ico")) {
        python -c "from PIL import Image; im=Image.open(r'$root\assets\pet.png').convert('RGBA'); s=max(im.size); sq=Image.new('RGBA',(s,s)); sq.alpha_composite(im,((s-im.width)//2,(s-im.height)//2)); sq.resize((256,256)).save(r'$root\assets\pet.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    }

    $pythonw = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
    $shell = New-Object -ComObject WScript.Shell
    $targets = @(
        (Join-Path ([Environment]::GetFolderPath("Startup")) "POPME.lnk"),   # 로그인할 때 자동 실행
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "POPME.lnk")    # 더블클릭으로 켜기
    )
    foreach ($lnk in $targets) {
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = $pythonw
        $sc.Arguments = "-m popme"
        $sc.WorkingDirectory = $root
        if (Test-Path "$root\assets\pet.ico") { $sc.IconLocation = "$root\assets\pet.ico" }
        $sc.Description = "POPME - 데스크톱 브리핑 비서"
        $sc.Save()
        Write-Host "만듦: $lnk"
    }
    Write-Host ""
    Write-Host "설치 끝! 바탕화면의 POPME를 더블클릭해서 켜세요." -ForegroundColor Green
    Write-Host "끄기: 캐릭터 우클릭 > 종료 (또는 트레이 아이콘 우클릭 > 종료)"
} catch {
    Write-Host ""
    Write-Host "설치하다 멈췄어요: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
