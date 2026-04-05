# Sourced by cli.sh — do not execute directly.

health_menu() {
    show_banner
    echo -e "${BOLD}═══ Health & Monitoring ═══${NC}"
    echo ""

    local options=(
        "Run all health checks"
        "Run specific checkers"
        "List available checkers"
        "JSON output"
        "Fail on warning (CI mode)"
        "Fail on critical only (CI mode)"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py check_health"
                ;;
            2)
                echo ""
                run_command "uv run python manage.py check_health --list" "Listing checkers"
                echo ""
                read -p "Enter checker names (space-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    confirm_and_run "uv run python manage.py check_health $checker_names"
                else
                    echo -e "${RED}No checkers specified${NC}"
                fi
                ;;
            3)
                run_command "uv run python manage.py check_health --list" "Available checkers"
                ;;
            4)
                confirm_and_run "uv run python manage.py check_health --json"
                ;;
            5)
                confirm_and_run "uv run python manage.py check_health --fail-on-warning"
                ;;
            6)
                confirm_and_run "uv run python manage.py check_health --fail-on-critical"
                ;;
            7)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}