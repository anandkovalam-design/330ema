#!/bin/sh
set -eu

data_dir="${ZERODHA_DATA_DIR:-/mnt/zerodha}"

mkdir -p "$data_dir"
chown -R dashboard:dashboard "$data_dir"

exec gosu dashboard "$@"
