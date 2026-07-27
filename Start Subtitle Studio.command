#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

clear
printf "\n  SUBTITLE STUDIO\n"
printf "  Preparing your local workspace\n"

if curl -fsS --max-time 2 "http://localhost:3000/api/health" >/dev/null 2>&1; then
  printf "\n  Subtitle Studio is already running. Opening it now.\n"
  open "http://localhost:3000"
  exit 0
fi

step() {
  printf "\n\033[36m  %s\033[0m\n" "$1"
}

refresh_homebrew_path() {
  if [ -x "/opt/homebrew/bin/brew" ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x "/usr/local/bin/brew" ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

if ! command -v brew >/dev/null 2>&1; then
  step "Installing Homebrew for the first run"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  refresh_homebrew_path
fi

refresh_homebrew_path

if ! command -v node >/dev/null 2>&1; then
  step "Installing Node.js"
  brew install node
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  step "Installing FFmpeg"
  brew install ffmpeg
fi

PYTHON_COMMAND=""
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_COMMAND="$(command -v python3.11)"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)' 2>/dev/null; then
  PYTHON_COMMAND="$(command -v python3)"
else
  step "Installing Python 3.11"
  brew install python@3.11
  PYTHON_COMMAND="$(brew --prefix python@3.11)/bin/python3.11"
fi

if [ ! -x ".venv/bin/python" ]; then
  step "Creating the application environment"
  "$PYTHON_COMMAND" -m venv .venv
fi

step "Checking application components"
".venv/bin/python" -m pip install --disable-pip-version-check --quiet -e ".[dev]"

if ! ".venv/bin/python" -c "import whisper" >/dev/null 2>&1; then
  step "Installing Whisper speech recognition (first run can take several minutes)"
  ".venv/bin/python" -m pip install --disable-pip-version-check openai-whisper
fi

npm install --no-audit --no-fund --silent

step "Starting Subtitle Studio"
printf "  The app will open at http://localhost:3000\n"
printf "  Keep this window open while you work. Press Control+C to stop.\n\n"

(
  attempt=0
  while [ "$attempt" -lt 120 ]; do
    if curl -fsS --max-time 2 "http://localhost:3000" >/dev/null 2>&1; then
      open "http://localhost:3000"
      exit 0
    fi
    attempt=$((attempt + 1))
    sleep 0.75
  done
) &
BROWSER_WATCHER_PID=$!

cleanup() {
  kill "$BROWSER_WATCHER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

npm run dev
