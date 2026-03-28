# Sourced by cli.sh — do not execute directly.

alerts_menu() {
    show_banner
    echo -e "${BOLD}═══ Alerts & Incidents ═══${NC}"
    echo ""

    local options=(
        "run_check - Run a specific checker"
        "check_and_alert - Run checks pipeline"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
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

    # shellcheck disable=SC2034
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
    echo -e "${BOLD}═══ Run Checks Pipeline ═══${NC}"
    echo ""
    echo "Run health checks through the orchestrated pipeline"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --checkers NAME...     Specific checkers to run"
    echo "  --hostname=HOST        Override hostname in labels"
    echo "  --no-incidents         Skip incident creation"
    echo ""

    local options=(
        "Run all checks"
        "Run specific checkers"
        "Run all checks (dry run)"
        "Run all checks (JSON output)"
        "Back"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only"
                ;;
            2)
                read -p "Enter checker names (space-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline --checks-only --checkers $checker_names"
                else
                    echo -e "${RED}Checker names required${NC}"
                fi
                ;;
            3)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only --dry-run"
                ;;
            4)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only --json"
                ;;
            5)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}