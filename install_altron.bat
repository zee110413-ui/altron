<# : batch part - cmd runs only these lines, PowerShell sees them as a comment
@echo off
chcp 65001 >nul
title Altron installer
set "ALTRON_BAT=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText($env:ALTRON_BAT, [Text.Encoding]::UTF8))"
echo.
pause
exit /b
#>

# ============================================================================================================
#  Altron installer: everything Altron needs, in one go. Safe to run again: what is already there is skipped,
#  interrupted downloads continue. Run it from an existing Altron folder to update it (your config is kept).
# ============================================================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Repo = 'zee110413-ui/altron'
$Branch = 'main'
# chat models with tool calling, tried in this order (the first that exists on Hugging Face is taken)
$LlmRepos = @('unsloth/Qwen3.5-9B-GGUF', 'unsloth/Qwen3-VL-8B-Instruct-GGUF', 'unsloth/Qwen3-8B-GGUF')
$LlmQuant = 'Q4_K_M'
$WhisperGpu = @('mobiuslabsgmbh/faster-whisper-large-v3-turbo', 'deepdml/faster-whisper-large-v3-turbo-ct2')
$WhisperCpu = @('Systran/faster-whisper-small')
$Voices = [ordered]@{ ru = 'ru_RU-dmitri-medium'; en = 'en_US-ryan-high' }
$BaritoneTag = 'v1.10.1'
$UA = @{ 'User-Agent' = 'altron-installer'; 'Accept' = 'application/json' }

function Say($ru, $en) { Write-Host "`n== $ru" -ForegroundColor Cyan; Write-Host "   $en" -ForegroundColor DarkCyan }
function Warn($text) { Write-Host "   ! $text" -ForegroundColor Yellow }
function Ok($text) { Write-Host "   + $text" -ForegroundColor Green }

function Get-File($url, $out) {
    if ((Test-Path $out) -and ((Get-Item $out).Length -gt 0)) { Ok "есть / present: $(Split-Path $out -Leaf)"; return }
    New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
    Write-Host "   скачиваю / downloading $(Split-Path $out -Leaf)"
    & curl.exe -L --fail --retry 3 -C - -o "$out.part" $url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
    Move-Item -Force "$out.part" $out
}

function Expand-Zip($zip, $dest) {
    New-Item -ItemType Directory -Force $dest | Out-Null
    & tar.exe -xf $zip -C $dest
    if ($LASTEXITCODE -ne 0) { Expand-Archive -Force $zip $dest }
}

function Get-HfFiles($repo) {
    try { return @((Invoke-RestMethod "https://huggingface.co/api/models/$repo" -Headers $UA).siblings | ForEach-Object { $_.rfilename }) }
    catch { return $null }
}

function Get-HfRepo($repo, $dest) {
    # a whole model folder (faster-whisper models are a few files)
    $files = Get-HfFiles $repo
    if (-not $files) { return $false }
    foreach ($f in $files | Where-Object { $_ -notmatch '^(\.gitattributes|README\.md)$' }) {
        Get-File "https://huggingface.co/$repo/resolve/main/$f" (Join-Path $dest $f)
    }
    return $true
}

function Get-GhRelease($repo, $tag) {
    $u = if ($tag) { "https://api.github.com/repos/$repo/releases/tags/$tag" } else { "https://api.github.com/repos/$repo/releases/latest" }
    return Invoke-RestMethod $u -Headers $UA
}

function Step($title, [scriptblock]$body) {
    try { & $body } catch { Warn "$title — не получилось / failed: $($_.Exception.Message)"; $global:Failed += $title }
}

$Failed = @()
$Updates = @{}
$Nvidia = [bool](Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'NVIDIA' })

# ------------------------------------------------------------------------------------------------ where
$Here = Split-Path -Parent $env:ALTRON_BAT
$Root = if (Test-Path (Join-Path $Here 'brain\altron.py')) { $Here } else { Join-Path $env:USERPROFILE 'Altron' }
$Brain = Join-Path $Root 'brain'
Say "Альтрон ставится в $Root" "Installing Altron into $Root"
if (-not $Nvidia) { Warn "Видеокарта NVIDIA не найдена: ИИ будет работать медленнее / no NVIDIA GPU: the AI will be slower" }

# ------------------------------------------------------------------------------------------------ code
Step 'code' {
    $have = Test-Path (Join-Path $Brain 'altron.py')
    $global:Fresh = -not (Test-Path (Join-Path $Brain 'config.json'))
    $get = -not $have
    if ($have) { $get = (Read-Host "`n   Обновить код Альтрона до последней версии? Update Altron's code? [y/N]") -match '^(y|д)' }
    if (-not $get) { return }
    Say 'Скачиваю код Альтрона' 'Downloading Altron'
    $tmp = Join-Path $env:TEMP 'altron-code'
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Get-File "https://codeload.github.com/$Repo/zip/refs/heads/$Branch" "$tmp\code.zip"
    Expand-Zip "$tmp\code.zip" "$tmp\x"
    $src = (Get-ChildItem "$tmp\x" -Directory | Select-Object -First 1).FullName
    if (Test-Path (Join-Path $Brain 'config.json')) {
        # the user's settings stay; new settings come in from the default config
        Copy-Item -Force "$src\brain\config.json" (Join-Path $Brain 'config.default.json')
        & robocopy.exe $src $Root /E /XF config.json /NFL /NDL /NJH /NJS /NP | Out-Null
    } else {
        & robocopy.exe $src $Root /E /NFL /NDL /NJH /NJS /NP | Out-Null
    }
    if ($LASTEXITCODE -ge 8) { throw "robocopy $LASTEXITCODE" }
    Ok 'код на месте / code in place'
}
if (-not (Test-Path (Join-Path $Brain 'altron.py'))) { Warn 'Нет кода Альтрона — дальше нельзя / no Altron code, stopping'; exit 1 }
$Cfg = Get-Content -Raw -Encoding UTF8 (Join-Path $Brain 'config.json') | ConvertFrom-Json
function Exists-FromBrain($rel) { return $rel -and (Test-Path (Join-Path $Brain $rel)) }

# ------------------------------------------------------------------------------------------------ python
$Py = $null
Step 'python' {
    Say 'Python' 'Python'
    foreach ($v in @('-3.11', '-3.12', '-3.10')) {
        try { & py $v -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $global:PyBase = @('py', $v); break } } catch {}
    }
    if (-not $global:PyBase) {
        $exe = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
        if (-not (Test-Path $exe)) {
            Get-File 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' "$env:TEMP\python-3.11.9-amd64.exe"
            Write-Host '   ставлю Python 3.11 / installing Python 3.11'
            Start-Process -Wait "$env:TEMP\python-3.11.9-amd64.exe" '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1'
        }
        $global:PyBase = @($exe)
    }
    $venv = Join-Path $Brain '.venv\Scripts\python.exe'
    if (-not (Test-Path $venv)) {
        $base = $global:PyBase
        if ($base.Count -gt 1) { & $base[0] $base[1] -m venv (Join-Path $Brain '.venv') } else { & $base[0] -m venv (Join-Path $Brain '.venv') }
    }
    $global:Py = $venv
    & $venv -m pip install -q --upgrade pip
    & $venv -m pip install -q -r (Join-Path $Brain 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip install' }
    if ($Nvidia) { & $venv -m pip install -q nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*' }
    Ok 'Python и пакеты готовы / Python and packages ready'
}

# ------------------------------------------------------------------------------------------------ llama.cpp
Step 'llama.cpp' {
    Say 'Сервер нейросети (llama.cpp)' 'AI server (llama.cpp)'
    $dir = Join-Path $Root 'tools\llama'
    if (Test-Path "$dir\llama-server.exe") { Ok 'есть / present'; return }
    $assets = (Get-GhRelease 'ggml-org/llama.cpp').assets
    $main = $null; $rt = $null
    if ($Nvidia) {
        $main = $assets | Where-Object { $_.name -match '^llama-.*-bin-win-cuda-12[\d.]*-x64\.zip$' } | Select-Object -First 1
        if (-not $main) { $main = $assets | Where-Object { $_.name -match '^llama-.*-bin-win-cuda-[\d.]+-x64\.zip$' } | Select-Object -First 1 }
        if ($main) {
            $ver = [regex]::Match($main.name, 'cuda-([\d.]+)-x64').Groups[1].Value
            $rt = $assets | Where-Object { $_.name -eq "cudart-llama-bin-win-cuda-$ver-x64.zip" } | Select-Object -First 1
        }
    }
    if (-not $main) { $main = $assets | Where-Object { $_.name -match '^llama-.*-bin-win-vulkan-x64\.zip$' } | Select-Object -First 1 }
    if (-not $main) { $main = $assets | Where-Object { $_.name -match '^llama-.*-bin-win-cpu-x64\.zip$' } | Select-Object -First 1 }
    if (-not $main) { throw 'no Windows build in the latest llama.cpp release' }
    $tmp = Join-Path $env:TEMP 'altron-llama'
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    foreach ($a in @($main, $rt) | Where-Object { $_ }) {
        Get-File $a.browser_download_url "$tmp\$($a.name)"
        Expand-Zip "$tmp\$($a.name)" "$tmp\x"
    }
    $srv = Get-ChildItem "$tmp\x" -Recurse -Filter llama-server.exe | Select-Object -First 1
    New-Item -ItemType Directory -Force $dir | Out-Null
    Copy-Item -Force "$($srv.DirectoryName)\*" $dir
    Get-ChildItem "$tmp\x" -Recurse -Filter '*.dll' | Copy-Item -Destination $dir -Force
    Ok "llama.cpp: $($main.name)"
}

# ------------------------------------------------------------------------------------------------ the AI model
Step 'model' {
    Say 'Модель ИИ (несколько ГБ, это долго)' 'AI model (several GB, takes a while)'
    if ($Cfg.llm_url) { Ok "нейросеть в интернете, модель не нужна / online AI: $($Cfg.llm_url)"; return }
    if (Exists-FromBrain $Cfg.llm_model) { Ok "есть / present: $($Cfg.llm_model)"; return }
    foreach ($repo in $LlmRepos) {
        $files = Get-HfFiles $repo
        if (-not $files) { continue }
        $gguf = $files | Where-Object { $_ -notmatch '/' -and $_ -notmatch '(?i)mmproj' -and $_ -match "(?i)$LlmQuant\.gguf$" } |
            Sort-Object Length | Select-Object -First 1
        if (-not $gguf) { continue }
        $mm = $files | Where-Object { $_ -notmatch '/' -and $_ -match '(?i)^mmproj.*f16.*\.gguf$' } | Select-Object -First 1
        Get-File "https://huggingface.co/$repo/resolve/main/$gguf" (Join-Path $Root "models\$gguf")
        $global:Updates.llm_model = "../models/$gguf"
        if ($mm) {
            $mmName = ($repo.Split('/')[1] -replace '-GGUF$', '') + "-$mm"
            Get-File "https://huggingface.co/$repo/resolve/main/$mm" (Join-Path $Root "models\$mmName")
            $global:Updates.llm_mmproj = "../models/$mmName"
        } else {
            $global:Updates.llm_mmproj = ''
            Warn 'у этой модели нет зрения (mmproj) — look работать не будет / this model has no vision'
        }
        Ok "модель / model: $repo"
        return
    }
    throw 'none of the models was found on Hugging Face'
}

# ------------------------------------------------------------------------------------------------ speech
Step 'whisper' {
    Say 'Распознавание речи (Whisper)' 'Speech recognition (Whisper)'
    $cpuDir = Join-Path $Root 'models\whisper-small'
    if (-not (Test-Path "$cpuDir\model.bin")) {
        foreach ($r in $WhisperCpu) { if (Get-HfRepo $r $cpuDir) { break } }
    }
    if (-not (Test-Path "$cpuDir\model.bin")) { throw 'whisper-small' }
    $global:Updates.stt_model_cpu = '../models/whisper-small'
    $global:Updates.stt_model = '../models/whisper-small'
    if ($Nvidia) {
        $gpuDir = Join-Path $Root 'models\whisper-large-v3-turbo'
        if (-not (Test-Path "$gpuDir\model.bin")) {
            foreach ($r in $WhisperGpu) { if (Get-HfRepo $r $gpuDir) { break } }
        }
        if (Test-Path "$gpuDir\model.bin") { $global:Updates.stt_model = '../models/whisper-large-v3-turbo' }
    }
    Ok 'Whisper готов / ready'
}

Step 'voices' {
    Say 'Голоса (Piper)' 'Voices (Piper)'
    $map = @{}
    foreach ($lang in $Voices.Keys) {
        $name = $Voices[$lang]
        $p = $name -split '-'
        $loc = $p[0]; $speaker = ($p[1..($p.Count - 2)]) -join '-'; $q = $p[-1]
        $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/$($loc.Split('_')[0])/$loc/$speaker/$q/$name"
        Get-File "$base.onnx" (Join-Path $Root "models\piper\$name.onnx")
        Get-File "$base.onnx.json" (Join-Path $Root "models\piper\$name.onnx.json")
        $map[$lang] = "../models/piper/$name.onnx"
    }
    $global:Updates.tts_voices = $map
    $global:Updates.tts_voice = $map[@($Voices.Keys)[0]]
}

# ------------------------------------------------------------------------------------------------ Baritone, Java
Step 'baritone' {
    Say 'Baritone (навигация тела)' 'Baritone (pathfinding)'
    $libs = Join-Path $Root 'mod\libs'
    $have = Get-ChildItem $libs -Filter 'baritone-api-forge-*.jar' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $have) {
        $asset = (Get-GhRelease 'cabaletta/baritone' $BaritoneTag).assets | Where-Object { $_.name -match '^baritone-api-forge-[\d.]+\.jar$' } | Select-Object -First 1
        if (-not $asset) { throw "no baritone-api-forge jar in $BaritoneTag" }
        Get-File $asset.browser_download_url (Join-Path $libs $asset.name)
        $have = Get-Item (Join-Path $libs $asset.name)
    } else { Ok "есть / present: $($have.Name)" }
    $global:Updates.extra_bot_mods = @("../mod/libs/$($have.Name)")
}

Step 'java' {
    Say 'Java 17 (для сборки мода)' 'Java 17 (to build the mod)'
    if (Get-ChildItem (Join-Path $Root 'tools') -Directory -Filter 'jdk-17*' -ErrorAction SilentlyContinue) { Ok 'есть / present'; return }
    Get-File 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse' "$env:TEMP\altron-jdk17.zip"
    Expand-Zip "$env:TEMP\altron-jdk17.zip" (Join-Path $Root 'tools')
    Ok 'JDK 17 готов / ready'
}

# ------------------------------------------------------------------------------------------------ the modpack
Step 'modpack' {
    Say 'Сборка Minecraft' 'Minecraft modpack'
    $mc = Join-Path $env:APPDATA '.minecraft'
    $packs = @(Get-ChildItem "$mc\versions" -Directory -ErrorAction SilentlyContinue |
        Where-Object { (Test-Path "$($_.FullName)\$($_.Name).json") -and (Test-Path "$($_.FullName)\mods") })
    if (-not $packs) { Warn 'Сборок с модами не нашёл — поставь сборку на Forge 1.20.1 и запусти установщик ещё раз / no modded pack found'; return }
    $pack = $packs[0]
    if ($packs.Count -gt 1) {
        for ($i = 0; $i -lt $packs.Count; $i++) { Write-Host "   $($i + 1) — $($packs[$i].Name)" }
        $n = Read-Host "   В какой сборке играть с Альтроном? Which pack? [1-$($packs.Count)]"
        if ($n -match '^\d+$' -and [int]$n -ge 1 -and [int]$n -le $packs.Count) { $pack = $packs[[int]$n - 1] }
    }
    if ((Get-Content -Raw "$($pack.FullName)\$($pack.Name).json") -notmatch '1\.20\.1') { Warn "«$($pack.Name)» похоже не 1.20.1 — Альтрон сделан для Forge 1.20.1 / not 1.20.1?" }
    $global:Updates.pack_version = $pack.Name
    if (-not (Get-ChildItem "$($pack.FullName)\mods" -Filter 'voicechat*.jar')) {
        $q = 'loaders=%5B%22forge%22%5D&game_versions=%5B%221.20.1%22%5D'
        $v = @(Invoke-RestMethod "https://api.modrinth.com/v2/project/simple-voice-chat/version?$q" -Headers $UA)[0]
        $f = @($v.files | Where-Object { $_.primary })[0]
        if (-not $f) { $f = $v.files[0] }
        Get-File $f.url "$($pack.FullName)\mods\$($f.filename)"
        Ok "добавил в сборку / added to the pack: $($f.filename)"
    } else { Ok 'Simple Voice Chat есть / present' }
}

# ------------------------------------------------------------------------------------------------ config
Step 'config' {
    Say 'Настройки' 'Settings'
    if ($global:Fresh) {
        # a new install speaks the language of this Windows first (the brain's window and Altron's voice)
        $c = (Get-Culture).TwoLetterISOLanguageName
        $global:Updates.languages = if (@('ru', 'uk', 'be', 'kk') -contains $c) { @('ru', 'en') } else { @('en', 'ru') }
    }
    $upd = Join-Path $env:TEMP 'altron-config-updates.json'
    [IO.File]::WriteAllText($upd, ($global:Updates | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))
    $merge = @'
import json, pathlib, sys
brain, upd = pathlib.Path(sys.argv[1]), json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
cfg_p, def_p = brain / "config.json", brain / "config.default.json"
cfg = json.loads(cfg_p.read_text(encoding="utf-8-sig"))
if def_p.exists():
    for k, v in json.loads(def_p.read_text(encoding="utf-8-sig")).items():
        cfg.setdefault(k, v)
    def_p.unlink()
cfg.update(upd)
cfg_p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("   + config.json: " + (", ".join(sorted(upd)) or "ok"))
'@
    $mp = Join-Path $env:TEMP 'altron-merge-config.py'
    [IO.File]::WriteAllText($mp, $merge, (New-Object Text.UTF8Encoding $false))
    & (Join-Path $Brain '.venv\Scripts\python.exe') $mp $Brain $upd
    if ($LASTEXITCODE -ne 0) { throw 'config merge' }
}

# ------------------------------------------------------------------------------------------------ build the mod
Step 'mod' {
    Say 'Собираю мод Альтрона (первый раз 5-15 минут)' 'Building the Altron mod (5-15 minutes the first time)'
    $jdk = Get-ChildItem (Join-Path $Root 'tools') -Directory -Filter 'jdk-17*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
    $env:GRADLE_USER_HOME = Join-Path $Root 'tools\gradle-home'
    Push-Location (Join-Path $Root 'mod')
    try { & .\gradlew.bat --no-daemon --console=plain -q build } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'gradle build (запусти build_mod.bat позже / run build_mod.bat later)' }
    Ok 'мод собран, мозг сам поставит его в сборку / built; the brain installs it into the pack'
}

Step 'shortcut' {
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Altron.lnk'))
    $lnk.TargetPath = Join-Path $Root 'start_altron.bat'
    $lnk.WorkingDirectory = $Root
    $lnk.Save()
    Ok 'ярлык на рабочем столе / desktop shortcut: Altron'
}

# ------------------------------------------------------------------------------------------------ done
if ($Failed) {
    Say "Готово, но с ошибками: $($Failed -join ', ')" "Done with errors: $($Failed -join ', ') — run the installer again to retry"
} else {
    Say 'Готово!' 'Done!'
}
Write-Host @"

   1. Ярлык «Altron» на рабочем столе — запускает мозг. Дождись строки «ИИ готов».
   2. Запусти сборку, зайди в мир, напиши в чате /altron.
   3. Говори в голосовом чате: «Альтрон, иди за мной».

   1. The "Altron" desktop shortcut starts the brain. Wait for "ИИ готов" (AI ready).
   2. Start the modpack, open your world and type /altron in chat.
   3. Talk in voice chat: "Altron, follow me".
"@
