# Sourced by cli.sh — do not execute directly.

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