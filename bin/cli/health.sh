# Sourced by cli.sh — do not execute directly.

health_menu() {
    show_banner
    echo -e "${BOLD}═══ Health ═══${NC}"
    echo ""

    local options=(
        "Run all health checks"
        "Run specific checkers"
        "Run a single checker"
        "List available checkers"
        "Preflight dashboard"
        "JSON output (all checks)"
        "CI mode: fail on warning"
        "CI mode: fail on critical only"
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
                echo ""
                run_command "uv run python manage.py check_health --list" "Available checkers"
                echo ""
                read -p "Enter checker name: " checker_name
                if [ -n "$checker_name" ]; then
                    confirm_and_run "uv run python manage.py run_check $checker_name"
                else
                    echo -e "${RED}Checker name required${NC}"
                fi
                ;;
            4)
                run_command "uv run python manage.py check_health --list" "Available checkers"
                ;;
            5)
                confirm_and_run "uv run python manage.py preflight"
                ;;
            6)
                confirm_and_run "uv run python manage.py check_health --json"
                ;;
            7)
                confirm_and_run "uv run python manage.py check_health --fail-on-warning"
                ;;
            8)
                confirm_and_run "uv run python manage.py check_health --fail-on-critical"
                ;;
            9)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}