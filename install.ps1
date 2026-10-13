# POPME 설치: 패키지 설치 + 설정 창 + 로그인 시 자동 실행 + 바탕화면 바로가기
# 실행: powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

python -m pip install -q -r "$root\requirements.txt"

# 설정 창: 준비물 확인 → 브리핑 AI(설치·로그인) → 캘린더 → 메일 → 날씨·시각. config.toml이 있으면 그 값으로 채워서 열림
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
Write-Host "끄기: 캐릭터 우클릭 > 종료 (또는 트레이 아이콘 우클릭 > 종료)"