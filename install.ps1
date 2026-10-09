# POPME 설치: 패키지 설치 + 설정 파일 준비 + 로그인 시 자동 실행 + 바탕화면 바로가기
# 실행: powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

python -m pip install -q -r "$root\requirements.txt"

# 개인 설정이 없으면 예시에서 복사 (이미 있으면 건드리지 않음)
if (-not (Test-Path "$root\config.toml")) {
    Copy-Item "$root\config.example.toml" "$root\config.toml"
    Write-Host "만듦: config.toml (필요하면 고치세요)"
}

# ChatGPT(Codex CLI)로 브리핑을 쓰는 설정이면 Codex 설치 + 로그인 안내
if (Select-String -Path "$root\config.toml" -Pattern '^\s*provider\s*=\s*"codex"' -Quiet) {
    $codex = Get-Command codex -ErrorAction SilentlyContinue
    $wg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\OpenAI.Codex_*\codex-x86_64-pc-windows-msvc.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $codex -and -not $wg) {
        Write-Host "Codex CLI 설치 중 (winget OpenAI.Codex)..."
        winget install --id OpenAI.Codex -e --silent --accept-package-agreements --accept-source-agreements
        $wg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\OpenAI.Codex_*\codex-x86_64-pc-windows-msvc.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    $exe = if ($codex) { $codex.Source } elseif ($wg) { $wg.FullName } else { $null }
    if ($exe) {
        $status = & $exe login status 2>&1 | Out-String
        if ($status -notmatch "Logged in") {
            Write-Host "ChatGPT 로그인 창을 엽니다. 브라우저에서 로그인해 주세요."
            & $exe login
        } else { Write-Host "Codex: ChatGPT 로그인 확인됨" }
    } else { Write-Host "Codex CLI를 설치하지 못했어요. 직접 설치: winget install OpenAI.Codex 후 codex login" }
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
Write-Host "끄기: 캐릭터 우클릭 > 종료 (또는 트레이 아이콘 우클릭 > 종료)"