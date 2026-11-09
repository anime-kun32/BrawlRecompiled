#!/usr/bin/env bash
# Unattended Linux build for a self-hosted runner (written for Arch): your disc image -> game modules -> translation
# -> compile -> launcher -> dist/BrawlRecompiled-linux.tar.gz. It is the Linux counterpart of tools\build-brawl.ps1
# plus tools/build-linux.sh and tools/package-linux.sh, which it calls for the compile and the packing.
#
#   BRAWL_ISO=/path/to/your/brawl.iso bash tools/ci-build-linux.sh
#
# Environment:
#   BRAWL_ISO          required: your RSBE01 disc image (.iso or .wbfs), read in place and never copied or uploaded.
#                      Only sys/ and files/module/ are extracted, into the git-ignored Disc/ folder of this checkout.
#   BRAWL_OUT          where the finished tarball is left (default ~/brawl-out)
#   BRAWL_DECOMP_REF   git ref of doldecomp/brawl to use for the function map (default: its default branch). The
#                      commit used is printed; pin it once you have a build that works.
#   FORCE_TRANSLATE=1  translate again even when the translation's inputs look unchanged
#   JOBS               parallel jobs (default: one per CPU, but at most one per 2.5 GB of free RAM, as build-brawl.ps1)
#
# Re-runs are cheap: each stage is skipped while its inputs are unchanged (stamps live next to its output), so a
# change that only touches runtime code goes straight to an incremental compile.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
wc=$root/wiicompiled
project=projects/brawl/recomp.yml
logs=$root/build-logs
out=${BRAWL_OUT:-$HOME/brawl-out}
mkdir -p "$logs" "$out"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
# Runs a command with its output in build-logs/<name>.log; shows the end of the log if it fails.
step() {
    local name=$1; shift
    echo "    $name ..."
    if ! "$@" >"$logs/$name.log" 2>&1; then
        tail -n 40 "$logs/$name.log" >&2
        fail "$name failed (full log: $logs/$name.log)"
    fi
}

# ---- 0. preflight
[[ -n ${BRAWL_ISO:-} ]] || fail "BRAWL_ISO is not set. Point it at your disc image, e.g. put BRAWL_ISO=/srv/brawl/brawl.iso in the runner's .env file."
[[ -r $BRAWL_ISO ]] || fail "cannot read BRAWL_ISO=$BRAWL_ISO (the runner's user needs read access)"
for tool in git python3 dotnet rsync curl tar unzip xz sha256sum; do
    command -v "$tool" >/dev/null || fail "'$tool' is not installed (see tools/ARCH-RUNNER.md for the package list)"
done
python3 -c 'import cryptography' 2>/dev/null || fail "python-cryptography is not installed (pacman -S python-cryptography)"

jobs=${JOBS:-}
if [[ -z $jobs ]]; then
    free_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    jobs=$(( free_kb / 2621440 ))
    (( jobs >= 1 )) || jobs=1
    (( jobs <= $(nproc) )) || jobs=$(nproc)
fi
export DOTNET_ROLL_FORWARD=Major DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
say "building $(git rev-parse --short HEAD) with $jobs jobs; disc: $(basename "$BRAWL_ISO")"

# ---- 1. disc image -> Disc/DATA (only what the build reads)
iso_id=$(stat -c '%s:%Y' "$BRAWL_ISO")   # size:mtime, so an 8 GB image is not hashed on every run
if [[ ! -f Disc/.iso-id || $(cat Disc/.iso-id) != "$iso_id" || ! -f Disc/DATA/sys/main.dol ]]; then
    say "extracting sys/ and files/module/ from the disc image"
    rm -rf Disc
    python3 tools/extract_wbfs.py "$BRAWL_ISO" Disc/DATA --only sys/ --only files/module/ | tee "$logs/extract.log" \
        || fail "extracting the disc image failed (is it a complete RSBE01 .iso or .wbfs?)"
    grep -q '^game RSBE01 ' "$logs/extract.log" || fail "this is not Brawl USA (RSBE01); the build needs that disc"
    grep -q 'missing from the image: 0$' "$logs/extract.log" || fail "the disc image is incomplete (see the MISSING lines above)"
    echo "$iso_id" > Disc/.iso-id
else
    say "disc already extracted (image unchanged)"
fi
mkdir -p game
cp -f Disc/DATA/sys/main.dol game/main.dol

# ---- 2. function map source: the Brawl decompilation
if [[ ! -d extras/brawl-decomp/.git ]]; then
    say "cloning doldecomp/brawl"
    mkdir -p extras
    git clone --quiet --depth 1 https://github.com/doldecomp/brawl.git extras/brawl-decomp
fi
if [[ -n ${BRAWL_DECOMP_REF:-} ]]; then
    git -C extras/brawl-decomp fetch --quiet --depth 1 origin "$BRAWL_DECOMP_REF"
    git -C extras/brawl-decomp checkout --quiet --detach FETCH_HEAD
fi
decomp_rev=$(git -C extras/brawl-decomp rev-parse HEAD)
echo "    doldecomp/brawl at $decomp_rev"

# ---- 3. all 126 modules pre-linked, plus main_translate.dol
mod_key=$(printf '%s\n' "$iso_id" "$decomp_rev" "$(sha256sum wiicompiled/projects/brawl/tools/build_modules.py wiicompiled/projects/brawl/tools/prelink_rels.py | awk '{print $1}')" | sha256sum | cut -d' ' -f1)
if [[ ! -f game/.modules-key || $(cat game/.modules-key) != "$mod_key" || ! -f game/brawl_modules.rel || ! -f game/main_translate.dol ]]; then
    say "building brawl_modules.rel and main_translate.dol"
    step build-modules python3 wiicompiled/projects/brawl/tools/build_modules.py \
        Disc/DATA/files/module extras/brawl-decomp/config/RSBE01_02 game/brawl_modules.rel wiicompiled game/main.dol
    [[ -f game/main_translate.dol ]] || fail "build_modules.py did not write game/main_translate.dol"
    echo "$mod_key" > game/.modules-key
fi
want_sha=$(grep -m1 -oP 'sha256:\s*\K[0-9a-f]{64}' wiicompiled/projects/brawl/recomp.yml || true)
have_sha=$(sha256sum game/main_translate.dol | cut -d' ' -f1)
if [[ -n $want_sha && $want_sha != "$have_sha" ]]; then
    fail "main_translate.dol is not the one this project was made for (want $want_sha, got $have_sha). Either the disc is not RSBE01 rev 1, or doldecomp/brawl at $decomp_rev differs from the one the project was made with: set BRAWL_DECOMP_REF."
fi

# ---- 4. translate (the slow stage)
say "translator"
step translator-build dotnet build wiicompiled/translator/src/Translator.Cli/Translator.Cli.csproj -c Release -v minimal
translator=$wc/translator/src/Translator.Cli/bin/Release/net8.0/Translator.Cli.dll
[[ -f $translator ]] || fail "translator was not built: $translator"

# What the translation depends on: the translator and the project's own files, the game's code, and the list of
# functions the runtime replaces natively (PPC_NATIVE_OVERRIDE lines). Changing what such a function *does* does not
# change the translation; adding or removing an override does. If in doubt, FORCE_TRANSLATE=1.
tr_key=$({
    git ls-files -z wiicompiled/translator wiicompiled/projects/brawl | xargs -0 sha256sum
    sha256sum game/main_translate.dol game/brawl_modules.rel
    grep -rh 'PPC_NATIVE_OVERRIDE' wiicompiled/runtime/src | LC_ALL=C sort
} | sha256sum | cut -d' ' -f1)
stamp=$wc/generated/.translate-key
if [[ -n ${FORCE_TRANSLATE:-} || ! -f $stamp || $(cat "$stamp") != "$tr_key" || ! -d $wc/generated/build_shards ]]; then
    say "translating the game (this takes a while)"
    cd "$wc"
    rm -f generated/.translate-key
    step translate dotnet "$translator" translate-recursive 0x8000403C --project "$project" \
        --outdir generated/functions --output-metadata generated/base_translation_output.json \
        --production-source-bundle generated/base_translation_sources.bin \
        --no-function-files --no-state-free-abi --data-in-text projects/brawl/resident_data_ranges.txt \
        --prune-stale --threads "$jobs"
    step data-init dotnet "$translator" generate-data-init --project "$project"
    step shards dotnet "$translator" emit-build-shards --project "$project" \
        --base-metadata generated/base_translation_output.json --base-functions-dir generated/functions \
        --native-source-dir runtime/src --out generated/build_shards --base-shards 600
    echo "$tr_key" > "$stamp"
    cd "$root"
else
    say "translation up to date, skipping"
fi

# ---- 5. compile with the portable clang toolchain (incremental after the first build)
say "compiling"
bash tools/build-linux.sh --jobs "$jobs" 2>&1 | tee "$logs/compile.log" || fail "compile failed (log: $logs/compile.log)"

# ---- 6. launcher and package
say "launcher"
step launcher dotnet publish launcher/linux/BrawlRecompiled.Linux.csproj -c Release -o launcher/linux/publish
say "packaging"
bash tools/package-linux.sh

# ---- 7. hand over
version=$(grep -oP '(?<=<Version>)[^<]+' launcher/BrawlRecompiled.Launcher.csproj)
final=$out/BrawlRecompiled-linux-v$version-$(git rev-parse --short HEAD).tar.gz
cp -f dist/BrawlRecompiled-linux.tar.gz "$final"
ln -sfn "$(basename "$final")" "$out/BrawlRecompiled-linux-latest.tar.gz"
sum=$(sha256sum "$final" | cut -d' ' -f1)
say "done: $final ($(du -h "$final" | cut -f1))"
echo "    sha256 $sum"
if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
    {
        echo "### Linux build"
        echo "- commit \`$(git rev-parse --short HEAD)\`, doldecomp/brawl \`${decomp_rev:0:12}\`"
        echo "- package: \`$final\` ($(du -h "$final" | cut -f1)), sha256 \`$sum\`"
        echo "- kept on the runner; nothing was uploaded"
    } >> "$GITHUB_STEP_SUMMARY"
fi
