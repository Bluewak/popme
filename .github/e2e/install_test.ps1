# 깨끗한 Windows(GitHub Actions)에서 릴리스 zip을 실제로 설치해 본다. 예전 버전을 쓰던 사람의 업데이트를 그대로 따라간다:
#   앱 폴더에 평문 비밀번호·캐릭터가 있는 옛 설치 → 새 zip 풀기 → install.bat → 개인 파일 이관 → 캐릭터 창 → uninstall.bat
# 사람 손이 필요한 곳(설정 창 [저장])은 같은 함수로 대신하고, 단계마다 화면을 캡처한다.
param([Parameter(Mandatory)][string]$Zip)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$out = Join-Path $env:GITHUB_WORKSPACE "e2e-out"
New-Item -ItemType Directory -Force $out | Out-Null

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string t);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
[W]::SetProcessDPIAware() | Out-Null

function Shot($name) {
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $bmp.Save((Join-Path $out "$name.png"))
    $g.Dispose(); $bmp.Dispose()
}
function Wait-Until($what, $sec, [scriptblock]$cond) {
    $end = (Get-Date).AddSeconds($sec)
    while ((Get-Date) -lt $end) { $r = & $cond; if ($r) { return $r }; Start-Sleep -Milliseconds 500 }
    throw "시간 초과: $what"
}
function Pet-Rect {
    $h = [W]::FindWindow("POPMEPet", [NullString]::Value)   # $null을 넘기면 빈 제목("")으로 바뀌어 못 찾는다
    if ($h -eq [IntPtr]::Zero) { return $null }
    $r = New-Object W+RECT
    [W]::GetWindowRect($h, [ref]$r) | Out-Null
    if ($r.Right - $r.Left -lt 10) { return $null }
    return $r
}
function Check($what, [bool]$ok) {
    if (-not $ok) { throw "실패: $what" }
    Write-Host "ok: $what"
}
function Write-Utf8($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false))) }

$dl = Join-Path $env:USERPROFILE "Downloads"
$root = Join-Path $dl "POPME"
$old = Join-Path $dl "POPME-old"
$data = Join-Path $env:LOCALAPPDATA "POPME"
$py = Join-Path $data "venv\Scripts\python.exe"
$secret = "e2e-" + [guid]::NewGuid().ToString("N")
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "POPME.lnk"

try {
    Expand-Archive $Zip $dl -Force
    Check "zip에 개발용 파일이 없음" (-not (Test-Path (Join-Path $root "tests")) -and -not (Test-Path (Join-Path $root "tools")) -and
                                     -not (Test-Path (Join-Path $root ".github")))

    # ── 예전 버전의 설치: 앱 폴더에 평문 비밀번호·내 캐릭터·내 이미지, 시작프로그램이 그 폴더를 가리킨다 ──
    New-Item -ItemType Directory -Force (Join-Path $old "assets") | Out-Null
    $cfg = [IO.File]::ReadAllText((Join-Path $root "popme\defaults\config.example.toml"))
    $cfg = $cfg -replace '(?m)^file = "character.toml".*$', 'file = "mine.toml"'
    $cfg = $cfg -replace '(?m)^briefing_after = .*$', 'briefing_after = "23:59"'   # 시험 중에 자동 수집(Chrome·AI 필요)이 돌지 않게
    $cfg = $cfg -replace '(?s)(\[mail\].*?\npassword = )""', "`$1`"$secret`""
    Write-Utf8 (Join-Path $old "config.toml") $cfg
    Write-Utf8 (Join-Path $old "mine.toml") (([IO.File]::ReadAllText((Join-Path $root "popme\defaults\character.tori.example.toml"))) -replace '(?m)^images = .*$', '')
    Copy-Item (Join-Path $root "popme\defaults\characters\tori\pet.png") (Join-Path $old "assets\pet.png")
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($startup)
    $sc.TargetPath = "notepad.exe"; $sc.WorkingDirectory = $old; $sc.Save()

    # 새 버전 알림: GitHub에 묻지 않고 오늘 이미 확인한 것처럼 둔다 (앱이 켜지고 60초 뒤에 말한다)
    New-Item -ItemType Directory -Force $data | Out-Null
    Write-Utf8 (Join-Path $data "update-check.json") ('{"day": "' + (Get-Date -Format "yyyy-MM-dd") + '", "tag": "v99.0.0"}')

    # ── README 「설치」: 더블클릭과 같게 cmd로. 마지막 pause는 빈 입력으로 넘긴다 ──
    $inst = Start-Process cmd.exe -ArgumentList "/c install.bat <NUL >`"$out\install-console.txt`" 2>&1" -WorkingDirectory $root -PassThru
    $setup = Wait-Until "설정 창 또는 설치 종료" 1200 {
        $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*popme.setup_window*" } | Select-Object -First 1
        if ($p) { $p } elseif ($inst.HasExited) { "exited" } else { $null }
    }
    if ($setup -eq "exited") { throw "설정 창이 뜨기 전에 설치가 끝났어요 (install-console.txt 참고)" }
    Start-Sleep 10
    Shot "1-setup-window"

    # 설정 창이 뜬 시점에 이관은 끝나 있다
    Check "옛 폴더에 설정이 남지 않음" (-not (Test-Path (Join-Path $old "config.toml")))
    Check "옛 폴더에 캐릭터가 남지 않음" (-not (Test-Path (Join-Path $old "mine.toml")))
    Check "옛 폴더에 이미지가 남지 않음" (-not (Test-Path (Join-Path $old "assets\pet.png")))
    Check "캐릭터·이미지가 데이터 폴더로" ((Test-Path (Join-Path $data "mine.toml")) -and (Test-Path (Join-Path $data "assets\pet.png")))
    Check "설정 파일에 평문 비밀번호가 없음" (-not (Select-String -Path (Join-Path $data "config.toml") -Pattern $secret -SimpleMatch -Quiet))
    $saved = & $py -c "from popme import vault; print(vault.get('mail'))"
    Check "비밀번호가 자격 증명 관리자에 있음" ("$saved".Trim() -eq $secret)

    $env:PYTHONPATH = $root
    & $py (Join-Path $here "save_defaults.py") | Tee-Object (Join-Path $out "save.txt")
    if ($LASTEXITCODE) { throw "설정 저장 실패" }
    Stop-Process -Id $setup.ProcessId -Force
    Wait-Until "설치 끝" 300 { $inst.HasExited } | Out-Null
    Check "install.bat 정상 종료" ($inst.ExitCode -eq 0)
    Check "설치 기록에 버전" (Select-String -Path (Join-Path $out "install-console.txt") -Pattern '> POPME \d+\.\d+\.\d+' -Quiet)
    & $py -m pip check | Tee-Object (Join-Path $out "pip-check.txt")
    Check "패키지 호환" ($LASTEXITCODE -eq 0)

    # ── 설치 직후: 내 캐릭터로 켜진다 ──
    try { Wait-Until "캐릭터 창" 180 { Pet-Rect } | Out-Null } catch { Shot "2-pet-timeout"; throw }
    Start-Sleep 8
    Shot "2-pet"
    Check "로그에 오류 없음" (-not (Select-String -Path (Join-Path $data "popme.log") -Pattern 'Traceback' -SimpleMatch -Quiet))
    Check "아이콘 파일이 데이터 폴더에" (Test-Path (Join-Path $data "assets\pet.ico"))
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($startup)
    Check "시작프로그램이 새 폴더를 가리킴" ($sc.WorkingDirectory.TrimEnd('\') -eq $root.TrimEnd('\'))
    Check "바로가기 아이콘이 데이터 폴더를 가리킴" ($sc.IconLocation -like "$data*")

    # ── 새 버전 알림: 말풍선과 트레이 메뉴까지 실제 앱에서 ──
    Wait-Until "새 버전 알림" 120 { Select-String -Path (Join-Path $data "popme.log") -Pattern 'v99.0.0' -SimpleMatch -Quiet } | Out-Null
    Start-Sleep 2
    Shot "3-update-notice"
    Check "알림 뒤에도 로그에 오류 없음" (-not (Select-String -Path (Join-Path $data "popme.log") -Pattern 'Traceback' -SimpleMatch -Quiet))
    Copy-Item (Join-Path $data "popme.log"), (Join-Path $data "install.log") $out   # 제거하면 데이터 폴더가 없어진다

    # ── README 「지우기」: 데이터까지 ──
    $un = Start-Process cmd.exe -ArgumentList "/c (echo y)| uninstall.bat >`"$out\uninstall-console.txt`" 2>&1" -WorkingDirectory $root -PassThru
    Wait-Until "제거 끝" 120 { $un.HasExited } | Out-Null
    Wait-Until "캐릭터 창 닫힘" 30 { -not (Pet-Rect) } | Out-Null
    Check "바로가기 지워짐" (-not (Test-Path $startup))
    Check "데이터 폴더 지워짐" (-not (Test-Path $data))
    Check "저장한 비밀번호 지워짐" ("$(cmdkey /list)" -notlike "*POPME/mail*")
    Write-Host "모두 통과"
} finally {
    if (Test-Path $data) { Copy-Item (Join-Path $data "popme.log"), (Join-Path $data "install.log") $out -ErrorAction SilentlyContinue }
}
