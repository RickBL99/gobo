#!/bin/zsh
# Build Gobo's Rust to WebAssembly and put it where the page loads it.
#
#   tools/build_wasm.sh
#
#   studio/static/direction.wasm   the shot compiler (crates/direction)
#   studio/static/gobo.wasm        scrim + flag + dingle (crates/gobo-wasm)
#
# Needs Rust and its wasm target (rustup target add wasm32-unknown-unknown).
# The .wasm files are checked in, like the catalogue snapshots, so running the
# studio (or building its Docker image) never needs a Rust toolchain.
set -e
cd "$(dirname "$0")/.."
cargo build --release -p cinematic-direction -p gobo-wasm --target wasm32-unknown-unknown
cp target/wasm32-unknown-unknown/release/cinematic_direction.wasm studio/static/direction.wasm
cp target/wasm32-unknown-unknown/release/gobo_wasm.wasm studio/static/gobo.wasm
# which source each was built from, so the tests can tell a stale .wasm
cat crates/direction/Cargo.toml crates/direction/src/*.rs | shasum -a 256 | cut -d' ' -f1 > studio/static/direction.wasm.src
cat crates/{scrim,flag,dingle,gobo-wasm}/Cargo.toml crates/{scrim,flag,dingle,gobo-wasm}/src/*.rs | shasum -a 256 | cut -d' ' -f1 > studio/static/gobo.wasm.src
for f in direction gobo; do ls -l studio/static/$f.wasm | awk -v f=$f '{print "studio/static/" f ".wasm  " $5 " bytes"}'; done
