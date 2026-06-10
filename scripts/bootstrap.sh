#!/usr/bin/env sh
set -e

usage() {
  cat <<'EOF'
Usage: bootstrap.sh [options]

Options:
  --path PATH                    Target repository path. Defaults to current git root or current directory.
  --language en|zh-CN            Harness language. Defaults to en.
  --adapter auto|generic|python|node|go|rust|monorepo|docker|bazel|java|kotlin|dotnet|swift|dart|ruby|php
                                 Project adapter. Defaults to auto.
  --agent-provider command|codex|claude-code|opencode
                                 Agent provider preset. Defaults to command.
  --agent-command PATH           Override provider CLI command path.
  --yes                          Accept defaults and skip prompts.
  --no-install                   Do not install Attestflow if python -m attestflow is unavailable.
  --no-doctor                    Skip attestflow doctor after init.
  -h, --help                     Show this help.

Environment:
  PYTHON_BIN                     Python executable. Defaults to python3.
  ATTESTFLOW_INSTALL_SPEC        pip install spec. Defaults to git+https://github.com/neaomiao/attestflow.git.
  ATTESTFLOW_SKIP_INSTALL=1      Same behavior as --no-install.
EOF
}

info() {
  printf '%s\n' "$*"
}

error() {
  printf 'ERROR: %s\n' "$*" >&2
}

is_choice() {
  value=$1
  shift
  for choice in "$@"; do
    if [ "$value" = "$choice" ]; then
      return 0
    fi
  done
  return 1
}

require_value() {
  option=$1
  value=${2:-}
  if [ -z "$value" ]; then
    error "$option requires a value"
    exit 2
  fi
}

prompt_choice() {
  label=$1
  default=$2
  choices=$3
  shift 3
  if [ "$ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    printf '%s\n' "$default"
    return 0
  fi
  while :; do
    printf '%s [%s] %s: ' "$label" "$default" "$choices" >&2
    answer=
    IFS= read -r answer || answer=
    if [ -z "$answer" ]; then
      answer=$default
    fi
    if is_choice "$answer" "$@"; then
      printf '%s\n' "$answer"
      return 0
    fi
    error "invalid choice: $answer"
  done
}

detect_repo_root() {
  if command -v git >/dev/null 2>&1; then
    git_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
    if [ -n "$git_root" ]; then
      printf '%s\n' "$git_root"
      return 0
    fi
  fi
  pwd
}

detect_adapter() {
  target=$1
  if [ -f "$target/pnpm-workspace.yaml" ] || [ -f "$target/turbo.json" ] || [ -f "$target/nx.json" ]; then
    printf '%s\n' monorepo
  elif [ -f "$target/package.json" ]; then
    printf '%s\n' node
  elif [ -f "$target/pyproject.toml" ] || [ -f "$target/setup.py" ] || [ -f "$target/setup.cfg" ]; then
    printf '%s\n' python
  elif [ -f "$target/go.mod" ]; then
    printf '%s\n' go
  elif [ -f "$target/Cargo.toml" ]; then
    printf '%s\n' rust
  elif [ -f "$target/Dockerfile" ] || [ -f "$target/compose.yaml" ] || [ -f "$target/docker-compose.yml" ]; then
    printf '%s\n' docker
  elif [ -f "$target/MODULE.bazel" ] || [ -f "$target/WORKSPACE.bazel" ] || [ -f "$target/WORKSPACE" ]; then
    printf '%s\n' bazel
  elif [ -f "$target/pom.xml" ] || [ -f "$target/build.gradle" ] || [ -f "$target/build.gradle.kts" ]; then
    printf '%s\n' java
  elif has_dotnet_project "$target"; then
    printf '%s\n' dotnet
  elif [ -f "$target/Package.swift" ]; then
    printf '%s\n' swift
  elif [ -f "$target/pubspec.yaml" ]; then
    printf '%s\n' dart
  elif [ -f "$target/Gemfile" ] || [ -f "$target/Rakefile" ]; then
    printf '%s\n' ruby
  elif [ -f "$target/composer.json" ]; then
    printf '%s\n' php
  else
    printf '%s\n' generic
  fi
}

has_dotnet_project() {
  find "$1" -maxdepth 1 \( -name '*.sln' -o -name '*.csproj' \) -print -quit 2>/dev/null | grep -q .
}

ensure_attestflow() {
  python_bin=$1
  if "$python_bin" -m attestflow --help >/dev/null 2>&1; then
    return 0
  fi
  if [ "$NO_INSTALL" = "1" ] || [ "${ATTESTFLOW_SKIP_INSTALL:-0}" = "1" ]; then
    error "python -m attestflow is unavailable and installation is disabled"
    return 1
  fi
  install_spec=${ATTESTFLOW_INSTALL_SPEC:-git+https://github.com/neaomiao/attestflow.git}
  info "Installing Attestflow with $python_bin -m pip install --user $install_spec"
  "$python_bin" -m pip install --user "$install_spec"
}

TARGET_PATH=
LANGUAGE=
ADAPTER=
AGENT_PROVIDER=
AGENT_COMMAND=
ASSUME_YES=0
NO_INSTALL=0
RUN_DOCTOR=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --path)
      require_value "$1" "${2:-}"
      TARGET_PATH=${2:-}
      shift 2
      ;;
    --language)
      require_value "$1" "${2:-}"
      LANGUAGE=${2:-}
      shift 2
      ;;
    --adapter)
      require_value "$1" "${2:-}"
      ADAPTER=${2:-}
      shift 2
      ;;
    --agent-provider)
      require_value "$1" "${2:-}"
      AGENT_PROVIDER=${2:-}
      shift 2
      ;;
    --agent-command)
      require_value "$1" "${2:-}"
      AGENT_COMMAND=${2:-}
      shift 2
      ;;
    --yes)
      ASSUME_YES=1
      shift
      ;;
    --no-install)
      NO_INSTALL=1
      shift
      ;;
    --no-doctor)
      RUN_DOCTOR=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      error "unknown option: $1"
      usage >&2
      exit 2
      ;;
  esac
done

PYTHON_BIN=${PYTHON_BIN:-python3}
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  error "Python executable not found: $PYTHON_BIN"
  exit 1
fi

if [ -z "$TARGET_PATH" ]; then
  TARGET_PATH=$(detect_repo_root)
fi
mkdir -p "$TARGET_PATH"
TARGET_PATH=$(cd "$TARGET_PATH" && pwd)

if [ -z "$LANGUAGE" ]; then
  LANGUAGE=$(prompt_choice "Select harness language" "en" "(en, zh-CN)" "en" "zh-CN")
elif ! is_choice "$LANGUAGE" "en" "zh-CN"; then
  error "unsupported language: $LANGUAGE"
  exit 2
fi

if [ -z "$ADAPTER" ]; then
  ADAPTER=$(prompt_choice "Select project adapter" "auto" "(auto, generic, python, node, go, rust, monorepo, docker, bazel, java, kotlin, dotnet, swift, dart, ruby, php)" "auto" "generic" "python" "node" "go" "rust" "monorepo" "docker" "bazel" "java" "kotlin" "dotnet" "swift" "dart" "ruby" "php")
elif ! is_choice "$ADAPTER" "auto" "generic" "python" "node" "go" "rust" "monorepo" "docker" "bazel" "java" "kotlin" "dotnet" "swift" "dart" "ruby" "php"; then
  error "unsupported adapter: $ADAPTER"
  exit 2
fi

if [ "$ADAPTER" = "auto" ]; then
  ADAPTER=$(detect_adapter "$TARGET_PATH")
  info "Detected adapter: $ADAPTER"
fi

if [ -z "$AGENT_PROVIDER" ]; then
  AGENT_PROVIDER=$(prompt_choice "Select agent provider" "command" "(command, codex, claude-code, opencode)" "command" "codex" "claude-code" "opencode")
elif ! is_choice "$AGENT_PROVIDER" "command" "codex" "claude-code" "opencode"; then
  error "unsupported agent provider: $AGENT_PROVIDER"
  exit 2
fi

ensure_attestflow "$PYTHON_BIN"

info "Initializing Attestflow in $TARGET_PATH"
if [ -n "$AGENT_COMMAND" ]; then
  "$PYTHON_BIN" -m attestflow init \
    --path "$TARGET_PATH" \
    --adapter "$ADAPTER" \
    --language "$LANGUAGE" \
    --agent-provider "$AGENT_PROVIDER" \
    --agent-command "$AGENT_COMMAND"
else
  "$PYTHON_BIN" -m attestflow init \
    --path "$TARGET_PATH" \
    --adapter "$ADAPTER" \
    --language "$LANGUAGE" \
    --agent-provider "$AGENT_PROVIDER"
fi

if [ "$RUN_DOCTOR" = "1" ]; then
  info "Running attestflow doctor"
  (cd "$TARGET_PATH" && "$PYTHON_BIN" -m attestflow doctor)
fi

info "Attestflow onboarding complete"
