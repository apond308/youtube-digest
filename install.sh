#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# YouTube Digest - Interactive Installer
#
# Usage:  ./install.sh
#
# What it does:
#   1. Installs system dependencies (optional, Linux only)
#   2. Creates a Python virtualenv and installs the project
#   3. Walks you through .env, channels.yaml, and subscribers.yaml setup
#   4. Optionally installs a systemd service
#   5. Optionally sets up a Cloudflare tunnel
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
TEMPLATE_DIR="${PROJECT_ROOT}/deploy/templates"
CURRENT_USER="$(id -un)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

ENV_FILE="${PROJECT_ROOT}/.env"
CHANNELS_FILE="${PROJECT_ROOT}/channels.yaml"
SUBSCRIBERS_FILE="${PROJECT_ROOT}/subscribers.yaml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '\033[0;32m[INFO]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[0;33m[WARN]\033[0m  %s\n' "$*"; }
error() { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; }

as_root() {
    if [[ "${EUID}" -eq 0 ]]; then "$@"
    else sudo "$@"; fi
}

prompt_default() {
    local var_name="$1" label="$2" default_value="${3:-}" value=""
    read -r -p "${label} [${default_value}]: " value || true
    value="${value:-${default_value}}"
    printf -v "${var_name}" '%s' "${value}"
}

prompt_secret() {
    local var_name="$1" label="$2" value=""
    read -r -s -p "${label}: " value; printf '\n'
    printf -v "${var_name}" '%s' "${value}"
}

prompt_secret_default() {
    local var_name="$1" label="$2" default_value="${3:-}" value=""
    if [[ -n "${default_value}" ]]; then
        read -r -s -p "${label} (press Enter to keep existing): " value
    else
        read -r -s -p "${label}: " value
    fi
    printf '\n'
    value="${value:-${default_value}}"
    printf -v "${var_name}" '%s' "${value}"
}

prompt_yes_no() {
    local var_name="$1" label="$2" default_answer="${3:-y}" value=""
    local suffix="[Y/n]"
    [[ "${default_answer}" =~ ^[Nn]$ ]] && suffix="[y/N]"
    read -r -p "${label} ${suffix}: " value || true
    value="${value:-${default_answer}}"
    if [[ "${value}" =~ ^[Yy]$ ]]; then
        printf -v "${var_name}" '%s' "true"
    else
        printf -v "${var_name}" '%s' "false"
    fi
}

backup_file() {
    local file_path="$1"
    if [[ -f "${file_path}" ]]; then
        cp "${file_path}" "${file_path}.bak.${TIMESTAMP}"
        info "Backed up ${file_path} -> ${file_path}.bak.${TIMESTAMP}"
    fi
}

escape_env() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

escape_sed() {
    printf '%s' "$1" | sed -e 's/[\\&]/\\\\&/g'
}

get_env_value() {
    local file_path="$1" key="$2" line="" raw=""
    while IFS= read -r line || [[ -n "${line}" ]]; do
        case "${line}" in
            \#*|"") continue ;;
            "${key}"=*)
                raw="${line#*=}"; raw="${raw%\"}"; raw="${raw#\"}"
                raw="${raw%\'}"; raw="${raw#\'}"
                printf '%s' "${raw}"; return 0 ;;
        esac
    done < "${file_path}"
    return 1
}

render_template() {
    local template_path="$1" output_path="$2"; shift 2
    local content; content="$(<"${template_path}")"
    while [[ "$#" -gt 1 ]]; do
        local key="$1" value="$2"; shift 2
        local escaped; escaped="$(escape_sed "${value}")"
        content="$(printf '%s' "${content}" | sed "s|${key}|${escaped}|g")"
    done
    printf '%s\n' "${content}" > "${output_path}"
}

# ---------------------------------------------------------------------------
# OS Detection
# ---------------------------------------------------------------------------

detect_os() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        if [[ "${ID:-}" == "ubuntu" || "${ID_LIKE:-}" == *"ubuntu"* || "${ID_LIKE:-}" == *"debian"* || "${ID:-}" == "debian" ]]; then
            echo "debian"
            return
        fi
    fi
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux-generic" ;;
        *)      echo "unsupported" ;;
    esac
}

OS_TYPE="$(detect_os)"

# ---------------------------------------------------------------------------
# Step 1: System dependencies (optional)
# ---------------------------------------------------------------------------

install_system_deps() {
    case "${OS_TYPE}" in
        debian)
            info "Installing system packages via apt..."
            as_root apt-get update -qq
            as_root apt-get install -y -qq python3 python3-venv python3-pip curl
            ;;
        macos)
            if command -v brew >/dev/null 2>&1; then
                info "Installing system packages via Homebrew..."
                brew install python3 curl 2>/dev/null || true
            else
                warn "Homebrew not found. Please install Python 3.11+ and curl manually."
            fi
            ;;
        *)
            warn "Automatic dependency install not supported on this OS."
            warn "Please ensure Python 3.11+ and curl are installed."
            ;;
    esac
}

SYSTEM_PYTHON=""
find_python() {
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            local ver
            ver="$("${candidate}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
            local major minor
            major="${ver%%.*}"; minor="${ver##*.}"
            if [[ "${major}" -ge 3 && "${minor}" -ge 11 ]]; then
                SYSTEM_PYTHON="$(command -v "${candidate}")"
                return 0
            fi
        fi
    done
    return 1
}

printf '\n'
info "=== YouTube Digest Installer ==="
printf '\n'

if ! find_python; then
    prompt_yes_no DO_SYS_INSTALL "Python 3.11+ not found. Install system dependencies?" "y"
    if [[ "${DO_SYS_INSTALL}" == "true" ]]; then
        install_system_deps
        if ! find_python; then
            error "Python 3.11+ still not available after install. Please install manually."
            exit 1
        fi
    else
        error "Python 3.11+ is required. Please install it and re-run."
        exit 1
    fi
else
    info "Found Python: ${SYSTEM_PYTHON}"
fi

# ---------------------------------------------------------------------------
# Step 2: Virtual environment and pip install
# ---------------------------------------------------------------------------

VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"
INSTALL_PY_DEPS="true"

if [[ -d "${VENV_DIR}" ]]; then
    info "Virtual environment already exists at ${VENV_DIR}"
    prompt_yes_no INSTALL_PY_DEPS "Reinstall Python dependencies?" "n"
else
    info "Creating virtual environment at ${VENV_DIR}"
    "${SYSTEM_PYTHON}" -m venv "${VENV_DIR}"
fi

if [[ "${INSTALL_PY_DEPS}" == "true" ]]; then
    info "Installing Python dependencies..."
    "${PIP_BIN}" install --upgrade pip setuptools wheel -q
    "${PIP_BIN}" install -e "${PROJECT_ROOT}" -q
    info "Python dependencies installed."
else
    info "Skipping Python dependency reinstall."
fi

# ---------------------------------------------------------------------------
# Step 3: Configuration files
# ---------------------------------------------------------------------------

printf '\n'
info "=== Configuration ==="
printf '\n'

# ---- .env ----

EXISTING_OPENAI_API_KEY=""
EXISTING_OPENAI_BASE_URL=""
EXISTING_MODEL_NAME=""
EXISTING_GMAIL_ADDRESS=""
EXISTING_GMAIL_APP_PASSWORD=""
EXISTING_MAX_VIDEOS_PER_DAY=""

if [[ -f "${ENV_FILE}" ]]; then
    EXISTING_OPENAI_API_KEY="$(get_env_value "${ENV_FILE}" "OPENAI_API_KEY" || true)"
    EXISTING_OPENAI_BASE_URL="$(get_env_value "${ENV_FILE}" "OPENAI_BASE_URL" || true)"
    EXISTING_MODEL_NAME="$(get_env_value "${ENV_FILE}" "MODEL_NAME" || true)"
    EXISTING_GMAIL_ADDRESS="$(get_env_value "${ENV_FILE}" "GMAIL_ADDRESS" || true)"
    EXISTING_GMAIL_APP_PASSWORD="$(get_env_value "${ENV_FILE}" "GMAIL_APP_PASSWORD" || true)"
    EXISTING_MAX_VIDEOS_PER_DAY="$(get_env_value "${ENV_FILE}" "MAX_VIDEOS_PER_DAY" || true)"
fi

REUSE_ENV="false"
REUSE_CHANNELS="false"
REUSE_SUBSCRIBERS="false"

if [[ -f "${ENV_FILE}" ]]; then
    prompt_yes_no REUSE_ENV "Existing .env found. Keep it?" "y"
fi
if [[ -f "${CHANNELS_FILE}" ]]; then
    prompt_yes_no REUSE_CHANNELS "Existing channels.yaml found. Keep it?" "y"
fi
if [[ -f "${SUBSCRIBERS_FILE}" ]]; then
    prompt_yes_no REUSE_SUBSCRIBERS "Existing subscribers.yaml found. Keep it?" "y"
fi

if [[ "${REUSE_ENV}" == "true" ]]; then
    OPENAI_API_KEY="${EXISTING_OPENAI_API_KEY}"
    OPENAI_BASE_URL="${EXISTING_OPENAI_BASE_URL:-https://api.openai.com/v1}"
    MODEL_NAME="${EXISTING_MODEL_NAME:-gpt-4o-mini}"
    GMAIL_ADDRESS="${EXISTING_GMAIL_ADDRESS}"
    GMAIL_APP_PASSWORD="${EXISTING_GMAIL_APP_PASSWORD}"
    MAX_VIDEOS_PER_DAY="${EXISTING_MAX_VIDEOS_PER_DAY:-5}"
    if [[ -z "${OPENAI_API_KEY}" || -z "${GMAIL_ADDRESS}" || -z "${GMAIL_APP_PASSWORD}" ]]; then
        warn "Existing .env is missing required keys; collecting values interactively."
        REUSE_ENV="false"
    else
        info "Reusing existing .env."
    fi
fi

if [[ "${REUSE_ENV}" != "true" ]]; then
    printf '\n'
    info "-- LLM API --"
    prompt_default OPENAI_BASE_URL "OpenAI-compatible base URL" "${EXISTING_OPENAI_BASE_URL:-https://api.openai.com/v1}"
    prompt_default MODEL_NAME "Model name" "${EXISTING_MODEL_NAME:-gpt-4o-mini}"
    prompt_secret_default OPENAI_API_KEY "OpenAI API key" "${EXISTING_OPENAI_API_KEY}"

    printf '\n'
    info "-- Email (Gmail SMTP) --"
    info "You need a Gmail App Password: https://support.google.com/accounts/answer/185833"
    prompt_default GMAIL_ADDRESS "Gmail address for sending digests" "${EXISTING_GMAIL_ADDRESS}"
    prompt_secret_default GMAIL_APP_PASSWORD "Gmail app password" "${EXISTING_GMAIL_APP_PASSWORD}"

    printf '\n'
    prompt_default MAX_VIDEOS_PER_DAY "Default max videos per subscriber per day" "${EXISTING_MAX_VIDEOS_PER_DAY:-5}"

    if [[ -z "${OPENAI_API_KEY}" || -z "${GMAIL_ADDRESS}" || -z "${GMAIL_APP_PASSWORD}" ]]; then
        error "OPENAI_API_KEY, GMAIL_ADDRESS, and GMAIL_APP_PASSWORD are required."
        exit 1
    fi

    backup_file "${ENV_FILE}"
    cat > "${ENV_FILE}" <<ENVFILE
OPENAI_API_KEY="$(escape_env "${OPENAI_API_KEY}")"
OPENAI_BASE_URL="$(escape_env "${OPENAI_BASE_URL}")"
MODEL_NAME="$(escape_env "${MODEL_NAME}")"
GMAIL_ADDRESS="$(escape_env "${GMAIL_ADDRESS}")"
GMAIL_APP_PASSWORD="$(escape_env "${GMAIL_APP_PASSWORD}")"
MAX_VIDEOS_PER_DAY="$(escape_env "${MAX_VIDEOS_PER_DAY}")"
ENVFILE
    chmod 600 "${ENV_FILE}"
    info ".env written."
fi

# ---- channels.yaml ----

if [[ "${REUSE_CHANNELS}" != "true" ]]; then
    if [[ ! -f "${CHANNELS_FILE}" ]]; then
        cp "${PROJECT_ROOT}/channels.example.yaml" "${CHANNELS_FILE}"
        info "Created channels.yaml from example. Edit it to add your own channels."
    fi
fi

# ---- subscribers.yaml ----

if [[ "${REUSE_SUBSCRIBERS}" != "true" ]]; then
    printf '\n'
    info "-- Initial subscriber --"
    prompt_default SUBSCRIBER_NAME "Subscriber name" "${CURRENT_USER}"
    prompt_default SUBSCRIBER_EMAIL "Subscriber email" "${GMAIL_ADDRESS:-you@gmail.com}"
    prompt_default SUBSCRIBER_MAX_VIDEOS "Max videos per digest" "${MAX_VIDEOS_PER_DAY:-5}"

    CHANNEL_NAMES=()
    if [[ -f "${CHANNELS_FILE}" ]]; then
        while IFS= read -r name; do
            CHANNEL_NAMES+=("${name}")
        done < <("${PYTHON_BIN}" -c "
import yaml
with open('${CHANNELS_FILE}') as f:
    data = yaml.safe_load(f)
for ch in data.get('channels', []):
    print(ch.get('name', ''))
")
    fi

    backup_file "${SUBSCRIBERS_FILE}"
    {
        echo "subscribers:"
        echo "  - name: \"${SUBSCRIBER_NAME}\""
        echo "    email: \"${SUBSCRIBER_EMAIL}\""
        echo "    max_videos: ${SUBSCRIBER_MAX_VIDEOS}"
        echo "    channels:"
        if [[ ${#CHANNEL_NAMES[@]} -gt 0 ]]; then
            for channel in "${CHANNEL_NAMES[@]}"; do
                [[ -n "${channel}" ]] && echo "      - \"${channel}\""
            done
        else
            echo "      # Add channel names from channels.yaml here"
        fi
    } > "${SUBSCRIBERS_FILE}"
    info "subscribers.yaml written."
fi

info "Configuration complete."

# ---------------------------------------------------------------------------
# Step 4: Systemd service (optional, Linux only)
# ---------------------------------------------------------------------------

INSTALL_SERVICE="false"
if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl >/dev/null 2>&1; then
    printf '\n'
    prompt_yes_no INSTALL_SERVICE "Install as a systemd service (starts on boot)?" "n"
fi

if [[ "${INSTALL_SERVICE}" == "true" ]]; then
    prompt_default RUN_USER "Linux user to run the service" "${CURRENT_USER}"
    prompt_default SERVER_HOST "Server bind host" "0.0.0.0"
    prompt_default SERVER_PORT "Server port" "8080"

    TMP_SERVICE="$(mktemp)"
    render_template "${TEMPLATE_DIR}/youtube-digest.service.tpl" "${TMP_SERVICE}" \
        "__RUN_USER__" "${RUN_USER}" \
        "__WORKING_DIR__" "${PROJECT_ROOT}" \
        "__PYTHON_BIN__" "${PYTHON_BIN}" \
        "__SERVER_HOST__" "${SERVER_HOST}" \
        "__SERVER_PORT__" "${SERVER_PORT}"
    as_root cp "${TMP_SERVICE}" /etc/systemd/system/youtube-digest.service
    rm -f "${TMP_SERVICE}"
    as_root systemctl daemon-reload
    as_root systemctl enable --now youtube-digest
    as_root systemctl restart youtube-digest
    info "youtube-digest systemd service installed and started."

    sleep 2
    if curl -fsS "http://127.0.0.1:${SERVER_PORT}/api/health" >/dev/null 2>&1; then
        info "Health check passed: http://127.0.0.1:${SERVER_PORT}/api/health"
    else
        warn "Health check failed. Check logs: sudo journalctl -u youtube-digest -f"
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Cloudflare tunnel (optional)
# ---------------------------------------------------------------------------

SETUP_CLOUDFLARE="false"
if command -v cloudflared >/dev/null 2>&1; then
    printf '\n'
    prompt_yes_no SETUP_CLOUDFLARE "Set up a Cloudflare tunnel for public access?" "n"
elif [[ "${OS_TYPE}" == "debian" ]]; then
    printf '\n'
    prompt_yes_no SETUP_CLOUDFLARE "Set up a Cloudflare tunnel for public access? (will install cloudflared)" "n"
fi

if [[ "${SETUP_CLOUDFLARE}" == "true" ]]; then
    if ! command -v cloudflared >/dev/null 2>&1; then
        info "Installing cloudflared..."
        if [[ ! -f /usr/share/keyrings/cloudflare-main.gpg ]]; then
            curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | as_root gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
        fi
        echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
            | as_root tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
        as_root apt-get update -qq
        as_root apt-get install -y -qq cloudflared
    fi

    [[ -z "${SERVER_PORT:-}" ]] && prompt_default SERVER_PORT "Server port" "8080"

    prompt_default BASE_DOMAIN "Base domain (e.g. example.com)" ""
    prompt_default SUBDOMAIN "Subdomain" "youtube"
    PUBLIC_HOSTNAME="${SUBDOMAIN}.${BASE_DOMAIN}"
    prompt_default TUNNEL_NAME "Cloudflare tunnel name" "youtube-digest"

    RUN_USER="${RUN_USER:-${CURRENT_USER}}"
    RUN_USER_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6)"

    USER_CLOUDFLARED_DIR="${RUN_USER_HOME}/.cloudflared"
    CERT_FILE="${USER_CLOUDFLARED_DIR}/cert.pem"
    if [[ ! -f "${CERT_FILE}" ]]; then
        info "Launching interactive Cloudflare login..."
        cloudflared tunnel login
    else
        info "Found existing Cloudflare cert at ${CERT_FILE}."
    fi

    TUNNEL_ID="$(cloudflared tunnel list --output json | jq -r --arg name "${TUNNEL_NAME}" 'map(select(.name == $name))[0].id // empty')"
    if [[ -z "${TUNNEL_ID}" ]]; then
        cloudflared tunnel create "${TUNNEL_NAME}" >/dev/null
        TUNNEL_ID="$(cloudflared tunnel list --output json | jq -r --arg name "${TUNNEL_NAME}" 'map(select(.name == $name))[0].id // empty')"
    fi
    if [[ -z "${TUNNEL_ID}" ]]; then
        error "Unable to find or create tunnel '${TUNNEL_NAME}'."
        exit 1
    fi

    cloudflared tunnel route dns "${TUNNEL_ID}" "${PUBLIC_HOSTNAME}" || warn "DNS route may already exist."

    CREDENTIALS_SRC="${USER_CLOUDFLARED_DIR}/${TUNNEL_ID}.json"
    if [[ ! -f "${CREDENTIALS_SRC}" ]]; then
        error "Missing tunnel credentials: ${CREDENTIALS_SRC}"
        exit 1
    fi
    as_root mkdir -p /etc/cloudflared
    as_root cp "${CREDENTIALS_SRC}" "/etc/cloudflared/${TUNNEL_ID}.json"
    as_root chmod 600 "/etc/cloudflared/${TUNNEL_ID}.json"

    TMP_CF_CONFIG="$(mktemp)"
    render_template "${TEMPLATE_DIR}/cloudflared-config.yml.tpl" "${TMP_CF_CONFIG}" \
        "__TUNNEL_ID__" "${TUNNEL_ID}" \
        "__PUBLIC_HOSTNAME__" "${PUBLIC_HOSTNAME}" \
        "__SERVER_PORT__" "${SERVER_PORT}"
    as_root cp "${TMP_CF_CONFIG}" /etc/cloudflared/config.yml
    rm -f "${TMP_CF_CONFIG}"

    if ! as_root systemctl enable --now cloudflared 2>/dev/null; then
        as_root cloudflared service install || true
        as_root systemctl enable --now cloudflared
    fi
    as_root systemctl restart cloudflared
    info "Cloudflare tunnel configured for https://${PUBLIC_HOSTNAME}"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

printf '\n'
info "=== Setup Complete ==="
printf '\n'

if [[ "${INSTALL_SERVICE}" == "true" ]]; then
    echo "  Service:  sudo systemctl status youtube-digest"
    echo "  Logs:     sudo journalctl -u youtube-digest -f"
else
    echo "  Run the server:"
    echo "    source .venv/bin/activate"
    echo "    youtube-digest serve"
    printf '\n'
    echo "  Or run a one-off batch digest:"
    echo "    source .venv/bin/activate"
    echo "    youtube-digest run"
fi

if [[ "${SETUP_CLOUDFLARE}" == "true" ]]; then
    printf '\n'
    echo "  Public URL: https://${PUBLIC_HOSTNAME}"
    echo "  Remember to configure Cloudflare Access policies in the dashboard."
fi

printf '\n'
echo "  Configuration files:"
echo "    .env               - API keys and credentials"
echo "    channels.yaml      - YouTube channels to follow"
echo "    subscribers.yaml   - Email subscribers"
printf '\n'
