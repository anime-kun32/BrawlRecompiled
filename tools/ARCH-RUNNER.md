# Self-hosted Arch runner

`.github/workflows/linux-build.yml` builds and packs the Linux download on your own machine, from your own disc image.
Nothing game-related leaves the machine: the image is read in place, and the package is left on the runner (no artifact
is uploaded; it contains recompiled game code).

## One-time setup

**Packages** (a starting set, not a verified minimum: if CMake's configure names something missing, add it):

```
sudo pacman -S --needed base-devel git python python-cryptography dotnet-sdk-8.0 rsync curl unzip xz pkgconf \
    vulkan-headers vulkan-icd-loader mesa libglvnd libx11 libxext libxcursor libxi libxrandr libxkbcommon \
    wayland wayland-protocols libdecor alsa-lib libpulse libdrm libusb icu krb5 openssl zlib
```

**Runner**: repo Settings > Actions > Runners > New self-hosted runner (Linux, x64), as a normal user, not root.
Register it with the label the workflow asks for:

```
./config.sh --url https://github.com/<you>/BrawlRecompiled --token <token> --labels arch
sudo ./svc.sh install "$USER" && sudo ./svc.sh start      # or just ./run.sh
```

**Build inputs**: create `.env` in the runner's folder, then restart the runner:

```
BRAWL_ISO=/path/to/your/brawl.iso        # required: Brawl USA (RSBE01), .iso or .wbfs
BRAWL_OUT=/path/to/where/packages/go     # optional, default ~/brawl-out
```

Only `sys/` and `files/module/` are read out of the image, into the git-ignored `Disc/` folder of the runner's checkout.
Needs about 2.5 GB of free RAM per compile job and a lot of disk (translated sources plus the build tree: plan for
tens of GB). The first build is long (translation, then ~600 shards); later ones skip the translation unless the
translator, the project files, the game or the list of `PPC_NATIVE_OVERRIDE`s changed. Run the workflow manually
with "retranslate" ticked to force it.

## Without GitHub

```
BRAWL_ISO=/path/to/brawl.iso bash tools/ci-build-linux.sh
```

The package ends up in `~/brawl-out/BrawlRecompiled-linux-latest.tar.gz`.

## Keep the runner safe

The workflow runs on `push` to `master` and by hand only. Do not add a `pull_request` trigger: on a public repo that
lets a stranger's fork run code on your machine. Also set Settings > Actions > General > "Approval for running fork
pull request workflows" to require approval, and treat write access to the repo as shell access to the runner.

## Rumble tap

The game sends every rumble request (GameCube pad and Wii Remote) to a Unix datagram socket, one JSON line each:
`$BRAWL_RUMBLE_SOCKET`, else `$XDG_RUNTIME_DIR/brawl-rumble.sock`. Set `BRAWL_RUMBLE_SOCKET=off` to disable it.
`python tools/rumble-listener.py` prints them (`--changes` for state changes only, `--raw` for the JSON). The message
format is documented in `wiicompiled/runtime/include/rumble_tap.h`.
