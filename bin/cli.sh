#!/usr/bin/env bash
#
# Interactive CLI for Server Maintenance
# Usage: ./bin/cli.sh [command]
#
# Commands:
#   (no args)    Start interactive mode
#   help         Show help message
#   install      Jump to installation menu
#   health       Jump to health menu
#   pipeline     Jump to pipeline menu
#   intel        Jump to intelligence menu
#   notify       Jump to notifications menu
#   cluster      Jump to cluster menu
#   update       Jump to updates menu
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/colors.sh"
source "$SCRIPT_DIR/lib/paths.sh"
source "$SCRIPT_DIR/lib/tuin.sh"
source "$SCRIPT_DIR/lib/pickers.sh"

PROJECT_ROOT="$PROJECT_DIR"
cd "$PROJECT_ROOT"

# ============================================================================
# Helper Functions (used by cli modules)
# ============================================================================

show_banner() {
    clear
    tuin_banner "Server Maintenance CLI"
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
    echo "  health       Jump to health menu"
    echo "  pipeline     Jump to pipeline menu"
    echo "  intel        Jump to intelligence menu"
    echo "  notify       Jump to notifications menu"
    echo "  cluster      Jump to cluster menu"
    echo "  update       Jump to updates menu"
    echo ""
}

confirm_and_run() {
    local cmd="$1"
    tuin_section "Command to run"
    echo -e "  ${CYAN}${cmd}${NC}"
    if tuin_confirm "Run this command?" n; then
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
source "$SCRIPT_DIR/cli/pipeline.sh"
source "$SCRIPT_DIR/cli/intelligence.sh"
source "$SCRIPT_DIR/cli/notifications.sh"
source "$SCRIPT_DIR/cli/cluster.sh"
source "$SCRIPT_DIR/cli/update.sh"

# ============================================================================
# Main Menu
# ============================================================================

main_menu_loop() {
    local TUIN_MENU_BACK="Exit"
    while true; do
        show_banner
        if tuin_menu "Select an option" \
            "Install / Setup" "Health" "Pipeline" "Intelligence" \
            "Notifications" "Cluster" "Updates"
        then
            case $TUIN_REPLY in
                "Install / Setup") install_project ;;
                "Health")          health_menu ;;
                "Pipeline")        pipeline_menu ;;
                "Intelligence")    intelligence_menu ;;
                "Notifications")   notify_menu ;;
                "Cluster")         cluster_menu ;;
                "Updates")         update_menu ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            echo -e "${GREEN}Goodbye!${NC}"
            return 0
        fi
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
        pipeline)
            show_banner
            pipeline_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        intel|intelligence)
            show_banner
            intelligence_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        notify)
            show_banner
            notify_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        cluster)
            show_banner
            cluster_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        update)
            show_banner
            update_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        "")
            main_menu_loop
            ;;
        *)
            echo "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"