#!/usr/bin/env bash
#
# Interactive CLI for Server Maintenance
# Usage: ./bin/cli.sh [command]
#
# Commands:
#   (no args)    Start interactive mode
#   help         Show help message
#   install      Jump to installation menu
#   health       Jump to health monitoring
#   alerts       Jump to alerts menu
#   intel        Jump to intelligence menu
#   pipeline     Jump to pipeline menu
#   notify       Jump to notifications menu
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# ============================================================================
# Helper Functions
# ============================================================================

show_banner() {
    clear
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                    Server Maintenance CLI                    ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    # Show alias hint if aliases are not configured
    if [ ! -f "$SCRIPT_DIR/aliases.sh" ]; then
        echo -e "${YELLOW}Tip:${NC} Run ${CYAN}bin/setup_aliases.sh${NC} for quick command aliases (sm-check-health, sm-run-check, etc.)"
        echo ""
    fi
}

show_help() {
    echo "Usage: ./bin/cli.sh [command]"
    echo ""
    echo "Interactive CLI for Server Maintenance"
    echo ""
    echo "Commands:"
    echo "  (no args)    Start interactive mode"
    echo "  help         Show this help message"
    echo "  install      Jump to installation menu"
    echo "  health       Jump to health monitoring"
    echo "  alerts       Jump to alerts menu"
    echo "  intel        Jump to intelligence menu"
    echo "  pipeline     Jump to pipeline menu"
    echo "  notify       Jump to notifications menu"
    echo ""
}

confirm_and_run() {
    local cmd="$1"
    echo ""
    echo -e "${BOLD}Command to run:${NC}"
    echo -e "  ${CYAN}${cmd}${NC}"
    echo ""
    read -p "Run this command? (y/n): " confirm

    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo ""
        eval "$cmd"
        return $?
    else
        echo -e "${YELLOW}Command cancelled${NC}"
        return 1
    fi
}

run_command() {
    local cmd="$1"
    local description="${2:-Running command}"

    echo ""
    echo -e "${CYAN}Command: ${cmd}${NC}"
    echo ""

    if eval "$cmd"; then
        echo ""
        echo -e "${GREEN}✓ ${description} completed successfully${NC}"
    else
        echo ""
        echo -e "${RED}✗ ${description} failed${NC}"
    fi
}

# ============================================================================
# Main Menu
# ============================================================================

show_main_menu() {
    echo -e "${BOLD}Select an option:${NC}"
    echo ""

    local options=(
        "Install / Setup Project"
        "Health & Monitoring"
        "Alerts & Incidents"
        "Intelligence & Recommendations"
        "Pipeline Orchestration"
        "Notifications"
        "Exit"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) install_project ;;
            2) health_menu ;;
            3) alerts_menu ;;
            4) intelligence_menu ;;
            5) pipeline_menu ;;
            6) notify_menu ;;
            7) echo -e "${GREEN}Goodbye!${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

# ============================================================================
# Install / Setup
# ============================================================================

install_project() {
    show_banner
    echo -e "${BOLD}═══ Install / Setup Project ═══${NC}"
    echo ""

    local options=(
        "Full installation (uv sync + pre-commit)"
        "Install dependencies only (uv sync)"
        "Install pre-commit hooks"
        "Setup shell aliases"
        "Check installation status"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                echo -e "${YELLOW}Running full installation...${NC}"
                run_command "uv sync" "Installing dependencies"
                run_command "uv run pre-commit install" "Installing pre-commit hooks"
                ;;
            2)
                run_command "uv sync" "Installing dependencies"
                ;;
            3)
                run_command "uv run pre-commit install" "Installing pre-commit hooks"
                ;;
            4)
                run_command "$SCRIPT_DIR/setup_aliases.sh" "Setting up shell aliases"
                ;;
            5)
                check_installation
                ;;
            6)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

check_installation() {
    echo -e "${BOLD}Checking installation status...${NC}"
    echo ""

    # Check uv
    if command -v uv &> /dev/null; then
        echo -e "${GREEN}✓${NC} uv is installed ($(uv --version))"
    else
        echo -e "${RED}✗${NC} uv is not installed"
    fi

    # Check .venv
    if [ -d ".venv" ]; then
        echo -e "${GREEN}✓${NC} Virtual environment exists"
    else
        echo -e "${RED}✗${NC} Virtual environment not found"
    fi

    # Check pre-commit
    if [ -f ".git/hooks/pre-commit" ]; then
        echo -e "${GREEN}✓${NC} Pre-commit hooks installed"
    else
        echo -e "${YELLOW}!${NC} Pre-commit hooks not installed"
    fi

    # Check aliases
    if [ -f "$SCRIPT_DIR/aliases.sh" ]; then
        echo -e "${GREEN}✓${NC} Shell aliases configured"
    else
        echo -e "${YELLOW}!${NC} Shell aliases not configured (run bin/setup_aliases.sh)"
    fi

    # Check Django
    if uv run python manage.py check &> /dev/null; then
        echo -e "${GREEN}✓${NC} Django is configured correctly"
    else
        echo -e "${RED}✗${NC} Django check failed"
    fi
}

# ============================================================================
# Health & Monitoring
# ============================================================================

health_menu() {
    show_banner
    echo -e "${BOLD}═══ Health & Monitoring ═══${NC}"
    echo ""
    echo -e "${CYAN}Command: check_health${NC}"
    echo "Check system health metrics (CPU, memory, disk)"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --environment=ENV  Environment name (default: development)"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "Run health check (default)"
        "Run with JSON output"
        "Specify environment"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py check_health"
                ;;
            2)
                confirm_and_run "uv run python manage.py check_health --json"
                ;;
            3)
                read -p "Enter environment name [development]: " env_name
                env_name="${env_name:-development}"
                confirm_and_run "uv run python manage.py check_health --environment=$env_name"
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

# ============================================================================
# Alerts & Incidents
# ============================================================================

alerts_menu() {
    show_banner
    echo -e "${BOLD}═══ Alerts & Incidents ═══${NC}"
    echo ""

    local options=(
        "run_check - Run a specific checker"
        "check_and_alert - Run checker and create alert"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) run_check_menu ;;
            2) check_and_alert_menu ;;
            3) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

run_check_menu() {
    show_banner
    echo -e "${BOLD}═══ run_check ═══${NC}"
    echo ""
    echo "Run a health checker and display results"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  CHECKER_NAME       Name of checker to run (required)"
    echo "  --list             List available checkers"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "List available checkers"
        "Run a checker"
        "Run with JSON output"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_check --list"
                ;;
            2)
                read -p "Enter checker name: " checker_name
                if [ -n "$checker_name" ]; then
                    confirm_and_run "uv run python manage.py run_check $checker_name"
                else
                    echo -e "${RED}Checker name required${NC}"
                fi
                ;;
            3)
                read -p "Enter checker name: " checker_name
                if [ -n "$checker_name" ]; then
                    confirm_and_run "uv run python manage.py run_check $checker_name --json"
                else
                    echo -e "${RED}Checker name required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

check_and_alert_menu() {
    show_banner
    echo -e "${BOLD}═══ check_and_alert ═══${NC}"
    echo ""
    echo "Run checker and create incident if threshold exceeded"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  CHECKER_NAME           Name of checker (required)"
    echo "  --threshold=VALUE      Alert threshold (required)"
    echo "  --severity=LEVEL       Severity: info|warning|error|critical"
    echo "  --title=TITLE          Custom incident title"
    echo ""

    read -p "Enter checker name: " checker_name
    if [ -z "$checker_name" ]; then
        echo -e "${RED}Checker name required${NC}"
        return
    fi

    read -p "Enter threshold value: " threshold
    if [ -z "$threshold" ]; then
        echo -e "${RED}Threshold required${NC}"
        return
    fi

    read -p "Enter severity [warning]: " severity
    severity="${severity:-warning}"

    read -p "Enter custom title (optional): " title

    local cmd="uv run python manage.py check_and_alert $checker_name --threshold=$threshold --severity=$severity"
    if [ -n "$title" ]; then
        cmd="$cmd --title=\"$title\""
    fi

    confirm_and_run "$cmd"
}

# ============================================================================
# Intelligence & Recommendations
# ============================================================================

intelligence_menu() {
    show_banner
    echo -e "${BOLD}═══ Intelligence & Recommendations ═══${NC}"
    echo ""
    echo -e "${CYAN}Command: get_recommendations${NC}"
    echo "Get AI-powered recommendations for system optimization"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --memory           Analyze memory usage"
    echo "  --disk             Analyze disk usage"
    echo "  --all              Analyze everything"
    echo "  --path=PATH        Path for disk analysis (default: /)"
    echo "  --top-n=N          Number of top processes (default: 10)"
    echo "  --threshold-mb=MB  Large file threshold (default: 100)"
    echo "  --old-days=DAYS    Old file age in days (default: 30)"
    echo "  --json             Output as JSON"
    echo "  --provider=NAME    Provider to use (default: local)"
    echo "  --list-providers   List available providers"
    echo ""

    local options=(
        "Memory analysis"
        "Disk analysis"
        "Full analysis (memory + disk)"
        "Custom options"
        "List providers"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py get_recommendations --memory"
                ;;
            2)
                read -p "Enter path to analyze [/var/log]: " disk_path
                disk_path="${disk_path:-/var/log}"
                confirm_and_run "uv run python manage.py get_recommendations --disk --path=$disk_path"
                ;;
            3)
                confirm_and_run "uv run python manage.py get_recommendations --all"
                ;;
            4)
                custom_recommendations
                ;;
            5)
                confirm_and_run "uv run python manage.py get_recommendations --list-providers"
                ;;
            6)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

custom_recommendations() {
    echo ""
    echo -e "${BOLD}Configure custom analysis:${NC}"

    local cmd="uv run python manage.py get_recommendations"

    read -p "Include memory analysis? (y/n) [y]: " inc_memory
    if [[ "${inc_memory:-y}" =~ ^[Yy]$ ]]; then
        cmd="$cmd --memory"
    fi

    read -p "Include disk analysis? (y/n) [y]: " inc_disk
    if [[ "${inc_disk:-y}" =~ ^[Yy]$ ]]; then
        cmd="$cmd --disk"
        read -p "  Path to analyze [/]: " disk_path
        if [ -n "$disk_path" ]; then
            cmd="$cmd --path=$disk_path"
        fi
    fi

    read -p "Top N processes [10]: " top_n
    if [ -n "$top_n" ]; then
        cmd="$cmd --top-n=$top_n"
    fi

    read -p "Large file threshold MB [100]: " threshold_mb
    if [ -n "$threshold_mb" ]; then
        cmd="$cmd --threshold-mb=$threshold_mb"
    fi

    read -p "Output as JSON? (y/n) [n]: " use_json
    if [[ "$use_json" =~ ^[Yy]$ ]]; then
        cmd="$cmd --json"
    fi

    confirm_and_run "$cmd"
}

# ============================================================================
# Pipeline Orchestration
# ============================================================================

pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ Pipeline Orchestration ═══${NC}"
    echo ""

    local options=(
        "run_pipeline - Execute a pipeline"
        "monitor_pipeline - Monitor pipeline execution"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) run_pipeline_menu ;;
            2) monitor_pipeline_menu ;;
            3) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}

run_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ run_pipeline ═══${NC}"
    echo ""
    echo "Execute a pipeline definition"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  PIPELINE_NAME      Name of pipeline (or path to definition)"
    echo "  --list             List available pipelines"
    echo "  --dry-run          Show what would be executed"
    echo "  --json             Output as JSON"
    echo ""

    local options=(
        "List available pipelines"
        "Run a pipeline"
        "Dry run (preview)"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --list"
                ;;
            2)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline $pipeline_name"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            3)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline $pipeline_name --dry-run"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

monitor_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ monitor_pipeline ═══${NC}"
    echo ""
    echo "Monitor pipeline execution status"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  PIPELINE_ID        ID of pipeline execution to monitor"
    echo "  --list             List recent pipeline executions"
    echo "  --follow           Follow execution in real-time"
    echo ""

    local options=(
        "List recent executions"
        "Monitor a pipeline"
        "Follow pipeline (real-time)"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py monitor_pipeline --list"
                ;;
            2)
                read -p "Enter pipeline execution ID: " pipeline_id
                if [ -n "$pipeline_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline $pipeline_id"
                else
                    echo -e "${RED}Pipeline ID required${NC}"
                fi
                ;;
            3)
                read -p "Enter pipeline execution ID: " pipeline_id
                if [ -n "$pipeline_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline $pipeline_id --follow"
                else
                    echo -e "${RED}Pipeline ID required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

# ============================================================================
# Notifications
# ============================================================================

notify_menu() {
    show_banner
    echo -e "${BOLD}═══ Notifications ═══${NC}"
    echo ""

    local options=(
        "list_notify_drivers - List available notification drivers"
        "test_notify - Send a test notification"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py list_notify_drivers"
                ;;
            2)
                test_notify_menu
                ;;
            3)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}

test_notify_menu() {
    show_banner
    echo -e "${BOLD}═══ test_notify ═══${NC}"
    echo ""
    echo "Send a test notification to verify driver configuration"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  DRIVER_NAME        Name of notification driver (required)"
    echo "  --channel=NAME     Specific channel/recipient"
    echo "  --message=TEXT     Custom test message"
    echo ""

    read -p "Enter driver name: " driver_name
    if [ -z "$driver_name" ]; then
        echo -e "${RED}Driver name required${NC}"
        return
    fi

    read -p "Enter channel (optional): " channel
    read -p "Enter custom message (optional): " message

    local cmd="uv run python manage.py test_notify $driver_name"
    if [ -n "$channel" ]; then
        cmd="$cmd --channel=$channel"
    fi
    if [ -n "$message" ]; then
        cmd="$cmd --message=\"$message\""
    fi

    confirm_and_run "$cmd"
}

# ============================================================================
# Main Entry Point
# ============================================================================

main() {
    case "${1:-}" in
        help|--help|-h)
            show_help
            exit 0
            ;;
        install)
            show_banner
            install_project
            echo ""
            read -p "Press Enter to continue..."
            ;;
        health)
            show_banner
            health_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        alerts)
            show_banner
            alerts_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        intel|intelligence)
            show_banner
            intelligence_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        pipeline)
            show_banner
            pipeline_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        notify)
            show_banner
            notify_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        "")
            while true; do
                show_banner
                show_main_menu
                echo ""
                read -p "Press Enter to continue..."
            done
            ;;
        *)
            echo "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
