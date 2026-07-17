#!/bin/sh
# Static analysis for the firmware sources.
#
# Requires: cppcheck (apt/brew install cppcheck), cpplint (pip install cpplint).
# Neither needs the ESP32 toolchain, so this runs anywhere — including
# sandboxes where the PlatformIO platform download is unavailable.
# clang-tidy is intentionally not wired up: it needs the full Arduino/ESP-IDF
# include tree to produce signal instead of noise. Run it via `pio check`
# on a machine with the toolchain installed if deeper analysis is wanted.
set -eu
cd "$(dirname "$0")"

echo "== cppcheck =="
# missingIncludeSystem: Arduino/ESP-IDF headers aren't present off-target.
cppcheck --enable=warning,style,performance,portability \
  --std=c++17 --language=c++ --inline-suppr \
  --suppress=missingIncludeSystem \
  --error-exitcode=1 \
  -I src src/main.cpp

echo "== cpplint =="
# legal/copyright: no license headers in this repo.
# build/include_subdir: config.h lives next to main.cpp by design.
cpplint --filter=-legal/copyright,-build/include_subdir --linelength=100 \
  src/main.cpp src/config.example.h

echo "lint clean"
