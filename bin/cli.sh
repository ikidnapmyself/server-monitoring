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

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/colors.sh"
source "$SCRIPT_DIR/lib/paths.sh"

PROJECT_ROOT="$PROJECT_DIR"
cd "$PROJECT_ROOT"

# ============================================================================
# Helper Functions (used by cli modules)
# ============================================================================

show_banner() {
    clear
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                    Server Maintenance CLI                    ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    if [ ! -f "$SCRIPT_DIR/aliases.sh" ]; then
        echo -e "${YELLOW}Tip:${NC} Run ${CYAN}bin/install.sh aliases${NC} for quick command aliases (sm-check-health, sm-run-check, etc.)"
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
# Source menu modules
# ============================================================================

source "$SCRIPT_DIR/cli/install_menu.sh"
source "$SCRIPT_DIR/cli/health.sh"
source "$SCRIPT_DIR/cli/alerts.sh"
source "$SCRIPT_DIR/cli/intelligence.sh"
source "$SCRIPT_DIR/cli/pipeline.sh"
source "$SCRIPT_DIR/cli/notifications.sh"

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